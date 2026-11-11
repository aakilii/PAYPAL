"""Tests for M0 setup: configuration safety checks and health endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import SANDBOX_PAYPAL_BASE_URL, get_settings
from app.main import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_config_endpoint_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-value")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "client-id-value")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret-paypal-value")

    with TestClient(app) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_mode"] in ("mock", "gemini")
    assert payload["paypal_mode"] in ("mock", "sandbox")
    assert payload["paypal_base_url"] == SANDBOX_PAYPAL_BASE_URL
    assert payload["has_gemini_key"] is True
    assert payload["has_paypal_credentials"] is True
    serialized = str(payload)
    assert "secret-gemini-value" not in serialized
    assert "secret-paypal-value" not in serialized


def test_refuses_non_sandbox_paypal_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    with pytest.raises(RuntimeError, match="Security check failed"):
        get_settings()
