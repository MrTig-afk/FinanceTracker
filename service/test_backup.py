"""test_backup.py — pytest suite for the v7 supervisor weekly-backup module.

ALL fixtures use synthetic data generated inline.
No real transactions, no real descriptions, no real account numbers.
Every path is under tmp_path — NEVER the real SQLITE_PATH / ./data/ / .env / logs/.
No network calls anywhere in this file. The supervisor loop is never started;
only service.backup is imported, never service.supervisor.main.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from pathlib import Path

import pytest

from service import backup

# ---------------------------------------------------------------------------
# Synthetic helpers — invented values; never real data
# ---------------------------------------------------------------------------

# Invented rows; not real transactions. (description, amount)
_SYNTH_ROWS = [
    ("SYNTH COFFEE SHOP", -4.50),
    ("SYNTH GROCERY STORE", -25.00),
    ("SYNTH SALARY CREDIT", 2000.00),
]


def make_db(path: Path) -> list[tuple[str, float]]:
    """Create a synthetic sqlite DB at ``path`` and return its inserted rows.

    Rows are invented in code (never real transactions). Returns the rows so a
    test can assert a snapshot copied them faithfully.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE tx (id INTEGER PRIMARY KEY, description TEXT, amount REAL)"
        )
        conn.executemany(
            "INSERT INTO tx (description, amount) VALUES (?, ?)", _SYNTH_ROWS
        )
        conn.commit()
    finally:
        conn.close()
    return list(_SYNTH_ROWS)


def read_rows(path: Path) -> list[tuple[str, float]]:
    """Read (description, amount) rows from a sqlite DB, ordered by id."""
    conn = sqlite3.connect(str(path))
    try:
        return [
            (row[0], row[1])
            for row in conn.execute(
                "SELECT description, amount FROM tx ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()


def touch_backup(backups: Path, when: dt.date) -> Path:
    """Create an empty, correctly-named backup file dated ``when``.

    prune()/is_due() never open backup files, so an empty file is a valid
    stand-in for date-based tests.
    """
    backups.mkdir(parents=True, exist_ok=True)
    path = backups / backup.backup_filename(when)
    path.write_bytes(b"")
    return path


# ---------------------------------------------------------------------------
# TestResolveSourceDb
# ---------------------------------------------------------------------------


class TestResolveSourceDb:
    def test_env_var_absolute_returned_as_is(self, tmp_path, monkeypatch) -> None:
        """An absolute $SQLITE_PATH is returned unchanged (highest priority)."""
        target = tmp_path / "somewhere" / "custom.sqlite"
        monkeypatch.setenv("SQLITE_PATH", str(target))
        # A decoy .env that must be ignored because the env var wins.
        (tmp_path / ".env").write_text("SQLITE_PATH=./data/ignored.sqlite\n")

        result = backup.resolve_source_db(tmp_path)

        assert result == target

    def test_env_file_relative_anchored_to_repo(self, tmp_path, monkeypatch) -> None:
        """With no env var, a relative .env value resolves under the tmp repo, not cwd."""
        monkeypatch.delenv("SQLITE_PATH", raising=False)
        (tmp_path / ".env").write_text("SQLITE_PATH=./data/financetracker.sqlite\n")

        result = backup.resolve_source_db(tmp_path)

        assert result == (tmp_path / "data" / "financetracker.sqlite").resolve()

    def test_default_when_no_env_and_no_file(self, tmp_path, monkeypatch) -> None:
        """No env var and no .env falls back to ./data/financetracker.sqlite under repo."""
        monkeypatch.delenv("SQLITE_PATH", raising=False)
        assert not (tmp_path / ".env").exists()

        result = backup.resolve_source_db(tmp_path)

        assert result == (tmp_path / "data" / "financetracker.sqlite").resolve()

    def test_empty_env_var_falls_through_to_env_file(self, tmp_path, monkeypatch) -> None:
        """A blank $SQLITE_PATH is ignored and the .env value is used instead."""
        monkeypatch.setenv("SQLITE_PATH", "   ")
        (tmp_path / ".env").write_text("SQLITE_PATH=./data/financetracker.sqlite\n")

        result = backup.resolve_source_db(tmp_path)

        assert result == (tmp_path / "data" / "financetracker.sqlite").resolve()


# ---------------------------------------------------------------------------
# TestBackupFilename
# ---------------------------------------------------------------------------


class TestBackupFilename:
    def test_filename_format(self) -> None:
        assert (
            backup.backup_filename(dt.date(2026, 7, 3))
            == "financetracker-2026-07-03.sqlite"
        )

    def test_filename_zero_padded(self) -> None:
        """Single-digit month/day are zero-padded so names sort lexically by date."""
        assert (
            backup.backup_filename(dt.date(2026, 1, 5))
            == "financetracker-2026-01-05.sqlite"
        )


# ---------------------------------------------------------------------------
# TestBackupsDir
# ---------------------------------------------------------------------------


class TestBackupsDir:
    def test_backups_dir_beside_db(self, tmp_path) -> None:
        source = tmp_path / "data" / "financetracker.sqlite"
        assert backup.backups_dir(source) == tmp_path / "data" / "backups"


# ---------------------------------------------------------------------------
# TestListBackups
# ---------------------------------------------------------------------------


class TestListBackups:
    def test_missing_dir_returns_empty(self, tmp_path) -> None:
        assert backup.list_backups(tmp_path / "nope") == []

    def test_sorted_oldest_first_and_junk_ignored(self, tmp_path) -> None:
        """Only pattern-matching real-dated files are listed, sorted oldest-first."""
        backups = tmp_path / "backups"
        backups.mkdir()
        # Create out of order to prove sorting.
        touch_backup(backups, dt.date(2026, 6, 1))
        touch_backup(backups, dt.date(2026, 5, 1))
        touch_backup(backups, dt.date(2026, 7, 1))
        # Junk that must be ignored (not deleted, not crashing).
        (backups / "notes.txt").write_text("x")
        (backups / "other.sqlite").write_bytes(b"")
        (backups / "financetracker-2026-07-01.sqlite.tmp").write_bytes(b"")
        (backups / "financetracker-2026-99-99.sqlite").write_bytes(b"")  # impossible date
        (backups / "financetracker-2026-06-15.sqlite").mkdir()  # dir, not a file

        result = backup.list_backups(backups)

        dates = [d for d, _ in result]
        assert dates == [dt.date(2026, 5, 1), dt.date(2026, 6, 1), dt.date(2026, 7, 1)]
        # Every returned path is a real file whose name matches the pattern.
        for _, path in result:
            assert path.is_file()
            assert backup.BACKUP_RE.match(path.name)

    def test_impossible_date_skipped_without_crash(self, tmp_path) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "financetracker-2026-13-40.sqlite").write_bytes(b"")
        assert backup.list_backups(backups) == []


# ---------------------------------------------------------------------------
# TestIsDue
# ---------------------------------------------------------------------------


class TestIsDue:
    TODAY = dt.date(2026, 7, 10)

    def test_missing_dir_is_due(self, tmp_path) -> None:
        assert backup.is_due(tmp_path / "nope", self.TODAY) is True

    def test_empty_dir_is_due(self, tmp_path) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        assert backup.is_due(backups, self.TODAY) is True

    def test_newest_exactly_seven_days_is_due(self, tmp_path) -> None:
        backups = tmp_path / "backups"
        touch_backup(backups, self.TODAY - dt.timedelta(days=7))
        assert backup.is_due(backups, self.TODAY) is True

    def test_newest_three_days_not_due(self, tmp_path) -> None:
        backups = tmp_path / "backups"
        touch_backup(backups, self.TODAY - dt.timedelta(days=3))
        assert backup.is_due(backups, self.TODAY) is False

    def test_newest_today_not_due(self, tmp_path) -> None:
        backups = tmp_path / "backups"
        touch_backup(backups, self.TODAY)
        assert backup.is_due(backups, self.TODAY) is False

    def test_future_dated_filename_is_due(self, tmp_path) -> None:
        # Clock skew / a manually copied future-dated file must not silently
        # disable backups until that date arrives.
        backups = tmp_path / "backups"
        touch_backup(backups, self.TODAY + dt.timedelta(days=30))
        assert backup.is_due(backups, self.TODAY) is True

    def test_uses_newest_of_many(self, tmp_path) -> None:
        """Due-check keys off the NEWEST date; old files alongside a recent one -> not due."""
        backups = tmp_path / "backups"
        touch_backup(backups, self.TODAY - dt.timedelta(days=30))
        touch_backup(backups, self.TODAY - dt.timedelta(days=2))  # recent
        assert backup.is_due(backups, self.TODAY) is False

    def test_only_junk_is_due(self, tmp_path) -> None:
        """A folder of only non-matching junk counts as no backups -> due."""
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "notes.txt").write_text("x")
        (backups / "other.sqlite").write_bytes(b"")
        (backups / "financetracker-2026-07-03.sqlite.tmp").write_bytes(b"")
        assert backup.is_due(backups, self.TODAY) is True

    def test_impossible_date_only_is_due(self, tmp_path) -> None:
        """A pattern-matching but impossible date is skipped -> treated as no backups."""
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "financetracker-2026-99-99.sqlite").write_bytes(b"")
        assert backup.is_due(backups, self.TODAY) is True

    def test_date_from_filename_not_mtime(self, tmp_path) -> None:
        """Due-ness is parsed from the filename date, never the file mtime.

        A file physically written 'now' but NAMED 30 days ago must read as due;
        one named today must read as not-due — proving mtime is irrelevant.
        """
        backups = tmp_path / "backups"
        old_named = touch_backup(backups, self.TODAY - dt.timedelta(days=30))
        # Its mtime is now (just written), but the NAME is 30 days old.
        assert old_named.stat().st_mtime > 0
        assert backup.is_due(backups, self.TODAY) is True

        touch_backup(backups, self.TODAY)  # now a today-named file exists
        assert backup.is_due(backups, self.TODAY) is False


# ---------------------------------------------------------------------------
# TestSnapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_happy_path_faithful_copy_no_tmp_left(self, tmp_path) -> None:
        """snapshot produces an openable copy with identical rows; source unchanged; no .tmp."""
        src = tmp_path / "src.sqlite"
        rows = make_db(src)
        dest = tmp_path / "backups" / "financetracker-2026-07-03.sqlite"

        backup.snapshot(src, dest)

        assert dest.exists()
        assert read_rows(dest) == rows
        # Source still opens and is unchanged.
        assert read_rows(src) == rows
        # No temp file left behind.
        assert not dest.with_name(dest.name + ".tmp").exists()

    def test_snapshot_while_writer_holds_source_open(self, tmp_path) -> None:
        """A copy is faithful even while another connection holds the source DB open."""
        src = tmp_path / "src.sqlite"
        rows = make_db(src)
        dest = tmp_path / "backups" / "financetracker-2026-07-03.sqlite"

        holder = sqlite3.connect(str(src))  # backend-style open handle held during backup
        try:
            holder.execute("SELECT COUNT(*) FROM tx").fetchone()
            backup.snapshot(src, dest)
        finally:
            holder.close()

        assert read_rows(dest) == rows

    def test_overwrites_same_day_file(self, tmp_path) -> None:
        """A pre-existing same-day dest is atomically replaced by the fresh snapshot."""
        src = tmp_path / "src.sqlite"
        rows = make_db(src)
        dest = tmp_path / "backups" / "financetracker-2026-07-03.sqlite"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"stale different content")

        backup.snapshot(src, dest)

        assert read_rows(dest) == rows
        assert not dest.with_name(dest.name + ".tmp").exists()

    def test_stale_tmp_is_replaced(self, tmp_path) -> None:
        """A leftover .tmp from a previous crash is cleared and the snapshot still succeeds."""
        src = tmp_path / "src.sqlite"
        rows = make_db(src)
        dest = tmp_path / "backups" / "financetracker-2026-07-03.sqlite"
        dest.parent.mkdir(parents=True)
        (dest.parent / (dest.name + ".tmp")).write_bytes(b"garbage from a crash")

        backup.snapshot(src, dest)

        assert read_rows(dest) == rows
        assert not dest.with_name(dest.name + ".tmp").exists()

    def test_locked_source_raises_and_leaves_nothing(self, tmp_path) -> None:
        """A source locked with BEGIN EXCLUSIVE -> OperationalError; no dest, no .tmp.

        Spec case 6 requires snapshot() to RAISE sqlite3.OperationalError on a locked
        source. snapshot runs in a worker thread with a bounded join so that, if the
        implementation instead blocks forever (sqlite3.Connection.backup() retries
        SQLITE_BUSY indefinitely and ignores the busy timeout), this test FAILS
        deterministically rather than hanging the whole suite.
        """
        src = tmp_path / "src.sqlite"
        make_db(src)
        dest = tmp_path / "backups" / "financetracker-2026-07-03.sqlite"

        outcome: dict[str, object] = {}

        def worker() -> None:
            try:
                backup.snapshot(src, dest)
                outcome["result"] = "returned-without-raising"
            except BaseException as exc:  # capture whatever it raises
                outcome["result"] = exc

        locker = sqlite3.connect(str(src), timeout=0.1)
        locker.execute("BEGIN EXCLUSIVE")
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=10)
        hung = thread.is_alive()

        try:
            assert not hung, (
                "snapshot() did not return within 10s on a BEGIN EXCLUSIVE-locked "
                "source: it hangs instead of raising sqlite3.OperationalError (spec "
                "case 6). sqlite3.Connection.backup() retries SQLITE_BUSY forever and "
                "ignores the busy timeout, so a locked DB would freeze the supervisor "
                "loop rather than fail fast and retry next tick."
            )
            assert isinstance(outcome.get("result"), sqlite3.OperationalError), (
                f"expected sqlite3.OperationalError, got {outcome.get('result')!r}"
            )
            assert not dest.exists()
            assert not dest.with_name(dest.name + ".tmp").exists()
        finally:
            # Release the lock so the (daemon) worker can unwind cleanly.
            try:
                locker.rollback()
            finally:
                locker.close()
            thread.join(timeout=5)

    def test_corrupt_source_raises_and_leaves_nothing(self, tmp_path) -> None:
        """Garbage bytes in a .sqlite source -> DatabaseError; no dest, no .tmp."""
        src = tmp_path / "src.sqlite"
        src.write_bytes(b"this is not a sqlite database at all")
        dest = tmp_path / "backups" / "financetracker-2026-07-03.sqlite"

        with pytest.raises(sqlite3.DatabaseError):
            backup.snapshot(src, dest)

        assert not dest.exists()
        assert not dest.with_name(dest.name + ".tmp").exists()

    def test_missing_source_raises_filenotfound(self, tmp_path) -> None:
        src = tmp_path / "does-not-exist.sqlite"
        dest = tmp_path / "backups" / "financetracker-2026-07-03.sqlite"

        with pytest.raises(FileNotFoundError):
            backup.snapshot(src, dest)

        assert not dest.exists()


# ---------------------------------------------------------------------------
# TestPrune
# ---------------------------------------------------------------------------


class TestPrune:
    def test_keeps_newest_deletes_older_and_ignores_decoys(self, tmp_path) -> None:
        """10 dated backups + decoys -> keep 8 newest; 2 oldest deleted; decoys survive."""
        backups = tmp_path / "backups"
        backups.mkdir()
        base = dt.date(2026, 1, 1)
        made = [touch_backup(backups, base + dt.timedelta(weeks=i)) for i in range(10)]

        # Decoys that must NEVER be touched.
        decoy_txt = backups / "notes.txt"
        decoy_txt.write_text("keep me")
        decoy_tmp = backups / "financetracker-2026-01-01.sqlite.tmp"
        decoy_tmp.write_bytes(b"tmp")
        decoy_other = backups / "other.sqlite"
        decoy_other.write_bytes(b"")
        decoy_dir = backups / "financetracker-2026-05-05.sqlite"  # a directory
        decoy_dir.mkdir()

        deleted = backup.prune(backups, keep=8)

        # The two oldest were deleted.
        assert set(deleted) == {made[0], made[1]}
        assert not made[0].exists()
        assert not made[1].exists()
        # The 8 newest remain.
        for path in made[2:]:
            assert path.exists()
        remaining_dates = [d for d, _ in backup.list_backups(backups)]
        assert len(remaining_dates) == 8
        # Every decoy survives untouched.
        assert decoy_txt.exists()
        assert decoy_tmp.exists()
        assert decoy_other.exists()
        assert decoy_dir.is_dir()

    def test_no_op_when_at_or_below_keep(self, tmp_path) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        base = dt.date(2026, 1, 1)
        for i in range(8):
            touch_backup(backups, base + dt.timedelta(weeks=i))

        deleted = backup.prune(backups, keep=8)

        assert deleted == []
        assert len(backup.list_backups(backups)) == 8

    def test_missing_dir_returns_empty(self, tmp_path) -> None:
        assert backup.prune(tmp_path / "nope", keep=8) == []


# ---------------------------------------------------------------------------
# TestRunBackupIfDue
# ---------------------------------------------------------------------------


def _make_repo(tmp_path, monkeypatch) -> Path:
    """Build a tmp 'repo' with a synthetic DB and a .env pointing at it.

    Uses a relative SQLITE_PATH in .env (anchored to the repo) so backups land in
    <repo>/data/backups — entirely inside tmp_path, never the real data/.
    """
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    make_db(repo / "data" / "financetracker.sqlite")
    (repo / ".env").write_text("SQLITE_PATH=./data/financetracker.sqlite\n")
    return repo


class TestRunBackupIfDue:
    def test_end_to_end_due_then_idempotent_then_advance(self, tmp_path, monkeypatch) -> None:
        """First run creates a backup + message; immediate re-run is a no-op; +7 days creates a second."""
        repo = _make_repo(tmp_path, monkeypatch)
        backups = repo / "data" / "backups"
        day1 = dt.date(2026, 7, 3)

        # 1. Due -> creates file + success message.
        msg = backup.run_backup_if_due(repo, today=day1)
        assert msg == "backup ok: financetracker-2026-07-03.sqlite (kept 1, pruned 0)"
        first_file = backups / "financetracker-2026-07-03.sqlite"
        assert first_file.exists()
        assert read_rows(first_file) == list(_SYNTH_ROWS)

        # 2. Immediate re-run same day -> not due -> no-op, no new file.
        assert backup.run_backup_if_due(repo, today=day1) is None
        assert [p.name for _, p in backup.list_backups(backups)] == [
            "financetracker-2026-07-03.sqlite"
        ]

        # 3. Seven days later -> due again -> second file.
        day2 = day1 + dt.timedelta(days=7)
        msg2 = backup.run_backup_if_due(repo, today=day2)
        assert msg2 == "backup ok: financetracker-2026-07-10.sqlite (kept 2, pruned 0)"
        assert len(backup.list_backups(backups)) == 2

    def test_not_due_leaves_no_log_and_no_file(self, tmp_path, monkeypatch) -> None:
        """A recent backup present -> run returns None and writes nothing new."""
        repo = _make_repo(tmp_path, monkeypatch)
        backups = repo / "data" / "backups"
        today = dt.date(2026, 7, 3)
        touch_backup(backups, today - dt.timedelta(days=2))  # recent

        assert backup.run_backup_if_due(repo, today=today) is None
        # Only the pre-seeded recent file exists; no today-dated file created.
        assert (backups / "financetracker-2026-07-01.sqlite").exists()
        assert not (backups / "financetracker-2026-07-03.sqlite").exists()

    def test_retention_integration_reports_kept8_pruned1(self, tmp_path, monkeypatch) -> None:
        """8 old backups + one due run -> exactly 8 files, oldest gone, 'kept 8, pruned 1'."""
        repo = _make_repo(tmp_path, monkeypatch)
        backups = repo / "data" / "backups"
        # 8 pre-seeded weekly backups, newest well over 7 days before the run.
        oldest_date = dt.date(2026, 5, 1)
        seeded = [
            touch_backup(backups, oldest_date + dt.timedelta(weeks=i)) for i in range(8)
        ]
        today = dt.date(2026, 7, 3)

        msg = backup.run_backup_if_due(repo, today=today)

        assert msg == "backup ok: financetracker-2026-07-03.sqlite (kept 8, pruned 1)"
        assert len(backup.list_backups(backups)) == 8
        # The single oldest pre-seeded file was pruned.
        assert not seeded[0].exists()
        assert seeded[1].exists()

    def test_missing_source_propagates_filenotfound(self, tmp_path, monkeypatch) -> None:
        """A due run whose source DB does not exist raises FileNotFoundError (caller logs it)."""
        monkeypatch.delenv("SQLITE_PATH", raising=False)
        repo = tmp_path / "repo"
        repo.mkdir()
        missing = tmp_path / "gone" / "financetracker.sqlite"
        monkeypatch.setenv("SQLITE_PATH", str(missing))

        with pytest.raises(FileNotFoundError):
            backup.run_backup_if_due(repo, today=dt.date(2026, 7, 3))

        # No backups dir/file was created next to the (missing) source.
        assert not (missing.parent / "backups").exists()


# ---------------------------------------------------------------------------
# TestPrivacy — the module makes zero network calls (matches its docstring)
# ---------------------------------------------------------------------------


class TestPrivacy:
    def test_backup_module_has_no_network_imports(self) -> None:
        """backup.py must import zero network libraries — backups never leave the machine."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(backup))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

        forbidden = {"requests", "httpx", "urllib", "socket", "http", "aiohttp"}
        leaked = imported_roots & forbidden
        assert not leaked, f"backup.py must import zero network libraries (found {leaked})"
