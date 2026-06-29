"""schema.py — DDL constants and schema initialisation for the FinanceTracker SQLite store.

Exposes init_schema(conn) which is idempotent (CREATE TABLE IF NOT EXISTS) and safe to
call repeatedly. No connection is opened here; no IO at import time.
"""
from __future__ import annotations

import sqlite3

_DDL = """
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_fingerprint TEXT NOT NULL UNIQUE,
    date            TEXT NOT NULL,
    description     TEXT NOT NULL,
    amount          TEXT NOT NULL,
    bank            TEXT NOT NULL,
    category        TEXT,
    year_month      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_date       ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_year_month ON transactions(year_month);
CREATE INDEX IF NOT EXISTS idx_txn_category   ON transactions(category);

CREATE TABLE IF NOT EXISTS file_fingerprints (
    fingerprint  TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Execute all DDL statements against conn; safe to call repeatedly (idempotent bootstrap).

    Uses CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS so it can be called on
    an existing database without error. The caller owns the connection lifecycle.
    """
    conn.executescript(_DDL)
