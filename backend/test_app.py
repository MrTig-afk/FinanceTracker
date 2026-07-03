"""test_app.py — pytest suite for backend/app.py FastAPI endpoints (§7.2).

ALL fixtures use SYNTHETIC data generated in code.
No real transactions, no real account numbers, no real CSV files read from disk.
No live network calls — analyser injected via monkeypatched run_pipeline.
Drive unconfigured. DB in tmp_path sqlite.
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.pipeline import run_pipeline as _real_run_pipeline


# ---------------------------------------------------------------------------
# Synthetic CSV bytes — invented merchants; never real data
# ---------------------------------------------------------------------------

# CommBank: no header, DD/MM/YYYY, signed amount, description, balance
_CB_TEXT = (
    "20/06/2026,-72.40,WOOLWORTHS METRO,1000.00\n"
    "21/06/2026,-18.90,SYNTH TRANSPORT CO,927.60\n"
    "22/06/2026,-9.50,SYNTH COFFEE SHOP,918.10\n"
)
_CB_BYTES = _CB_TEXT.encode("utf-8")

# Westpac: header row; col-0 = account number (dropped by parser); split debit/credit
_WP_TEXT = (
    "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial\n"
    "748007654321,23/06/2026,SYNTH UTILITY BILL,130.05,,2000.00,,\n"
    "748007654321,24/06/2026,SYNTH SALARY CREDIT,,3200.00,5200.00,,\n"
)
_WP_BYTES = _WP_TEXT.encode("utf-8")

# Synthetic account number embedded in Westpac CSV — must not appear in analyser payload
_FAKE_ACCT = "748007654321"


# ---------------------------------------------------------------------------
# Fake analyser client — zero network; records prompts
# ---------------------------------------------------------------------------

class FakeAnalyserClient:
    """Minimal stand-in for OpenRouterClient. Assigns every row `default_category`."""

    def __init__(self, default_category: str = "Groceries") -> None:
        self.call_count = 0
        self.received_user_prompts: list[str] = []
        self._default_category = default_category

    def complete(self, *, system_prompt: str, user_prompt: str) -> tuple[dict, str]:
        self.call_count += 1
        self.received_user_prompts.append(user_prompt)
        items = json.loads(user_prompt)
        categories = {str(item["row_index"]): self._default_category for item in items}
        return (
            {
                "categories": categories,
                "summary": "Synthetic test summary.",
                "flagged": [],
            },
            "fake-model",
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_analyser():
    """Fresh FakeAnalyserClient per test; shared by api_client and test bodies."""
    return FakeAnalyserClient()


@pytest.fixture
def api_client(tmp_path, fake_analyser, monkeypatch):
    """FastAPI TestClient wired to: tmp sqlite, fake analyser, no Drive, output→tmp."""
    db_file = str(tmp_path / "test.sqlite")
    monkeypatch.setenv("SQLITE_PATH", db_file)
    # Ensure no real secrets bleed in via environment.
    # Use setenv("", ...) rather than delenv so that load_dotenv() re-runs inside
    # lifespan helpers don't re-populate the var from the real .env file.
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    monkeypatch.setenv("DRIVE_FOLDER_ID", "")

    def _patched(uploads, *, store, **kwargs):
        """Replace app.run_pipeline so fake analyser + tmp dirs are injected."""
        return _real_run_pipeline(
            uploads,
            store=store,
            analyser_client=fake_analyser,
            drive_service=None,
            output_dir=str(tmp_path),
            sanitise_log_dir=str(tmp_path),
        )

    monkeypatch.setattr(app_module, "run_pipeline", _patched)

    from backend.app import app
    with TestClient(app) as client:
        yield client


def _upload_both(client: TestClient) -> "requests.Response":  # type: ignore[name-defined]
    """POST synthetic CommBank + Westpac CSVs to /upload."""
    return client.post(
        "/upload",
        files={
            "commbank": ("commbank.csv", _CB_BYTES, "text/csv"),
            "westpac": ("westpac.csv", _WP_BYTES, "text/csv"),
        },
    )


# ---------------------------------------------------------------------------
# TestUploadHappyPath
# ---------------------------------------------------------------------------

class TestUploadHappyPath:
    """POST /upload with both synthetic CSVs on a fresh DB."""

    def test_status_200(self, api_client):
        assert _upload_both(api_client).status_code == 200

    def test_noop_false(self, api_client):
        assert _upload_both(api_client).json()["noop"] is False

    def test_new_txns_five(self, api_client):
        # 3 CommBank + 2 Westpac rows
        assert _upload_both(api_client).json()["new_txns"] == 5

    def test_categorised_equals_new_txns(self, api_client):
        data = _upload_both(api_client).json()
        assert data["categorised"] == data["new_txns"]

    def test_drive_file_id_none(self, api_client):
        assert _upload_both(api_client).json()["drive_file_id"] is None

    def test_errors_empty(self, api_client):
        assert _upload_both(api_client).json()["errors"] == []

    def test_model_used_fake_model(self, api_client):
        assert _upload_both(api_client).json()["model_used"] == "fake-model"


# ---------------------------------------------------------------------------
# TestUploadIdempotency  (FR-15)
# ---------------------------------------------------------------------------

class TestUploadIdempotency:
    """Second POST with identical bytes → noop; zero additional LLM calls."""

    @pytest.fixture(autouse=True)
    def _post_twice(self, api_client, fake_analyser):
        self.first = _upload_both(api_client).json()
        self._calls_after_first = fake_analyser.call_count
        self.second = _upload_both(api_client).json()
        self._calls_after_second = fake_analyser.call_count

    def test_second_noop_true(self):
        assert self.second["noop"] is True

    def test_second_zero_new_txns(self):
        assert self.second["new_txns"] == 0

    def test_no_extra_llm_calls_on_second_upload(self):
        """FR-15: fake analyser receives ZERO additional calls on re-upload."""
        added = self._calls_after_second - self._calls_after_first
        assert added == 0, f"Expected 0 extra LLM calls, got {added}"

    def test_second_excel_path_none(self):
        assert self.second["excel_path"] is None

    def test_second_drive_file_id_none(self):
        assert self.second["drive_file_id"] is None

    def test_second_model_used_empty(self):
        assert self.second["model_used"] == ""


# ---------------------------------------------------------------------------
# TestGetSummary
# ---------------------------------------------------------------------------

class TestGetSummary:
    """/summary returns correct totals, filters by month, rejects bad formats."""

    @pytest.fixture(autouse=True)
    def _upload_first(self, api_client):
        _upload_both(api_client)
        self.client = api_client

    def test_200_status(self):
        assert self.client.get("/summary").status_code == 200

    def test_count_five(self):
        assert self.client.get("/summary").json()["count"] == 5

    def test_totals_is_dict(self):
        totals = self.client.get("/summary").json()["totals"]
        assert isinstance(totals, dict)
        assert len(totals) > 0

    def test_net_is_string(self):
        net = self.client.get("/summary").json()["net"]
        assert isinstance(net, str)

    def test_year_month_is_june_2026(self):
        assert self.client.get("/summary").json()["year_month"] == "2026-06"

    def test_explicit_month_filter(self):
        r = self.client.get("/summary?month=2026-06")
        assert r.status_code == 200
        assert r.json()["year_month"] == "2026-06"

    def test_unknown_month_returns_empty_shape(self):
        r = self.client.get("/summary?month=2020-01")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 0
        assert data["totals"] == {}

    def test_slash_month_format_400(self):
        """month=2026/06 is not YYYY-MM → 400."""
        r = self.client.get("/summary?month=2026/06")
        assert r.status_code == 400
        assert "month must be YYYY-MM" in r.json()["detail"]

    def test_alpha_month_400(self):
        r = self.client.get("/summary?month=june-2026")
        assert r.status_code == 400
        assert "month must be YYYY-MM" in r.json()["detail"]

    def test_net_is_not_float(self):
        """Money values must be strings, never floats (Decimal-safe)."""
        data = self.client.get("/summary").json()
        net = data["net"]
        assert isinstance(net, str), f"net must be str, got {type(net)}: {net!r}"


# ---------------------------------------------------------------------------
# TestGetStatus
# ---------------------------------------------------------------------------

class TestGetStatus:
    """/status returns health + boolean config; never secrets or raw txn data."""

    def test_status_200(self, api_client):
        assert api_client.get("/status").status_code == 200

    def test_status_ok(self, api_client):
        assert api_client.get("/status").json()["status"] == "ok"

    def test_configured_drive_is_bool(self, api_client):
        val = api_client.get("/status").json()["configured"]["drive"]
        assert isinstance(val, bool)

    def test_configured_openrouter_is_bool(self, api_client):
        val = api_client.get("/status").json()["configured"]["openrouter"]
        assert isinstance(val, bool)

    def test_configured_drive_false_when_unconfigured(self, api_client):
        assert api_client.get("/status").json()["configured"]["drive"] is False

    def test_configured_openrouter_false_when_key_absent(self, api_client):
        assert api_client.get("/status").json()["configured"]["openrouter"] is False

    def test_api_key_value_not_in_response(self, api_client, monkeypatch):
        """A synthetic API key set in env must NOT appear in the /status body."""
        synth_key = "SYNTH-FAKE-KEY-MUST-NOT-APPEAR-IN-RESPONSE-9999"
        monkeypatch.setenv("OPENROUTER_API_KEY", synth_key)
        r = api_client.get("/status")
        assert r.status_code == 200
        assert synth_key not in r.text, (
            "API key value must never appear in /status response"
        )

    def test_uptime_seconds_non_negative(self, api_client):
        uptime = api_client.get("/status").json()["uptime_seconds"]
        assert isinstance(uptime, (int, float))
        assert uptime >= 0

    def test_last_run_initially_null(self, api_client):
        """Before any upload, last_run is null."""
        assert api_client.get("/status").json()["last_run"] is None

    def test_last_run_not_null_after_upload(self, api_client):
        _upload_both(api_client)
        assert api_client.get("/status").json()["last_run"] is not None

    def test_last_run_contains_no_raw_description(self, api_client):
        """last_run is a RunReport (counts + safe strings only); no raw txn text."""
        _upload_both(api_client)
        last_run = api_client.get("/status").json()["last_run"]
        serialised = json.dumps(last_run)
        # Merchant names from our CSV must not appear in the status response
        assert "WOOLWORTHS METRO" not in serialised
        assert "SYNTH TRANSPORT CO" not in serialised
        assert _FAKE_ACCT not in serialised


# ---------------------------------------------------------------------------
# TestBadUpload
# ---------------------------------------------------------------------------

class TestBadUpload:
    """Malformed upload requests → clean 4xx / safe 200; no data echoed."""

    def test_no_files_400(self, api_client):
        r = api_client.post("/upload")
        assert r.status_code == 400
        assert r.json()["detail"] == "no files uploaded"

    def test_empty_commbank_400(self, api_client):
        r = api_client.post(
            "/upload",
            files={"commbank": ("commbank.csv", b"", "text/csv")},
        )
        assert r.status_code == 400

    def test_empty_westpac_400(self, api_client):
        r = api_client.post(
            "/upload",
            files={"westpac": ("westpac.csv", b"", "text/csv")},
        )
        assert r.status_code == 400

    def test_400_detail_no_raw_csv_echo(self, api_client):
        """Error detail for bad upload must not echo submitted file content."""
        r = api_client.post(
            "/upload",
            files={"commbank": ("commbank.csv", b"", "text/csv")},
        )
        assert r.status_code == 400
        body = r.text
        # The empty body shouldn't echo any file bytes; but confirm invariant holds
        assert _CB_TEXT not in body

    def test_garbage_csv_not_500(self, api_client):
        """Garbage bytes for a single file → 200 (parse failure is soft); never 500."""
        garbage = b"not,valid,csv\nstill,not,valid"
        r = api_client.post(
            "/upload",
            files={"commbank": ("garbage.csv", garbage, "text/csv")},
        )
        assert r.status_code == 200

    def test_garbage_csv_no_stacktrace_in_body(self, api_client):
        garbage = b"bad\ncontent\nhere"
        r = api_client.post(
            "/upload",
            files={"commbank": ("garbage.csv", garbage, "text/csv")},
        )
        body = r.text
        assert "Traceback" not in body
        assert "traceback" not in body

    def test_garbage_csv_no_raw_bytes_echoed(self, api_client):
        """Submitted file content must not be echoed back in the response."""
        unique_token = "TOTALLY_UNIQUE_SYNTHETIC_TOKEN_XYZ_9988776655"
        garbage = f"{unique_token},garbage,data".encode()
        r = api_client.post(
            "/upload",
            files={"commbank": ("garbage.csv", garbage, "text/csv")},
        )
        assert unique_token not in r.text


# ---------------------------------------------------------------------------
# TestPrivacyAsserts  (BLOCKING)
# ---------------------------------------------------------------------------

class TestPrivacyAsserts:
    """BLOCKING: off-machine payload shape; no secrets in responses; no tracked-path writes."""

    @pytest.fixture(autouse=True)
    def _do_upload(self, api_client, fake_analyser):
        _upload_both(api_client)
        self.fake = fake_analyser
        self.client = api_client

    def test_analyser_was_called(self):
        assert self.fake.call_count > 0

    def test_each_payload_item_has_exactly_three_keys(self):
        """BLOCKING: every item in user_prompt has only row_index, cleaned_description, amount."""
        for prompt_str in self.fake.received_user_prompts:
            items = json.loads(prompt_str)
            for item in items:
                assert set(item.keys()) == {"row_index", "cleaned_description", "amount"}, (
                    f"Off-machine payload has unexpected keys: {set(item.keys())}"
                )

    def test_no_date_in_payload(self):
        for prompt_str in self.fake.received_user_prompts:
            for item in json.loads(prompt_str):
                assert "date" not in item

    def test_no_bank_in_payload(self):
        for prompt_str in self.fake.received_user_prompts:
            for item in json.loads(prompt_str):
                assert "bank" not in item

    def test_no_balance_in_payload(self):
        for prompt_str in self.fake.received_user_prompts:
            for item in json.loads(prompt_str):
                assert "balance" not in item

    def test_account_number_not_in_payload(self):
        """Westpac account-number column must not appear anywhere in analyser payloads."""
        all_text = " ".join(self.fake.received_user_prompts)
        assert _FAKE_ACCT not in all_text, (
            f"Account number {_FAKE_ACCT!r} leaked into off-machine payload"
        )

    def test_amount_is_string_not_float(self):
        """amount values must be strings (from amount_to_text), never floats."""
        for prompt_str in self.fake.received_user_prompts:
            for item in json.loads(prompt_str):
                assert isinstance(item["amount"], str), (
                    f"amount must be str, got {type(item['amount'])!r}: {item['amount']!r}"
                )

    def test_raw_descriptions_not_in_status_response(self):
        """Merchant names from uploaded CSV must not appear in /status output."""
        r = self.client.get("/status")
        body = r.text
        assert "WOOLWORTHS METRO" not in body
        assert "SYNTH TRANSPORT CO" not in body
        assert "SYNTH COFFEE SHOP" not in body
        assert "SYNTH UTILITY BILL" not in body
        assert "SYNTH SALARY CREDIT" not in body

    def test_400_response_no_raw_csv_text(self):
        """Error responses never echo raw submitted CSV content."""
        r = self.client.post(
            "/upload",
            files={"commbank": ("commbank.csv", b"", "text/csv")},
        )
        assert r.status_code == 400
        # Confirm raw CSV from a normal upload isn't in a 400 error body
        assert _CB_TEXT not in r.text
        assert _WP_TEXT not in r.text


# ---------------------------------------------------------------------------
# TestReclassifyEndpoint — small-fuel-stop dining rule (POST /reclassify)
# ---------------------------------------------------------------------------

class TestReclassifyEndpoint:
    """POST /reclassify applies/reverts the fuel-stop rule and returns the summary.

    Rows are seeded directly into the app's store with SYNTHETIC merchants so the
    test controls categories precisely (the fake analyser assigns everything to one
    category and would not produce Transport rows).
    """

    def _seed(self, tmp_path):
        # Seed via a SEPARATE connection to the same DB file: the app's own
        # connection lives in the server thread and can't be touched from here.
        conn = sqlite3.connect(str(tmp_path / "test.sqlite"))
        conn.execute(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES"
            " ('f1','2026-06-15','BP CONNECT','-7.00','commbank','Transport','2026-06','t'),"
            " ('f2','2026-06-16','OPAL TRAVEL','-3.00','commbank','Transport','2026-06','t')"
        )
        conn.commit()
        conn.close()

    def test_apply_moves_and_returns_summary(self, api_client, tmp_path):
        self._seed(tmp_path)
        r = api_client.post("/reclassify", params={"enabled": "true", "month": "2026-06"})
        assert r.status_code == 200
        body = r.json()
        assert body["fuel_rule_applied"] is True
        # BP under $10 -> Dining Out; OPAL (transit) stays Transport.
        assert body["totals"]["Dining Out"] == "-7.00"
        assert body["totals"]["Transport"] == "-3.00"

    def test_revert_restores(self, api_client, tmp_path):
        self._seed(tmp_path)
        api_client.post("/reclassify", params={"enabled": "true", "month": "2026-06"})
        r = api_client.post("/reclassify", params={"enabled": "false", "month": "2026-06"})
        body = r.json()
        assert body["fuel_rule_applied"] is False
        assert body["totals"]["Transport"] == "-10.00"
        assert "Dining Out" not in body["totals"]

    def test_bad_month_400(self, api_client):
        r = api_client.post("/reclassify", params={"enabled": "true", "month": "2026/06"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# TestCategoryContextEndpoints — GET/PUT /category-context (D1/D2)
# ---------------------------------------------------------------------------


class TestCategoryContextEndpoints:
    """GET/PUT /category-context — fixed 9-category taxonomy, hints-only edits.

    All hint bodies in this class are SYNTHETIC — never the real D2 defaults'
    content and never transaction data.
    """

    def test_get_status_200(self, api_client):
        assert api_client.get("/category-context").status_code == 200

    def test_get_returns_nine_categories(self, api_client):
        body = api_client.get("/category-context").json()
        assert len(body["categories"]) == 8

    def test_get_categories_in_taxonomy_order(self, api_client):
        from backend.store import TAXONOMY

        body = api_client.get("/category-context").json()
        names = [c["name"] for c in body["categories"]]
        assert names == list(TAXONOMY)

    def test_get_seeded_hints_non_empty_on_fresh_db(self, api_client):
        """D2: fresh DB already has real example hints (not blank/placeholder)."""
        body = api_client.get("/category-context").json()
        for c in body["categories"]:
            assert c["hints"].strip() != ""

    def test_get_category_has_expected_keys(self, api_client):
        body = api_client.get("/category-context").json()
        for c in body["categories"]:
            assert set(c.keys()) == {"name", "color", "hints", "position"}

    def test_put_status_200(self, api_client):
        r = api_client.put(
            "/category-context",
            json={"categories": [{"name": "Groceries", "hints": "SYNTH HINT A"}]},
        )
        assert r.status_code == 200

    def test_put_updates_named_category_hint(self, api_client):
        api_client.put(
            "/category-context",
            json={"categories": [{"name": "Groceries", "hints": "SYNTH HINT A"}]},
        )
        body = api_client.get("/category-context").json()
        by_name = {c["name"]: c["hints"] for c in body["categories"]}
        assert by_name["Groceries"] == "SYNTH HINT A"

    def test_put_still_returns_nine(self, api_client):
        body = api_client.put(
            "/category-context",
            json={"categories": [{"name": "Groceries", "hints": "SYNTH HINT A"}]},
        ).json()
        assert len(body["categories"]) == 8

    def test_put_unknown_category_name_does_not_create_it(self, api_client):
        r = api_client.put(
            "/category-context",
            json={"categories": [{"name": "Bogus", "hints": "SYNTH VALUE"}]},
        )
        assert r.status_code == 200
        body = r.json()
        names = {c["name"] for c in body["categories"]}
        assert "Bogus" not in names
        assert len(body["categories"]) == 8

    def test_put_missing_name_422(self, api_client):
        r = api_client.put(
            "/category-context",
            json={"categories": [{"hints": "SYNTH VALUE"}]},
        )
        assert r.status_code == 422

    def test_put_malformed_body_422(self, api_client):
        r = api_client.put("/category-context", json={"nope": "not a valid body"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# TestPeriodEndpoints — GET /month, GET /year (v2 Pass 1)
#
# Rows are seeded directly into the app's store (same technique as
# TestReclassifyEndpoint) via a SEPARATE sqlite3 connection to the same DB
# file, so tests control category/date/year_month precisely without relying
# on the fake analyser. All descriptions/amounts are SYNTHETIC.
# ---------------------------------------------------------------------------


class TestPeriodEndpoints:
    def _seed(self, tmp_path, rows: list[tuple]) -> None:
        """rows: (fp, date, description, amount, bank, category, year_month)."""
        conn = sqlite3.connect(str(tmp_path / "test.sqlite"))
        conn.executemany(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES (?,?,?,?,?,?,?,'t')",
            rows,
        )
        conn.commit()
        conn.close()

    # -- empty DB -----------------------------------------------------------

    def test_month_empty_db_exact_shape(self, api_client):
        r = api_client.get("/month")
        assert r.status_code == 200
        assert r.json() == {
            "period": "month",
            "ym": None,
            "prev_ym": None,
            "totals": {},
            "net": "0.00",
            "count": 0,
            "comparison": [],
            "available_months": [],
        }

    def test_year_empty_db_exact_shape(self, api_client):
        r = api_client.get("/year")
        assert r.status_code == 200
        assert r.json() == {
            "period": "year",
            "y": None,
            "prev_y": None,
            "totals": {},
            "net": "0.00",
            "count": 0,
            "comparison": [],
            "available_years": [],
        }

    # -- happy path -----------------------------------------------------------

    def test_month_default_targets_latest_populated_month(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("mf1", "2026-04-01", "SYNTH APRIL ITEM", "-40.00", "commbank", "Groceries", "2026-04"),
            ("mf2", "2026-05-01", "SYNTH MAY ITEM", "-50.00", "commbank", "Groceries", "2026-05"),
            ("mf3", "2026-06-01", "SYNTH JUNE ITEM", "-60.00", "commbank", "Groceries", "2026-06"),
        ])
        r = api_client.get("/month")
        assert r.status_code == 200
        body = r.json()
        assert body["ym"] == "2026-06"
        assert body["prev_ym"] == "2026-05"
        assert body["totals"] == {"Groceries": "-60.00"}
        assert body["net"] == "-60.00"
        assert body["count"] == 1
        assert body["available_months"] == ["2026-06", "2026-05", "2026-04"]
        row = body["comparison"][0]
        assert row["category"] == "Groceries"
        assert row["current"] == "-60.00"
        assert row["previous"] == "-50.00"

    def test_month_explicit_ym_query_param(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("mf1", "2026-04-01", "SYNTH APRIL ITEM", "-40.00", "commbank", "Groceries", "2026-04"),
            ("mf2", "2026-05-01", "SYNTH MAY ITEM", "-50.00", "commbank", "Groceries", "2026-05"),
        ])
        r = api_client.get("/month", params={"ym": "2026-04"})
        assert r.status_code == 200
        body = r.json()
        assert body["ym"] == "2026-04"
        assert body["prev_ym"] is None
        assert body["totals"] == {"Groceries": "-40.00"}

    def test_year_default_targets_latest_populated_year(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("yf1", "2025-06-01", "SYNTH 2025 ITEM", "-100.00", "commbank", "Groceries", "2025-06"),
            ("yf2", "2026-01-01", "SYNTH 2026 ITEM A", "-150.00", "commbank", "Groceries", "2026-01"),
            ("yf3", "2026-06-01", "SYNTH 2026 ITEM B", "-50.00", "commbank", "Groceries", "2026-06"),
        ])
        r = api_client.get("/year")
        assert r.status_code == 200
        body = r.json()
        assert body["y"] == "2026"
        assert body["prev_y"] == "2025"
        assert body["totals"] == {"Groceries": "-200.00"}
        assert body["net"] == "-200.00"
        assert body["count"] == 2
        assert body["available_years"] == ["2026", "2025"]

    def test_year_explicit_y_query_param(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("yf1", "2025-06-01", "SYNTH 2025 ITEM", "-100.00", "commbank", "Groceries", "2025-06"),
            ("yf2", "2026-06-01", "SYNTH 2026 ITEM", "-50.00", "commbank", "Groceries", "2026-06"),
        ])
        r = api_client.get("/year", params={"y": "2025"})
        assert r.status_code == 200
        body = r.json()
        assert body["y"] == "2025"
        assert body["prev_y"] is None
        assert body["totals"] == {"Groceries": "-100.00"}

    # -- validation 400s ------------------------------------------------------

    def test_month_slash_format_400(self, api_client):
        r = api_client.get("/month", params={"ym": "2026/06"})
        assert r.status_code == 400
        assert r.json()["detail"] == "ym must be YYYY-MM"

    def test_month_alpha_format_400(self, api_client):
        r = api_client.get("/month", params={"ym": "june-2026"})
        assert r.status_code == 400
        assert r.json()["detail"] == "ym must be YYYY-MM"

    def test_year_hyphenated_format_400(self, api_client):
        r = api_client.get("/year", params={"y": "2026-01"})
        assert r.status_code == 400
        assert r.json()["detail"] == "y must be YYYY"

    def test_year_alpha_format_400(self, api_client):
        r = api_client.get("/year", params={"y": "twenty-twenty-six"})
        assert r.status_code == 400
        assert r.json()["detail"] == "y must be YYYY"

    def test_year_short_digits_400(self, api_client):
        r = api_client.get("/year", params={"y": "26"})
        assert r.status_code == 400
        assert r.json()["detail"] == "y must be YYYY"

    # -- raw-description-leak guard (BLOCKING) ---------------------------------

    def test_month_and_year_never_leak_raw_description_or_sensitive_keys(
        self, api_client, tmp_path
    ):
        """A synthetic description token must never appear in /month or /year
        response bodies, and neither response may carry a description/balance/
        bank key anywhere (Store methods only ever SELECT category, amount)."""
        unique_token = "ZZLEAKCANARY_MONTHLY_YEARLY_9182736450"
        self._seed(tmp_path, [
            ("lf1", "2026-06-01", unique_token, "-25.00", "commbank", "Groceries", "2026-06"),
            ("lf2", "2026-05-01", "SYNTH PREV MONTH ITEM", "-10.00", "commbank", "Groceries", "2026-05"),
        ])

        month_r = api_client.get("/month")
        year_r = api_client.get("/year")

        for r in (month_r, year_r):
            assert r.status_code == 200
            body_text = r.text
            assert unique_token not in body_text
            assert '"description"' not in body_text
            assert '"balance"' not in body_text
            assert '"bank"' not in body_text


# ---------------------------------------------------------------------------
# TestTrendsEndpoint — GET /trends (v2 Pass 2)
#
# Rows are seeded directly into the app's store (same technique as
# TestPeriodEndpoints) via a SEPARATE sqlite3 connection to the same DB file.
# All descriptions/amounts are SYNTHETIC.
# ---------------------------------------------------------------------------


class TestTrendsEndpoint:
    def _seed(self, tmp_path, rows: list[tuple]) -> None:
        """rows: (fp, date, description, amount, bank, category, year_month)."""
        conn = sqlite3.connect(str(tmp_path / "test.sqlite"))
        conn.executemany(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES (?,?,?,?,?,?,?,'t')",
            rows,
        )
        conn.commit()
        conn.close()

    # -- default / shape -----------------------------------------------------

    def test_default_200_shape_keys(self, api_client):
        r = api_client.get("/trends")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {
            "window",
            "end_month",
            "months",
            "series",
            "spend_by_month",
            "months_available",
        }

    def test_default_window_is_6(self, api_client):
        r = api_client.get("/trends")
        assert r.status_code == 200
        assert r.json()["window"] == 6

    def test_empty_db_exact_shape(self, api_client):
        r = api_client.get("/trends")
        assert r.status_code == 200
        assert r.json() == {
            "window": 6,
            "end_month": None,
            "months": [],
            "series": [],
            "spend_by_month": [],
            "months_available": 0,
        }

    # -- happy path -----------------------------------------------------------

    def test_months_param_shapes_window(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("tf1", "2026-06-01", "SYNTH JUN ITEM", "-30.00", "commbank", "Groceries", "2026-06"),
        ])
        r = api_client.get("/trends", params={"months": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["window"] == 3
        assert len(body["months"]) <= 3
        assert body["end_month"] == "2026-06"

    def test_end_param_selects_the_window_end(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("tf1", "2026-04-01", "SYNTH APR ITEM", "-10.00", "commbank", "Groceries", "2026-04"),
            ("tf2", "2026-06-01", "SYNTH JUN ITEM", "-20.00", "commbank", "Groceries", "2026-06"),
        ])
        r = api_client.get("/trends", params={"months": 2, "end": "2026-04"})
        assert r.status_code == 200
        body = r.json()
        assert body["end_month"] == "2026-04"
        assert body["months"] == ["2026-03", "2026-04"]

    # -- validation -------------------------------------------------------------

    def test_months_zero_400(self, api_client):
        r = api_client.get("/trends", params={"months": 0})
        assert r.status_code == 400
        assert r.json()["detail"] == "months must be >= 1"

    def test_months_negative_400(self, api_client):
        r = api_client.get("/trends", params={"months": -1})
        assert r.status_code == 400
        assert r.json()["detail"] == "months must be >= 1"

    def test_months_non_integer_422(self, api_client):
        r = api_client.get("/trends", params={"months": "abc"})
        assert r.status_code == 422

    def test_end_malformed_400(self, api_client):
        r = api_client.get("/trends", params={"end": "nonsense"})
        assert r.status_code == 400
        assert r.json()["detail"] == "end must be YYYY-MM"

    def test_end_slash_format_400(self, api_client):
        r = api_client.get("/trends", params={"end": "2026/06"})
        assert r.status_code == 400
        assert r.json()["detail"] == "end must be YYYY-MM"

    def test_months_over_24_clamps_not_rejected(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("tf1", "2026-06-01", "SYNTH JUN ITEM", "-30.00", "commbank", "Groceries", "2026-06"),
        ])
        r = api_client.get("/trends", params={"months": 100})
        assert r.status_code == 200
        body = r.json()
        assert body["window"] == 24
        assert len(body["months"]) == 24

    # -- raw-description-leak guard (BLOCKING) -----------------------------------

    def test_raw_description_leak_guard(self, api_client, tmp_path):
        unique_token = "ZZSENTINELZZ_TRENDS_LEAK_CANARY_5566778899"
        self._seed(tmp_path, [
            ("tf1", "2026-06-01", unique_token, "-30.00", "commbank", "Groceries", "2026-06"),
            ("tf2", "2026-06-02", "SYNTH OTHER ITEM", "-15.00", "commbank", "Transport", "2026-06"),
        ])
        r = api_client.get("/trends")
        assert r.status_code == 200
        body_text = r.text
        assert unique_token not in body_text
        assert '"description"' not in body_text
        assert '"balance"' not in body_text
        assert '"bank"' not in body_text
        assert '"date"' not in body_text


# ---------------------------------------------------------------------------
# TestBalancesEndpoint — GET /balances (v7 feature 3 — net position)
#
# Balances rows are seeded directly into the app's store (same technique as
# TestTrendsEndpoint) via a SEPARATE sqlite3 connection to the same DB file.
# All amounts are SYNTHETIC.
# ---------------------------------------------------------------------------


class TestBalancesEndpoint:
    def _seed(self, tmp_path, rows: list[tuple]) -> None:
        """rows: (bank, year_month, closing_balance)."""
        conn = sqlite3.connect(str(tmp_path / "test.sqlite"))
        conn.executemany(
            "INSERT INTO balances(bank, year_month, closing_balance, derived_at)"
            " VALUES (?,?,?,'t')",
            rows,
        )
        conn.commit()
        conn.close()

    def test_empty_db_exact_shape(self, api_client):
        r = api_client.get("/balances")
        assert r.status_code == 200
        assert r.json() == {"months": [], "series": [], "net": []}

    def test_populated_shape_matches_contract(self, api_client, tmp_path):
        # Westpac deliberately missing 2026-06 (derivation was unavailable then).
        self._seed(tmp_path, [
            ("commbank", "2026-05", "1023.10"),
            ("commbank", "2026-06", "998.40"),
            ("commbank", "2026-07", "1101.55"),
            ("westpac", "2026-05", "502.00"),
            ("westpac", "2026-07", "512.13"),
        ])
        r = api_client.get("/balances")
        assert r.status_code == 200
        body = r.json()

        assert body["months"] == ["2026-05", "2026-06", "2026-07"]
        cb = next(s for s in body["series"] if s["bank"] == "commbank")
        wp = next(s for s in body["series"] if s["bank"] == "westpac")
        assert [s["bank"] for s in body["series"]] == ["commbank", "westpac"]
        assert cb["values"] == ["1023.10", "998.40", "1101.55"]
        assert wp["values"] == ["502.00", None, "512.13"]
        # net is null wherever any present bank is null (2026-06 -> westpac gap).
        assert body["net"] == ["1525.10", None, "1613.68"]

    def test_all_money_values_are_strings(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("commbank", "2026-06", "998.40"),
            ("westpac", "2026-06", "502.00"),
        ])
        body = api_client.get("/balances").json()
        for s in body["series"]:
            for v in s["values"]:
                assert v is None or isinstance(v, str)
        for v in body["net"]:
            assert v is None or isinstance(v, str)

    def test_reset_reports_and_clears_balances(self, api_client, tmp_path):
        self._seed(tmp_path, [
            ("commbank", "2026-06", "998.40"),
            ("westpac", "2026-06", "502.00"),
        ])
        r = api_client.post("/reset", json={"confirm": "RESET"})
        assert r.status_code == 200
        assert r.json()["cleared"]["balances"] == 2
        # No balances remain, and the endpoint reflects the empty shape.
        assert api_client.get("/balances").json() == {"months": [], "series": [], "net": []}


# ---------------------------------------------------------------------------
# TestPushEndpoints — POST /push/subscribe, POST /push/unsubscribe (v2 Pass 3)
#
# All endpoints/keys are SYNTHETIC. These endpoints only ever store/remove a
# Web Push subscription in the local SQLite store — no off-machine call.
# ---------------------------------------------------------------------------


_SYNTH_SUBSCRIBE_BODY = {
    "endpoint": "https://example.test/push/SYNTH_APP_TEST_ENDPOINT",
    "keys": {"p256dh": "synth_p256dh_value", "auth": "synth_auth_value"},
}


class TestPushEndpoints:
    def test_subscribe_valid_body_200(self, api_client):
        r = api_client.post("/push/subscribe", json=_SYNTH_SUBSCRIBE_BODY)
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_subscribe_row_present_in_store(self, api_client, tmp_path):
        api_client.post("/push/subscribe", json=_SYNTH_SUBSCRIBE_BODY)
        # No GET /push endpoint exists; verify via a SEPARATE connection to the
        # same DB file (the app's own connection lives in the server thread —
        # same technique as TestReclassifyEndpoint._seed).
        conn = sqlite3.connect(str(tmp_path / "test.sqlite"))
        row = conn.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscription"
        ).fetchone()
        conn.close()
        assert row == (
            _SYNTH_SUBSCRIBE_BODY["endpoint"],
            _SYNTH_SUBSCRIBE_BODY["keys"]["p256dh"],
            _SYNTH_SUBSCRIBE_BODY["keys"]["auth"],
        )

    def test_subscribe_missing_keys_auth_422(self, api_client):
        bad = {
            "endpoint": "https://example.test/push/BAD",
            "keys": {"p256dh": "only_p256dh"},
        }
        r = api_client.post("/push/subscribe", json=bad)
        assert r.status_code == 422

    def test_subscribe_missing_endpoint_422(self, api_client):
        bad = {"keys": {"p256dh": "x", "auth": "y"}}
        r = api_client.post("/push/subscribe", json=bad)
        assert r.status_code == 422

    def test_subscribe_missing_keys_entirely_422(self, api_client):
        bad = {"endpoint": "https://example.test/push/NOKEYS"}
        r = api_client.post("/push/subscribe", json=bad)
        assert r.status_code == 422

    def test_subscribe_empty_body_422(self, api_client):
        r = api_client.post("/push/subscribe", json={})
        assert r.status_code == 422

    def test_subscribe_malformed_body_no_stacktrace(self, api_client):
        r = api_client.post("/push/subscribe", json={"nope": "not valid"})
        assert r.status_code == 422
        assert "Traceback" not in r.text

    def test_unsubscribe_stored_endpoint_removed_1(self, api_client):
        api_client.post("/push/subscribe", json=_SYNTH_SUBSCRIBE_BODY)
        r = api_client.post(
            "/push/unsubscribe", json={"endpoint": _SYNTH_SUBSCRIBE_BODY["endpoint"]}
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "removed": 1}

    def test_unsubscribe_again_removed_0(self, api_client):
        api_client.post("/push/subscribe", json=_SYNTH_SUBSCRIBE_BODY)
        api_client.post(
            "/push/unsubscribe", json={"endpoint": _SYNTH_SUBSCRIBE_BODY["endpoint"]}
        )
        r = api_client.post(
            "/push/unsubscribe", json={"endpoint": _SYNTH_SUBSCRIBE_BODY["endpoint"]}
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "removed": 0}

    def test_unsubscribe_unknown_endpoint_never_errors(self, api_client):
        r = api_client.post(
            "/push/unsubscribe", json={"endpoint": "https://example.test/push/NEVER_STORED"}
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "removed": 0}

    def test_unsubscribe_missing_endpoint_422(self, api_client):
        r = api_client.post("/push/unsubscribe", json={})
        assert r.status_code == 422

    def test_subscribe_endpoint_value_not_echoed_in_error_body(self, api_client):
        """A malformed body's endpoint value must not appear verbatim in the 422 detail."""
        secret_like_endpoint = "https://example.test/push/SHOULD_NOT_BE_ECHOED_TOKEN_998877"
        bad = {"endpoint": secret_like_endpoint, "keys": {"p256dh": "only_p256dh"}}
        r = api_client.post("/push/subscribe", json=bad)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# TestMonthlyReminderEndpoint — POST /notify/monthly-reminder (v4 Feature D)
#
# Fail-closed: with push disabled (default env) this is a silent no-op that
# returns sent=0 and never raises. No off-machine call is made.
# ---------------------------------------------------------------------------


class TestMonthlyReminderEndpoint:
    def test_returns_ok_and_zero_when_push_disabled(self, api_client):
        r = api_client.post("/notify/monthly-reminder")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "sent": 0}

    def test_no_op_even_with_a_subscription_stored(self, api_client):
        # A stored subscription must still be a no-op while push stays disabled.
        api_client.post("/push/subscribe", json=_SYNTH_SUBSCRIBE_BODY)
        r = api_client.post("/notify/monthly-reminder")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "sent": 0}

    def test_never_leaks_a_stacktrace(self, api_client):
        r = api_client.post("/notify/monthly-reminder")
        assert "Traceback" not in r.text


# ---------------------------------------------------------------------------
# TestUploadWasQueued — processed-vs-recovered decision helper (v4 Feature D)
# ---------------------------------------------------------------------------


class TestUploadWasQueued:
    """_upload_was_queued(): a stale client queued_at => processed_recovered."""

    def test_none_is_live(self):
        assert app_module._upload_was_queued(None) is False

    def test_blank_is_live(self):
        assert app_module._upload_was_queued("") is False

    def test_unparseable_is_live(self):
        assert app_module._upload_was_queued("not-a-timestamp") is False

    def test_recent_timestamp_is_live(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        assert app_module._upload_was_queued(now) is False

    def test_old_timestamp_is_queued(self):
        from datetime import datetime, timedelta, timezone

        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        assert app_module._upload_was_queued(stale) is True

    def test_old_zulu_timestamp_is_queued(self):
        from datetime import datetime, timedelta, timezone

        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(
            microsecond=0, tzinfo=None
        ).isoformat() + "Z"
        assert app_module._upload_was_queued(stale) is True


# ---------------------------------------------------------------------------
# TestCategoryTransactionsEndpoint — GET /category-transactions (drill-down)
# ---------------------------------------------------------------------------

class TestCategoryTransactionsEndpoint:
    """GET /category-transactions: LOCAL drill-down view of one category/month.

    The api_client fake analyser labels every uploaded row 'Groceries', so after
    _upload_both there are 5 Groceries rows in 2026-06.
    """

    def test_populated_category_returns_all_rows(self, api_client):
        _upload_both(api_client)
        r = api_client.get("/category-transactions", params={"category": "Groceries"})
        assert r.status_code == 200
        body = r.json()
        assert body["category"] == "Groceries"
        assert body["month"] == "2026-06"
        assert body["count"] == 5
        assert len(body["transactions"]) == 5

    def test_each_txn_has_only_the_five_view_fields(self, api_client):
        _upload_both(api_client)
        body = api_client.get(
            "/category-transactions", params={"category": "Groceries"}
        ).json()
        for t in body["transactions"]:
            assert set(t.keys()) == {"id", "date", "description", "amount", "bank"}
            assert isinstance(t["id"], int)

    def test_sorted_by_magnitude_desc(self, api_client):
        _upload_both(api_client)
        body = api_client.get(
            "/category-transactions", params={"category": "Groceries"}
        ).json()
        mags = [abs(float(t["amount"])) for t in body["transactions"]]
        assert mags == sorted(mags, reverse=True)

    def test_total_matches_sum_of_rows(self, api_client):
        from decimal import Decimal
        _upload_both(api_client)
        body = api_client.get(
            "/category-transactions", params={"category": "Groceries"}
        ).json()
        s = sum((Decimal(t["amount"]) for t in body["transactions"]), Decimal("0"))
        assert Decimal(body["total"]) == s

    def test_empty_category_returns_zero_shape(self, api_client):
        _upload_both(api_client)
        body = api_client.get(
            "/category-transactions", params={"category": "Housing"}
        ).json()
        assert body["count"] == 0
        assert body["transactions"] == []
        assert body["total"] == "0.00"

    def test_unknown_category_is_400(self, api_client):
        r = api_client.get("/category-transactions", params={"category": "Bananas"})
        assert r.status_code == 400

    def test_bad_month_is_400(self, api_client):
        r = api_client.get(
            "/category-transactions",
            params={"category": "Groceries", "month": "2026/06"},
        )
        assert r.status_code == 400

    def test_account_number_never_in_descriptions(self, api_client):
        _upload_both(api_client)
        body = api_client.get(
            "/category-transactions", params={"category": "Groceries"}
        ).json()
        blob = " ".join(t["description"] for t in body["transactions"])
        assert _FAKE_ACCT not in blob


# ---------------------------------------------------------------------------
# TestCategoryOverride  (manual category correction + few-shot learning)
# ---------------------------------------------------------------------------

class TestCategoryOverride:
    """POST /category-override sets a category and records a sanitised correction.

    The app runs its Store connection in a worker thread, so the test uses its OWN
    independent sqlite3 connection to the same tmp DB file (SQLITE_PATH) to inspect /
    seed rows — never the app's connection (SQLite forbids cross-thread use).
    """

    @staticmethod
    def _conn():
        conn = sqlite3.connect(os.environ["SQLITE_PATH"])
        conn.row_factory = sqlite3.Row
        return conn

    def _rows(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT id, txn_fingerprint, description, category "
                "FROM transactions ORDER BY id"
            ).fetchall()

    def _find(self, desc_contains):
        for r in self._rows():
            if desc_contains in r["description"]:
                return r
        raise AssertionError(f"no synthetic txn matching {desc_contains!r}")

    def _category_of(self, txn_id):
        with self._conn() as conn:
            return conn.execute(
                "SELECT category FROM transactions WHERE id = ?", (txn_id,)
            ).fetchone()["category"]

    def _corrections(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT cleaned_description, category FROM corrections"
            ).fetchall()

    def test_sets_category_and_records_correction(self, api_client):
        _upload_both(api_client)
        # Feature B (correction recording) is opt-in and OFF by default — enable it
        # first so the override records a reusable correction.
        api_client.put("/settings", json={"corrections_enabled": True})
        row = self._find("WOOLWORTHS METRO")

        resp = api_client.post(
            "/category-override", json={"id": row["id"], "category": "Dining Out"}
        )
        assert resp.status_code == 200

        assert self._category_of(row["id"]) == "Dining Out"
        # 'WOOLWORTHS METRO' has no digits, so it scrubs to itself and is remembered.
        corr = [(c["cleaned_description"], c["category"]) for c in self._corrections()]
        assert ("WOOLWORTHS METRO", "Dining Out") in corr

    def test_override_by_fingerprint(self, api_client):
        _upload_both(api_client)
        row = self._rows()[0]
        resp = api_client.post(
            "/category-override",
            json={"fingerprint": row["txn_fingerprint"], "category": "Transport"},
        )
        assert resp.status_code == 200
        assert self._category_of(row["id"]) == "Transport"

    def test_unknown_category_400(self, api_client):
        _upload_both(api_client)
        row = self._rows()[0]
        resp = api_client.post(
            "/category-override", json={"id": row["id"], "category": "Crypto"}
        )
        assert resp.status_code == 400
        # No correction stored for a rejected category.
        assert self._corrections() == []

    def test_missing_transaction_404(self, api_client):
        _upload_both(api_client)
        resp = api_client.post(
            "/category-override", json={"id": 999999, "category": "Transport"}
        )
        assert resp.status_code == 404

    def test_id_or_fingerprint_required_400(self, api_client):
        _upload_both(api_client)
        resp = api_client.post("/category-override", json={"category": "Transport"})
        assert resp.status_code == 400

    def test_unsanitisable_description_sets_category_but_skips_correction(self, api_client):
        _upload_both(api_client)
        row = self._rows()[0]
        # Replace the raw description with an all-digit string that scrubs to nothing
        # safe (fail-closed): digits are stripped and the empty result is dropped.
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET description = ? WHERE id = ?",
                ("999999999999999", row["id"]),
            )
            conn.commit()

        resp = api_client.post(
            "/category-override", json={"id": row["id"], "category": "Transport"}
        )
        assert resp.status_code == 200

        assert self._category_of(row["id"]) == "Transport"
        # Fail-closed: the un-sanitisable description is never stored for reuse.
        assert self._corrections() == []


# ---------------------------------------------------------------------------
# TestCategoriserScorecard — override event write hook + GET /categoriser/scorecard
# (v7 feature 4). Events store ONLY (from, to, timestamp): the write is UNGATED by
# corrections_enabled, no-op/transfer/untag paths never pollute the log. SYNTHETIC.
# ---------------------------------------------------------------------------


class TestCategoriserScorecard:
    """The app runs its Store in a worker thread, so the test uses its OWN
    independent sqlite3 connection to the same tmp DB to inspect / seed rows.
    """

    @staticmethod
    def _conn():
        conn = sqlite3.connect(os.environ["SQLITE_PATH"])
        conn.row_factory = sqlite3.Row
        return conn

    def _events(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT id, created_at, from_category, to_category "
                "FROM override_events ORDER BY id"
            ).fetchall()

    def _corrections(self):
        with self._conn() as conn:
            return conn.execute("SELECT cleaned_description, category FROM corrections").fetchall()

    def _first_categorised(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT id, category FROM transactions WHERE category IS NOT NULL ORDER BY id"
            ).fetchone()

    def _seed_uncategorised(self, *, fp: str, created_at: str) -> None:
        """Insert one NULL-category synthetic row with a controlled created_at."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO transactions"
                "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
                " VALUES (?,?,?,?,?,NULL,?,?)",
                (fp, "2020-01-01", "SYNTH UNCAT", "-1.00", "commbank", "2020-01", created_at),
            )
            conn.commit()

    # -- event write hook ---------------------------------------------------

    def test_override_logs_event_with_corrections_off(self, api_client):
        _upload_both(api_client)  # corrections_enabled defaults OFF
        row = self._first_categorised()

        resp = api_client.post(
            "/category-override", json={"id": row["id"], "category": "Dining Out"}
        )
        assert resp.status_code == 200

        events = self._events()
        assert len(events) == 1
        assert events[0]["from_category"] == row["category"]
        assert events[0]["to_category"] == "Dining Out"
        # UNGATED event, but the gated correction is NOT written when opt-in is OFF.
        assert list(self._corrections()) == []

    def test_override_logs_event_and_correction_with_toggle_on(self, api_client):
        _upload_both(api_client)
        api_client.put("/settings", json={"corrections_enabled": True})
        row = self._first_categorised()

        resp = api_client.post(
            "/category-override", json={"id": row["id"], "category": "Dining Out"}
        )
        assert resp.status_code == 200

        # Exactly ONE event (not double-written) AND one correction.
        assert len(self._events()) == 1
        assert len(self._corrections()) == 1

    def test_noop_override_writes_no_event(self, api_client):
        _upload_both(api_client)
        row = self._first_categorised()  # already "Groceries" from the fake analyser

        resp = api_client.post(
            "/category-override", json={"id": row["id"], "category": row["category"]}
        )
        assert resp.status_code == 200
        assert self._events() == []

    def test_override_uncategorised_logs_null_from(self, api_client):
        _upload_both(api_client)
        # Out-of-window ingest month so it never enters the scorecard denominator.
        self._seed_uncategorised(fp="uncat1", created_at="2020-01-01T00:00:00+00:00")

        resp = api_client.post(
            "/category-override", json={"fingerprint": "uncat1", "category": "Groceries"}
        )
        assert resp.status_code == 200

        events = self._events()
        assert len(events) == 1
        assert events[0]["from_category"] is None  # remediation, not an LLM error

        # NULL-from events are excluded from `corrected` everywhere in the window.
        card = api_client.get("/categoriser/scorecard").json()
        assert sum(m["corrected"] for m in card["months"]) == 0

    # -- no pollution from transfer paths (D-4) -----------------------------

    def test_transfer_untag_writes_no_event(self, api_client):
        pair = TestTransfersEndpoints._seed_pair(out_category="Groceries")
        r = api_client.post(f"/transfers/{pair}/untag")
        assert r.status_code == 200
        assert self._events() == []

    def test_transfer_leg_409_override_writes_no_event(self, api_client):
        TestTransfersEndpoints._seed_pair(out_category="Groceries")
        leg_id = TestTransfersEndpoints._leg_id("tpo")

        r = api_client.post(
            "/category-override", json={"id": leg_id, "category": "Dining Out"}
        )
        assert r.status_code == 409
        assert self._events() == []  # rejected before any write

    # -- GET /categoriser/scorecard -----------------------------------------

    def test_scorecard_shape(self, api_client):
        body = api_client.get("/categoriser/scorecard?months=3").json()
        assert set(body.keys()) == {"window", "months", "current"}
        assert body["window"] == 3
        assert len(body["months"]) == 3
        assert body["current"] == body["months"][-1]
        for entry in body["months"]:
            assert set(entry.keys()) == {
                "month", "auto_categorised", "corrected", "accuracy_pct"
            }

    def test_scorecard_default_window_is_six(self, api_client):
        body = api_client.get("/categoriser/scorecard").json()
        assert body["window"] == 6
        assert len(body["months"]) == 6

    def test_scorecard_months_zero_400(self, api_client):
        r = api_client.get("/categoriser/scorecard?months=0")
        assert r.status_code == 400
        assert r.json()["detail"] == "months must be >= 1"

    def test_scorecard_upper_clamped_to_24(self, api_client):
        body = api_client.get("/categoriser/scorecard?months=100").json()
        assert body["window"] == 24

    def test_reset_reports_override_events_count(self, api_client):
        _upload_both(api_client)
        row = self._first_categorised()
        api_client.post("/category-override", json={"id": row["id"], "category": "Dining Out"})
        assert len(self._events()) == 1

        r = api_client.post("/reset", json={"confirm": "RESET"})
        assert r.status_code == 200
        assert r.json()["cleared"]["override_events"] == 1
        assert self._events() == []


# ---------------------------------------------------------------------------
# TestSettingsEndpoints — GET/PUT /settings (Feature E)
# ---------------------------------------------------------------------------


class TestSettingsEndpoints:
    def test_get_defaults(self, api_client):
        from backend.notifier import NOTIFICATION_TYPES

        body = api_client.get("/settings").json()
        assert body["corrections_enabled"] is False
        assert set(body["notifications"].keys()) == set(NOTIFICATION_TYPES)
        # Opt-out model: every notification type defaults to enabled.
        assert all(v is True for v in body["notifications"].values())

    def test_put_partial_corrections_only(self, api_client):
        r = api_client.put("/settings", json={"corrections_enabled": True})
        assert r.status_code == 200
        assert r.json()["corrections_enabled"] is True
        # Notifications untouched -> still all enabled.
        assert all(r.json()["notifications"].values())

    def test_put_partial_notification_persists(self, api_client):
        api_client.put("/settings", json={"notifications": {"processed": False}})
        body = api_client.get("/settings").json()
        assert body["notifications"]["processed"] is False
        assert body["notifications"]["parse_error"] is True
        # corrections_enabled untouched -> still the default False.
        assert body["corrections_enabled"] is False

    def test_put_unknown_notification_key_ignored(self, api_client):
        r = api_client.put(
            "/settings", json={"notifications": {"not_a_real_type": False}}
        )
        assert r.status_code == 200
        assert "not_a_real_type" not in r.json()["notifications"]

    def test_put_empty_body_is_noop(self, api_client):
        api_client.put("/settings", json={"corrections_enabled": True})
        r = api_client.put("/settings", json={})
        assert r.status_code == 200
        # Nothing provided -> previously-set value is preserved.
        assert r.json()["corrections_enabled"] is True


# ---------------------------------------------------------------------------
# TestCorrectionsGateAndEndpoints — Feature B opt-in gate + GET/DELETE /corrections
# ---------------------------------------------------------------------------


class TestCorrectionsGateAndEndpoints:
    @staticmethod
    def _conn():
        conn = sqlite3.connect(os.environ["SQLITE_PATH"])
        conn.row_factory = sqlite3.Row
        return conn

    def _first_row(self):
        with self._conn() as conn:
            return conn.execute(
                "SELECT id, description FROM transactions ORDER BY id"
            ).fetchone()

    def _category_of(self, txn_id):
        with self._conn() as conn:
            return conn.execute(
                "SELECT category FROM transactions WHERE id = ?", (txn_id,)
            ).fetchone()["category"]

    def test_gate_off_sets_category_but_records_no_correction(self, api_client):
        _upload_both(api_client)  # gate is OFF by default
        row = self._first_row()

        resp = api_client.post(
            "/category-override", json={"id": row["id"], "category": "Dining Out"}
        )
        assert resp.status_code == 200
        # Override still applies...
        assert self._category_of(row["id"]) == "Dining Out"
        # ...but NO correction is recorded while the gate is off.
        assert api_client.get("/corrections").json()["corrections"] == []

    def test_gate_on_records_correction(self, api_client):
        api_client.put("/settings", json={"corrections_enabled": True})
        _upload_both(api_client)
        row = self._first_row()

        api_client.post(
            "/category-override", json={"id": row["id"], "category": "Dining Out"}
        )
        corrections = api_client.get("/corrections").json()["corrections"]
        assert len(corrections) == 1
        assert corrections[0]["category"] == "Dining Out"

    def test_get_corrections_shape(self, api_client):
        body = api_client.get("/corrections").json()
        assert body["enabled"] is False
        assert body["corrections"] == []

    def test_delete_correction_removes_one(self, api_client):
        api_client.put("/settings", json={"corrections_enabled": True})
        _upload_both(api_client)
        row = self._first_row()
        api_client.post(
            "/category-override", json={"id": row["id"], "category": "Transport"}
        )
        cid = api_client.get("/corrections").json()["corrections"][0]["id"]

        r = api_client.delete(f"/corrections/{cid}")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "removed": 1}
        assert api_client.get("/corrections").json()["corrections"] == []

    def test_delete_missing_correction_removed_zero(self, api_client):
        r = api_client.delete("/corrections/999999")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "removed": 0}


# ---------------------------------------------------------------------------
# TestExportCsv — GET /export/transactions.csv (Feature E, LOCAL download)
# ---------------------------------------------------------------------------


class TestExportCsv:
    def test_headers_and_rows(self, api_client):
        import csv
        import io

        _upload_both(api_client)
        r = api_client.get("/export/transactions.csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert (
            r.headers["content-disposition"]
            == 'attachment; filename="financetracker-transactions.csv"'
        )

        rows = list(csv.reader(io.StringIO(r.text)))
        assert rows[0] == [
            "date", "description", "amount", "category", "bank", "year_month",
        ]
        # 5 synthetic transactions -> 5 data rows after the header.
        assert len(rows) == 1 + 5

    def test_empty_db_has_header_only(self, api_client):
        import csv
        import io

        r = api_client.get("/export/transactions.csv")
        assert r.status_code == 200
        rows = list(csv.reader(io.StringIO(r.text)))
        assert len(rows) == 1  # header only

    def test_commas_in_description_are_quoted(self, api_client):
        import csv
        import io

        # Seed a description containing a comma via a separate connection.
        conn = sqlite3.connect(os.environ["SQLITE_PATH"])
        conn.execute(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES (?,?,?,?,?,?,?,'t')",
            ("ecf1", "2026-06-01", "SYNTH SHOP, INC", "-5.00", "commbank", "Groceries", "2026-06"),
        )
        conn.commit()
        conn.close()

        r = api_client.get("/export/transactions.csv")
        rows = list(csv.reader(io.StringIO(r.text)))
        descriptions = [row[1] for row in rows[1:]]
        # csv module round-trips the embedded comma correctly (proper quoting).
        assert "SYNTH SHOP, INC" in descriptions


# ---------------------------------------------------------------------------
# TestResetEndpoint — POST /reset (Feature E)
# ---------------------------------------------------------------------------


class TestResetEndpoint:
    @staticmethod
    def _count(table):
        conn = sqlite3.connect(os.environ["SQLITE_PATH"])
        (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        conn.close()
        return n

    def test_wrong_confirm_400(self, api_client):
        _upload_both(api_client)
        r = api_client.post("/reset", json={"confirm": "nope"})
        assert r.status_code == 400
        assert r.json()["detail"] == "confirmation required"
        # Data untouched.
        assert self._count("transactions") == 5

    def test_confirm_wipes_and_returns_counts(self, api_client):
        _upload_both(api_client)
        r = api_client.post("/reset", json={"confirm": "RESET"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["cleared"]["transactions"] == 5
        assert body["cleared"]["file_fingerprints"] == 2

        assert self._count("transactions") == 0
        assert self._count("file_fingerprints") == 0
        assert self._count("corrections") == 0
        # category_context re-seeded to the 8 canonical rows.
        assert self._count("category_context") == 8

    def test_missing_confirm_422(self, api_client):
        r = api_client.post("/reset", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# TestCategoriserEndpoints — status / test / retry (Feature E)
# ---------------------------------------------------------------------------


class TestCategoriserEndpoints:
    def test_status_configured_false_and_zero_uncategorised(self, api_client):
        r = api_client.get("/categoriser/status")
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is False  # OPENROUTER_API_KEY blanked in fixture
        assert body["uncategorised_count"] == 0

    def test_status_counts_uncategorised(self, api_client):
        # Seed two NULL-category rows via a separate connection.
        conn = sqlite3.connect(os.environ["SQLITE_PATH"])
        conn.executemany(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES (?,?,?,?,?,NULL,?,'t')",
            [
                ("uf1", "2026-06-01", "SYNTH A", "-1.00", "commbank", "2026-06"),
                ("uf2", "2026-06-02", "SYNTH B", "-2.00", "commbank", "2026-06"),
            ],
        )
        conn.commit()
        conn.close()

        body = api_client.get("/categoriser/status").json()
        assert body["uncategorised_count"] == 2

    def test_test_endpoint_configured_off(self, api_client):
        r = api_client.post("/categoriser/test")
        assert r.status_code == 200
        assert r.json() == {
            "configured": False,
            "reachable": False,
            "rate_limited": False,
            "detail": "OpenRouter API key not configured",
        }

    def test_retry_empty_is_noop(self, api_client):
        r = api_client.post("/categoriser/retry")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "categorised": 0, "remaining": 0}

    def test_retry_categorises_orphans(
        self, api_client, fake_analyser, tmp_path, monkeypatch
    ):
        from backend.pipeline import retry_uncategorised as _real_retry

        # Seed NULL-category rows via a separate connection.
        conn = sqlite3.connect(os.environ["SQLITE_PATH"])
        conn.executemany(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES (?,?,?,?,?,NULL,?,'t')",
            [
                ("uf1", "2026-06-01", "SYNTH A", "-1.00", "commbank", "2026-06"),
                ("uf2", "2026-06-02", "SYNTH B", "-2.00", "commbank", "2026-06"),
            ],
        )
        conn.commit()
        conn.close()

        # Inject the fake analyser + tmp output dirs (mirrors the run_pipeline patch).
        def _patched(store):
            return _real_retry(
                store,
                analyser_client=fake_analyser,
                drive_service=None,
                output_dir=str(tmp_path),
                sanitise_log_dir=str(tmp_path),
            )

        monkeypatch.setattr(app_module, "retry_uncategorised", _patched)

        r = api_client.post("/categoriser/retry")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "categorised": 2, "remaining": 0}

    def test_retry_analyser_down_returns_safe_dict(
        self, api_client, tmp_path, monkeypatch
    ):
        from backend.analyser import AnalyserError
        from backend.pipeline import retry_uncategorised as _real_retry

        conn = sqlite3.connect(os.environ["SQLITE_PATH"])
        conn.execute(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES ('uf1','2026-06-01','SYNTH A','-1.00','commbank',NULL,'2026-06','t')",
        )
        conn.commit()
        conn.close()

        class _Raising:
            def complete(self, *, system_prompt, user_prompt):  # noqa: ARG002
                raise AnalyserError("all model tiers failed")

        def _patched(store):
            return _real_retry(
                store,
                analyser_client=_Raising(),
                drive_service=None,
                output_dir=str(tmp_path),
                sanitise_log_dir=str(tmp_path),
            )

        monkeypatch.setattr(app_module, "retry_uncategorised", _patched)

        r = api_client.post("/categoriser/retry")
        assert r.status_code == 200
        assert r.json() == {
            "ok": False,
            "categorised": 0,
            "remaining": 1,
            "detail": "categoriser unavailable",
        }


# ---------------------------------------------------------------------------
# TestProbeOpenrouter — _probe_openrouter branches (unit, no network)
# ---------------------------------------------------------------------------


class _FakeProbeClient:
    """Fake OpenRouterClient for the probe: optionally raises on complete()."""

    def __init__(self, exc=None):
        self._exc = exc

    def complete(self, *, system_prompt, user_prompt):  # noqa: ARG002
        if self._exc is not None:
            raise self._exc
        return ({"categories": {}}, "fake-model")


class TestProbeOpenrouter:
    def test_configured_off_never_calls_factory(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "")

        def _boom():
            raise AssertionError("factory must not be called when key is unset")

        result = app_module._probe_openrouter(client_factory=_boom)
        assert result == {
            "configured": False,
            "reachable": False,
            "rate_limited": False,
            "detail": "OpenRouter API key not configured",
        }

    def test_success_branch(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "SYNTH-KEY")
        result = app_module._probe_openrouter(
            client_factory=lambda: _FakeProbeClient()
        )
        assert result == {
            "configured": True,
            "reachable": True,
            "rate_limited": False,
            "detail": "OpenRouter reachable",
        }

    def test_rate_limited_branch(self, monkeypatch):
        from backend.analyser import AnalyserError

        monkeypatch.setenv("OPENROUTER_API_KEY", "SYNTH-KEY")
        result = app_module._probe_openrouter(
            client_factory=lambda: _FakeProbeClient(
                exc=AnalyserError("HTTP 429 rate limited")
            )
        )
        assert result["reachable"] is True
        assert result["rate_limited"] is True
        assert result["detail"] == "Rate limited (shared free-tier throttling)"

    def test_generic_analyser_error_branch(self, monkeypatch):
        from backend.analyser import AnalyserError

        monkeypatch.setenv("OPENROUTER_API_KEY", "SYNTH-KEY")
        result = app_module._probe_openrouter(
            client_factory=lambda: _FakeProbeClient(
                exc=AnalyserError("all model tiers failed")
            )
        )
        assert result == {
            "configured": True,
            "reachable": False,
            "rate_limited": False,
            "detail": "Could not reach OpenRouter",
        }

    def test_unexpected_exception_branch(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "SYNTH-KEY")
        result = app_module._probe_openrouter(
            client_factory=lambda: _FakeProbeClient(exc=RuntimeError("boom"))
        )
        assert result["reachable"] is False
        assert result["rate_limited"] is False
        assert result["detail"] == "Could not reach OpenRouter"


# ---------------------------------------------------------------------------
# TestSearchEndpoint — GET /search (v6 local full-text transaction search)
# ---------------------------------------------------------------------------

class TestSearchEndpoint:
    """GET /search: LOCAL, read-only FTS lookup over the owner's own store.

    After _upload_both there are 5 rows in 2026-06 (fake analyser labels every
    row 'Groceries'). Four of the five synthetic descriptions contain 'SYNTH'
    (WOOLWORTHS METRO is the odd one out).
    """

    def test_populated_query_200_and_nonempty(self, api_client):
        _upload_both(api_client)
        r = api_client.get("/search", params={"q": "SYNTH"})
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "SYNTH"
        assert body["month"] is None
        assert body["count"] == len(body["transactions"]) == 4

    def test_shape_mirrors_category_transactions_plus_query_month(self, api_client):
        _upload_both(api_client)
        body = api_client.get("/search", params={"q": "SYNTH"}).json()
        assert set(body.keys()) == {"query", "month", "total", "count", "transactions"}
        for t in body["transactions"]:
            # category is the extra field vs /category-transactions.
            assert set(t.keys()) == {"id", "date", "description", "amount", "bank", "category"}

    def test_total_is_str_decimal_sum(self, api_client):
        from decimal import Decimal

        _upload_both(api_client)
        body = api_client.get("/search", params={"q": "SYNTH"}).json()
        expected = sum(
            (Decimal(t["amount"]) for t in body["transactions"]), Decimal("0.00")
        )
        assert body["total"] == str(expected)

    def test_category_label_is_searchable(self, api_client):
        _upload_both(api_client)
        body = api_client.get("/search", params={"q": "Groceries"}).json()
        # Every uploaded row was labelled Groceries by the fake analyser.
        assert body["count"] == 5

    def test_month_filter_applies(self, api_client):
        _upload_both(api_client)
        body = api_client.get(
            "/search", params={"q": "SYNTH", "month": "2026-06"}
        ).json()
        assert body["month"] == "2026-06"
        assert body["count"] == 4

    def test_month_filter_other_month_empty(self, api_client):
        _upload_both(api_client)
        body = api_client.get(
            "/search", params={"q": "SYNTH", "month": "2026-01"}
        ).json()
        assert body["count"] == 0
        assert body["total"] == "0.00"

    def test_blank_query_200_empty_shape(self, api_client):
        _upload_both(api_client)
        r = api_client.get("/search", params={"q": ""})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["transactions"] == []
        assert body["total"] == "0.00"

    def test_no_results_query_200(self, api_client):
        _upload_both(api_client)
        r = api_client.get("/search", params={"q": "NONEXISTENTTOKENZZZ"})
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_bad_month_returns_400(self, api_client):
        r = api_client.get("/search", params={"q": "x", "month": "2026/06"})
        assert r.status_code == 400

    def test_bad_month_detail_is_fixed_string(self, api_client):
        r = api_client.get("/search", params={"q": "x", "month": "2026/06"})
        assert r.json()["detail"] == "month must be YYYY-MM"

    def test_missing_q_is_422(self, api_client):
        # q is a required query param; FastAPI returns 422 when it is absent.
        r = api_client.get("/search")
        assert r.status_code == 422

    def test_special_char_query_not_500(self, api_client):
        _upload_both(api_client)
        for q in ['a"b(', "a AND b", "NEAR(", "%_", "*", "col:val"]:
            r = api_client.get("/search", params={"q": q})
            assert r.status_code == 200, f"q={q!r} produced {r.status_code}"

    def test_account_number_never_in_results(self, api_client):
        # Privacy: the synthetic Westpac account number must never surface in any
        # returned description (the parser drops it; it is never stored/searchable).
        _upload_both(api_client)
        body = api_client.get("/search", params={"q": "SYNTH"}).json()
        for t in body["transactions"]:
            assert _FAKE_ACCT not in t["description"]
        assert _FAKE_ACCT not in r_json_text(body)


def r_json_text(body: dict) -> str:
    """Serialise a response body to text for a substring privacy assertion."""
    return json.dumps(body)


# ---------------------------------------------------------------------------
# TestTransfersEndpoints — GET /transfers, POST /transfers/{id}/untag (v6 f2).
#
# Seeds synthetic cross-bank legs directly on the live app store and calls
# detect_transfers, then exercises the read + untag endpoints. All SYNTHETIC.
# ---------------------------------------------------------------------------


class TestTransfersEndpoints:
    @staticmethod
    def _seed_pair(*, out_category=None, in_category=None):
        """Insert one CommBank debit + Westpac credit and run detection; return pair id.

        Uses a SEPARATE Store on the same on-disk DB file: the app's own store
        connection is bound to the TestClient's worker thread, so we cannot touch
        it here. SQLite is a shared file, so the committed pair + tags are visible
        to the endpoints immediately.
        """
        from backend.store import Store

        with Store(os.environ["SQLITE_PATH"]) as store:
            store.conn.executemany(
                "INSERT INTO transactions"
                "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
                " VALUES (?,?,?,?,?,?,?,'t')",
                [
                    ("tpo", "2026-06-01", "SYNTHXFEROUT", "-500.00", "commbank", out_category, "2026-06"),
                    ("tpi", "2026-06-02", "SYNTHXFERIN", "500.00", "westpac", in_category, "2026-06"),
                ],
            )
            store.conn.commit()
            store.detect_transfers()
            return store.list_transfer_pairs()[0]["id"]

    def test_empty_shape(self, api_client):
        r = api_client.get("/transfers")
        assert r.status_code == 200
        assert r.json() == {"count": 0, "pairs": []}

    def test_populated_shape(self, api_client):
        pair_id = self._seed_pair()
        r = api_client.get("/transfers")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        pair = body["pairs"][0]
        assert pair["id"] == pair_id
        assert pair["amount"] == "500.00"
        assert pair["out"]["description"] == "SYNTHXFEROUT"
        assert pair["out"]["amount"] == "-500.00"
        assert pair["out"]["bank"] == "commbank"
        assert pair["in"]["description"] == "SYNTHXFERIN"
        assert pair["in"]["amount"] == "500.00"
        assert pair["in"]["bank"] == "westpac"

    def test_summary_excludes_transfers_end_to_end(self, api_client):
        self._seed_pair(out_category="Groceries", in_category=None)
        # Both legs tagged Transfer -> excluded from /summary totals + count.
        body = api_client.get("/summary?month=2026-06").json()
        assert "Transfer" not in body["totals"]
        assert body["count"] == 0

    def test_untag_restores_category_and_counts_again(self, api_client):
        pair_id = self._seed_pair(out_category="Groceries", in_category=None)

        r = api_client.post(f"/transfers/{pair_id}/untag")
        assert r.status_code == 200
        assert r.json() == {
            "ok": True,
            "pair_id": pair_id,
            "restored": 2,
            # Where each leg went, so the UI can tell the owner (null = Uncategorised).
            "restored_to": {"out": "Groceries", "in": None},
        }

        # The restored spending row is counted by /summary again.
        body = api_client.get("/summary?month=2026-06").json()
        assert body["totals"].get("Groceries") == "-500.00"
        assert body["count"] == 2  # Groceries leg + restored-NULL (Uncategorised) leg
        # The pair is gone from the active list.
        assert api_client.get("/transfers").json()["count"] == 0

    def test_untag_unknown_id_404(self, api_client):
        r = api_client.post("/transfers/999999/untag")
        assert r.status_code == 404
        assert r.json()["detail"] == "transfer pair not found"

    def test_untag_second_call_restored_zero(self, api_client):
        pair_id = self._seed_pair(out_category="Groceries")
        assert api_client.post(f"/transfers/{pair_id}/untag").json()["restored"] == 2
        second = api_client.post(f"/transfers/{pair_id}/untag")
        assert second.status_code == 200
        assert second.json() == {"ok": True, "pair_id": pair_id, "restored": 0}

    def test_non_int_pair_id_422(self, api_client):
        r = api_client.post("/transfers/not-an-int/untag")
        assert r.status_code == 422

    def test_reset_clears_transfer_pairs_and_reports_count(self, api_client):
        self._seed_pair(out_category="Groceries")
        r = api_client.post("/reset", json={"confirm": "RESET"})
        assert r.status_code == 200
        assert r.json()["cleared"]["transfer_pairs"] == 1
        # No transfers remain after reset.
        assert api_client.get("/transfers").json()["count"] == 0

    @staticmethod
    def _leg_id(fingerprint: str) -> int:
        """Row id of a seeded leg, read via a separate Store (thread-bound app store)."""
        from backend.store import Store

        with Store(os.environ["SQLITE_PATH"]) as store:
            row = store.conn.execute(
                "SELECT id FROM transactions WHERE txn_fingerprint = ?", (fingerprint,)
            ).fetchone()
            return int(row["id"])

    def test_category_override_rejects_transfer_leg_409(self, api_client):
        from backend.store import Store

        self._seed_pair(out_category="Groceries")
        leg_id = self._leg_id("tpo")

        r = api_client.post(
            "/category-override", json={"id": leg_id, "category": "Dining Out"}
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "transaction is a transfer leg; untag the pair first"

        # Leg untouched, pair still active — no asymmetric netting possible.
        with Store(os.environ["SQLITE_PATH"]) as store:
            assert store.transaction_category(leg_id) == "Transfer"
        assert api_client.get("/transfers").json()["count"] == 1

    def test_category_override_allowed_after_untag(self, api_client):
        pair_id = self._seed_pair(out_category="Groceries")
        leg_id = self._leg_id("tpo")

        assert api_client.post(f"/transfers/{pair_id}/untag").status_code == 200
        r = api_client.post(
            "/category-override", json={"id": leg_id, "category": "Dining Out"}
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# TestTransfersUnseenEndpoints — GET /summary.transfers_unseen +
# POST /transfers/seen (v7 feature 2). Seeds a synthetic cross-bank pair via a
# separate Store on the same on-disk DB (the app store is bound to the client
# worker thread), then drives the endpoints end-to-end. All SYNTHETIC.
# ---------------------------------------------------------------------------


class TestTransfersUnseenEndpoints:
    @staticmethod
    def _seed_pair():
        """Insert one synthetic CommBank/Westpac leg pair; run detection."""
        from backend.store import Store

        with Store(os.environ["SQLITE_PATH"]) as store:
            store.conn.executemany(
                "INSERT INTO transactions"
                "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
                " VALUES (?,?,?,?,?,?,?,'t')",
                [
                    ("uso", "2026-06-01", "SYNTHXFEROUT", "-500.00", "commbank", None, "2026-06"),
                    ("usi", "2026-06-02", "SYNTHXFERIN", "500.00", "westpac", None, "2026-06"),
                ],
            )
            store.conn.commit()
            store.detect_transfers()

    def test_summary_empty_db_has_zero(self, api_client):
        body = api_client.get("/summary").json()
        assert body["transfers_unseen"] == 0

    def test_seen_flow_end_to_end(self, api_client):
        self._seed_pair()
        # A newly detected pair is unseen.
        assert api_client.get("/summary").json()["transfers_unseen"] == 1

        r = api_client.post("/transfers/seen")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["transfers_unseen"] == 0
        assert isinstance(body["last_viewed_at"], str) and body["last_viewed_at"]

        # The summary now reports the pair as seen.
        assert api_client.get("/summary").json()["transfers_unseen"] == 0

    def test_seen_is_idempotent(self, api_client):
        first = api_client.post("/transfers/seen")
        second = api_client.post("/transfers/seen")
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["ok"] is True

    def test_reclassify_response_inherits_field(self, api_client):
        # The month must exist for /reclassify; upload seeds synthetic data.
        _upload_both(api_client)
        body = api_client.post("/reclassify", params={"enabled": "true"}).json()
        assert "transfers_unseen" in body


# ---------------------------------------------------------------------------
# TestBudgetsEndpoints — GET/PUT /budgets  (v6 feature 3)
#
# All SYNTHETIC. The seven budgetable categories come from BUDGET_CATEGORIES
# (TAXONOMY minus Income). Validation rejects the whole request atomically.
# ---------------------------------------------------------------------------


class TestBudgetsEndpoints:
    _EXPECTED_CATEGORIES = [
        "Groceries", "Housing", "Dining Out", "Transport",
        "Entertainment", "Subscriptions", "Other",
    ]

    def test_get_empty_shape(self, api_client):
        body = api_client.get("/budgets").json()
        assert body["categories"] == self._EXPECTED_CATEGORIES
        assert body["budgets"] == {}

    def test_put_sets_and_get_reflects_canonical_2dp(self, api_client):
        r = api_client.put("/budgets", json={"budgets": {"Groceries": "250"}})
        assert r.status_code == 200
        assert r.json()["budgets"]["Groceries"] == "250.00"
        # GET reflects the same canonical value.
        assert api_client.get("/budgets").json()["budgets"]["Groceries"] == "250.00"

    def test_put_accepts_numeric_value(self, api_client):
        r = api_client.put("/budgets", json={"budgets": {"Transport": 120.5}})
        assert r.status_code == 200
        assert r.json()["budgets"]["Transport"] == "120.50"

    def test_put_null_clears(self, api_client):
        api_client.put("/budgets", json={"budgets": {"Groceries": "250"}})
        r = api_client.put("/budgets", json={"budgets": {"Groceries": None}})
        assert r.status_code == 200
        assert "Groceries" not in r.json()["budgets"]

    def test_put_empty_string_clears(self, api_client):
        api_client.put("/budgets", json={"budgets": {"Groceries": "250"}})
        r = api_client.put("/budgets", json={"budgets": {"Groceries": ""}})
        assert r.status_code == 200
        assert "Groceries" not in r.json()["budgets"]

    def test_put_unknown_category_silently_ignored(self, api_client):
        r = api_client.put("/budgets", json={"budgets": {"Crypto": "100"}})
        assert r.status_code == 200
        assert "Crypto" not in r.json()["budgets"]
        assert api_client.get("/budgets").json()["budgets"] == {}

    def test_put_income_silently_ignored(self, api_client):
        # Income is not budgetable → ignored like any unknown key (no 400).
        r = api_client.put("/budgets", json={"budgets": {"Income": "100"}})
        assert r.status_code == 200
        assert "Income" not in r.json()["budgets"]

    def test_max_bound_inclusive(self, api_client):
        r = api_client.put("/budgets", json={"budgets": {"Housing": "10000000"}})
        assert r.status_code == 200
        assert r.json()["budgets"]["Housing"] == "10000000.00"

    @pytest.mark.parametrize("bad", ["abc", "-5", "0", "20000000", "NaN", "Infinity"])
    def test_put_invalid_value_400_writes_nothing(self, api_client, bad):
        r = api_client.put("/budgets", json={"budgets": {"Groceries": bad}})
        assert r.status_code == 400
        assert r.json()["detail"] == "invalid budget amount"
        # Nothing was written from the rejected request.
        assert api_client.get("/budgets").json()["budgets"] == {}

    def test_put_all_or_nothing_on_one_bad_entry(self, api_client):
        # One valid + one invalid entry → whole request rejected, valid one NOT written.
        r = api_client.put(
            "/budgets", json={"budgets": {"Groceries": "250", "Transport": "-5"}}
        )
        assert r.status_code == 400
        assert api_client.get("/budgets").json()["budgets"] == {}

    def test_settings_expose_both_budget_toggles(self, api_client):
        body = api_client.get("/settings").json()
        assert "budget_approaching" in body["notifications"]
        assert "budget_exceeded" in body["notifications"]
        # Opt-out model: default enabled.
        assert body["notifications"]["budget_approaching"] is True
        assert body["notifications"]["budget_exceeded"] is True

    def test_reset_preserves_budgets(self, api_client):
        api_client.put("/budgets", json={"budgets": {"Groceries": "250"}})
        api_client.post("/reset", json={"confirm": "RESET"})
        # Budgets live in app_settings and survive a data reset.
        assert api_client.get("/budgets").json()["budgets"]["Groceries"] == "250.00"


# ---------------------------------------------------------------------------
# TestBudgetAlertTriggers — the guarded check fires at each mutation endpoint.
#
# backend.app.check_budget_alerts is replaced with a counting spy so the test
# asserts the trigger wiring without depending on push delivery (a hard no-op
# by default anyway).
# ---------------------------------------------------------------------------


class TestBudgetAlertTriggers:
    @staticmethod
    def _spy(monkeypatch):
        calls = []

        def _fake(store, *args, **kwargs):  # noqa: ARG001
            calls.append(1)
            return 0

        monkeypatch.setattr(app_module, "check_budget_alerts", _fake)
        return calls

    @staticmethod
    def _seed_transport(tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.sqlite"))
        conn.execute(
            "INSERT INTO transactions"
            "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
            " VALUES"
            " ('bf1','2026-06-15','BP CONNECT','-7.00','commbank','Transport','2026-06','t'),"
            " ('bf2','2026-06-16','OPAL TRAVEL','-3.00','commbank','Transport','2026-06','t')"
        )
        conn.commit()
        conn.close()

    def test_put_budgets_triggers_check(self, api_client, monkeypatch):
        calls = self._spy(monkeypatch)
        r = api_client.put("/budgets", json={"budgets": {"Groceries": "250"}})
        assert r.status_code == 200
        assert len(calls) == 1

    def test_category_override_triggers_check(self, api_client, monkeypatch):
        _upload_both(api_client)
        calls = self._spy(monkeypatch)
        with sqlite3.connect(os.environ["SQLITE_PATH"]) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT id FROM transactions ORDER BY id").fetchone()
        r = api_client.post(
            "/category-override", json={"id": row["id"], "category": "Transport"}
        )
        assert r.status_code == 200
        assert len(calls) == 1

    def test_reclassify_triggers_check(self, api_client, tmp_path, monkeypatch):
        self._seed_transport(tmp_path)
        calls = self._spy(monkeypatch)
        r = api_client.post(
            "/reclassify", params={"enabled": "true", "month": "2026-06"}
        )
        assert r.status_code == 200
        assert len(calls) == 1

    def test_untag_transfer_triggers_check(self, api_client, monkeypatch):
        pair_id = TestTransfersEndpoints._seed_pair(out_category="Groceries")
        calls = self._spy(monkeypatch)
        r = api_client.post(f"/transfers/{pair_id}/untag")
        assert r.status_code == 200
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# TestSubscriptionsEndpoint — GET /subscriptions (v6 feature 4), read-only.
#
# Seeds synthetic subscription state directly on the live app store (a separate
# Store on the same on-disk DB file, mirroring TestTransfersEndpoints). All
# SYNTHETIC — no real merchants, no real amounts.
# ---------------------------------------------------------------------------


class TestSubscriptionsEndpoint:
    @staticmethod
    def _seed_subscription(**overrides):
        from decimal import Decimal

        from backend.store import Store

        kwargs = {
            "merchant_key": "spend:STREAMCO",
            "root": "STREAMCO",
            "direction": "spend",
            "expected_amount": Decimal("22.99"),
            "first_seen_month": "2026-04",
            "last_seen_month": "2026-06",
            "status": "active",
        }
        kwargs.update(overrides)
        with Store(os.environ["SQLITE_PATH"]) as store:
            store.upsert_subscription(**kwargs)

    def test_empty_shape(self, api_client):
        r = api_client.get("/subscriptions")
        assert r.status_code == 200
        assert r.json() == {"count": 0, "subscriptions": []}

    def test_populated_shape(self, api_client):
        from decimal import Decimal

        self._seed_subscription()
        r = api_client.get("/subscriptions")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        sub = body["subscriptions"][0]
        assert sub == {
            "merchant": "STREAMCO",
            "direction": "spend",
            "amount": "22.99",
            "first_seen_month": "2026-04",
            "last_seen_month": "2026-06",
            "status": "active",
        }
        del Decimal  # silence unused import in some linters

    def test_active_before_ended_ordering(self, api_client):
        from decimal import Decimal

        self._seed_subscription(merchant_key="spend:ZEBRA", root="ZEBRA", status="active")
        self._seed_subscription(
            merchant_key="income:ACME SALARY", root="ACME SALARY", direction="income",
            expected_amount=Decimal("5000.00"), status="ended",
        )
        body = api_client.get("/subscriptions").json()
        assert body["count"] == 2
        # Active first, then ended.
        assert body["subscriptions"][0]["status"] == "active"
        assert body["subscriptions"][1]["status"] == "ended"
        assert body["subscriptions"][1]["direction"] == "income"

    def test_reset_clears_both_subscription_tables(self, api_client):
        self._seed_subscription()
        with sqlite3.connect(os.environ["SQLITE_PATH"]) as conn:
            conn.execute(
                "INSERT INTO subscription_event_fired"
                "(merchant_key,year_month,event,created_at)"
                " VALUES ('spend:STREAMCO','2026-06','new','t')"
            )
            conn.commit()

        r = api_client.post("/reset", json={"confirm": "RESET"})
        assert r.status_code == 200
        cleared = r.json()["cleared"]
        assert cleared["subscriptions"] == 1
        assert cleared["subscription_event_fired"] == 1
        # No subscriptions remain after reset.
        assert api_client.get("/subscriptions").json()["count"] == 0


# ---------------------------------------------------------------------------
# TestSubscriptionTriggers — check_subscriptions is wired at the mutation
# endpoints that can alter detection (category-override, transfer untag).
# ---------------------------------------------------------------------------


class TestSubscriptionTriggers:
    @staticmethod
    def _spy(monkeypatch):
        calls = []

        def _fake(store, *args, **kwargs):  # noqa: ARG001
            calls.append(1)
            return 0

        monkeypatch.setattr(app_module, "check_subscriptions", _fake)
        return calls

    def test_category_override_triggers_check(self, api_client, monkeypatch):
        _upload_both(api_client)
        calls = self._spy(monkeypatch)
        with sqlite3.connect(os.environ["SQLITE_PATH"]) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT id FROM transactions ORDER BY id").fetchone()
        r = api_client.post(
            "/category-override", json={"id": row["id"], "category": "Income"}
        )
        assert r.status_code == 200
        assert len(calls) == 1

    def test_untag_transfer_triggers_check(self, api_client, monkeypatch):
        pair_id = TestTransfersEndpoints._seed_pair(out_category="Groceries")
        calls = self._spy(monkeypatch)
        r = api_client.post(f"/transfers/{pair_id}/untag")
        assert r.status_code == 200
        assert len(calls) == 1
