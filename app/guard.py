"""Deterministic rule guard that checks every purchase before money moves."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.catalog import get_product
from app.config import get_settings
from app.db import is_kill_switch_on, new_id, utc_now
from app.ledger import append_event
from app.mandate import get_mandate_by_id, parse_iso_datetime

BLOCKING_CODES = frozenset(
    {
        "KILL_SWITCH",
        "MANDATE_INACTIVE",
        "MANDATE_EXPIRED",
        "OVER_ITEM_CAP",
        "OVER_DAY_CAP",
        "ITEM_MISMATCH",
    }
)

RiskEvaluator = Callable[[dict[str, Any]], dict[str, Any]]


def _default_heuristic_only_risk(product: dict[str, Any]) -> dict[str, Any]:
    """Fallback risk evaluator before M4 risk module is wired."""
    return {"score": 0.0, "reasons": ["Risk module not wired; default score 0.0."]}


def _todays_approved_spend(
    conn: sqlite3.Connection,
    mandate_id: str,
    now_dt: datetime,
) -> float:
    """Sum today's (UTC) approved or human-approved HELD purchases for this mandate."""
    target_date = now_dt.astimezone(timezone.utc).date()
    rows = conn.execute(
        """
        SELECT price_usd, decision, human_resolution, created_at
        FROM attempts
        WHERE mandate_id = ?
          AND (decision = 'APPROVED' OR (decision = 'HELD' AND human_resolution = 'approved'))
        """,
        (mandate_id,),
    ).fetchall()

    total = 0.0
    for row in rows:
        created_dt = parse_iso_datetime(row["created_at"]).astimezone(timezone.utc)
        if created_dt.date() == target_date:
            total += float(row["price_usd"])
    return round(total, 2)


def evaluate_purchase(
    conn: sqlite3.Connection,
    mandate_id: str,
    product_id: str,
    now: datetime | None = None,
    request_text: str | None = None,
    risk_evaluator: RiskEvaluator | None = None,
) -> dict[str, Any]:
    """Validate a proposed product purchase against catalog truth and mandate rules.

    Never accepts price or seller from the caller: loads both from data/catalog.json.
    """
    product = get_product(product_id)
    if product is None:
        raise KeyError(f"Product '{product_id}' does not exist in catalog.")

    now_dt = (now or utc_now()).astimezone(timezone.utc)
    now_iso = now_dt.isoformat()
    real_price = float(product["price_usd"])
    real_seller = str(product["seller"])
    mandate = get_mandate_by_id(conn, mandate_id)

    checks: list[dict[str, Any]] = []
    reason_codes: list[str] = []

    # 1. KILL_SWITCH
    kill_on = is_kill_switch_on(conn)
    checks.append(
        {
            "name": "KILL_SWITCH",
            "passed": not kill_on,
            "skipped": False,
            "detail": "Kill switch is ON — all agent spending is frozen."
            if kill_on
            else "Kill switch is off.",
        }
    )
    if kill_on:
        reason_codes.append("KILL_SWITCH")

    # 2. MANDATE_INACTIVE
    mandate_active = (
        mandate is not None
        and bool(mandate.get("confirmed_at"))
        and mandate.get("status") == "active"
    )
    checks.append(
        {
            "name": "MANDATE_INACTIVE",
            "passed": mandate_active,
            "skipped": False,
            "detail": "Mandate is confirmed and active."
            if mandate_active
            else "Mandate is missing, unconfirmed, or revoked.",
        }
    )
    if not mandate_active:
        reason_codes.append("MANDATE_INACTIVE")

    if mandate is not None:
        # 3. MANDATE_EXPIRED
        expires_dt = parse_iso_datetime(str(mandate["expires_at"]))
        not_expired = now_dt < expires_dt
        checks.append(
            {
                "name": "MANDATE_EXPIRED",
                "passed": not_expired,
                "skipped": False,
                "detail": f"Valid until {mandate['expires_at']}."
                if not_expired
                else f"Mandate expired at {mandate['expires_at']} (checked at {now_iso}).",
            }
        )
        if not not_expired:
            reason_codes.append("MANDATE_EXPIRED")

        # 4. OVER_ITEM_CAP
        max_item = float(mandate["max_item_usd"])
        within_item = real_price <= max_item
        checks.append(
            {
                "name": "OVER_ITEM_CAP",
                "passed": within_item,
                "skipped": False,
                "detail": f"Catalog price ${real_price:.2f} <= item cap ${max_item:.2f}."
                if within_item
                else f"Catalog price ${real_price:.2f} exceeds item cap ${max_item:.2f}.",
            }
        )
        if not within_item:
            reason_codes.append("OVER_ITEM_CAP")

        # 5. OVER_DAY_CAP
        spent_today = _todays_approved_spend(conn, mandate_id, now_dt)
        max_day = float(mandate["max_day_usd"])
        within_day = round(spent_today + real_price, 2) <= max_day
        checks.append(
            {
                "name": "OVER_DAY_CAP",
                "passed": within_day,
                "skipped": False,
                "detail": (
                    f"Today's approved spend (${spent_today:.2f}) + ${real_price:.2f} "
                    f"<= daily cap ${max_day:.2f}."
                )
                if within_day
                else (
                    f"Today's approved spend (${spent_today:.2f}) + ${real_price:.2f} "
                    f"exceeds daily cap ${max_day:.2f}."
                ),
            }
        )
        if not within_day:
            reason_codes.append("OVER_DAY_CAP")

        # 6. ITEM_MISMATCH
        haystack = " ".join(
            [str(product["title"]), " ".join(str(t) for t in product.get("tags", []))]
        ).lower()
        keywords = [str(k).lower() for k in mandate["item_keywords"]]
        missing_keywords = [kw for kw in keywords if kw not in haystack]
        item_matches = len(missing_keywords) == 0
        checks.append(
            {
                "name": "ITEM_MISMATCH",
                "passed": item_matches,
                "skipped": False,
                "detail": f"All mandate keywords matched: {keywords}."
                if item_matches
                else f"Product missing required keywords: {missing_keywords}.",
            }
        )
        if not item_matches:
            reason_codes.append("ITEM_MISMATCH")

        # 7. UNKNOWN_SELLER (HELD if failed)
        allowed_sellers = [str(s).lower() for s in mandate["allowed_sellers"]]
        known_seller = real_seller.lower() in allowed_sellers
        checks.append(
            {
                "name": "UNKNOWN_SELLER",
                "passed": known_seller,
                "skipped": False,
                "detail": f"Seller '{real_seller}' is in allowed_sellers."
                if known_seller
                else f"Seller '{real_seller}' is not in allowed_sellers {allowed_sellers} (held for human approval).",
            }
        )
        if not known_seller:
            reason_codes.append("UNKNOWN_SELLER")
    else:
        for skipped_code in (
            "MANDATE_EXPIRED",
            "OVER_ITEM_CAP",
            "OVER_DAY_CAP",
            "ITEM_MISMATCH",
            "UNKNOWN_SELLER",
        ):
            checks.append(
                {
                    "name": skipped_code,
                    "passed": None,
                    "skipped": True,
                    "detail": "Skipped because mandate does not exist.",
                }
            )

    # 8. HIGH_RISK (Cost rule: run risk check ONLY if no BLOCKED reason exists)
    has_blocking = any(code in BLOCKING_CODES for code in reason_codes)
    threshold = get_settings().risk_threshold
    risk_score = 0.0
    risk_reasons: list[str] = []

    if has_blocking:
        risk_reasons = ["Skipped risk evaluation because deterministic guard rules already blocked this purchase."]
        checks.append(
            {
                "name": "HIGH_RISK",
                "passed": None,
                "skipped": True,
                "detail": risk_reasons[0],
            }
        )
    else:
        evaluator = risk_evaluator or _default_heuristic_only_risk
        risk_res = evaluator(product)
        risk_score = float(risk_res.get("score", 0.0))
        risk_reasons = [str(r) for r in risk_res.get("reasons", [])]
        low_risk = risk_score < threshold
        checks.append(
            {
                "name": "HIGH_RISK",
                "passed": low_risk,
                "skipped": False,
                "detail": (
                    f"Risk score {risk_score:.2f} < threshold {threshold:.2f}."
                    if low_risk
                    else f"Risk score {risk_score:.2f} >= threshold {threshold:.2f} (held for human approval)."
                ),
            }
        )
        if not low_risk:
            reason_codes.append("HIGH_RISK")

    # Decision precedence: any BLOCKED -> BLOCKED; else any HELD -> HELD; else APPROVED
    if has_blocking:
        decision = "BLOCKED"
    elif reason_codes:
        decision = "HELD"
    else:
        decision = "APPROVED"

    attempt_id = new_id("att")
    conn.execute(
        """
        INSERT INTO attempts (
            id, mandate_id, product_id, seller, price_usd, decision,
            reason_codes, checks, risk_score, risk_reasons,
            human_resolution, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'decided', ?)
        """,
        (
            attempt_id,
            mandate_id,
            product_id,
            real_seller,
            real_price,
            decision,
            json.dumps(reason_codes),
            json.dumps(checks),
            risk_score,
            json.dumps(risk_reasons),
            now_iso,
        ),
    )

    user_request_text = request_text or (mandate["raw_text"] if mandate else "")
    ledger_body = {
        "attempt_id": attempt_id,
        "mandate_id": mandate_id,
        "product_id": product_id,
        "decision": decision,
        "reason_codes": reason_codes,
        "checks": checks,
        "risk_score": risk_score,
        "risk_reasons": risk_reasons,
        "request_text": user_request_text,
        "mandate_snapshot": mandate,
        "product_snapshot": product,
    }
    append_event(
        conn,
        event_type="PURCHASE_DECIDED",
        ref_id=attempt_id,
        body=ledger_body,
        ts=now_iso,
    )

    return {
        "id": attempt_id,
        "mandate_id": mandate_id,
        "product_id": product_id,
        "product_title": product["title"],
        "seller": real_seller,
        "price_usd": real_price,
        "decision": decision,
        "reason_codes": reason_codes,
        "checks": checks,
        "risk_score": risk_score,
        "risk_reasons": risk_reasons,
        "human_resolution": None,
        "status": "decided",
        "created_at": now_iso,
    }
