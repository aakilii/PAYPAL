"""Shared pytest fixtures ensuring offline mock defaults unless a test overrides them."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_safe_test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
