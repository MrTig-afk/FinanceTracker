"""test_drive_uploader.py — pytest suite for §7.8 Drive uploader (FR-31).

ALL fixtures use SYNTHETIC data generated in code.
No real transactions. No real credentials. No real Google API calls.
A FakeDriveService injects a recording stub; build_drive_service() is NEVER called.
No network I/O anywhere in this file.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.drive_uploader import is_configured, upload_file

# ---------------------------------------------------------------------------
# Fake Drive service — recording stub; never touches the network
# ---------------------------------------------------------------------------

FOLDER_MIMETYPE = "application/vnd.google-apps.folder"


class _FakeExecutable:
    """Wraps a pre-configured dict result for a chained .execute() call."""

    def __init__(self, result: dict) -> None:
        self._result = result

    def execute(self) -> dict:
        return self._result


class FakeFiles:
    """Recording stub for the service.files() resource.

    Records every list() and create() call in order.
    Returns responses from pre-configured queues.
    """

    def __init__(self) -> None:
        self.list_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self._list_queue: list[dict] = []
        self._create_queue: list[dict] = []

    def set_list_responses(self, responses: list[dict]) -> None:
        self._list_queue = list(responses)

    def set_create_responses(self, responses: list[dict]) -> None:
        self._create_queue = list(responses)

    def list(self, **kwargs) -> _FakeExecutable:  # noqa: A003
        self.list_calls.append(kwargs)
        return _FakeExecutable(self._list_queue.pop(0))

    def create(self, **kwargs) -> _FakeExecutable:
        self.create_calls.append(kwargs)
        return _FakeExecutable(self._create_queue.pop(0))


class FakeDriveService:
    """Minimal Drive service stub; always returns the same FakeFiles instance."""

    def __init__(self) -> None:
        self._files = FakeFiles()

    def files(self) -> FakeFiles:
        return self._files


class FakeMedia:
    """Fake MediaFileUpload — records arguments; never reads the file from disk."""

    def __init__(self, path: str, *, mimetype: str = "", resumable: bool = False) -> None:
        self.path = path
        self.mimetype = mimetype
        self.resumable = resumable


# ---------------------------------------------------------------------------
# Shared synthetic constants
# ---------------------------------------------------------------------------

FAKE_ROOT = "ROOT_FOLDER_SYNTHETIC_ID"
FAKE_YEAR = "2026"
FAKE_MONTH = "06"
FAKE_YEAR_ID = "YEAR_FOLDER_SYNTHETIC_ID"
FAKE_MONTH_ID = "MONTH_FOLDER_SYNTHETIC_ID"
FAKE_FILE_ID = "FILE_SYNTHETIC_ID_abc123"

_EMPTY_LIST: dict = {"files": []}
_EXISTING_YEAR_LIST: dict = {"files": [{"id": FAKE_YEAR_ID, "name": FAKE_YEAR}]}
_EXISTING_MONTH_LIST: dict = {"files": [{"id": FAKE_MONTH_ID, "name": FAKE_MONTH}]}


def _fake_xlsx(tmp_path: Path) -> Path:
    """Return a synthetic placeholder xlsx inside tmp_path.

    Content is a non-empty placeholder; FakeMedia bypasses real file reading.
    """
    p = tmp_path / "financetracker-2026-06.xlsx"
    p.write_bytes(b"SYNTHETIC_PLACEHOLDER_NOT_REAL_XLSX")
    return p


def _folders_absent_service() -> FakeDriveService:
    """Fake service where both year and month folders are absent (must be created)."""
    fake = FakeDriveService()
    fake.files().set_list_responses([_EMPTY_LIST, _EMPTY_LIST])
    fake.files().set_create_responses([
        {"id": FAKE_YEAR_ID},   # create year folder
        {"id": FAKE_MONTH_ID},  # create month folder
        {"id": FAKE_FILE_ID},   # file upload
    ])
    return fake


def _folders_exist_service() -> FakeDriveService:
    """Fake service where both year and month folders already exist (idempotent)."""
    fake = FakeDriveService()
    fake.files().set_list_responses([_EXISTING_YEAR_LIST, _EXISTING_MONTH_LIST])
    fake.files().set_create_responses([{"id": FAKE_FILE_ID}])  # file upload only
    return fake


# ---------------------------------------------------------------------------
# TestUploadFileFoldersAbsent — configured happy path; folders created fresh
# ---------------------------------------------------------------------------


class TestUploadFileFoldersAbsent:
    """upload_file creates year + month folders then uploads when both are absent."""

    def test_returns_drive_file_id(self, tmp_path) -> None:
        """upload_file returns the Drive file id returned by the service."""
        fake = _folders_absent_service()
        result = upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        assert result == FAKE_FILE_ID

    def test_exactly_two_list_calls_happen(self, tmp_path) -> None:
        """Exactly 2 list calls are made: one for year folder, one for month folder."""
        fake = _folders_absent_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        assert len(fake.files().list_calls) == 2, (
            f"Expected 2 list calls, got {len(fake.files().list_calls)}"
        )

    def test_year_folder_list_query_references_root_as_parent(self, tmp_path) -> None:
        """Year folder list query string contains name='YEAR' and 'ROOT' in parents."""
        fake = _folders_absent_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        year_query = fake.files().list_calls[0]["q"]
        assert f"name='{FAKE_YEAR}'" in year_query, \
            f"Year list query must contain name='{FAKE_YEAR}': {year_query!r}"
        assert f"'{FAKE_ROOT}' in parents" in year_query, \
            f"Year list query must reference ROOT as parent: {year_query!r}"

    def test_month_folder_list_query_references_year_id_as_parent(self, tmp_path) -> None:
        """Month folder list query string contains name='MONTH' and year_id in parents."""
        fake = _folders_absent_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        month_query = fake.files().list_calls[1]["q"]
        assert f"name='{FAKE_MONTH}'" in month_query, \
            f"Month list query must contain name='{FAKE_MONTH}': {month_query!r}"
        assert f"'{FAKE_YEAR_ID}' in parents" in month_query, \
            f"Month list query must reference year folder id as parent: {month_query!r}"

    def test_year_folder_create_body_has_root_as_parent(self, tmp_path) -> None:
        """First folder create body: name=year, parents=[ROOT], mimeType=folder."""
        fake = _folders_absent_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        year_body = fake.files().create_calls[0]["body"]
        assert year_body["name"] == FAKE_YEAR
        assert year_body["parents"] == [FAKE_ROOT]
        assert year_body["mimeType"] == FOLDER_MIMETYPE

    def test_month_folder_create_body_has_year_id_as_parent(self, tmp_path) -> None:
        """Second folder create body: name=month, parents=[year_id], mimeType=folder."""
        fake = _folders_absent_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        month_body = fake.files().create_calls[1]["body"]
        assert month_body["name"] == FAKE_MONTH
        assert month_body["parents"] == [FAKE_YEAR_ID]
        assert month_body["mimeType"] == FOLDER_MIMETYPE

    def test_file_upload_create_body_has_month_id_as_parent(self, tmp_path) -> None:
        """File upload create body: name=xlsx filename, parents=[month_id]."""
        fake = _folders_absent_service()
        xlsx = _fake_xlsx(tmp_path)
        upload_file(
            xlsx,
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        file_body = fake.files().create_calls[2]["body"]
        assert file_body["name"] == xlsx.name
        assert file_body["parents"] == [FAKE_MONTH_ID]

    def test_exactly_three_creates_year_month_file_order(self, tmp_path) -> None:
        """Exactly 3 create calls in year-folder → month-folder → file order."""
        fake = _folders_absent_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        creates = fake.files().create_calls
        assert len(creates) == 3, f"Expected 3 create calls, got {len(creates)}"
        # First two are folder creates (have mimeType in body)
        assert creates[0]["body"]["mimeType"] == FOLDER_MIMETYPE
        assert creates[1]["body"]["mimeType"] == FOLDER_MIMETYPE
        # Third is the file upload (has media_body kwarg; no mimeType in body)
        assert "media_body" in creates[2]


# ---------------------------------------------------------------------------
# TestUploadFileFoldersExist — idempotency: no folder creates when already present
# ---------------------------------------------------------------------------


class TestUploadFileFoldersExist:
    """When both year and month folders already exist, no folder create calls are made."""

    def test_returns_drive_file_id(self, tmp_path) -> None:
        fake = _folders_exist_service()
        result = upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        assert result == FAKE_FILE_ID

    def test_zero_folder_creates_only_file_upload(self, tmp_path) -> None:
        """With both folders pre-existing, exactly 1 create call occurs (file upload)."""
        fake = _folders_exist_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        creates = fake.files().create_calls
        assert len(creates) == 1, (
            f"Expected 1 create call (file upload only), got {len(creates)}"
        )
        # Confirm it is the file upload, not a folder create
        assert "media_body" in creates[0], (
            "The single create call must be a file upload (has media_body)"
        )

    def test_still_two_list_calls_to_find_existing_folders(self, tmp_path) -> None:
        """Even with existing folders, 2 list calls still happen (query-first design)."""
        fake = _folders_exist_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        assert len(fake.files().list_calls) == 2

    def test_existing_year_id_used_as_parent_for_month_query(self, tmp_path) -> None:
        """Month list query uses the id from the existing year folder — not a new id."""
        fake = _folders_exist_service()
        upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        month_query = fake.files().list_calls[1]["q"]
        assert f"'{FAKE_YEAR_ID}' in parents" in month_query


# ---------------------------------------------------------------------------
# TestNotConfigured — service=None with no folder/sa_path returns None; zero calls
# ---------------------------------------------------------------------------


class TestNotConfigured:
    def test_service_none_no_env_returns_none(self, tmp_path, monkeypatch) -> None:
        """service=None with empty DRIVE_FOLDER_ID → returns None (upload skipped)."""
        monkeypatch.setenv("DRIVE_FOLDER_ID", "")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

        result = upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=None,
            folder_id=None,
        )
        assert result is None

    def test_service_injected_no_folder_id_returns_none_zero_calls(
        self, tmp_path, monkeypatch
    ) -> None:
        """service injected but folder_id is empty/None → returns None, zero service calls."""
        monkeypatch.setenv("DRIVE_FOLDER_ID", "")

        fake = FakeDriveService()
        result = upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=None,
        )
        assert result is None
        assert len(fake.files().list_calls) == 0, "No list calls when folder_id is absent"
        assert len(fake.files().create_calls) == 0, "No create calls when folder_id is absent"

    def test_not_configured_logs_skip_message(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        """Not-configured path emits 'drive upload skipped: not configured' log."""
        monkeypatch.setenv("DRIVE_FOLDER_ID", "")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

        with caplog.at_level(logging.INFO, logger="backend.drive_uploader.uploader"):
            upload_file(
                _fake_xlsx(tmp_path),
                year=FAKE_YEAR,
                month=FAKE_MONTH,
                service=None,
                folder_id=None,
            )

        messages = [r.message for r in caplog.records]
        assert any("not configured" in m for m in messages), (
            f"Expected a 'not configured' log record; got: {messages!r}"
        )


# ---------------------------------------------------------------------------
# TestIsConfigured
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_false_when_drive_folder_id_empty(self, monkeypatch) -> None:
        """is_configured() returns False when DRIVE_FOLDER_ID is empty."""
        monkeypatch.setenv("DRIVE_FOLDER_ID", "")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "/some/path/sa.json")
        assert is_configured() is False

    def test_false_when_sa_json_path_does_not_exist(self, tmp_path, monkeypatch) -> None:
        """is_configured() returns False when GOOGLE_SERVICE_ACCOUNT_JSON path is absent."""
        nonexistent = str(tmp_path / "does_not_exist_synthetic.json")
        monkeypatch.setenv("DRIVE_FOLDER_ID", "SOME_SYNTHETIC_FOLDER_ID")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", nonexistent)
        assert is_configured() is False

    def test_true_when_both_set_and_file_exists(self, tmp_path, monkeypatch) -> None:
        """is_configured() returns True when folder_id set AND sa_path file exists.

        Uses a synthetic dummy file — NOT a real service-account key.
        is_configured() only checks existence, never parses the JSON.
        """
        dummy_sa = tmp_path / "synthetic-dummy-sa-placeholder.json"
        dummy_sa.write_text('{"type": "synthetic-placeholder-not-real"}')

        monkeypatch.setenv("DRIVE_FOLDER_ID", "SYNTHETIC_FOLDER_ID_ABC")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(dummy_sa))

        assert is_configured() is True

    def test_false_when_sa_json_env_empty(self, monkeypatch) -> None:
        """is_configured() returns False when GOOGLE_SERVICE_ACCOUNT_JSON is empty."""
        monkeypatch.setenv("DRIVE_FOLDER_ID", "SYNTHETIC_FOLDER_ID")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        assert is_configured() is False

    def test_false_when_folder_id_whitespace_only(self, monkeypatch) -> None:
        """Whitespace-only DRIVE_FOLDER_ID is treated as empty (strip → '') → False."""
        monkeypatch.setenv("DRIVE_FOLDER_ID", "   ")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "/some/path.json")
        assert is_configured() is False


# ---------------------------------------------------------------------------
# TestSaPathNeverLogged — SA JSON path must not appear in any log record
# ---------------------------------------------------------------------------


class TestSaPathNeverLogged:
    def test_sa_path_absent_from_skip_log_records(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        """The SA JSON path must NOT appear in any log record on the skip (not-configured) path."""
        secret_sa_path = str(tmp_path / "my-secret-synthetic-service-account.json")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", secret_sa_path)
        monkeypatch.setenv("DRIVE_FOLDER_ID", "")

        with caplog.at_level(logging.DEBUG, logger="backend.drive_uploader.uploader"):
            upload_file(
                _fake_xlsx(tmp_path),
                year=FAKE_YEAR,
                month=FAKE_MONTH,
                service=None,
                folder_id=None,
            )

        for record in caplog.records:
            msg = record.getMessage()
            assert secret_sa_path not in msg, (
                f"SA path must NEVER appear in log output; found in: {msg!r}"
            )

    def test_sa_path_absent_from_upload_complete_log(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        """The SA JSON path must NOT appear in the upload-complete log record."""
        secret_sa_path = str(tmp_path / "another-secret-synthetic-sa.json")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", secret_sa_path)
        monkeypatch.setenv("DRIVE_FOLDER_ID", FAKE_ROOT)

        # Inject service — build_drive_service is never called
        fake = _folders_exist_service()

        with caplog.at_level(logging.DEBUG, logger="backend.drive_uploader.uploader"):
            upload_file(
                _fake_xlsx(tmp_path),
                year=FAKE_YEAR,
                month=FAKE_MONTH,
                service=fake,
                folder_id=FAKE_ROOT,
                media_factory=FakeMedia,
            )

        for record in caplog.records:
            msg = record.getMessage()
            assert secret_sa_path not in msg, (
                f"SA path must NEVER appear in log output; found in: {msg!r}"
            )

    def test_build_drive_service_never_called_when_service_injected(
        self, tmp_path, monkeypatch
    ) -> None:
        """When service is injected, build_drive_service is never invoked.

        Verified by monkeypatching build_drive_service to raise AssertionError,
        then confirming upload_file still succeeds.
        """
        import backend.drive_uploader.uploader as _uploader

        def _must_not_be_called(*args, **kwargs):
            raise AssertionError(
                "build_drive_service must NOT be called when a service is injected"
            )

        monkeypatch.setattr(_uploader, "build_drive_service", _must_not_be_called)
        monkeypatch.setenv("DRIVE_FOLDER_ID", FAKE_ROOT)

        fake = _folders_exist_service()
        result = upload_file(
            _fake_xlsx(tmp_path),
            year=FAKE_YEAR,
            month=FAKE_MONTH,
            service=fake,
            folder_id=FAKE_ROOT,
            media_factory=FakeMedia,
        )
        assert result == FAKE_FILE_ID
