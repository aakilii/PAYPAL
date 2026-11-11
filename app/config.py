"""Application configuration, mode switches, and PayPal sandbox safety check."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

# Load local .env once on import without overwriting explicit process env vars (helpful in tests).
load_dotenv(override=False)

SANDBOX_PAYPAL_BASE_URL = "https://api-m.sandbox.paypal.com"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

LlmMode = Literal["mock", "gemini"]
PayPalMode = Literal["mock", "sandbox"]


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    return float(value.strip())


def validate_paypal_sandbox_url(base_url: str) -> str:
    """Refuse to start if PayPal is pointed anywhere other than the official sandbox."""
    normalized = base_url.strip().rstrip("/")
    if normalized != SANDBOX_PAYPAL_BASE_URL:
        raise RuntimeError(
            "Security check failed: PAYPAL_BASE_URL must be exactly "
            f"'{SANDBOX_PAYPAL_BASE_URL}', got '{base_url}'. Refusing to start."
        )
    return normalized


@dataclass(frozen=True)
class Settings:
    llm_mode: LlmMode
    gemini_api_key: str
    gemini_model: str
    paypal_mode: PayPalMode
    paypal_base_url: str
    paypal_client_id: str
    paypal_client_secret: str
    app_base_url: str
    db_path: str
    risk_threshold: float
    dev_mode: bool

    def to_public_dict(self) -> dict[str, object]:
        """Return safe configuration fields for the UI without ever exposing secrets."""
        return {
            "llm_mode": self.llm_mode,
            "gemini_model": self.gemini_model,
            "paypal_mode": self.paypal_mode,
            "paypal_base_url": self.paypal_base_url,
            "risk_threshold": self.risk_threshold,
            "dev_mode": self.dev_mode,
            "has_gemini_key": bool(self.gemini_api_key),
            "has_paypal_credentials": bool(self.paypal_client_id and self.paypal_client_secret),
        }


def get_settings() -> Settings:
    """Read and validate settings from environment variables."""
    raw_llm_mode = (os.getenv("LLM_MODE") or "mock").strip().lower()
    if raw_llm_mode not in ("mock", "gemini"):
        raise ValueError(f"LLM_MODE must be 'mock' or 'gemini', got '{raw_llm_mode}'.")

    raw_paypal_mode = (os.getenv("PAYPAL_MODE") or "mock").strip().lower()
    if raw_paypal_mode not in ("mock", "sandbox"):
        raise ValueError(f"PAYPAL_MODE must be 'mock' or 'sandbox', got '{raw_paypal_mode}'.")

    raw_paypal_url = os.getenv("PAYPAL_BASE_URL")
    candidate_url = (
        raw_paypal_url if raw_paypal_url is not None and raw_paypal_url.strip() else SANDBOX_PAYPAL_BASE_URL
    )
    paypal_base_url = validate_paypal_sandbox_url(candidate_url)

    risk_threshold = _parse_float(os.getenv("RISK_THRESHOLD"), 0.7)
    if not (0.0 < risk_threshold <= 1.0):
        raise ValueError(f"RISK_THRESHOLD must be in (0, 1], got {risk_threshold}.")

    app_base_url = (os.getenv("APP_BASE_URL") or "http://127.0.0.1:8000").strip().rstrip("/")
    db_path = (os.getenv("DB_PATH") or "mandate.db").strip()
    gemini_model = (os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip()

    return Settings(
        llm_mode=raw_llm_mode,  # type: ignore[arg-type]
        gemini_api_key=(os.getenv("GEMINI_API_KEY") or "").strip(),
        gemini_model=gemini_model,
        paypal_mode=raw_paypal_mode,  # type: ignore[arg-type]
        paypal_base_url=paypal_base_url,
        paypal_client_id=(os.getenv("PAYPAL_CLIENT_ID") or "").strip(),
        paypal_client_secret=(os.getenv("PAYPAL_CLIENT_SECRET") or "").strip(),
        app_base_url=app_base_url,
        db_path=db_path,
        risk_threshold=risk_threshold,
        dev_mode=_parse_bool(os.getenv("DEV_MODE"), default=False),
    )
