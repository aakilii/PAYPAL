"""PayPal Sandbox REST API client (OAuth2, Orders v2, Customer Disputes v1) + mock mode."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import uuid
from typing import Any

import httpx

from app.config import Settings, get_settings, validate_paypal_sandbox_url
from app.db import new_id, utc_now_iso
from app.ledger import append_event


class PayPalError(RuntimeError):
    """Raised when a PayPal Sandbox request fails or credentials are missing."""


_PAYPAL_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")

# Simple in-process token cache keyed by (client_id, base_url)
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()


def clear_token_cache() -> None:
    """Reset cached OAuth tokens (useful in tests)."""
    with _TOKEN_LOCK:
        _TOKEN_CACHE.clear()


def _invalidate_token(settings: Settings) -> None:
    cache_key = (settings.paypal_client_id, settings.paypal_base_url)
    with _TOKEN_LOCK:
        _TOKEN_CACHE.pop(cache_key, None)


def validate_paypal_order_id(paypal_order_id: str) -> str:
    """Ensure PayPal order token contains only safe identifier characters."""
    cleaned = paypal_order_id.strip()
    if not _PAYPAL_ORDER_ID_RE.fullmatch(cleaned):
        raise ValueError("Invalid PayPal order token format.")
    return cleaned


def _require_sandbox_credentials(settings: Settings) -> None:
    validate_paypal_sandbox_url(settings.paypal_base_url)
    if not settings.paypal_client_id or not settings.paypal_client_secret:
        raise PayPalError(
            "PayPal Sandbox credentials are missing in .env (complete Task U1 or switch PAYPAL_MODE=mock)."
        )


def _extract_paypal_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            msg = payload.get("message") or payload.get("error_description") or payload.get("name")
            details = payload.get("details")
            if isinstance(details, list) and details:
                issue = details[0].get("description") or details[0].get("issue")
                if issue:
                    return f"PayPal HTTP {response.status_code}: {msg} ({issue})"
            if msg:
                return f"PayPal HTTP {response.status_code}: {msg}"
    except ValueError:
        pass
    return f"PayPal HTTP {response.status_code}: {response.text[:200]}"


def get_access_token(settings: Settings | None = None, http_client: httpx.Client | None = None) -> str:
    """Fetch and cache a PayPal Sandbox OAuth2 access token."""
    cfg = settings or get_settings()
    _require_sandbox_credentials(cfg)

    cache_key = (cfg.paypal_client_id, cfg.paypal_base_url)
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        now_mono = time.monotonic()
        if cached is not None and now_mono < cached[1]:
            return cached[0]

    url = f"{cfg.paypal_base_url}/v1/oauth2/token"
    try:
        if http_client is not None:
            resp = http_client.post(
                url,
                auth=(cfg.paypal_client_id, cfg.paypal_client_secret),
                headers={"Accept": "application/json"},
                data={"grant_type": "client_credentials"},
                timeout=15.0,
            )
        else:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    url,
                    auth=(cfg.paypal_client_id, cfg.paypal_client_secret),
                    headers={"Accept": "application/json"},
                    data={"grant_type": "client_credentials"},
                )
    except httpx.HTTPError as exc:
        raise PayPalError(f"Could not reach PayPal OAuth endpoint: {exc}") from exc

    if resp.status_code >= 400:
        raise PayPalError(_extract_paypal_error_message(resp))

    data = resp.json()
    token = str(data.get("access_token", ""))
    if not token:
        raise PayPalError("PayPal OAuth response did not contain access_token.")

    expires_in = int(data.get("expires_in", 300))
    # Refresh 60s before expiry (or immediately if short-lived)
    ttl = max(10, expires_in - 60)
    with _TOKEN_LOCK:
        _TOKEN_CACHE[cache_key] = (token, time.monotonic() + ttl)
    return token


def _find_approval_link(links: list[dict[str, Any]]) -> str | None:
    """PayPal Orders v2 returns 'payer-action' (with payment_source) or 'approve'."""
    for target_rel in ("payer-action", "approve"):
        for link in links:
            href = link.get("href")
            if link.get("rel") == target_rel and isinstance(href, str):
                if href.startswith(("https://", "http://")):
                    return href
    return None


def _post_paypal_json(
    settings: Settings,
    path: str,
    payload: dict[str, Any],
    idempotency_key: str,
    action_label: str,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """POST JSON to PayPal Sandbox REST API with idempotency and single 401 token-refresh retry."""
    url = f"{settings.paypal_base_url}{path}"
    for attempt_idx in range(2):
        token = get_access_token(settings, http_client=http_client)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": idempotency_key,
        }
        try:
            if http_client is not None:
                resp = http_client.post(url, headers=headers, json=payload, timeout=20.0)
            else:
                with httpx.Client(timeout=20.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise PayPalError(f"Network error {action_label}: {exc}") from exc

        if resp.status_code == 401 and attempt_idx == 0:
            _invalidate_token(settings)
            continue

        if resp.status_code >= 400:
            raise PayPalError(_extract_paypal_error_message(resp))

        return resp.json()

    raise PayPalError(f"PayPal authorization failed while {action_label}.")


def create_order_for_attempt(
    conn: sqlite3.Connection,
    attempt: dict[str, Any],
    http_client: httpx.Client | None = None,
    base_url_override: str | None = None,
) -> dict[str, Any]:
    """Create a PayPal order (mock or sandbox) for an approved or human-approved attempt."""
    settings = get_settings()
    callback_base = (base_url_override or settings.app_base_url).rstrip("/")
    attempt_id = str(attempt["id"])
    amount_usd = round(float(attempt["price_usd"]), 2)

    # Idempotency: if an order already exists for this attempt, return it.
    existing = conn.execute(
        "SELECT * FROM orders WHERE attempt_id = ? ORDER BY created_at DESC LIMIT 1",
        (attempt_id,),
    ).fetchone()
    if existing is not None:
        return dict(existing)

    if settings.paypal_mode == "mock":
        paypal_order_id = f"MOCK-{uuid.uuid4().hex[:6].upper()}"
        approval_url = f"{callback_base}/paypal/return?token={paypal_order_id}&PayerID=MOCKBUYER"
        order_status = "CREATED"
        source = "mock"
    else:
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": attempt_id,
                    "custom_id": attempt_id,
                    "description": f"Mandate order for {attempt.get('product_title', attempt['product_id'])}",
                    "amount": {
                        "currency_code": "USD",
                        "value": f"{amount_usd:.2f}",
                    },
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "Mandate Demo",
                        "shipping_preference": "NO_SHIPPING",
                        "user_action": "PAY_NOW",
                        "return_url": f"{callback_base}/paypal/return",
                        "cancel_url": f"{callback_base}/paypal/cancel",
                    }
                }
            },
        }
        data = _post_paypal_json(
            settings,
            path="/v2/checkout/orders",
            payload=payload,
            idempotency_key=attempt_id,
            action_label="creating PayPal order",
            http_client=http_client,
        )
        paypal_order_id = str(data.get("id", ""))
        if not paypal_order_id:
            raise PayPalError("PayPal create-order response missing 'id'.")
        order_status = str(data.get("status", "CREATED"))
        approval_url = _find_approval_link(data.get("links", []))
        source = "sandbox"

    order_id = new_id("ord")
    created_at = utc_now_iso()
    conn.execute(
        """
        INSERT INTO orders (
            id, attempt_id, paypal_order_id, capture_id, status,
            amount_usd, source, approval_url, created_at
        )
        VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            attempt_id,
            paypal_order_id,
            order_status,
            amount_usd,
            source,
            approval_url,
            created_at,
        ),
    )
    conn.execute(
        "UPDATE attempts SET status = 'order_created' WHERE id = ?",
        (attempt_id,),
    )

    order = {
        "id": order_id,
        "attempt_id": attempt_id,
        "paypal_order_id": paypal_order_id,
        "capture_id": None,
        "status": order_status,
        "amount_usd": amount_usd,
        "source": source,
        "approval_url": approval_url,
        "created_at": created_at,
    }
    append_event(
        conn,
        event_type="ORDER_CREATED",
        ref_id=order_id,
        body=order,
        ts=created_at,
    )
    return order


def capture_order_by_paypal_id(
    conn: sqlite3.Connection,
    paypal_order_id: str,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Capture an approved PayPal order (mock or sandbox) and log ORDER_CAPTURED."""
    safe_token = validate_paypal_order_id(paypal_order_id)
    row = conn.execute(
        "SELECT * FROM orders WHERE paypal_order_id = ?",
        (safe_token,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Order with PayPal token '{safe_token}' not found.")

    order = dict(row)
    if order["status"] == "COMPLETED" and order["capture_id"]:
        return order
    if order["status"] == "CANCELLED":
        raise PayPalError(f"Cannot capture cancelled PayPal order '{safe_token}'.")

    settings = get_settings()
    if order["source"] == "mock" or settings.paypal_mode == "mock":
        capture_id = f"CAP-{safe_token}"
        new_status = "COMPLETED"
    else:
        data = _post_paypal_json(
            settings,
            path=f"/v2/checkout/orders/{safe_token}/capture",
            payload={},
            idempotency_key=f"{order['attempt_id']}-capture",
            action_label="capturing PayPal order",
            http_client=http_client,
        )
        new_status = str(data.get("status", "COMPLETED"))
        capture_id = None
        units = data.get("purchase_units") or []
        if units and isinstance(units[0], dict):
            captures = (units[0].get("payments") or {}).get("captures") or []
            if captures and isinstance(captures[0], dict):
                cap_status = str(captures[0].get("status") or "").upper()
                if cap_status in ("DECLINED", "FAILED"):
                    raise PayPalError(f"PayPal capture failed with status '{cap_status}'.")
                capture_id = captures[0].get("id")
        if not capture_id:
            capture_id = f"CAP-{safe_token}"

    conn.execute(
        "UPDATE orders SET capture_id = ?, status = ? WHERE id = ?",
        (capture_id, new_status, order["id"]),
    )
    conn.execute(
        "UPDATE attempts SET status = 'paid' WHERE id = ?",
        (order["attempt_id"],),
    )

    captured_ts = utc_now_iso()
    updated = {
        **order,
        "capture_id": capture_id,
        "status": new_status,
    }
    append_event(
        conn,
        event_type="ORDER_CAPTURED",
        ref_id=str(order["id"]),
        body={
            "order_id": order["id"],
            "attempt_id": order["attempt_id"],
            "paypal_order_id": safe_token,
            "capture_id": capture_id,
            "status": new_status,
            "amount_usd": float(order["amount_usd"]),
            "source": order["source"],
        },
        ts=captured_ts,
    )
    return updated


def cancel_order_by_paypal_id(conn: sqlite3.Connection, paypal_order_id: str) -> dict[str, Any] | None:
    """Mark a pending order (CREATED or PAYER_ACTION_REQUIRED) as CANCELLED."""
    safe_token = validate_paypal_order_id(paypal_order_id)
    row = conn.execute(
        "SELECT * FROM orders WHERE paypal_order_id = ?",
        (safe_token,),
    ).fetchone()
    if row is None:
        return None
    if row["status"] in ("CREATED", "PAYER_ACTION_REQUIRED"):
        conn.execute(
            "UPDATE orders SET status = 'CANCELLED' WHERE id = ?",
            (row["id"],),
        )
    updated = conn.execute("SELECT * FROM orders WHERE id = ?", (row["id"],)).fetchone()
    return dict(updated) if updated is not None else None


def fetch_sandbox_disputes(
    http_client: httpx.Client | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read real PayPal Sandbox disputes if enabled/present; never fail hard on missing scope."""
    settings = get_settings()
    if settings.paypal_mode != "sandbox":
        return [], "PAYPAL_MODE is mock; skipping live Sandbox dispute query."

    try:
        token = get_access_token(settings, http_client=http_client)
        url = f"{settings.paypal_base_url}/v1/customer/disputes"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if http_client is not None:
            resp = http_client.get(url, headers=headers, timeout=15.0)
        else:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url, headers=headers)

        if resp.status_code >= 400:
            return [], _extract_paypal_error_message(resp)

        items = resp.json().get("items") or []
        return list(items), None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def record_paypal_error(conn: sqlite3.Connection, ref_id: str, message: str) -> None:
    """Record a PayPal failure in the ledger so errors are visible and auditable."""
    append_event(
        conn,
        event_type="PAYPAL_ERROR",
        ref_id=ref_id,
        body={"ref_id": ref_id, "message": message},
    )
