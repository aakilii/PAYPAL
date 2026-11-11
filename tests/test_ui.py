"""Tests for M7: Static Web UI routes and assets."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_serves_index_css_and_js(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ui.db"))
    with TestClient(app) as client:
        index_res = client.get("/")
        assert index_res.status_code == 200
        assert "Mandate" in index_res.text
        assert "Injection attack" in index_res.text
        assert "Verify chain" in index_res.text

        css_res = client.get("/static/style.css")
        assert css_res.status_code == 200
        assert "prefers-color-scheme: dark" in css_res.text

        js_res = client.get("/static/app.js")
        assert js_res.status_code == 200
        assert "/api/agent/run" in js_res.text
