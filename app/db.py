"""SQLite schema setup and database helpers for Mandate."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import get_settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mandates (
    id TEXT PRIMARY KEY,
    raw_text TEXT NOT NULL,
    item_keywords TEXT NOT NULL,
    max_item_usd REAL NOT NULL,
    max_day_usd REAL NOT NULL,
    allowed_sellers TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    assumptions TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    mandate_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    seller TEXT NOT NULL,
    price_usd REAL NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'BLOCKED', 'HELD')),
    reason_codes TEXT NOT NULL,
    checks TEXT NOT NULL,
    risk_score REAL NOT NULL,
    risk_reasons TEXT NOT NULL,
    human_resolution TEXT CHECK (human_resolution IN ('approved', 'denied') OR human_resolution IS NULL),
    status TEXT NOT NULL CHECK (status IN ('decided', 'order_created', 'paid', 'denied')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    paypal_order_id TEXT NOT NULL,
    capture_id TEXT,
    status TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('sandbox', 'mock')),
    approval_url TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disputes (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    paypal_dispute_id TEXT,
    claim TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('paypal', 'simulated')),
    evidence TEXT NOT NULL,
    strength TEXT NOT NULL CHECK (strength IN ('STRONG', 'WEAK', 'NO_RECORD')),
    draft_reply TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    body_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""


def utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""
    return utc_now().isoformat()


def new_id(prefix: str) -> str:
    """Generate a short, readable unique identifier with a domain prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def resolve_db_path(db_path: str | None = None) -> str:
    """Return explicit db_path or fall back to the configured DB_PATH."""
    return db_path if db_path is not None else get_settings().db_path


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection configured with Row factory, foreign keys, and busy timeout."""
    path = resolve_db_path(db_path)
    conn = sqlite3.connect(path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


@contextmanager
def db_session(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success, rolls back on error, and always closes."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Create all tables and default settings if they do not exist yet."""
    path = resolve_db_path(db_path)
    with db_session(path) as conn:
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('kill_switch', 'false')"
        )


def is_kill_switch_on(conn: sqlite3.Connection) -> bool:
    """Read whether the agent kill switch is currently active."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'kill_switch'"
    ).fetchone()
    if row is None:
        return False
    return str(row["value"]).strip().lower() == "true"


def set_kill_switch_value(conn: sqlite3.Connection, enabled: bool) -> None:
    """Persist the kill switch state ('true' or 'false')."""
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES ('kill_switch', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("true" if enabled else "false",),
    )
