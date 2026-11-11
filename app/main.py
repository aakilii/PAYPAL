"""FastAPI entry point for Mandate."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.catalog import list_public_catalog
from app.config import get_settings
from app.db import db_session, init_db
from app.guard import list_holds, resolve_hold
from app.ledger import dev_tamper_entry, list_events, verify
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
