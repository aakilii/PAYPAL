"""Tests for code hardening, mandate revocation, input validation, and security headers."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import paypal
from app.db import db_session
from app.ledger import canonical_json, list_events, verify
from app.main import app


def _create_default_mandate(client: TestClient) -> dict:
    draft = client.post(
        "/api/mandates/parse",
        json={"text": "get me a black jacket under $35"},
    ).json()
    return client.post("/api/mandates", json=draft).json()["mandate"]


def test_mandate_revoke_and_spent_today_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "revoke.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    monkeypatch.setenv("LLM_MODE", "mock")

    with TestClient(app) as client:
        mandate = _create_default_mandate(client)
        active_before = client.get("/api/mandates/active").json()
        assert active_before["mandate"]["id"] == mandate["id"]
        assert active_before["spent_today_usd"] == 0.0

        # Run normal buy ($24) -> spent_today_usd updates to 24.0
        buy_res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        )
        assert buy_res.status_code == 200
        active_after_buy = client.get("/api/mandates/active").json()
        assert active_after_buy["spent_today_usd"] == 24.0

        # Revoke mandate -> logged to ledger, active becomes None, guard blocks subsequent attempt
        rev_res = client.post(f"/api/mandates/{mandate['id']}/revoke")
        assert rev_res.status_code == 200
        assert rev_res.json()["mandate"]["status"] == "revoked"
        assert client.get("/api/mandates/active").json()["mandate"] is None

        # Double revoke returns 400; unknown mandate returns 404
        assert client.post(f"/api/mandates/{mandate['id']}/revoke").status_code == 400
        assert client.post("/api/mandates/mnd_missing/revoke").status_code == 404

        blocked = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        assert blocked["attempt"]["decision"] == "BLOCKED"
        assert "MANDATE_INACTIVE" in blocked["attempt"]["reason_codes"]

        with db_session(db_file) as conn:
            events = list_events(conn)
            assert any(e["type"] == "MANDATE_REVOKED" for e in events)
            assert verify(conn)["ok"] is True


def test_input_validation_bounds_and_nan_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "validation.db")
    monkeypatch.setenv("DB_PATH", db_file)

    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})

    with TestClient(app) as client:
        assert client.post("/api/mandates/parse", json={"text": "   "}).status_code == 422

        overflow_res = client.post(
            "/api/mandates",
            json={
                "raw_text": "black jacket",
                "item_keywords": ["black", "jacket"],
                "max_item_usd": 35.0,
                "max_day_usd": 70.0,
                "allowed_sellers": ["thrift-shop"],
                "ttl_hours": 999999999.0,
                "assumptions": [],
            },
        )
        assert overflow_res.status_code == 422

        mandate = _create_default_mandate(client)
        blank_agent = client.post(
            "/api/agent/run",
            json={"mandate_id": mandate["id"], "request_text": "   ", "attack": "none"},
        )
        assert blank_agent.status_code == 422


def test_security_headers_and_paypal_hardening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "sec.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")

    with TestClient(app) as client:
        idx = client.get("/")
        assert idx.headers.get("x-content-type-options") == "nosniff"
        assert idx.headers.get("x-frame-options") == "DENY"
        assert "default-src 'self'" in idx.headers.get("content-security-policy", "")

        # Reject path-traversal or malformed PayPal tokens
        bad_ret = client.get("/paypal/return?token=../../v1/oauth2/token", follow_redirects=False)
        assert bad_ret.status_code == 400
        bad_cancel = client.get("/paypal/cancel?token=bad/token", follow_redirects=False)
        assert bad_cancel.status_code == 400

        # Reject spoofed X-Forwarded-Host containing path/credentials
        mandate = _create_default_mandate(client)
        spoofed = client.post(
            "/api/agent/run",
            headers={"X-Forwarded-Proto": "javascript", "X-Forwarded-Host": "evil.com/phish"},
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        order = spoofed["order"]
        assert order["approval_url"].startswith("http://127.0.0.1:8000/paypal/return?")

        # Cancelled order cannot be captured later
        client.get(f"/paypal/cancel?token={order['paypal_order_id']}", follow_redirects=False)
        cap_cancelled = client.get(
            f"/paypal/return?token={order['paypal_order_id']}",
            follow_redirects=False,
        )
        assert cap_cancelled.status_code == 303
        assert "paypal_error=" in cap_cancelled.headers["location"]


def test_paypal_401_refreshes_token_and_retries_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paypal.clear_token_cache()
    db_file = str(tmp_path / "retry401.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("PAYPAL_MODE", "sandbox")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sbx-client-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sbx-client-secret")

    oauth_calls = 0
    order_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal oauth_calls, order_calls
        if request.url.path == "/v1/oauth2/token":
            oauth_calls += 1
            return httpx.Response(200, json={"access_token": f"tok_{oauth_calls}", "expires_in": 3600})
        if request.url.path == "/v2/checkout/orders":
            order_calls += 1
            if order_calls == 1:
                return httpx.Response(401, json={"error": "invalid_token"})
            assert request.headers.get("authorization") == "Bearer tok_2"
            return httpx.Response(
                201,
                json={
                    "id": "ORDER-RETRY-OK",
                    "status": "CREATED",
                    "links": [
                        {
                            "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER-RETRY-OK",
                            "rel": "payer-action",
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"message": "Not found"})

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client
    monkeypatch.setattr(
        paypal.httpx,
        "Client",
        lambda **_kwargs: real_client_cls(transport=transport),
    )

    with TestClient(app) as client:
        mandate = _create_default_mandate(client)
        res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        assert res["paypal_error"] is None
        assert res["order"]["paypal_order_id"] == "ORDER-RETRY-OK"
        assert oauth_calls == 2
        assert order_calls == 2

