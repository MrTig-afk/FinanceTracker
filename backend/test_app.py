"""test_app.py — pytest suite for backend/app.py FastAPI endpoints (§7.2).

ALL fixtures use SYNTHETIC data generated in code.
No real transactions, no real account numbers, no real CSV files read from disk.
No live network calls — analyser injected via monkeypatched run_pipeline.
Drive unconfigured. DB in tmp_path sqlite.
"""
from __future__ import annotations

import json
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
        assert len(body["categories"]) == 9

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
        assert len(body["categories"]) == 9

    def test_put_unknown_category_name_does_not_create_it(self, api_client):
        r = api_client.put(
            "/category-context",
            json={"categories": [{"name": "Bogus", "hints": "SYNTH VALUE"}]},
        )
        assert r.status_code == 200
        body = r.json()
        names = {c["name"] for c in body["categories"]}
        assert "Bogus" not in names
        assert len(body["categories"]) == 9

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
