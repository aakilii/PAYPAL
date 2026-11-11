"""Dispute defender: builds evidence packets strictly from the ledger and drafts factual replies."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.db import new_id, utc_now_iso
from app.ledger import append_event, list_events, verify
from app.llm import draft_dispute_reply_llm
from app.paypal import fetch_sandbox_disputes


def build_evidence_packet_from_ledger(
    conn: sqlite3.Connection,
    dispute: dict[str, Any],
) -> tuple[dict[str, Any], str, list[str]]:
    """Assemble an evidence packet using ONLY events stored in the ledger.

    Returns (packet_dict, strength, missing_requirements).
    """
    order_ref = str(dispute["order_id"])
    events = list_events(conn)
    ledger_check = verify(conn)

    order_created_ev: dict[str, Any] | None = None
    order_captured_ev: dict[str, Any] | None = None
    for ev in events:
        body = ev.get("body") or {}
        if not isinstance(body, dict):
            continue
        matches_order = (
            ev["ref_id"] == order_ref
            or body.get("order_id") == order_ref
            or body.get("paypal_order_id") == order_ref
        )
        if not matches_order:
            continue
        if ev["type"] == "ORDER_CREATED":
            order_created_ev = ev
        elif ev["type"] == "ORDER_CAPTURED":
            order_captured_ev = ev

    if order_created_ev is None and order_captured_ev is None:
        packet = {
            "dispute": {
                "id": dispute["id"],
                "order_id": order_ref,
                "paypal_dispute_id": dispute.get("paypal_dispute_id"),
                "claim": dispute["claim"],
                "source": dispute["source"],
            },
            "order": None,
            "mandate": None,
            "request_text": None,
            "guard_decision": None,
            "human_resolution": None,
            "ledger_verification": ledger_check,
        }
        return packet, "NO_RECORD", ["No matching order events exist in the ledger."]

    created_body = (order_created_ev or {}).get("body") or {}
    captured_body = (order_captured_ev or {}).get("body") or {}
    attempt_id = str(captured_body.get("attempt_id") or created_body.get("attempt_id") or "")
    paypal_order_id = captured_body.get("paypal_order_id") or created_body.get("paypal_order_id")
    capture_id = captured_body.get("capture_id")
    amount_usd = captured_body.get("amount_usd") or created_body.get("amount_usd")

    purchase_decided_ev: dict[str, Any] | None = None
    human_resolved_ev: dict[str, Any] | None = None
    for ev in events:
        if ev["ref_id"] != attempt_id:
            continue
        if ev["type"] == "PURCHASE_DECIDED":
            purchase_decided_ev = ev
        elif ev["type"] == "HUMAN_RESOLVED":
            human_resolved_ev = ev

    decided_body = (purchase_decided_ev or {}).get("body") or {}
    mandate_snap = decided_body.get("mandate_snapshot")
    mandate_id = (
        str(mandate_snap.get("id"))
        if isinstance(mandate_snap, dict) and mandate_snap.get("id")
        else None
    )

    mandate_confirmed_ev: dict[str, Any] | None = None
    if mandate_id:
        for ev in events:
            if ev["type"] == "MANDATE_CONFIRMED" and ev["ref_id"] == mandate_id:
                mandate_confirmed_ev = ev
                break

    confirmed_body = (mandate_confirmed_ev or {}).get("body") or {}
    mandate_confirmed_at = confirmed_body.get("confirmed_at") or (
        mandate_confirmed_ev["ts"] if mandate_confirmed_ev else None
    )

    human_body = (human_resolved_ev or {}).get("body") or {}
    human_resolution = human_body.get("resolution")
    decision = decided_body.get("decision")

    missing: list[str] = []
    if not (mandate_confirmed_ev and mandate_confirmed_at):
        missing.append("Confirmed mandate record is missing from the ledger.")
    decision_valid = decision == "APPROVED" or (
        decision == "HELD" and human_resolution == "approved"
    )
    if not decision_valid:
        missing.append("Purchase decision was neither APPROVED nor HELD-then-human-approved.")
    if not (order_captured_ev and capture_id):
        missing.append("Order capture event (capture_id) is missing from the ledger.")
    if not ledger_check.get("ok"):
        bad_seq = ledger_check.get("first_bad_seq")
        missing.append(f"Ledger tamper check failed at seq #{bad_seq}.")

    strength = "STRONG" if not missing else "WEAK"

    packet = {
        "dispute": {
            "id": dispute["id"],
            "order_id": order_ref,
            "paypal_dispute_id": dispute.get("paypal_dispute_id"),
            "claim": dispute["claim"],
            "source": dispute["source"],
        },
        "order": {
            "order_id": captured_body.get("order_id") or created_body.get("id") or order_ref,
            "attempt_id": attempt_id or None,
            "paypal_order_id": paypal_order_id,
            "capture_id": capture_id,
            "amount_usd": amount_usd,
            "source": captured_body.get("source") or created_body.get("source"),
            "created_at": order_created_ev["ts"] if order_created_ev else None,
            "captured_at": order_captured_ev["ts"] if order_captured_ev else None,
        },
        "mandate": {
            "id": mandate_id,
            "raw_text": confirmed_body.get("raw_text")
            or (mandate_snap.get("raw_text") if isinstance(mandate_snap, dict) else None),
            "confirmed_at": mandate_confirmed_at,
            "max_item_usd": confirmed_body.get("max_item_usd"),
            "max_day_usd": confirmed_body.get("max_day_usd"),
            "allowed_sellers": confirmed_body.get("allowed_sellers"),
        },
        "request_text": decided_body.get("request_text"),
        "guard_decision": {
            "decision": decision,
            "reason_codes": decided_body.get("reason_codes"),
            "checks": decided_body.get("checks"),
            "risk_score": decided_body.get("risk_score"),
            "product": decided_body.get("product_snapshot"),
        },
        "human_resolution": (
            {
                "resolution": human_resolution,
                "resolved_at": human_resolved_ev["ts"],
            }
            if human_resolved_ev
            else None
        ),
        "ledger_verification": ledger_check,
        "missing_requirements": missing,
    }
    return packet, strength, missing


def _template_reply(
    packet: dict[str, Any],
    strength: str,
    missing: list[str],
) -> str:
    dispute_order_id = str(packet["dispute"]["order_id"])
    order = packet.get("order")

    if strength == "NO_RECORD" or not order:
        return (
            f"Do not contest this dispute for order {dispute_order_id} (amount unknown / $0.00 recorded). "
            "No matching order, mandate confirmation, or capture event exists in the ledger."
        )

    order_id = str(order.get("order_id") or dispute_order_id)
    pp_id = order.get("paypal_order_id")
    cap_id = order.get("capture_id")
    amount = float(order["amount_usd"]) if order.get("amount_usd") is not None else 0.0

    if strength == "WEAK":
        reasons_text = " ".join(missing)
        return (
            f"The merchant should not contest this dispute for order {order_id} "
            f"(PayPal {pp_id or 'N/A'}, amount ${amount:.2f}). "
            f"Evidence is WEAK because required ledger proof is incomplete: {reasons_text}"
        )

    mandate = packet.get("mandate") or {}
    guard = packet.get("guard_decision") or {}
    human = packet.get("human_resolution")
    approval_note = (
        f"Guard held the item and a human explicitly approved it at {human['resolved_at']}"
        if human and human.get("resolution") == "approved"
        else f"Guard approved every deterministic rule check ({guard.get('decision')})"
    )
    return (
        f"Contest dispute with STRONG ledger evidence for order {order_id} "
        f"(PayPal order {pp_id}, capture {cap_id}, amount ${amount:.2f}). "
        f"Customer confirmed mandate {mandate.get('id')} at {mandate.get('confirmed_at')} "
        f"for request \"{packet.get('request_text') or mandate.get('raw_text')}\". "
        f"{approval_note}. Ledger SHA-256 chain verified intact."
    )


def _passes_post_check(
    reply: str,
    packet: dict[str, Any],
    strength: str,
) -> bool:
    lowered = reply.lower()
    dispute_order_id = str(packet["dispute"]["order_id"]).lower()
    order = packet.get("order") or {}
    order_id = str(order.get("order_id") or "").lower()
    pp_id = str(order.get("paypal_order_id") or "").lower()

    has_order_id = any(
        oid and oid in lowered for oid in (dispute_order_id, order_id, pp_id)
    )
    if not has_order_id:
        return False

    amount_val = order.get("amount_usd")
    if amount_val is not None:
        amt_float = float(amount_val)
        amt_candidates = {f"{amt_float:.2f}", f"${amt_float:g}", str(int(amt_float)) if amt_float.is_integer() else ""}
        if not any(tok and tok in lowered for tok in amt_candidates):
            return False

    if strength in ("WEAK", "NO_RECORD"):
        if "not contest" not in lowered:
            return False

    return True


def _row_to_dispute(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "order_id": row["order_id"],
        "paypal_dispute_id": row["paypal_dispute_id"],
        "claim": row["claim"],
        "source": row["source"],
        "evidence": json.loads(row["evidence"]) if row["evidence"] else None,
        "strength": row["strength"],
        "draft_reply": row["draft_reply"],
        "created_at": row["created_at"],
    }


def simulate_dispute(
    conn: sqlite3.Connection,
    order_id: str,
    claim: str = "I did not authorize this AI purchase.",
) -> dict[str, Any]:
    """Create a simulated PayPal-shaped dispute and log DISPUTE_RECEIVED to the ledger."""
    dispute_id = new_id("dsp")
    created_at = utc_now_iso()
    sim_pp_dispute_id = f"PP-D-SIM-{dispute_id[-6:].upper()}"

    conn.execute(
        """
        INSERT INTO disputes (
            id, order_id, paypal_dispute_id, claim, source,
            evidence, strength, draft_reply, created_at
        )
        VALUES (?, ?, ?, ?, 'simulated', '{}', 'NO_RECORD', '', ?)
        """,
        (dispute_id, order_id.strip(), sim_pp_dispute_id, claim.strip(), created_at),
    )

    append_event(
        conn,
        event_type="DISPUTE_RECEIVED",
        ref_id=dispute_id,
        body={
            "dispute_id": dispute_id,
            "order_id": order_id.strip(),
            "paypal_dispute_id": sim_pp_dispute_id,
            "claim": claim.strip(),
            "source": "simulated",
        },
        ts=created_at,
    )

    row = conn.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
    assert row is not None
    return _row_to_dispute(row)


def list_disputes(conn: sqlite3.Connection, sync_paypal: bool = False) -> tuple[list[dict[str, Any]], str | None]:
    """List disputes, optionally checking PayPal Sandbox Disputes API first."""
    sync_note: str | None = None
    if sync_paypal:
        items, sync_note = fetch_sandbox_disputes()
        for item in items:
            pp_disp_id = str(item.get("dispute_id") or "")
            if not pp_disp_id:
                continue
            existing = conn.execute(
                "SELECT id FROM disputes WHERE paypal_dispute_id = ?",
                (pp_disp_id,),
            ).fetchone()
            if existing is not None:
                continue

            txns = item.get("disputed_transactions") or []
            seller_tx_id = ""
            if txns and isinstance(txns[0], dict):
                seller_tx_id = str(txns[0].get("seller_transaction_id") or "")

            matched_order = None
            if seller_tx_id:
                matched_order = conn.execute(
                    "SELECT id FROM orders WHERE capture_id = ? OR paypal_order_id = ?",
                    (seller_tx_id, seller_tx_id),
                ).fetchone()

            order_ref = str(matched_order["id"]) if matched_order else (seller_tx_id or pp_disp_id)
            claim_reason = str(item.get("reason") or "PayPal Sandbox dispute")
            dispute_id = new_id("dsp")
            created_at = str(item.get("create_time") or utc_now_iso())

            conn.execute(
                """
                INSERT INTO disputes (
                    id, order_id, paypal_dispute_id, claim, source,
                    evidence, strength, draft_reply, created_at
                )
                VALUES (?, ?, ?, ?, 'paypal', '{}', 'NO_RECORD', '', ?)
                """,
                (dispute_id, order_ref, pp_disp_id, claim_reason, created_at),
            )
            append_event(
                conn,
                event_type="DISPUTE_RECEIVED",
                ref_id=dispute_id,
                body={
                    "dispute_id": dispute_id,
                    "order_id": order_ref,
                    "paypal_dispute_id": pp_disp_id,
                    "claim": claim_reason,
                    "source": "paypal",
                },
                ts=created_at,
            )

    rows = conn.execute("SELECT * FROM disputes ORDER BY created_at DESC").fetchall()
    return [_row_to_dispute(r) for r in rows], sync_note


def defend_dispute(conn: sqlite3.Connection, dispute_id: str) -> dict[str, Any]:
    """Build ledger-only evidence packet, compute strength, draft reply, and log it."""
    row = conn.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
    if row is None:
        raise KeyError(f"Dispute '{dispute_id}' not found.")

    dispute = _row_to_dispute(row)
    packet, strength, missing = build_evidence_packet_from_ledger(conn, dispute)

    template_fn = lambda: _template_reply(packet, strength, missing)  # noqa: E731
    draft_text, llm_meta = draft_dispute_reply_llm(packet, template_fn)

    post_check_fallback = False
    if not _passes_post_check(draft_text, packet, strength):
        draft_text = template_fn()
        post_check_fallback = True

    conn.execute(
        """
        UPDATE disputes
        SET evidence = ?, strength = ?, draft_reply = ?
        WHERE id = ?
        """,
        (json.dumps(packet), strength, draft_text, dispute_id),
    )

    append_event(
        conn,
        event_type="DISPUTE_REPLY_DRAFTED",
        ref_id=dispute_id,
        body={
            "dispute_id": dispute_id,
            "order_id": dispute["order_id"],
            "strength": strength,
            "draft_reply": draft_text,
            "post_check_fallback": post_check_fallback,
            "llm_meta": llm_meta,
        },
    )

    updated = conn.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
    assert updated is not None
    result = _row_to_dispute(updated)
    result["llm_meta"] = llm_meta
    result["post_check_fallback"] = post_check_fallback
    return result
