"""FastAPI entry point for Mandate."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.config import get_settings
from app.db import db_session, init_db
from app.ledger import dev_tamper_entry, list_events, verify


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
