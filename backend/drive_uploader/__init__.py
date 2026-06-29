"""drive_uploader — Google Drive upload stage for FinanceTracker (§7.8, FR-31).

Uploads the monthly .xlsx workbook to the owner's own Google Drive via a service
account, organised under a <root>/<year>/<month>/ folder hierarchy.

CONFIG-GATED: if GOOGLE_SERVICE_ACCOUNT_JSON is missing/unreadable or DRIVE_FOLDER_ID
is empty, upload_file() returns None and logs a graceful INFO skip — no crash, no
Drive calls, no network activity.  The default shipped state is therefore local-only safe.

No filesystem or network access occurs on a bare ``import backend.drive_uploader``.
"""
from __future__ import annotations

from .uploader import build_drive_service, is_configured, upload_file

__all__ = [
    "upload_file",
    "is_configured",
    "build_drive_service",
]
