"""Tests for M4: Seller risk scoring heuristic/AI maxing and HELD human-in-the-loop flow."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import llm, risk
from app.catalog import get_product
from app.db import db_session, init_db
from app.guard import evaluate_purchase
from app.ledger import list_events, verify
from app.main import app
from app.mandate import MandateConfirmRequest, confirm_mandate


def test_heuristic_risk_scores() -> None:
    clean = get_product("prod_denim_jacket")
    assert clean is not None
    score_clean, reasons_clean = risk.compute_heuristic_risk(clean)
    assert score_clean == 0.0
    assert len(reasons_clean) == 1

    # cheap-jackets-now.shop: domain_age=6 (+0.30), price 9 < 0.4*50 (+0.25), pressure (+0.15), unverified (+0.15) = 0.85
    scam = get_product("prod_cheap_black_jacket")
    assert scam is not None
    score_scam, reasons_scam = risk.compute_heuristic_risk(scam)
    assert score_scam == 0.85
    assert len(reasons_scam) == 4

    # thrift-shop suspicious clearance: price 14 < 0.4*90 (+0.25), pressure (+0.15) = 0.40
    clearance = get_product("prod_suspicious_fleece")
    assert clearance is not None
    score_clr, _ = risk.compute_heuristic_risk(clearance)
    assert score_clr == 0.40


def test_live_risk_takes_max_of_heuristic_and_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODE", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def fake_assess_risk_llm(**_kwargs):
        return (
            llm.RiskAssessmentSchema(score=0.82, reasons=["Listing copies brand template."]),
            {"provider": "gemini", "fallback_used": False, "fallback_reason": None},
        )

    monkeypatch.setattr(risk, "assess_risk_llm", fake_assess_risk_llm)
    clearance = get_product("prod_suspicious_fleece")
    assert clearance is not None
    res = risk.evaluate_risk(clearance)
    assert res["heuristic_score"] == 0.40
    assert res["ai_score"] == 0.82
    assert res["score"] == 0.82
    assert any("AI risk check:" in r for r in res["reasons"])


def test_held_approve_and_deny_flow_with_daily_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "holds.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("LLM_MODE", "mock")

    with TestClient(app) as client:
        with db_session(db_file) as conn:
            # Allow flash-deals-outlet.shop so prod_flash_parka triggers HIGH_RISK (0.85 >= 0.70)
            mandate = confirm_mandate(
                conn,
                MandateConfirmRequest(
                    raw_text="black jacket under $35",
                    item_keywords=["black", "jacket"],
                    max_item_usd=35.0,
                    max_day_usd=35.0,
                    allowed_sellers=["thrift-shop", "flash-deals-outlet.shop"],
                    ttl_hours=24.0,
                    assumptions=[],
                ),
            )
            held1 = evaluate_purchase(conn, mandate["id"], "prod_flash_parka")
            held2 = evaluate_purchase(conn, mandate["id"], "prod_cheap_black_jacket")

        assert held1["decision"] == "HELD"
        assert held1["reason_codes"] == ["HIGH_RISK"]
        assert held2["decision"] == "HELD"
        assert "UNKNOWN_SELLER" in held2["reason_codes"]
        assert "HIGH_RISK" in held2["reason_codes"]

        holds = client.get("/api/holds").json()["holds"]
        assert len(holds) == 2

        # Approve held1 ($19) -> succeeds and writes HUMAN_RESOLVED
        app_res = client.post(f"/api/holds/{held1['id']}/approve")
        assert app_res.status_code == 200
        assert app_res.json()["attempt"]["human_resolution"] == "approved"

        # Deny held2 ($9) -> succeeds and writes HUMAN_RESOLVED
        deny_res = client.post(f"/api/holds/{held2['id']}/deny")
        assert deny_res.status_code == 200
        assert deny_res.json()["attempt"]["human_resolution"] == "denied"
        assert deny_res.json()["attempt"]["status"] == "denied"

        # Pending holds list is now empty
        assert client.get("/api/holds").json()["holds"] == []

        # New $24 purchase exceeds daily cap ($19 + $24 = $43 > $35) because held1 was human-approved
        with db_session(db_file) as conn:
            blocked = evaluate_purchase(conn, mandate["id"], "prod_denim_jacket")
            assert blocked["decision"] == "BLOCKED"
            assert "OVER_DAY_CAP" in blocked["reason_codes"]
            events = list_events(conn)
            human_events = [e for e in events if e["type"] == "HUMAN_RESOLVED"]
            assert len(human_events) == 2
            assert verify(conn)["ok"] is True
