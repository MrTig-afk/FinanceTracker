"""schema.py — DDL constants and schema initialisation for the FinanceTracker SQLite store.

Exposes init_schema(conn) which is idempotent (CREATE TABLE IF NOT EXISTS) and safe to
call repeatedly. No connection is opened here; no IO at import time.
"""
from __future__ import annotations

import sqlite3

_DDL = """
CREATE TABLE IF NOT EXISTS transactions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_fingerprint      TEXT NOT NULL UNIQUE,
    date                 TEXT NOT NULL,
    description          TEXT NOT NULL,
    amount               TEXT NOT NULL,
    bank                 TEXT NOT NULL,
    category             TEXT,
    year_month           TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    reclassified_by_rule INTEGER NOT NULL DEFAULT 0,
    balance              TEXT
);

CREATE INDEX IF NOT EXISTS idx_txn_date       ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_year_month ON transactions(year_month);
CREATE INDEX IF NOT EXISTS idx_txn_category   ON transactions(category);

CREATE TABLE IF NOT EXISTS file_fingerprints (
    fingerprint  TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category_context (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    color       TEXT NOT NULL,
    hints       TEXT NOT NULL DEFAULT '',
    position    INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ctx_position ON category_context(position);

CREATE TABLE IF NOT EXISTS push_subscription (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cleaned_description TEXT NOT NULL UNIQUE,
    category            TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corrections_created ON corrections(created_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transfer_pairs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id_out        INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
    txn_id_in         INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
    status            TEXT NOT NULL DEFAULT 'active',
    prev_category_out TEXT,
    prev_category_in  TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transfer_status ON transfer_pairs(status);

CREATE TABLE IF NOT EXISTS budget_alert_fired (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    year_month  TEXT NOT NULL,
    threshold   INTEGER NOT NULL,          -- 80 or 100
    created_at  TEXT NOT NULL,
    UNIQUE(category, year_month, threshold)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_key      TEXT NOT NULL UNIQUE,   -- "<direction>:<root>"
    root              TEXT NOT NULL,          -- scrubbed, uppercased description root
    direction         TEXT NOT NULL,          -- 'spend' | 'income'
    expected_amount   TEXT NOT NULL,          -- str(Decimal), ABSOLUTE magnitude
    first_seen_month  TEXT NOT NULL,          -- 'YYYY-MM'
    last_seen_month   TEXT NOT NULL,          -- 'YYYY-MM' (monthly cadence anchor)
    status            TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'ended'
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

CREATE TABLE IF NOT EXISTS subscription_event_fired (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_key TEXT NOT NULL,
    year_month   TEXT NOT NULL,
    event        TEXT NOT NULL,               -- 'new' | 'price_change' | 'missed_income'
    created_at   TEXT NOT NULL,
    UNIQUE(merchant_key, year_month, event)
);
"""

# ---------------------------------------------------------------------------
# Full-text search index (FTS5) — kept OUT of _DDL because `USING fts5` raises
# sqlite3.OperationalError on a SQLite build without the FTS5 module, which would
# abort the whole executescript. init_search_index() runs this separately under a
# try/except so callers gracefully fall back to a LIKE search when FTS5 is absent.
#
# External-content table (content='transactions'): the FTS index stores no
# duplicated text — it references the transactions rows by rowid=id. Three
# triggers keep it in sync across every INSERT/UPDATE/DELETE on transactions, so
# no store.py write method needs to touch the index directly.
# ---------------------------------------------------------------------------
_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS transactions_fts USING fts5(
    description,
    category,
    content='transactions',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS transactions_fts_ai AFTER INSERT ON transactions BEGIN
    INSERT INTO transactions_fts(rowid, description, category)
    VALUES (new.id, new.description, new.category);
END;

CREATE TRIGGER IF NOT EXISTS transactions_fts_ad AFTER DELETE ON transactions BEGIN
    INSERT INTO transactions_fts(transactions_fts, rowid, description, category)
    VALUES ('delete', old.id, old.description, old.category);
END;

CREATE TRIGGER IF NOT EXISTS transactions_fts_au AFTER UPDATE ON transactions BEGIN
    INSERT INTO transactions_fts(transactions_fts, rowid, description, category)
    VALUES ('delete', old.id, old.description, old.category);
    INSERT INTO transactions_fts(rowid, description, category)
    VALUES (new.id, new.description, new.category);
END;
"""


def search_index_available(conn: sqlite3.Connection) -> bool:
    """True iff the transactions_fts virtual table exists (FTS5 usable on this conn)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transactions_fts' LIMIT 1"
    ).fetchone()
    return row is not None


def init_search_index(conn: sqlite3.Connection) -> bool:
    """Create the FTS5 index + sync triggers; backfill an existing DB. Idempotent.

    Returns True when FTS5 is available and the index is in place, False when the
    bundled SQLite lacks FTS5 (callers then use the LIKE fallback). Never raises.
    """
    already = search_index_available(conn)
    try:
        conn.executescript(_FTS_DDL)
    except sqlite3.OperationalError:
        return False  # no FTS5 on this build -> LIKE fallback
    if not already:
        # Newly created -> backfill from the content table (idempotent rebuild).
        conn.execute("INSERT INTO transactions_fts(transactions_fts) VALUES ('rebuild')")
    conn.commit()
    return True


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a pre-existing database up to the current schema.

    SQLite has no 'ADD COLUMN IF NOT EXISTS', so we inspect the table and add any
    missing column. Idempotent: a no-op on a database already at the current schema.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
    if "reclassified_by_rule" not in cols:
        conn.execute(
            "ALTER TABLE transactions "
            "ADD COLUMN reclassified_by_rule INTEGER NOT NULL DEFAULT 0"
        )
    if "balance" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN balance TEXT")


def init_schema(conn: sqlite3.Connection) -> None:
    """Execute all DDL statements against conn; safe to call repeatedly (idempotent bootstrap).

    Uses CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS so it can be called on
    an existing database without error, then runs additive migrations for databases
    created by an earlier schema version. The caller owns the connection lifecycle.
    """
    conn.executescript(_DDL)
    _migrate(conn)
    # Full-text search index + sync triggers. Guarded internally (never raises);
    # a pre-existing DB gets the index created and 'rebuild'-backfilled here on
    # first startup, while an already-indexed DB is left untouched.
    init_search_index(conn)
