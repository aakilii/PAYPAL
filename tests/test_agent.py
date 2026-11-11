"""Tests for M5: Shopper agent, prompt-injection block, kill switch, and activity feed."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import db_session
from app.ledger import verify
from app.main import app


def _create_default_mandate(client: TestClient) -> dict:
    draft = client.post(
        "/api/mandates/parse",
        json={"text": "get me a black jacket under $35"},
    ).json()
    return client.post("/api/mandates", json=draft).json()["mandate"]


def test_agent_normal_over_cap_and_fake_seller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "agent_scenarios.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MODE", "mock")

    with TestClient(app) as client:
        mandate = _create_default_mandate(client)

        # 1. Normal buy -> Black Denim Jacket ($24, thrift-shop) -> APPROVED
        normal = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        assert normal["agent_pick"]["product_id"] == "prod_denim_jacket"
        assert normal["attempt"]["decision"] == "APPROVED"

        # 2. Over cap -> Black Leather Jacket ($48, thrift-shop) -> BLOCKED OVER_ITEM_CAP
        over_cap = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black leather jacket",
                "attack": "none",
            },
        ).json()
        assert over_cap["agent_pick"]["product_id"] == "prod_leather_jacket"
        assert over_cap["attempt"]["decision"] == "BLOCKED"
        assert "OVER_ITEM_CAP" in over_cap["attempt"]["reason_codes"]

        # 3. Fake seller -> cheap-jackets-now.shop ($9) -> HELD UNKNOWN_SELLER + HIGH_RISK
        fake = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "buy black winter jacket from cheap-jackets-now.shop",
                "attack": "none",
            },
        ).json()
        assert fake["agent_pick"]["product_id"] == "prod_cheap_black_jacket"
        assert fake["attempt"]["decision"] == "HELD"
        assert "UNKNOWN_SELLER" in fake["attempt"]["reason_codes"]
        assert "HIGH_RISK" in fake["attempt"]["reason_codes"]


def test_injection_attack_is_blocked_with_over_item_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "injection.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MODE", "mock")

    with TestClient(app) as client:
        mandate = _create_default_mandate(client)
        res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black jacket",
                "attack": "injection",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["agent_pick"]["product_id"] == "prod_injection_bundle"
        assert "gullible on purpose" in body["agent_pick"]["attack_label"]
        assert body["attempt"]["decision"] == "BLOCKED"
        assert "OVER_ITEM_CAP" in body["attempt"]["reason_codes"]
        assert body["safe_agent_comparison"] is not None
        assert body["safe_agent_comparison"]["product_id"] == "prod_denim_jacket"


def test_kill_switch_blocks_everything_and_activity_feed_polls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "killswitch.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MODE", "mock")

    with TestClient(app) as client:
        mandate = _create_default_mandate(client)

        assert client.get("/api/kill-switch").json()["enabled"] is False
        toggle = client.post("/api/kill-switch", json={"enabled": True}).json()
        assert toggle["enabled"] is True

        blocked = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        assert blocked["attempt"]["decision"] == "BLOCKED"
        assert "KILL_SWITCH" in blocked["attempt"]["reason_codes"]

        feed_all = client.get("/api/activity").json()
        assert len(feed_all["events"]) == 3  # MANDATE_CONFIRMED, KILL_SWITCH_CHANGED, PURCHASE_DECIDED
        latest_seq = feed_all["latest_seq"]

        # Poll since=latest_seq -> empty until a new event arrives
        assert client.get(f"/api/activity?since={latest_seq}").json()["events"] == []

        client.post("/api/kill-switch", json={"enabled": False})
        feed_delta = client.get(f"/api/activity?since={latest_seq}").json()
        assert len(feed_delta["events"]) == 1
        assert feed_delta["events"][0]["type"] == "KILL_SWITCH_CHANGED"

        with db_session(db_file) as conn:
            assert verify(conn)["ok"] is True
