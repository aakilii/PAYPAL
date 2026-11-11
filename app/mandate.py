"""Mandate parsing, default assumptions, validation, and user confirmation."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db import new_id, utc_now, utc_now_iso
from app.ledger import append_event
from app.llm import parse_mandate_llm

DEFAULT_ITEM_CAP_USD = 35.0
DEFAULT_ALLOWED_SELLERS = ["thrift-shop"]
DEFAULT_TTL_HOURS = 24.0


class MandateParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def strip_non_empty_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Shopping request text cannot be empty.")
        return cleaned


class MandateDraft(BaseModel):
    raw_text: str
    item_keywords: list[str]
    max_item_usd: float
    max_day_usd: float
    allowed_sellers: list[str]
    ttl_hours: float
    assumptions: list[str]
    llm_meta: dict[str, Any] = Field(default_factory=dict)


class MandateConfirmRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=500)
    item_keywords: list[str] = Field(min_length=1, max_length=25)
    max_item_usd: float = Field(gt=0.0, le=100_000.0)
    max_day_usd: float = Field(gt=0.0, le=1_000_000.0)
    allowed_sellers: list[str] = Field(min_length=1, max_length=25)
    ttl_hours: float = Field(default=DEFAULT_TTL_HOURS, gt=0.0, le=8760.0)
    assumptions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("raw_text cannot be empty.")
        return cleaned

    @field_validator("max_item_usd", "max_day_usd", "ttl_hours")
    @classmethod
    def validate_finite_numbers(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Numeric mandate fields must be finite.")
        return round(float(value), 2)

    @field_validator("item_keywords", "allowed_sellers")
    @classmethod
    def normalize_string_list(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for val in values:
            norm = val.strip().lower()
            if norm and norm not in cleaned:
                cleaned.append(norm)
        if not cleaned:
            raise ValueError("List must contain at least one non-empty value.")
        return cleaned

    @model_validator(mode="after")
    def validate_daily_cap(self) -> MandateConfirmRequest:
        if self.max_day_usd < self.max_item_usd:
            raise ValueError("Daily cap (max_day_usd) must be at least max_item_usd.")
        return self


def parse_mandate_draft(text: str) -> MandateDraft:
    """Turn natural language shopping instructions into an unconfirmed mandate draft."""
    cleaned_text = text.strip()
    if not cleaned_text:
        raise ValueError("Shopping request text cannot be empty.")

    parsed, llm_meta = parse_mandate_llm(cleaned_text)
    assumptions: list[str] = []

    keywords: list[str] = []
    for kw in parsed.item_keywords:
        norm = kw.strip().lower()
        if norm and norm not in keywords:
            keywords.append(norm)
    if not keywords:
        keywords = ["item"]
        assumptions.append("item_keywords assumed ['item'] (no clear product words detected)")

    if parsed.max_item_usd is not None and parsed.max_item_usd > 0:
        max_item_usd = round(float(parsed.max_item_usd), 2)
    else:
        max_item_usd = DEFAULT_ITEM_CAP_USD
        assumptions.append(
            f"max_item_usd assumed ${max_item_usd:.2f} (no item price cap specified)"
        )

    if parsed.max_day_usd is not None and parsed.max_day_usd >= max_item_usd:
        max_day_usd = round(float(parsed.max_day_usd), 2)
    else:
        max_day_usd = round(2.0 * max_item_usd, 2)
        assumptions.append(
            f"max_day_usd assumed ${max_day_usd:.2f} (2x item cap)"
        )

    if parsed.allowed_sellers:
        allowed_sellers = []
        for s in parsed.allowed_sellers:
            norm_s = s.strip().lower()
            if norm_s and norm_s not in allowed_sellers:
                allowed_sellers.append(norm_s)
    else:
        allowed_sellers = list(DEFAULT_ALLOWED_SELLERS)
        assumptions.append(
            f"allowed_sellers assumed {allowed_sellers} (default trusted seller)"
        )

    if parsed.ttl_hours is not None and parsed.ttl_hours > 0:
        ttl_hours = float(parsed.ttl_hours)
    else:
        ttl_hours = DEFAULT_TTL_HOURS
        assumptions.append(f"expires_at assumed {int(ttl_hours)} hours from confirmation")

    return MandateDraft(
        raw_text=cleaned_text,
        item_keywords=keywords,
        max_item_usd=max_item_usd,
        max_day_usd=max_day_usd,
        allowed_sellers=allowed_sellers,
        ttl_hours=ttl_hours,
        assumptions=assumptions,
        llm_meta=llm_meta,
    )


def confirm_mandate(conn: sqlite3.Connection, req: MandateConfirmRequest) -> dict[str, Any]:
    """Save a user-confirmed mandate and append MANDATE_CONFIRMED to the ledger."""
    mandate_id = new_id("mnd")
    now_dt = utc_now()
    confirmed_at = now_dt.isoformat()
    expires_at = (now_dt + timedelta(hours=req.ttl_hours)).isoformat()

    conn.execute(
        """
        INSERT INTO mandates (
            id, raw_text, item_keywords, max_item_usd, max_day_usd,
            allowed_sellers, expires_at, confirmed_at, status, assumptions
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        (
            mandate_id,
            req.raw_text.strip(),
            json.dumps(req.item_keywords),
            req.max_item_usd,
            req.max_day_usd,
            json.dumps(req.allowed_sellers),
            expires_at,
            confirmed_at,
            json.dumps(req.assumptions),
        ),
    )

    mandate = {
        "id": mandate_id,
        "raw_text": req.raw_text.strip(),
        "item_keywords": req.item_keywords,
        "max_item_usd": req.max_item_usd,
        "max_day_usd": req.max_day_usd,
        "allowed_sellers": req.allowed_sellers,
        "expires_at": expires_at,
        "confirmed_at": confirmed_at,
        "status": "active",
        "assumptions": req.assumptions,
    }

    append_event(
        conn,
        event_type="MANDATE_CONFIRMED",
        ref_id=mandate_id,
        body=mandate,
        ts=confirmed_at,
    )
    return mandate


def _row_to_mandate(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "raw_text": row["raw_text"],
        "item_keywords": json.loads(row["item_keywords"]),
        "max_item_usd": float(row["max_item_usd"]),
        "max_day_usd": float(row["max_day_usd"]),
        "allowed_sellers": json.loads(row["allowed_sellers"]),
        "expires_at": row["expires_at"],
        "confirmed_at": row["confirmed_at"],
        "status": row["status"],
        "assumptions": json.loads(row["assumptions"]),
    }


def get_mandate_by_id(conn: sqlite3.Connection, mandate_id: str) -> dict[str, Any] | None:
    """Fetch a mandate by its ID, or None if not found."""
    row = conn.execute(
        "SELECT * FROM mandates WHERE id = ?",
        (mandate_id,),
    ).fetchone()
    return _row_to_mandate(row) if row is not None else None


def get_active_mandate(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the most recently confirmed active mandate, or None."""
    row = conn.execute(
        """
        SELECT * FROM mandates
        WHERE status = 'active'
        ORDER BY confirmed_at DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    return _row_to_mandate(row) if row is not None else None


def parse_iso_datetime(ts_str: str) -> datetime:
    """Parse an ISO timestamp string into an aware UTC datetime."""
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def revoke_mandate(conn: sqlite3.Connection, mandate_id: str) -> dict[str, Any]:
    """Revoke an active mandate and append MANDATE_REVOKED to the hash-chained ledger."""
    mandate = get_mandate_by_id(conn, mandate_id)
    if mandate is None:
        raise KeyError(f"Mandate '{mandate_id}' not found.")
    if mandate["status"] == "revoked":
        raise ValueError(f"Mandate '{mandate_id}' is already revoked.")

    conn.execute(
        "UPDATE mandates SET status = 'revoked' WHERE id = ?",
        (mandate_id,),
    )
    revoked_at = utc_now_iso()
    append_event(
        conn,
        event_type="MANDATE_REVOKED",
        ref_id=mandate_id,
        body={"mandate_id": mandate_id, "revoked_at": revoked_at},
        ts=revoked_at,
    )
    updated = get_mandate_by_id(conn, mandate_id)
    assert updated is not None
    return updated
