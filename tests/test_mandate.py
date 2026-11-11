"""Tests for M2: Mandate LLM/mock parsing, default assumptions, validation, and confirmation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.db import db_session, init_db
from app.ledger import list_events, verify
from app.main import app


def test_parse_mandate_defaults_and_does_not_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "mandate_parse.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MODE", "mock")

    with TestClient(app) as client:
        res = client.post("/api/mandates/parse", json={"text": "get me a black jacket under $35"})
        assert res.status_code == 200
        draft = res.json()

        assert draft["item_keywords"] == ["black", "jacket"]
        assert draft["max_item_usd"] == 35.0
        assert draft["max_day_usd"] == 70.0
        assert draft["allowed_sellers"] == ["thrift-shop"]
        assert draft["ttl_hours"] == 24.0
        assert len(draft["assumptions"]) == 3

        active_res = client.get("/api/mandates/active")
        assert active_res.status_code == 200
        assert active_res.json()["mandate"] is None


def test_confirm_mandate_writes_ledger_and_active_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "mandate_confirm.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MODE", "mock")

    with TestClient(app) as client:
        draft = client.post(
            "/api/mandates/parse",
            json={"text": "get me a black jacket under $35"},
        ).json()

        # User edits allowed sellers before confirming
        draft["allowed_sellers"] = ["thrift-shop", "vintage-vault"]
        confirm_res = client.post("/api/mandates", json=draft)
        assert confirm_res.status_code == 200
        mandate = confirm_res.json()["mandate"]
        assert mandate["status"] == "active"
        assert mandate["allowed_sellers"] == ["thrift-shop", "vintage-vault"]

        active = client.get("/api/mandates/active").json()["mandate"]
        assert active["id"] == mandate["id"]

        with db_session(db_file) as conn:
            events = list_events(conn)
            assert len(events) == 1
            assert events[0]["type"] == "MANDATE_CONFIRMED"
            assert events[0]["ref_id"] == mandate["id"]
            assert verify(conn)["ok"] is True


def test_confirm_rejects_daily_cap_below_item_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "mandate_bad.db")
    monkeypatch.setenv("DB_PATH", db_file)

    with TestClient(app) as client:
        res = client.post(
            "/api/mandates",
            json={
                "raw_text": "black jacket",
                "item_keywords": ["black", "jacket"],
                "max_item_usd": 50.0,
                "max_day_usd": 30.0,
                "allowed_sellers": ["thrift-shop"],
                "ttl_hours": 24.0,
                "assumptions": [],
            },
        )
        assert res.status_code == 422


def test_gemini_parse_retries_once_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODE", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-unit-test")

    call_count = 0

    class BrokenModels:
        def generate_content(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Simulated invalid JSON from model")

    class BrokenClient:
        def __init__(self, api_key: str) -> None:
            self.models = BrokenModels()

    monkeypatch.setattr(llm.genai, "Client", BrokenClient)

    parsed, meta = llm.parse_mandate_llm("get me a black jacket under $35")
    assert call_count == 2  # initial attempt + 1 retry
    assert meta["fallback_used"] is True
    assert parsed.item_keywords == ["black", "jacket"]
    assert parsed.max_item_usd == 35.0
