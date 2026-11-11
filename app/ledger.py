"""Append-only, SHA-256 hash-chained ledger for tamper-evident audit logging."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from app.config import get_settings
from app.db import utc_now_iso

GENESIS_HASH = "GENESIS"

ALLOWED_EVENT_TYPES = frozenset(
    {
        "MANDATE_CONFIRMED",
        "PURCHASE_DECIDED",
        "HUMAN_RESOLVED",
        "ORDER_CREATED",
        "ORDER_CAPTURED",
        "DISPUTE_RECEIVED",
        "DISPUTE_REPLY_DRAFTED",
        "KILL_SWITCH_CHANGED",
        "PAYPAL_ERROR",
    }
)


def canonical_json(data: Any) -> str:
    """Serialize data with sorted keys and no whitespace for deterministic hashing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(
    prev_hash: str,
    event_type: str,
    ref_id: str,
    body: dict[str, Any],
    ts: str,
) -> str:
    """Compute SHA-256(prev_hash + canonical_json(type, ref_id, body, ts))."""
    payload = canonical_json(
        {
            "body": body,
            "ref_id": ref_id,
            "ts": ts,
            "type": event_type,
        }
    )
    digest = hashlib.sha256((prev_hash + payload).encode("utf-8"))
    return digest.hexdigest()


def append_event(
    conn: sqlite3.Connection,
    event_type: str,
    ref_id: str,
    body: dict[str, Any],
    ts: str | None = None,
) -> dict[str, Any]:
    """Append a single immutable event to the hash-chained ledger."""
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"Unsupported ledger event type: {event_type}")

    event_ts = ts or utc_now_iso()
    last_row = conn.execute(
        "SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = str(last_row["hash"]) if last_row is not None else GENESIS_HASH

    body_json = canonical_json(body)
    entry_hash = compute_entry_hash(prev_hash, event_type, ref_id, body, event_ts)

    cursor = conn.execute(
        """
        INSERT INTO ledger (type, ref_id, body_json, prev_hash, hash, ts)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_type, ref_id, body_json, prev_hash, entry_hash, event_ts),
    )
    seq = int(cursor.lastrowid)
    return {
        "seq": seq,
        "type": event_type,
        "ref_id": ref_id,
        "body": body,
        "body_json": body_json,
        "prev_hash": prev_hash,
        "hash": entry_hash,
        "ts": event_ts,
    }


def list_events(
    conn: sqlite3.Connection,
    since_seq: int | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return ledger events in sequence order, optionally after a sequence number."""
    if since_seq is not None:
        rows = conn.execute(
            """
            SELECT seq, type, ref_id, body_json, prev_hash, hash, ts
            FROM ledger
            WHERE seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (since_seq, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT seq, type, ref_id, body_json, prev_hash, hash, ts
            FROM ledger
            ORDER BY seq ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            parsed_body = json.loads(row["body_json"])
        except json.JSONDecodeError:
            parsed_body = {"_corrupted_raw": row["body_json"]}
        events.append(
            {
                "seq": int(row["seq"]),
                "type": row["type"],
                "ref_id": row["ref_id"],
                "body": parsed_body,
                "body_json": row["body_json"],
                "prev_hash": row["prev_hash"],
                "hash": row["hash"],
                "ts": row["ts"],
            }
        )
    return events


def verify(conn: sqlite3.Connection) -> dict[str, Any]:
    """Walk the entire ledger and verify every prev_hash link and SHA-256 digest."""
    rows = conn.execute(
        "SELECT seq, type, ref_id, body_json, prev_hash, hash, ts FROM ledger ORDER BY seq ASC"
    ).fetchall()

    if not rows:
        return {
            "ok": True,
            "checked": 0,
            "first_bad_seq": None,
            "head_hash": GENESIS_HASH,
        }

    expected_prev = GENESIS_HASH
    checked = 0
    tail_hash = str(rows[-1]["hash"])

    for row in rows:
        seq = int(row["seq"])
        checked += 1

        if row["prev_hash"] != expected_prev:
            return {
                "ok": False,
                "checked": checked,
                "first_bad_seq": seq,
                "head_hash": tail_hash,
            }

        try:
            body = json.loads(row["body_json"])
        except json.JSONDecodeError:
            return {
                "ok": False,
                "checked": checked,
                "first_bad_seq": seq,
                "head_hash": tail_hash,
            }

        expected_hash = compute_entry_hash(
            row["prev_hash"],
            row["type"],
            row["ref_id"],
            body,
            row["ts"],
        )
        if row["hash"] != expected_hash:
            return {
                "ok": False,
                "checked": checked,
                "first_bad_seq": seq,
                "head_hash": tail_hash,
            }

        expected_prev = str(row["hash"])

    return {
        "ok": True,
        "checked": checked,
        "first_bad_seq": None,
        "head_hash": expected_prev,
    }


def dev_tamper_entry(conn: sqlite3.Connection, seq: int) -> dict[str, Any]:
    """DEV_MODE ONLY: Mutate one stored ledger body to demonstrate tamper detection.

    This function exists solely so the hackathon demo can show `verify()` catching
    an out-of-band database edit. It refuses to run unless DEV_MODE=true.
    """
    if not get_settings().dev_mode:
        raise PermissionError("Tamper demo is disabled unless DEV_MODE=true.")

    row = conn.execute(
        "SELECT seq, body_json FROM ledger WHERE seq = ?",
        (seq,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Ledger entry seq={seq} not found.")

    try:
        body = json.loads(row["body_json"])
    except json.JSONDecodeError:
        body = {}

    if isinstance(body, dict):
        # Flip or inflate a numeric field if present, plus mark the body visibly altered.
        if "max_item_usd" in body and isinstance(body["max_item_usd"], (int, float)):
            body["max_item_usd"] = round(float(body["max_item_usd"]) + 500.0, 2)
        elif "price_usd" in body and isinstance(body["price_usd"], (int, float)):
            body["price_usd"] = round(float(body["price_usd"]) + 500.0, 2)
        body["_tampered_demo"] = True
        tampered_json = canonical_json(body)
    else:
        tampered_json = '{"_tampered_demo":true}'

    conn.execute(
        "UPDATE ledger SET body_json = ? WHERE seq = ?",
        (tampered_json, seq),
    )
    return {"tampered_seq": seq, "new_body_json": tampered_json}
