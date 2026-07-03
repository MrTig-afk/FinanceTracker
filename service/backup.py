"""Weekly local SQLite backups for the FinanceTracker supervisor.

Backups contain raw transaction data (descriptions, amounts, balances). They are
SENSITIVE: they live only under the data directory (gitignored, ``data/backups/``
by default), never leave this machine, and are never committed. This module makes
ZERO network calls and has no side effects at import time.

The supervisor calls ``run_backup_if_due`` on every tick. When a new weekly backup
is due it is taken with the SQLite ONLINE BACKUP API against a read-only source
connection (never a raw file copy, since the backend may be mid-write), written to
a temp name and atomically renamed into place, then old backups are pruned to the
newest ``KEEP``. Any failure propagates to the caller, which logs it; the next tick
still sees the backup as due, so failures retry naturally.
"""

import os
import re
import sqlite3
import datetime as dt
from pathlib import Path

BACKUP_SUBDIR = "backups"
BACKUP_RE = re.compile(r"^financetracker-(\d{4})-(\d{2})-(\d{2})\.sqlite$")
KEEP = 8
DUE_AFTER_DAYS = 7


def resolve_source_db(repo: Path) -> Path:
    """Resolve the live SQLite path the same way the backend does, stdlib-only.

    Priority: $SQLITE_PATH env var > .env file > './data/financetracker.sqlite'.
    Relative values are anchored to ``repo`` (the Task Scheduler cwd is not the
    repo root), never to the current working directory.
    """
    value = os.environ.get("SQLITE_PATH", "").strip()
    if not value:
        env_file = repo / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                key, _, raw = line.partition("=")
                if key.strip() == "SQLITE_PATH" and raw.strip():
                    value = raw.strip()
                    break
    if not value:
        value = "./data/financetracker.sqlite"
    path = Path(value)
    return path if path.is_absolute() else (repo / path).resolve()


def backups_dir(source_db: Path) -> Path:
    """Directory that holds the dated backup files, beside the live DB."""
    return source_db.parent / BACKUP_SUBDIR


def backup_filename(today: dt.date) -> str:
    """Filename for a snapshot taken on ``today``."""
    return f"financetracker-{today:%Y-%m-%d}.sqlite"


def list_backups(backups: Path) -> list[tuple[dt.date, Path]]:
    """Return (date, path) for every valid backup file, sorted oldest-first.

    Only regular files directly inside ``backups`` whose name fully matches
    ``BACKUP_RE`` and encodes a real calendar date are included. A missing dir
    yields ``[]``; junk names and impossible dates are silently ignored.
    """
    if not backups.is_dir():
        return []
    found: list[tuple[dt.date, Path]] = []
    for entry in backups.iterdir():
        if not entry.is_file():
            continue
        match = BACKUP_RE.match(entry.name)
        if match is None:
            continue
        y, m, d = match.groups()
        try:
            when = dt.date(int(y), int(m), int(d))
        except ValueError:
            continue
        found.append((when, entry))
    found.sort(key=lambda item: (item[0], item[1].name))
    return found


def is_due(backups: Path, today: dt.date) -> bool:
    """True if no valid backup exists or the newest is >= DUE_AFTER_DAYS old.

    A future-dated filename (clock skew, manual copy) is treated as due rather
    than silently disabling backups until that date arrives.
    """
    existing = list_backups(backups)
    if not existing:
        return True
    newest_date = existing[-1][0]
    if newest_date > today:
        return True
    return (today - newest_date).days >= DUE_AFTER_DAYS


def snapshot(source_db: Path, dest: Path) -> None:
    """Copy ``source_db`` to ``dest`` via the SQLite ONLINE BACKUP API.

    Never a raw file copy: the source is opened read-only and the backend may be
    mid-write. The snapshot is written to a ``.tmp`` sibling (which does not match
    ``BACKUP_RE``) then atomically renamed, so a crash can never leave a
    half-written file that looks like a valid backup. On failure the temp file is
    removed and the exception re-raised for the caller to log.
    """
    if not source_db.exists():
        raise FileNotFoundError(source_db)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    try:
        # timeout=5 bounds how long sqlite3 waits on SQLITE_BUSY for the probe
        # query below. Without a bound, Connection.backup() itself retries a
        # busy source FOREVER (it ignores the connection's busy timeout), which
        # would hang this call indefinitely and, since run_backup_if_due runs
        # synchronously inside the supervisor's while-True loop, freeze the
        # whole supervisor rather than fail fast and retry next tick.
        src = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            # Cheap probe BEFORE backup(): if the source is exclusively locked
            # (e.g. a wedged writer holding BEGIN EXCLUSIVE), this raises
            # sqlite3.OperationalError within ~5s instead of blocking forever.
            # There is a small race window between this probe and the backup()
            # call below where a fresh exclusive lock could be taken; that is
            # acceptable because the backend only ever holds short commit-time
            # locks, so backup() blocking briefly on one of those is harmless —
            # the probe exists to catch a genuinely wedged writer, not to make
            # every possible lock impossible.
            src.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def prune(backups: Path, keep: int = KEEP) -> list[Path]:
    """Delete oldest backups so at most ``keep`` remain; return deleted paths.

    Deletion scope is hard-limited to the paths returned by ``list_backups`` —
    only ``BACKUP_RE``-matched regular files directly inside ``backups``. Never
    globs wider, recurses, or touches ``.tmp`` files, the live DB, or subdirs.
    A locked file is skipped (retried on a later prune); only files actually
    unlinked are returned.
    """
    existing = list_backups(backups)
    if len(existing) <= keep:
        return []
    deleted: list[Path] = []
    for _, path in existing[: len(existing) - keep]:
        try:
            path.unlink()
        except OSError:
            continue
        deleted.append(path)
    return deleted


def run_backup_if_due(repo: Path, today: dt.date | None = None) -> str | None:
    """Take and prune a weekly backup if one is due; return a log line or None.

    Returns ``None`` on the common not-due path (no log spam every tick). On a due
    tick it snapshots and prunes, returning a one-line success message. Exceptions
    propagate to the supervisor, which logs them; the failed run writes no dated
    file, so the next tick still sees the backup as due and retries.
    """
    today = today or dt.date.today()
    source = resolve_source_db(repo)
    backups = backups_dir(source)
    if not is_due(backups, today):
        return None
    filename = backup_filename(today)
    dest = backups / filename
    snapshot(source, dest)
    deleted = prune(backups)
    kept = len(list_backups(backups))
    return f"backup ok: {filename} (kept {kept}, pruned {len(deleted)})"
