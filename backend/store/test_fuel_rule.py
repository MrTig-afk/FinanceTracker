"""test_fuel_rule.py — small-fuel-stop reclassification rule (matcher + store methods).

SYNTHETIC, invented merchants only. No real transactions, no real CSVs.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.store import Store
from backend.store.fuel_rule import is_fuel_convenience


# ---------------------------------------------------------------------------
# Merchant matcher
# ---------------------------------------------------------------------------

class TestFuelMatcher:
    @pytest.mark.parametrize(
        "desc",
        [
            "BP CONNECT SYDNEY",
            "7-ELEVEN 1234 MELB",
            "7 ELEVEN CBD",
            "7ELEVEN NORTH",
            "SEVEN ELEVEN CITY",
            "AMPOL FOODARY",
            "CALTEX WOOLWORTHS",
            "COLES EXPRESS FUEL",
            "REDDY EXPRESS RICHMOND",
            "SHELL SERVICE STATION",
            "MOBIL ROADHOUSE",
            "UNITED PETROLEUM",
            "METRO PETROLEUM",
            "OTR HINDLEY ST",
            "PUMA ENERGY",
        ],
    )
    def test_fuel_convenience_positive(self, desc):
        assert is_fuel_convenience(desc) is True

    @pytest.mark.parametrize(
        "desc",
        [
            "OPAL TRAVEL",           # public transport — must NOT match
            "MYKI TOP UP",
            "SKYBUS MELBOURNE",
            "WOOLWORTHS METRO",
            "BPAY BILL PAYMENT",     # not BP
            "MOBILE PHONE RECHARGE",  # not MOBIL
            "SHELLHARBOUR COUNCIL",   # not SHELL (no word boundary)
            "",
        ],
    )
    def test_fuel_convenience_negative(self, desc):
        assert is_fuel_convenience(desc) is False

    def test_none_is_false(self):
        assert is_fuel_convenience(None) is False


# ---------------------------------------------------------------------------
# Store apply / revert
# ---------------------------------------------------------------------------

def _insert(store: Store, *, desc: str, amount: str, category: str,
            ym: str = "2026-06", fp: str | None = None) -> None:
    store.conn.execute(
        "INSERT INTO transactions"
        "(txn_fingerprint, date, description, amount, bank, category, year_month, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fp or (desc + amount), f"{ym}-15", desc, amount, "commbank",
         category, ym, "2026-06-15T00:00:00Z"),
    )
    store.conn.commit()


def _seed(store: Store) -> None:
    _insert(store, desc="BP CONNECT", amount="-8.50", category="Transport", fp="bp-snack")     # move
    _insert(store, desc="7-ELEVEN CBD", amount="-4.00", category="Transport", fp="7e-snack")   # move
    _insert(store, desc="BP CONNECT", amount="-65.00", category="Transport", fp="bp-fuel")     # stay: >= $10
    _insert(store, desc="OPAL TRAVEL", amount="-3.20", category="Transport", fp="opal")        # stay: transit
    _insert(store, desc="WOOLWORTHS", amount="-50.00", category="Groceries", fp="woolies")     # untouched
    _insert(store, desc="7-ELEVEN PIE", amount="-6.00", category="Dining Out", fp="genuine")   # genuine dining


class TestApplyRevert:
    def test_apply_moves_only_qualifying(self):
        with Store(":memory:") as store:
            _seed(store)
            assert store.apply_fuel_dining_rule("2026-06") == 2
            totals = store.summary("2026-06")["totals"]
            # Transport keeps the >$10 fuel and the transit row: -65.00 + -3.20
            assert totals["Transport"] == "-68.20"
            # Dining Out = genuine -6.00 plus moved -8.50 and -4.00
            assert totals["Dining Out"] == "-18.50"
            assert totals["Groceries"] == "-50.00"

    def test_boundary_ten_dollars(self):
        with Store(":memory:") as store:
            _insert(store, desc="BP", amount="-10.00", category="Transport", fp="bp10")
            _insert(store, desc="BP", amount="-9.99", category="Transport", fp="bp999")
            # Strictly under $10: only -9.99 moves.
            assert store.apply_fuel_dining_rule("2026-06") == 1

    def test_apply_is_idempotent(self):
        with Store(":memory:") as store:
            _seed(store)
            assert store.apply_fuel_dining_rule("2026-06") == 2
            assert store.apply_fuel_dining_rule("2026-06") == 0

    def test_revert_restores_only_marked(self):
        with Store(":memory:") as store:
            _seed(store)
            store.apply_fuel_dining_rule("2026-06")
            assert store.revert_fuel_dining_rule("2026-06") == 2
            totals = store.summary("2026-06")["totals"]
            # Transport back to original four-way sum: -8.50 -4.00 -65.00 -3.20
            assert totals["Transport"] == "-80.70"
            # Genuine dining row is untouched (marker was 0).
            assert totals["Dining Out"] == "-6.00"

    def test_flag_tracks_state(self):
        with Store(":memory:") as store:
            _seed(store)
            assert store.summary("2026-06")["fuel_rule_applied"] is False
            store.apply_fuel_dining_rule("2026-06")
            assert store.summary("2026-06")["fuel_rule_applied"] is True
            store.revert_fuel_dining_rule("2026-06")
            assert store.summary("2026-06")["fuel_rule_applied"] is False

    def test_default_month_is_latest(self):
        with Store(":memory:") as store:
            _seed(store)
            assert store.apply_fuel_dining_rule() == 2

    def test_empty_db_is_noop(self):
        with Store(":memory:") as store:
            assert store.apply_fuel_dining_rule() == 0
            assert store.revert_fuel_dining_rule() == 0
            assert store.fuel_rule_applied() is False


# ---------------------------------------------------------------------------
# Migration: a DB created before the marker column gains it on open
# ---------------------------------------------------------------------------

class TestMigration:
    def test_pre_existing_db_gets_column(self, tmp_path):
        db = str(tmp_path / "old.sqlite")
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE transactions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " txn_fingerprint TEXT NOT NULL UNIQUE,"
            " date TEXT NOT NULL, description TEXT NOT NULL, amount TEXT NOT NULL,"
            " bank TEXT NOT NULL, category TEXT, year_month TEXT NOT NULL,"
            " created_at TEXT NOT NULL);"
        )
        conn.execute(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES ('fp1','2026-06-15','BP CONNECT','-5.00','commbank','Transport','2026-06','t')"
        )
        conn.commit()
        conn.close()

        # Opening Store runs the additive migration; the rule then works.
        with Store(db) as store:
            cols = {r[1] for r in store.conn.execute("PRAGMA table_info(transactions)")}
            assert "reclassified_by_rule" in cols
            assert store.apply_fuel_dining_rule("2026-06") == 1
