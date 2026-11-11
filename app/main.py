"""FastAPI entry point for Mandate."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run startup safety checks before serving any request."""
    get_settings()
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
