"""FastAPI entry point for Mandate."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import os
from pathlib import Path
import re
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.responses import Response

from app.agent import run as run_agent
from app.catalog import get_product, list_public_catalog
from app.config import get_settings
from app.db import db_session, init_db, is_kill_switch_on, set_kill_switch_value, utc_now
from app.defender import defend_dispute, list_disputes, simulate_dispute
from app.guard import _todays_approved_spend, get_attempt_by_id, list_holds, resolve_hold
from app.ledger import append_event, dev_tamper_entry, list_events, verify
from app.mandate import (
    MandateConfirmRequest,
    MandateParseRequest,
    confirm_mandate,
    get_active_mandate,
    get_mandate_by_id,
    parse_mandate_draft,
    revoke_mandate,
)
from app.paypal import (
    PayPalError,
    cancel_order_by_paypal_id,
    capture_order_by_paypal_id,
    create_order_for_attempt,
    record_paypal_error,
    validate_paypal_order_id,
)

_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(?::\d{1,5})?$")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run startup safety checks and initialize SQLite tables before serving."""
    settings = get_settings()
    init_db(settings.db_path)
    yield


app = FastAPI(
    title="Mandate",
    description="Safety and proof layer between AI shopping agents and PayPal.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def add_security_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach baseline HTTP security headers to all responses."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
    )
    return response


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index() -> FileResponse:
    """Serve the single-page web UI."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
def health() -> dict[str, str]:
    """Basic liveness probe."""
    return {"status": "ok"}


@app.get("/api/config")
def api_config() -> dict[str, object]:
    """Return non-secret runtime settings for the UI."""
    return get_settings().to_public_dict()


@app.post("/api/mandates/parse")
def api_parse_mandate(payload: MandateParseRequest) -> dict[str, object]:
    """Parse plain-English text into an unconfirmed draft mandate with assumptions."""
    try:
        draft = parse_mandate_draft(payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return draft.model_dump()


@app.post("/api/mandates")
def api_confirm_mandate(payload: MandateConfirmRequest) -> dict[str, object]:
    """Confirm and save a mandate, recording MANDATE_CONFIRMED in the ledger."""
    with db_session(write=True) as conn:
        mandate = confirm_mandate(conn, payload)
    return {"mandate": mandate}


@app.post("/api/mandates/{mandate_id}/revoke")
def api_revoke_mandate(mandate_id: str) -> dict[str, object]:
    """Revoke an active mandate and append MANDATE_REVOKED to the ledger."""
    try:
        with db_session(write=True) as conn:
            mandate = revoke_mandate(conn, mandate_id)
        return {"mandate": mandate}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/mandates/active")
def api_get_active_mandate() -> dict[str, object]:
    """Return the current active confirmed mandate and today's approved spend."""
    with db_session() as conn:
        mandate = get_active_mandate(conn)
        spent_today_usd = (
            _todays_approved_spend(conn, str(mandate["id"]), utc_now())
            if mandate is not None
            else 0.0
        )
    return {"mandate": mandate, "spent_today_usd": spent_today_usd}


@app.get("/api/catalog")
def api_get_catalog() -> dict[str, object]:
    """Return storefront-safe product catalog."""
    return {"products": list_public_catalog()}


@app.get("/api/holds")
def api_get_holds(include_resolved: bool = False) -> dict[str, object]:
    """List HELD purchase attempts waiting for human review."""
    with db_session() as conn:
        holds = list_holds(conn, include_resolved=include_resolved)
    return {"holds": holds}


def _resolve_callback_base_url(request: Request) -> str:
    """Use configured APP_BASE_URL when non-local, or derive validated host from proxy headers."""
    configured = (os.getenv("APP_BASE_URL") or "").strip().rstrip("/")
    is_local_cfg = not configured or "127.0.0.1" in configured or "localhost" in configured
    if not is_local_cfg:
        return configured

    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip().lower()
    if (
        host
        and proto in ("http", "https")
        and _SAFE_HOST_RE.fullmatch(host)
        and not host.startswith(("127.0.0.1", "localhost", "testserver"))
    ):
        return f"{proto}://{host}".rstrip("/")
    return get_settings().app_base_url


def _try_create_order_for_attempt(
    attempt: dict[str, object],
    base_url_override: str | None = None,
) -> tuple[dict[str, object] | None, str | None]:
    """Create a PayPal order safely; on failure roll back partial order and log PAYPAL_ERROR."""
    attempt_id = str(attempt["id"])
    try:
        with db_session(write=True) as conn:
            order = create_order_for_attempt(conn, attempt, base_url_override=base_url_override)
            refreshed = get_attempt_by_id(conn, attempt_id)
            if refreshed is not None:
                attempt.update(refreshed)
        return order, None
    except PayPalError as exc:
        with db_session(write=True) as err_conn:
            record_paypal_error(err_conn, attempt_id, str(exc))
        return None, str(exc)


@app.post("/api/holds/{attempt_id}/approve")
def api_approve_hold(attempt_id: str, request: Request) -> dict[str, object]:
    """Human-in-the-loop approval of a HELD purchase attempt."""
    try:
        with db_session(write=True) as conn:
            attempt = resolve_hold(conn, attempt_id, "approved")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    order, paypal_error = _try_create_order_for_attempt(attempt, _resolve_callback_base_url(request))
    return {"attempt": attempt, "order": order, "paypal_error": paypal_error}


@app.post("/api/holds/{attempt_id}/deny")
def api_deny_hold(attempt_id: str) -> dict[str, object]:
    """Human-in-the-loop denial of a HELD purchase attempt."""
    try:
        with db_session(write=True) as conn:
            attempt = resolve_hold(conn, attempt_id, "denied")
        return {"attempt": attempt}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AgentRunRequest(BaseModel):
    mandate_id: str = Field(min_length=1, max_length=64)
    request_text: str = Field(min_length=1, max_length=500)
    attack: str = Field(default="none")

    @field_validator("mandate_id", "request_text")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty.")
        return cleaned


class KillSwitchRequest(BaseModel):
    enabled: bool


def _summarize_event(event: dict[str, object]) -> str:
    etype = str(event.get("type", ""))
    body = event.get("body")
    b = body if isinstance(body, dict) else {}
    if etype == "MANDATE_CONFIRMED":
        return f"Mandate confirmed (cap ${b.get('max_item_usd', '?')}/item, keywords {b.get('item_keywords', [])})."
    if etype == "MANDATE_REVOKED":
        return f"Mandate {b.get('mandate_id', '?')} revoked by user."
    if etype == "PURCHASE_DECIDED":
        prod = b.get("product_snapshot") if isinstance(b.get("product_snapshot"), dict) else {}
        return f"Guard {b.get('decision', '?')}: {prod.get('title', b.get('product_id', '?'))} (${prod.get('price_usd', '?')}) {b.get('reason_codes', [])}"
    if etype == "HUMAN_RESOLVED":
        return f"Human {b.get('resolution', '?')} held attempt {b.get('attempt_id', '?')} (${b.get('price_usd', '?')})."
    if etype == "KILL_SWITCH_CHANGED":
        return f"Kill switch turned {'ON (agent frozen)' if b.get('enabled') else 'OFF (agent active)'}."
    if etype == "ORDER_CREATED":
        return f"Order created ({b.get('source', '?')}): {b.get('paypal_order_id', '?')} for ${b.get('amount_usd', '?')}."
    if etype == "ORDER_CAPTURED":
        return f"Payment captured ({b.get('source', '?')}): {b.get('paypal_order_id', '?')} (${b.get('amount_usd', '?')})."
    if etype == "DISPUTE_RECEIVED":
        return f"Dispute received ({b.get('source', '?')}): {b.get('claim', '')}"
    if etype == "DISPUTE_REPLY_DRAFTED":
        return f"Dispute defense drafted (strength: {b.get('strength', '?')})."
    if etype == "PAYPAL_ERROR":
        return f"PayPal error: {b.get('message', 'Request failed.')}"
    return etype


@app.post("/api/agent/run")
def api_agent_run(payload: AgentRunRequest, request: Request) -> dict[str, object]:
    """Have the shopper agent propose a product and run it through the Guard."""
    try:
        with db_session(write=True) as conn:
            result = run_agent(
                conn,
                mandate_id=payload.mandate_id,
                request_text=payload.request_text,
                attack=payload.attack,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    attempt = result["attempt"]
    paypal_error = None
    if attempt["decision"] == "APPROVED":
        order, paypal_error = _try_create_order_for_attempt(attempt, _resolve_callback_base_url(request))
        result["order"] = order
    result["paypal_error"] = paypal_error
    return result


@app.get("/paypal/return")
def paypal_return(token: str = "", PayerID: str = "") -> RedirectResponse:
    """Handle PayPal buyer approval redirect, capture the order, and go to Merchant tab."""
    if not token:
        raise HTTPException(status_code=400, detail="Missing PayPal order token.")
    try:
        safe_token = validate_paypal_order_id(token)
        with db_session(write=True) as conn:
            order = capture_order_by_paypal_id(conn, safe_token)
        return RedirectResponse(url=f"/?tab=merchant&captured={order['id']}", status_code=303)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PayPalError as exc:
        with db_session(write=True) as err_conn:
            record_paypal_error(err_conn, token[:64], str(exc))
        return RedirectResponse(url=f"/?tab=merchant&paypal_error={quote(str(exc))}", status_code=303)


@app.get("/paypal/cancel")
def paypal_cancel(token: str = "") -> RedirectResponse:
    """Handle PayPal buyer cancellation redirect cleanly."""
    if token:
        try:
            safe_token = validate_paypal_order_id(token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with db_session(write=True) as conn:
            cancel_order_by_paypal_id(conn, safe_token)
    return RedirectResponse(url="/?tab=shop&paypal_cancelled=1", status_code=303)


@app.get("/api/orders")
def api_get_orders() -> dict[str, object]:
    """Merchant view: list orders with verified_mandate badge status."""
    with db_session() as conn:
        ledger_ok = bool(verify(conn)["ok"])
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
        orders: list[dict[str, object]] = []
        for row in rows:
            order = dict(row)
            attempt = get_attempt_by_id(conn, order["attempt_id"])
            mandate = get_mandate_by_id(conn, attempt["mandate_id"]) if attempt else None
            product = get_product(attempt["product_id"]) if attempt else None

            mandate_confirmed = bool(mandate and mandate.get("confirmed_at"))
            decision_ok = bool(
                attempt
                and (
                    attempt["decision"] == "APPROVED"
                    or (attempt["decision"] == "HELD" and attempt.get("human_resolution") == "approved")
                )
            )
            verified_mandate = mandate_confirmed and decision_ok and ledger_ok
            orders.append(
                {
                    **order,
                    "product_id": attempt["product_id"] if attempt else None,
                    "product_title": product["title"] if product else None,
                    "seller": attempt["seller"] if attempt else None,
                    "mandate_id": attempt["mandate_id"] if attempt else None,
                    "decision": attempt["decision"] if attempt else None,
                    "human_resolution": attempt["human_resolution"] if attempt else None,
                    "verified_mandate": verified_mandate,
                    "ledger_ok": ledger_ok,
                }
            )
    return {"orders": orders, "ledger_ok": ledger_ok}


class DisputeSimulateRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    claim: str = Field(default="I did not authorize this AI purchase.", min_length=1, max_length=400)

    @field_validator("order_id", "claim")
    @classmethod
    def strip_non_empty_dispute(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty.")
        return cleaned


@app.post("/api/disputes/simulate")
def api_simulate_dispute(payload: DisputeSimulateRequest) -> dict[str, object]:
    """Create a simulated PayPal-shaped dispute and log DISPUTE_RECEIVED."""
    with db_session(write=True) as conn:
        dispute = simulate_dispute(conn, order_id=payload.order_id, claim=payload.claim)
    return {"dispute": dispute}


@app.get("/api/disputes")
def api_get_disputes(sync_paypal: bool = False) -> dict[str, object]:
    """Return disputes, optionally checking PayPal Sandbox Customer Disputes API."""
    with db_session(write=sync_paypal) as conn:
        disputes, sync_note = list_disputes(conn, sync_paypal=sync_paypal)
    return {"disputes": disputes, "sync_note": sync_note}


@app.post("/api/disputes/{dispute_id}/defend")
def api_defend_dispute(dispute_id: str) -> dict[str, object]:
    """Build ledger-only evidence packet and draft factual merchant defense reply."""
    try:
        with db_session(write=True) as conn:
            dispute = defend_dispute(conn, dispute_id)
        return {"dispute": dispute}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/kill-switch")
def api_get_kill_switch() -> dict[str, bool]:
    """Check whether the global agent kill switch is active."""
    with db_session() as conn:
        return {"enabled": is_kill_switch_on(conn)}


@app.post("/api/kill-switch")
def api_set_kill_switch(payload: KillSwitchRequest) -> dict[str, bool]:
    """Freeze or unfreeze the agent and log KILL_SWITCH_CHANGED in the ledger."""
    with db_session(write=True) as conn:
        set_kill_switch_value(conn, payload.enabled)
        append_event(
            conn,
            event_type="KILL_SWITCH_CHANGED",
            ref_id="global",
            body={"enabled": payload.enabled},
        )
    return {"enabled": payload.enabled}


@app.get("/api/activity")
def api_get_activity(since: str | None = None) -> dict[str, object]:
    """Return ledger events for the live activity feed (polls every 2s)."""
    since_seq: int | None = None
    since_ts: str | None = None
    if since is not None and since.strip():
        cleaned = since.strip()
        if cleaned.isdigit():
            since_seq = int(cleaned)
        else:
            since_ts = cleaned

    with db_session() as conn:
        events = list_events(conn, since_seq=since_seq)

    if since_ts is not None:
        events = [e for e in events if str(e["ts"]) > since_ts]

    feed = [{**e, "summary": _summarize_event(e)} for e in events]
    latest_seq = max((int(e["seq"]) for e in feed), default=(since_seq or 0))
    return {"events": feed, "latest_seq": latest_seq}


@app.get("/api/ledger")
def api_get_ledger() -> dict[str, object]:
    """Return all ledger entries and the current verification result."""
    with db_session() as conn:
        events = list_events(conn)
        verification = verify(conn)
    return {"events": events, "verification": verification}


@app.get("/api/ledger/verify")
def api_verify_ledger() -> dict[str, object]:
    """Walk the entire hash chain and report integrity."""
    with db_session() as conn:
        return verify(conn)


@app.post("/api/dev/tamper/{seq}")
def api_dev_tamper(seq: int) -> dict[str, object]:
    """DEV_MODE ONLY: mutate one stored entry so the UI can demonstrate tamper detection."""
    try:
        with db_session(write=True) as conn:
            result = dev_tamper_entry(conn, seq)
            verification = verify(conn)
        return {**result, "verification": verification}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
