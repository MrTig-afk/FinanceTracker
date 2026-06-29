"""uploader.py — Google Drive upload stage for FinanceTracker (§7.8, FR-31).

Uploads the monthly .xlsx workbook to the owner's own Google Drive via a service
account, organised under a <root>/<year>/<month>/ folder hierarchy.

CONFIG-GATED: if GOOGLE_SERVICE_ACCOUNT_JSON is missing/unreadable OR DRIVE_FOLDER_ID
is empty, upload_file() returns None and logs a graceful INFO skip — no crash, no
Drive calls, no network activity.

Injectable service: the Drive `service` object is an optional parameter so tests pass
a fake service without ever constructing real credentials or touching the network.
`build_drive_service()` is the sole place real credentials are built and is only called
when the config is present. A bare ``import backend.drive_uploader`` never imports
google libs, never reads the service-account file, never makes network calls.

Privacy / security notes
------------------------
- Service-account JSON contents and the private key are NEVER logged.
- Only filenames, Drive folder ids, and skip/uploaded status appear in logs.
- *.xlsx and service-account JSON are gitignored and must never be committed.

Secrets
-------
GOOGLE_SERVICE_ACCOUNT_JSON and DRIVE_FOLDER_ID are read from .env via python-dotenv.
Never hardcoded. Never read in module scope.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FOLDER_MIMETYPE: str = "application/vnd.google-apps.folder"
DRIVE_SCOPES: list[str] = ["https://www.googleapis.com/auth/drive.file"]
_XLSX_MIMETYPE: str = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _resolve_sa_path(override=None) -> str | None:
    """Resolve the service-account JSON file path.

    Priority: override argument > $GOOGLE_SERVICE_ACCOUNT_JSON > None.
    Calls load_dotenv() first (no-op if already loaded). No file IO.
    """
    if override is not None:
        val = str(override).strip()
        return val if val else None
    load_dotenv()  # no-op if already loaded
    val = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    return val if val else None


def _resolve_folder_id(override=None) -> str | None:
    """Resolve the Drive root folder ID.

    Priority: override argument > $DRIVE_FOLDER_ID > None.
    Treats empty string as None. Calls load_dotenv() first (no-op if already loaded).
    """
    if override is not None:
        val = str(override).strip()
        return val if val else None
    load_dotenv()  # no-op if already loaded
    val = os.getenv("DRIVE_FOLDER_ID", "").strip()
    return val if val else None


def is_configured(*, sa_path=None, folder_id=None) -> bool:
    """Return True only when Drive upload is fully configured and ready.

    Conditions (both required):
    - A non-empty DRIVE_FOLDER_ID is set (env or override).
    - A non-empty GOOGLE_SERVICE_ACCOUNT_JSON path is set AND the file exists on disk.

    Does NOT parse or validate the JSON — that happens only in build_drive_service(),
    which is called only when configured. Does not log secret contents.
    """
    resolved_folder = _resolve_folder_id(folder_id)
    resolved_sa = _resolve_sa_path(sa_path)
    if not resolved_folder or not resolved_sa:
        return False
    return Path(resolved_sa).exists()


# ---------------------------------------------------------------------------
# Service builder — only place real credentials are constructed
# ---------------------------------------------------------------------------


def build_drive_service(sa_path: str | os.PathLike):
    """Build and return an authenticated Google Drive v3 service object.

    Google client libraries are imported lazily here so that a bare
    ``import backend.drive_uploader`` never loads them, never parses credentials,
    and never makes network calls.

    NEVER logs JSON contents, the private key, or any other credential field.
    NEVER called by tests — they inject a fake service via the ``service`` parameter
    of upload_file() instead.
    """
    # Lazy imports — only executed when real Drive access is needed
    from google.oauth2 import service_account  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415

    creds = service_account.Credentials.from_service_account_file(
        str(sa_path), scopes=DRIVE_SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Folder find-or-create helper (idempotent)
# ---------------------------------------------------------------------------


def _find_or_create_folder(service, *, name: str, parent_id: str) -> str:
    """Return the Drive folder id for ``name`` under ``parent_id``.

    Queries first; creates only when absent (idempotent — re-runs find existing
    folders and never create duplicates, satisfying FR-31).

    Raises RuntimeError if the create API call returns no 'id' (unexpected).
    """
    q = (
        f"mimeType='{FOLDER_MIMETYPE}' and trashed=false "
        f"and name='{name}' and '{parent_id}' in parents"
    )
    resp = (
        service.files()
        .list(q=q, spaces="drive", fields="files(id,name)", pageSize=1)
        .execute()
    )
    files = resp.get("files", [])
    if files:
        return files[0]["id"]

    # Not found — create the folder
    meta = {"name": name, "mimeType": FOLDER_MIMETYPE, "parents": [parent_id]}
    created = service.files().create(body=meta, fields="id").execute()
    folder_id = created.get("id")
    if not folder_id:
        raise RuntimeError(
            f"Drive folder create returned no 'id' for folder "
            f"'{name}' under parent '{parent_id}'"
        )
    return folder_id


# ---------------------------------------------------------------------------
# Public upload function
# ---------------------------------------------------------------------------


def upload_file(
    local_path: Path,
    *,
    year: str,
    month: str,
    service=None,
    folder_id: str | None = None,
    sa_path: str | os.PathLike | None = None,
    media_factory=None,
) -> str | None:
    """Upload ``local_path`` to Drive under ``<root>/<year>/<month>/``.

    Returns the Drive file id on success, or None if the upload is skipped.

    The upload is skipped (returns None, logs INFO, makes ZERO Drive calls) when:
    - ``service`` is None AND the config is incomplete (no folder id, or the
      service-account JSON path is absent / the file does not exist).
    - ``service`` is injected but no ``folder_id`` resolves.

    Parameters
    ----------
    local_path:
        Path to the local .xlsx file to upload.
    year:
        Year string (e.g. '2026') — used as the Drive sub-folder name.
    month:
        Month string (e.g. '06') — used as the Drive sub-folder name.
    service:
        Injected Drive service object (for testing). When None and configured,
        build_drive_service() is called internally.
    folder_id:
        Override for the Drive root folder ID. Falls back to $DRIVE_FOLDER_ID.
    sa_path:
        Override for the service-account JSON path. Falls back to
        $GOOGLE_SERVICE_ACCOUNT_JSON.
    media_factory:
        Test seam. When provided, called as ``media_factory(path, mimetype=...,
        resumable=...)`` instead of the real MediaFileUpload. Tests pass FakeMedia
        here to avoid needing a real readable file or the google library at call time.
    """
    # 1. Resolve config
    resolved_folder_id = _resolve_folder_id(folder_id)
    resolved_sa_path = _resolve_sa_path(sa_path)

    # 2. Config gate
    if service is None:
        if not is_configured(sa_path=resolved_sa_path, folder_id=resolved_folder_id):
            logger.info("drive upload skipped: not configured")
            return None
        # Configured — build the real service (only path that ever constructs creds)
        service = build_drive_service(resolved_sa_path)
    else:
        # Service injected — still require a folder_id to know where to upload
        if not resolved_folder_id:
            logger.info("drive upload skipped: not configured")
            return None

    # 3. Root folder id
    root_id: str = resolved_folder_id  # type: ignore[assignment]  # guarded above

    # 4. Find or create the year sub-folder
    year_id = _find_or_create_folder(service, name=year, parent_id=root_id)

    # 5. Find or create the month sub-folder under the year folder
    month_id = _find_or_create_folder(service, name=month, parent_id=year_id)

    # 6. Build media (lazy import so tests with injected service need no google libs)
    if media_factory is not None:
        media = media_factory(str(local_path), mimetype=_XLSX_MIMETYPE, resumable=False)
    else:
        from googleapiclient.http import MediaFileUpload  # noqa: PLC0415 — lazy

        media = MediaFileUpload(str(local_path), mimetype=_XLSX_MIMETYPE, resumable=False)

    # 7. Upload the file
    created = (
        service.files()
        .create(
            body={"name": local_path.name, "parents": [month_id]},
            media_body=media,
            fields="id",
        )
        .execute()
    )

    # 8. Log success and return the Drive file id (filename + id only — no secrets)
    file_id: str = created["id"]
    logger.info(
        "drive upload complete: file=%s id=%s", local_path.name, file_id
    )
    return file_id
