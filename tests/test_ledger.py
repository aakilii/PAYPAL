"""Tests for M1: SQLite database initialization and tamper-evident hash-chained ledger."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import db_session, init_db
from app.ledger import GENESIS_HASH, append_event, dev_tamper_entry, list_events, verify
from app.main import app


def test_empty_ledger_verifies(tmp_path: Path) -> None:
    db_file = str(tmp_path / "empty.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        result = verify(conn)
    assert result == {
        "ok": True,
        "checked": 0,
        "first_bad_seq": None,
        "head_hash": GENESIS_HASH,
    }


def test_hash_chain_links_and_verifies(tmp_path: Path) -> None:
    db_file = str(tmp_path / "chain.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        e1 = append_event(conn, "MANDATE_CONFIRMED", "mnd_1", {"max_item_usd": 35.0})
        e2 = append_event(conn, "PURCHASE_DECIDED", "att_1", {"decision": "APPROVED"})
        e3 = append_event(conn, "ORDER_CREATED", "ord_1", {"amount_usd": 24.0})
        result = verify(conn)
        events = list_events(conn)

    assert e1["prev_hash"] == GENESIS_HASH
    assert e2["prev_hash"] == e1["hash"]
    assert e3["prev_hash"] == e2["hash"]
    assert len(events) == 3
    assert result == {
        "ok": True,
        "checked": 3,
        "first_bad_seq": None,
        "head_hash": e3["hash"],
    }


def test_tamper_body_causes_verify_to_fail_at_right_seq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "tamper.db")
    monkeypatch.setenv("DEV_MODE", "true")
    init_db(db_file)
    with db_session(db_file) as conn:
        append_event(conn, "MANDATE_CONFIRMED", "mnd_1", {"max_item_usd": 35.0})
        e2 = append_event(conn, "PURCHASE_DECIDED", "att_1", {"price_usd": 24.0})
        append_event(conn, "ORDER_CREATED", "ord_1", {"amount_usd": 24.0})

        dev_tamper_entry(conn, e2["seq"])
        result = verify(conn)

    assert result["ok"] is False
    assert result["first_bad_seq"] == e2["seq"]
    assert result["checked"] == 2


def test_dev_tamper_blocked_when_dev_mode_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "nodev.db")
    monkeypatch.setenv("DEV_MODE", "false")
    init_db(db_file)
    with db_session(db_file) as conn:
        e1 = append_event(conn, "MANDATE_CONFIRMED", "mnd_1", {"max_item_usd": 35.0})
        with pytest.raises(PermissionError):
            dev_tamper_entry(conn, e1["seq"])


def test_ledger_api_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = str(tmp_path / "api.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("DEV_MODE", "true")

    with TestClient(app) as client:
        with db_session(db_file) as conn:
            append_event(conn, "KILL_SWITCH_CHANGED", "global", {"enabled": False})
            e2 = append_event(conn, "MANDATE_CONFIRMED", "mnd_2", {"max_item_usd": 50.0})

        res = client.get("/api/ledger")
        assert res.status_code == 200
        data = res.json()
        assert len(data["events"]) == 2
        assert data["verification"]["ok"] is True

        tamper_res = client.post(f"/api/dev/tamper/{e2['seq']}")
        assert tamper_res.status_code == 200
        assert tamper_res.json()["verification"]["ok"] is False
        assert tamper_res.json()["verification"]["first_bad_seq"] == e2["seq"]

        verify_res = client.get("/api/ledger/verify")
        assert verify_res.status_code == 200
        assert verify_res.json()["ok"] is False
