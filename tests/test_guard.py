"""Tests for M3: Catalog loading and deterministic Guard rule checks & precedence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.catalog import get_product, list_products
from app.db import db_session, init_db, set_kill_switch_value
from app.guard import evaluate_purchase
from app.ledger import list_events, verify
from app.main import app
from app.mandate import MandateConfirmRequest, confirm_mandate, parse_iso_datetime


def _seed_mandate(
    conn,
    *,
    keywords: list[str] | None = None,
    max_item_usd: float = 35.0,
    max_day_usd: float = 70.0,
    allowed_sellers: list[str] | None = None,
    ttl_hours: float = 24.0,
):
    req = MandateConfirmRequest(
        raw_text="get me a black jacket under $35",
        item_keywords=keywords or ["black", "jacket"],
        max_item_usd=max_item_usd,
        max_day_usd=max_day_usd,
        allowed_sellers=allowed_sellers or ["thrift-shop"],
        ttl_hours=ttl_hours,
        assumptions=[],
    )
    return confirm_mandate(conn, req)


def test_catalog_has_expected_products_and_public_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cat.db"))
    products = list_products()
    assert len(products) >= 12
    assert get_product("prod_denim_jacket")["price_usd"] == 24.0
    assert get_product("prod_injection_bundle")["price_usd"] == 199.0

    with TestClient(app) as client:
        res = client.get("/api/catalog")
        assert res.status_code == 200
        pub = res.json()["products"]
        assert len(pub) == len(products)
        assert "market_price_usd" not in pub[0]


def test_guard_approves_valid_purchase(tmp_path: Path) -> None:
    db_file = str(tmp_path / "ok.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        mandate = _seed_mandate(conn)
        result = evaluate_purchase(conn, mandate["id"], "prod_denim_jacket")
        assert result["decision"] == "APPROVED"
        assert result["reason_codes"] == []
        assert result["price_usd"] == 24.0
        assert result["seller"] == "thrift-shop"
        assert len(result["checks"]) == 8
        assert all(c["passed"] is True for c in result["checks"])
        events = list_events(conn)
        assert events[-1]["type"] == "PURCHASE_DECIDED"
        assert verify(conn)["ok"] is True


def test_guard_kill_switch_blocks(tmp_path: Path) -> None:
    db_file = str(tmp_path / "ks.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        mandate = _seed_mandate(conn)
        set_kill_switch_value(conn, True)
        result = evaluate_purchase(conn, mandate["id"], "prod_denim_jacket")
    assert result["decision"] == "BLOCKED"
    assert "KILL_SWITCH" in result["reason_codes"]


def test_guard_mandate_inactive_and_revoked(tmp_path: Path) -> None:
    db_file = str(tmp_path / "inactive.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        missing_res = evaluate_purchase(conn, "mnd_does_not_exist", "prod_denim_jacket")
        assert missing_res["decision"] == "BLOCKED"
        assert "MANDATE_INACTIVE" in missing_res["reason_codes"]

        mandate = _seed_mandate(conn)
        conn.execute("UPDATE mandates SET status = 'revoked' WHERE id = ?", (mandate["id"],))
        revoked_res = evaluate_purchase(conn, mandate["id"], "prod_denim_jacket")
        assert revoked_res["decision"] == "BLOCKED"
        assert "MANDATE_INACTIVE" in revoked_res["reason_codes"]


def test_guard_mandate_expired(tmp_path: Path) -> None:
    db_file = str(tmp_path / "expired.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        mandate = _seed_mandate(conn, ttl_hours=1.0)
        future = parse_iso_datetime(mandate["expires_at"]) + timedelta(minutes=5)
        result = evaluate_purchase(conn, mandate["id"], "prod_denim_jacket", now=future)
    assert result["decision"] == "BLOCKED"
    assert "MANDATE_EXPIRED" in result["reason_codes"]


def test_guard_over_item_cap(tmp_path: Path) -> None:
    db_file = str(tmp_path / "itemcap.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        mandate = _seed_mandate(conn, max_item_usd=35.0, max_day_usd=100.0)
        result = evaluate_purchase(conn, mandate["id"], "prod_leather_jacket")
    assert result["decision"] == "BLOCKED"
    assert result["reason_codes"] == ["OVER_ITEM_CAP"]


def test_guard_over_day_cap_counts_approved_and_human_approved(tmp_path: Path) -> None:
    db_file = str(tmp_path / "daycap.db")
    init_db(db_file)
    now_dt = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    with db_session(db_file) as conn:
        mandate = _seed_mandate(conn, max_item_usd=35.0, max_day_usd=50.0, ttl_hours=48.0)
        # First purchase $24 -> APPROVED
        r1 = evaluate_purchase(conn, mandate["id"], "prod_denim_jacket", now=now_dt)
        assert r1["decision"] == "APPROVED"

        # Second purchase $30 ($24 + $30 = $54 > $50 daily cap) -> BLOCKED OVER_DAY_CAP
        # Also test with cotton zip jacket ($29): $24 + $29 = $53 > $50
        r2 = evaluate_purchase(conn, mandate["id"], "prod_cotton_hoodie", now=now_dt)
        assert r2["decision"] == "BLOCKED"
        assert "OVER_DAY_CAP" in r2["reason_codes"]


def test_guard_item_mismatch(tmp_path: Path) -> None:
    db_file = str(tmp_path / "mismatch.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        mandate = _seed_mandate(conn, keywords=["black", "jacket"])
        # Black jeans matches "black" but NOT "jacket"
        r1 = evaluate_purchase(conn, mandate["id"], "prod_black_jeans")
        r2 = evaluate_purchase(conn, mandate["id"], "prod_red_scarf")
    assert r1["decision"] == "BLOCKED"
    assert "ITEM_MISMATCH" in r1["reason_codes"]
    assert r2["decision"] == "BLOCKED"
    assert "ITEM_MISMATCH" in r2["reason_codes"]


def test_guard_unknown_seller_held(tmp_path: Path) -> None:
    db_file = str(tmp_path / "seller.db")
    init_db(db_file)
    with db_session(db_file) as conn:
        mandate = _seed_mandate(conn, allowed_sellers=["thrift-shop"])
        result = evaluate_purchase(conn, mandate["id"], "prod_bomber_jacket")
    assert result["decision"] == "HELD"
    assert result["reason_codes"] == ["UNKNOWN_SELLER"]


def test_guard_high_risk_held_and_precedence_skips_risk_on_blocked(tmp_path: Path) -> None:
    db_file = str(tmp_path / "risk.db")
    init_db(db_file)

    called = 0

    def spy_high_risk(_product):
        nonlocal called
        called += 1
        return {"score": 0.9, "reasons": ["High risk test signal"]}

    with db_session(db_file) as conn:
        # 1. Allowed seller, under cap, matching item -> risk triggers HELD
        mandate = _seed_mandate(
            conn,
            allowed_sellers=["thrift-shop", "vintage-vault"],
            max_item_usd=35.0,
        )
        held_res = evaluate_purchase(
            conn, mandate["id"], "prod_denim_jacket", risk_evaluator=spy_high_risk
        )
        assert held_res["decision"] == "HELD"
        assert held_res["reason_codes"] == ["HIGH_RISK"]
        assert called == 1

        # 2. Unknown seller AND over item cap -> BLOCKED overrides HELD, risk check skipped!
        strict_mandate = _seed_mandate(conn, allowed_sellers=["thrift-shop"], max_item_usd=20.0)
        blocked_res = evaluate_purchase(
            conn, strict_mandate["id"], "prod_bomber_jacket", risk_evaluator=spy_high_risk
        )
        assert blocked_res["decision"] == "BLOCKED"
        assert "OVER_ITEM_CAP" in blocked_res["reason_codes"]
        assert "UNKNOWN_SELLER" in blocked_res["reason_codes"]
        assert called == 1  # Not called when already BLOCKED!
        risk_check = next(c for c in blocked_res["checks"] if c["name"] == "HIGH_RISK")
        assert risk_check["skipped"] is True
