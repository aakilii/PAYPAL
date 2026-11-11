"""Tests for M8: Dispute defender evidence packet, strength ratings, and factual post-checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import defender
from app.db import db_session
from app.ledger import list_events, verify
from app.main import app


def _create_confirmed_mandate(client: TestClient, *, allowed_sellers: list[str] | None = None) -> dict:
    draft = client.post(
        "/api/mandates/parse",
        json={"text": "get me a black jacket under $35"},
    ).json()
    if allowed_sellers is not None:
        draft["allowed_sellers"] = allowed_sellers
    return client.post("/api/mandates", json=draft).json()["mandate"]


def test_defender_strong_for_captured_order_and_human_approved_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "def_strong.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    monkeypatch.setenv("LLM_MODE", "mock")

    with TestClient(app) as client:
        mandate = _create_confirmed_mandate(client)
        run_res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        order = run_res["order"]
        client.get(f"/paypal/return?token={order['paypal_order_id']}&PayerID=MOCKBUYER", follow_redirects=False)

        sim = client.post(
            "/api/disputes/simulate",
            json={"order_id": order["id"], "claim": "I never approved this jacket."},
        ).json()["dispute"]
        assert sim["source"] == "simulated"

        defended = client.post(f"/api/disputes/{sim['id']}/defend").json()["dispute"]
        assert defended["strength"] == "STRONG"
        assert order["id"] in defended["draft_reply"]
        assert "24.00" in defended["draft_reply"]
        assert defended["evidence"]["mandate"]["confirmed_at"] == mandate["confirmed_at"]
        assert defended["evidence"]["order"]["capture_id"] == f"CAP-{order['paypal_order_id']}"
        assert defended["evidence"]["request_text"] == "get me a black denim jacket"
        assert defended["evidence"]["ledger_verification"]["ok"] is True

        # Also check HELD -> human-approved -> captured -> STRONG
        held_run = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black bomber jacket",
                "attack": "none",
            },
        ).json()
        app_hold = client.post(f"/api/holds/{held_run['attempt']['id']}/approve").json()
        hold_order = app_hold["order"]
        client.get(f"/paypal/return?token={hold_order['paypal_order_id']}", follow_redirects=False)

        sim_hold = client.post(
            "/api/disputes/simulate",
            json={"order_id": hold_order["id"], "claim": "Unauthorized purchase."},
        ).json()["dispute"]
        defended_hold = client.post(f"/api/disputes/{sim_hold['id']}/defend").json()["dispute"]
        assert defended_hold["strength"] == "STRONG"
        assert defended_hold["evidence"]["human_resolution"]["resolution"] == "approved"


def test_defender_no_record_for_fake_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "def_norecord.db")
    monkeypatch.setenv("DB_PATH", db_file)

    with TestClient(app) as client:
        sim = client.post(
            "/api/disputes/simulate",
            json={"order_id": "ord_fake_999", "claim": "Unknown charge."},
        ).json()["dispute"]
        defended = client.post(f"/api/disputes/{sim['id']}/defend").json()["dispute"]

        assert defended["strength"] == "NO_RECORD"
        assert "not contest" in defended["draft_reply"].lower()
        assert "ord_fake_999" in defended["draft_reply"]


def test_defender_weak_when_uncaptured_or_ledger_tampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "def_weak.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("DEV_MODE", "true")

    with TestClient(app) as client:
        mandate = _create_confirmed_mandate(client)
        run_res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        order = run_res["order"]

        # Dispute before capture -> WEAK
        sim = client.post(
            "/api/disputes/simulate",
            json={"order_id": order["id"], "claim": "Not captured yet."},
        ).json()["dispute"]
        defended_uncap = client.post(f"/api/disputes/{sim['id']}/defend").json()["dispute"]
        assert defended_uncap["strength"] == "WEAK"
        assert "not contest" in defended_uncap["draft_reply"].lower()

        # Capture, then tamper with ledger -> still WEAK because ledger is broken!
        client.get(f"/paypal/return?token={order['paypal_order_id']}", follow_redirects=False)
        with db_session(db_file) as conn:
            first_seq = list_events(conn)[0]["seq"]
        client.post(f"/api/dev/tamper/{first_seq}")

        defended_tampered = client.post(f"/api/disputes/{sim['id']}/defend").json()["dispute"]
        assert defended_tampered["strength"] == "WEAK"
        assert "not contest" in defended_tampered["draft_reply"].lower()
        assert defended_tampered["evidence"]["ledger_verification"]["ok"] is False


def test_defender_post_check_falls_back_when_llm_omits_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "def_postcheck.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MODE", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    # Mock LLM returning vague text missing order_id and amount
    monkeypatch.setattr(
        defender,
        "draft_dispute_reply_llm",
        lambda _packet, _tpl: ("The buyer definitely approved this item earlier.", {"provider": "gemini"}),
    )

    with TestClient(app) as client:
        mandate = _create_confirmed_mandate(client)
        run_res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        order = run_res["order"]
        client.get(f"/paypal/return?token={order['paypal_order_id']}", follow_redirects=False)

        sim = client.post(
            "/api/disputes/simulate",
            json={"order_id": order["id"], "claim": "Dispute claim."},
        ).json()["dispute"]
        defended = client.post(f"/api/disputes/{sim['id']}/defend").json()["dispute"]

        assert defended["post_check_fallback"] is True
        assert order["id"] in defended["draft_reply"]
        assert "24.00" in defended["draft_reply"]

        with db_session(db_file) as conn:
            assert verify(conn)["ok"] is True
