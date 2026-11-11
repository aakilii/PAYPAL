"""FastAPI entry point for Mandate."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent import run as run_agent
from app.catalog import list_public_catalog
from app.config import get_settings
from app.db import db_session, init_db, is_kill_switch_on, set_kill_switch_value
from app.guard import list_holds, resolve_hold
from app.ledger import append_event, dev_tamper_entry, list_events, verify
from app.mandate import (
    MandateConfirmRequest,
    MandateParseRequest,
    confirm_mandate,
    get_active_mandate,
    parse_mandate_draft,
)


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
    with db_session() as conn:
        mandate = confirm_mandate(conn, payload)
    return {"mandate": mandate}


@app.get("/api/mandates/active")
def api_get_active_mandate() -> dict[str, object]:
    """Return the current active confirmed mandate, if one exists."""
    with db_session() as conn:
        mandate = get_active_mandate(conn)
    return {"mandate": mandate}


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


@app.post("/api/holds/{attempt_id}/approve")
def api_approve_hold(attempt_id: str) -> dict[str, object]:
    """Human-in-the-loop approval of a HELD purchase attempt."""
    try:
        with db_session() as conn:
            attempt = resolve_hold(conn, attempt_id, "approved")
        return {"attempt": attempt}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/holds/{attempt_id}/deny")
def api_deny_hold(attempt_id: str) -> dict[str, object]:
    """Human-in-the-loop denial of a HELD purchase attempt."""
    try:
        with db_session() as conn:
            attempt = resolve_hold(conn, attempt_id, "denied")
        return {"attempt": attempt}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AgentRunRequest(BaseModel):
    mandate_id: str = Field(min_length=1)
    request_text: str = Field(min_length=1, max_length=500)
    attack: str = Field(default="none")


class KillSwitchRequest(BaseModel):
    enabled: bool


def _summarize_event(event: dict[str, object]) -> str:
    etype = str(event.get("type", ""))
    body = event.get("body")
    b = body if isinstance(body, dict) else {}
    if etype == "MANDATE_CONFIRMED":
        return f"Mandate confirmed (cap ${b.get('max_item_usd', '?')}/item, keywords {b.get('item_keywords', [])})."
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
def api_agent_run(payload: AgentRunRequest) -> dict[str, object]:
    """Have the shopper agent propose a product and run it through the Guard."""
    try:
        with db_session() as conn:
            return run_agent(
                conn,
                mandate_id=payload.mandate_id,
                request_text=payload.request_text,
                attack=payload.attack,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/kill-switch")
def api_get_kill_switch() -> dict[str, bool]:
    """Check whether the global agent kill switch is active."""
    with db_session() as conn:
        return {"enabled": is_kill_switch_on(conn)}


@app.post("/api/kill-switch")
def api_set_kill_switch(payload: KillSwitchRequest) -> dict[str, bool]:
    """Freeze or unfreeze the agent and log KILL_SWITCH_CHANGED in the ledger."""
    with db_session() as conn:
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
        with db_session() as conn:
            result = dev_tamper_entry(conn, seq)
            verification = verify(conn)
        return {**result, "verification": verification}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
