"""Tests for M6: PayPal mock and sandbox order creation, capture, cancel, and error handling."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import paypal
from app.db import db_session
from app.ledger import list_events, verify
from app.main import app


def _setup_mandate(client: TestClient, *, allowed_sellers: list[str] | None = None) -> dict:
    draft = client.post(
        "/api/mandates/parse",
        json={"text": "get me a black jacket under $35"},
    ).json()
    if allowed_sellers is not None:
        draft["allowed_sellers"] = allowed_sellers
    return client.post("/api/mandates", json=draft).json()["mandate"]


def test_mock_order_create_capture_and_verified_badge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "paypal_mock.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    monkeypatch.setenv("DEV_MODE", "true")

    with TestClient(app) as client:
        mandate = _setup_mandate(client)
        run_res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()

        assert run_res["paypal_error"] is None
        order = run_res["order"]
        assert order is not None
        assert order["paypal_order_id"].startswith("MOCK-")
        assert order["source"] == "mock"
        assert order["status"] == "CREATED"
        assert order["approval_url"].endswith(f"token={order['paypal_order_id']}&PayerID=MOCKBUYER")

        # Simulate buyer returning from PayPal approval link
        ret = client.get(
            f"/paypal/return?token={order['paypal_order_id']}&PayerID=MOCKBUYER",
            follow_redirects=False,
        )
        assert ret.status_code == 303
        assert ret.headers["location"].startswith("/?tab=merchant&captured=")

        orders = client.get("/api/orders").json()["orders"]
        assert len(orders) == 1
        assert orders[0]["status"] == "COMPLETED"
        assert orders[0]["capture_id"] == f"CAP-{order['paypal_order_id']}"
        assert orders[0]["verified_mandate"] is True

        # Tampering the ledger causes verified_mandate to become False!
        with db_session(db_file) as conn:
            events = list_events(conn)
            assert any(e["type"] == "ORDER_CREATED" for e in events)
            assert any(e["type"] == "ORDER_CAPTURED" for e in events)
            first_seq = events[0]["seq"]

        client.post(f"/api/dev/tamper/{first_seq}")
        orders_after_tamper = client.get("/api/orders").json()["orders"]
        assert orders_after_tamper[0]["verified_mandate"] is False


def test_human_approved_hold_creates_order_and_cancel_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = str(tmp_path / "paypal_hold.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("PAYPAL_MODE", "mock")

    with TestClient(app) as client:
        mandate = _setup_mandate(client, allowed_sellers=["thrift-shop"])
        run_res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black bomber jacket",
                "attack": "none",
            },
        ).json()
        assert run_res["attempt"]["decision"] == "HELD"
        assert run_res["order"] is None

        approve_res = client.post(f"/api/holds/{run_res['attempt']['id']}/approve").json()
        order = approve_res["order"]
        assert order is not None
        assert order["paypal_order_id"].startswith("MOCK-")

        cancel_res = client.get(
            f"/paypal/cancel?token={order['paypal_order_id']}",
            follow_redirects=False,
        )
        assert cancel_res.status_code == 303
        assert cancel_res.headers["location"] == "/?tab=shop&paypal_cancelled=1"
        orders = client.get("/api/orders").json()["orders"]
        assert orders[0]["status"] == "CANCELLED"


def test_sandbox_http_flow_with_mock_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paypal.clear_token_cache()
    db_file = str(tmp_path / "paypal_sbx.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("PAYPAL_MODE", "sandbox")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sbx-client-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sbx-client-secret")

    seen_requests: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((request.method, str(request.url), dict(request.headers)))
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok_123", "expires_in": 3600})
        if request.url.path == "/v2/checkout/orders":
            body = json.loads(request.content.decode("utf-8"))
            assert body["intent"] == "CAPTURE"
            assert body["payment_source"]["paypal"]["experience_context"]["shipping_preference"] == "NO_SHIPPING"
            return httpx.Response(
                201,
                json={
                    "id": "5O190127TN364715T",
                    "status": "CREATED",
                    "links": [
                        {
                            "href": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T",
                            "rel": "payer-action",
                            "method": "GET",
                        }
                    ],
                },
            )
        if request.url.path == "/v2/checkout/orders/5O190127TN364715T/capture":
            return httpx.Response(
                201,
                json={
                    "id": "5O190127TN364715T",
                    "status": "COMPLETED",
                    "purchase_units": [
                        {"payments": {"captures": [{"id": "CAP-SBX-999", "status": "COMPLETED"}]}}
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
        mandate = _setup_mandate(client)
        res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        order = res["order"]
        assert order["paypal_order_id"] == "5O190127TN364715T"
        assert order["source"] == "sandbox"
        assert "checkoutnow?token=5O190127TN364715T" in order["approval_url"]

        # Verify PayPal-Request-Id idempotency header was sent
        order_req = next(r for r in seen_requests if r[1].endswith("/v2/checkout/orders"))
        assert "paypal-request-id" in order_req[2]

        cap = client.get("/paypal/return?token=5O190127TN364715T", follow_redirects=False)
        assert cap.status_code == 303
        orders = client.get("/api/orders").json()["orders"]
        assert orders[0]["capture_id"] == "CAP-SBX-999"
        assert orders[0]["verified_mandate"] is True


def test_sandbox_missing_credentials_records_paypal_error_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paypal.clear_token_cache()
    db_file = str(tmp_path / "paypal_err.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("PAYPAL_MODE", "sandbox")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "")

    with TestClient(app) as client:
        mandate = _setup_mandate(client)
        res = client.post(
            "/api/agent/run",
            json={
                "mandate_id": mandate["id"],
                "request_text": "get me a black denim jacket",
                "attack": "none",
            },
        ).json()
        assert res["attempt"]["decision"] == "APPROVED"
        assert res["order"] is None
        assert "credentials are missing" in res["paypal_error"]
        assert client.get("/api/orders").json()["orders"] == []

        with db_session(db_file) as conn:
            events = list_events(conn)
            assert any(e["type"] == "PAYPAL_ERROR" for e in events)
            assert verify(conn)["ok"] is True
