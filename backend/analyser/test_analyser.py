"""test_analyser.py — pytest suite for the §7.6 OpenRouter analyser.

ALL fixtures use SYNTHETIC data generated in code.
No real transactions, no real account numbers, no real merchant names.
No live network calls — all HTTP is intercepted by fake post_fn helpers.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from backend.sanitiser import SanitisedTxn, SanitiseResult
from backend.analyser import (
    categorise,
    build_context_prompt,
    OpenRouterClient,
    AnalysisResult,
    AnalyserError,
)
from backend.analyser.analyser import build_prompt
from backend.analyser.parse import extract_json_object
from backend.store import CategoryContext

# ---------------------------------------------------------------------------
# Synthetic payload — invented merchants; never real data
# ---------------------------------------------------------------------------

PAYLOAD = (
    SanitisedTxn(0, "WOOLWORTHS METRO", Decimal("-72.40")),
    SanitisedTxn(1, "AGL ENERGY", Decimal("-130.05")),
    SanitisedTxn(2, "UBER TRIP", Decimal("-18.90")),
    SanitisedTxn(3, "NETSTREAM MONTHLY", Decimal("-15.99")),
    SanitisedTxn(4, "ACME PAYROLL", Decimal("3200.00")),
    SanitisedTxn(5, "HARBOUR CAFE", Decimal("-9.50")),
)

# Canned LLM response content — invented categories matching the synthetic payload
CANNED_GOOD_CONTENT = json.dumps({
    "categories": {
        "0": "Groceries",
        "1": "Housing",
        "2": "Transport",
        "3": "Subscriptions",
        "4": "Income",
        "5": "Dining Out",
    },
    "summary": "Mostly groceries, utilities and dining.",
    "flagged": [4],
})

CANNED_FENCED_CONTENT = f"```json\n{CANNED_GOOD_CONTENT}\n```"

CANNED_BARE_FENCE_CONTENT = f"```\n{CANNED_GOOD_CONTENT}\n```"

CANNED_UNKNOWN_CAT_CONTENT = json.dumps({
    "categories": {
        "0": "Groceries",
        "1": "Housing",
        "2": "Transport",
        "3": "Subscriptions",
        "4": "Income",
        "5": "Crypto",  # unknown — must be coerced to "Other"
    },
    "summary": "Spending overview.",
    "flagged": [],
})

# Synthetic model identifiers — invented strings; never real model IDs
_DEFAULT_MODEL = "synth/primary-model"
_FALLBACK_MODEL = "synth/fallback-model"
_ROUTER_MODEL = "synth/router-model"

_SYNTH_API_KEY = "test-api-key-synthetic-abc123"


# ---------------------------------------------------------------------------
# Fake HTTP helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    """Minimal fake HTTP response with .status_code, .json(), .text."""

    def __init__(self, status_code: int, content_str: str | None = None) -> None:
        self.status_code = status_code
        self._content_str = content_str
        self.text = content_str or ""

    def json(self) -> dict:
        if self.status_code < 200 or self.status_code >= 300 or self._content_str is None:
            return {"error": "simulated error response"}
        return {"choices": [{"message": {"content": self._content_str}}]}


def ok_response(content_str: str) -> FakeResponse:
    """Return a fake 200 response with the given LLM content string."""
    return FakeResponse(200, content_str)


def error_response(status: int) -> FakeResponse:
    """Return a fake error response with the given HTTP status code."""
    return FakeResponse(status)


class RecordingPostFn:
    """Intercepts HTTP calls, records them, and returns queued FakeResponses in order.

    Raises AssertionError if called more times than there are queued responses,
    ensuring tests never silently absorb unexpected extra HTTP calls.
    """

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.calls: list[dict] = []
        self._queue: list[FakeResponse] = list(responses)

    def __call__(
        self,
        url: str,
        *,
        headers: dict | None = None,
        json: dict | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append({
            "url": url,
            "headers": headers or {},
            "json": json,
            "timeout": timeout,
        })
        if not self._queue:
            raise AssertionError(
                "RecordingPostFn: no more queued responses — unexpected extra HTTP call"
            )
        return self._queue.pop(0)


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------

def _make_client(
    post_fn: RecordingPostFn | None = None,
    *,
    api_key: str = _SYNTH_API_KEY,
    model: str = _DEFAULT_MODEL,
    fallback_model: str = _FALLBACK_MODEL,
    router_fallback: str = _ROUTER_MODEL,
) -> OpenRouterClient:
    """Build an OpenRouterClient with fully synthetic config. No env, no network."""
    return OpenRouterClient(
        api_key=api_key,
        base_url="http://fake.invalid/v1",
        model=model,
        fallback_model=fallback_model,
        router_fallback=router_fallback,
        post_fn=post_fn if post_fn is not None else RecordingPostFn([]),
    )


def _make_sanitise_result(
    payload: tuple[SanitisedTxn, ...] = PAYLOAD,
    dropped: tuple[int, ...] = (),
) -> SanitiseResult:
    """Build a synthetic SanitiseResult for testing."""
    return SanitiseResult(
        payload=payload,
        dropped=dropped,
        run_id="synthetic-run-id-00000000",
        timestamp="2025-03-10T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# parse.py unit tests — pure function, no network
# ---------------------------------------------------------------------------

class TestExtractJsonObject:
    """Defensive JSON extraction helper (parse.py) — pure function tests."""

    def test_clean_json_parses(self):
        """A plain JSON object string parses correctly."""
        raw = '{"categories": {"0": "Groceries"}, "flagged": []}'
        result = extract_json_object(raw)
        assert result["categories"] == {"0": "Groceries"}

    def test_fenced_json_stripped_and_parsed(self):
        """A ```json ... ``` fenced response is stripped and parsed."""
        raw = '```json\n{"key": "value"}\n```'
        result = extract_json_object(raw)
        assert result == {"key": "value"}

    def test_bare_fence_stripped_and_parsed(self):
        """A ``` ... ``` bare-fenced response is stripped and parsed."""
        raw = '```\n{"key": "value"}\n```'
        result = extract_json_object(raw)
        assert result == {"key": "value"}

    def test_prose_wrapped_json_extracted(self):
        """JSON embedded in surrounding prose is extracted via brace-trim."""
        raw = 'Here is the result: {"key": "value"} as requested.'
        result = extract_json_object(raw)
        assert result == {"key": "value"}

    def test_fenced_parses_to_same_dict_as_unfenced(self):
        """Fenced and unfenced variants of the same JSON produce identical dicts."""
        obj = {"categories": {"0": "Groceries"}, "flagged": [0]}
        plain = json.dumps(obj)
        fenced = f"```json\n{plain}\n```"
        assert extract_json_object(plain) == extract_json_object(fenced)

    def test_raises_on_no_braces(self):
        """Input without braces raises ValueError."""
        with pytest.raises(ValueError):
            extract_json_object("no braces here at all")

    def test_raises_on_empty_string(self):
        """Empty string raises ValueError."""
        with pytest.raises(ValueError):
            extract_json_object("")

    def test_raises_on_json_array_not_object(self):
        """A JSON array (not object) raises ValueError."""
        with pytest.raises(ValueError):
            extract_json_object("[1, 2, 3]")

    def test_raises_on_malformed_json(self):
        """Malformed JSON raises ValueError."""
        with pytest.raises(ValueError):
            extract_json_object("{bad json without closing}")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    """FR-22/FR-24: good LLM response → correct mapping, LOCAL totals, summary, flagged."""

    def test_categories_mapped_per_row_index(self):
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.categories[0] == "Groceries"
        assert result.categories[1] == "Housing"
        assert result.categories[2] == "Transport"
        assert result.categories[3] == "Subscriptions"
        assert result.categories[4] == "Income"
        assert result.categories[5] == "Dining Out"

    def test_category_totals_match_sanitised_amounts(self):
        """Totals are computed locally from SanitisedTxn.amount, not from LLM output."""
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.category_totals["Groceries"] == "-72.40"
        assert result.category_totals["Housing"] == "-130.05"
        assert result.category_totals["Transport"] == "-18.90"
        assert result.category_totals["Subscriptions"] == "-15.99"
        assert result.category_totals["Income"] == "3200.00"
        assert result.category_totals["Dining Out"] == "-9.50"

    def test_category_totals_not_from_llm(self):
        """LLM-supplied category_totals are silently ignored; local computation is used."""
        content = json.dumps({
            "categories": {
                "0": "Groceries", "1": "Housing", "2": "Transport",
                "3": "Subscriptions", "4": "Income", "5": "Dining Out",
            },
            "summary": "ok",
            "flagged": [],
            "category_totals": {"Groceries": "-999.99"},  # fake LLM total — must be ignored
        })
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        # Must reflect local computation from PAYLOAD[0].amount, not the LLM's fake total
        assert result.category_totals["Groceries"] == "-72.40"

    def test_summary_returned(self):
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.summary == "Mostly groceries, utilities and dining."

    def test_flagged_returned(self):
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.flagged == [4]

    def test_model_used_equals_default(self):
        """On first-tier success model_used must equal the default (primary) model id."""
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.model_used == _DEFAULT_MODEL

    def test_exactly_one_http_call_on_happy_path(self):
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert len(recorder.calls) == 1

    def test_analysis_result_is_frozen(self):
        """AnalysisResult must be immutable (frozen dataclass)."""
        import dataclasses

        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
            result.model_used = "hacked"  # type: ignore[misc]

    def test_all_payload_row_indexes_present_in_categories(self):
        """Every row_index in the payload has a corresponding entry in categories."""
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        for txn in PAYLOAD:
            assert txn.row_index in result.categories


# ---------------------------------------------------------------------------
# Fenced JSON
# ---------------------------------------------------------------------------

class TestFencedJson:
    """FR-24 edge-case 2: LLM wraps response in markdown fences — must be stripped."""

    def test_json_fence_parses_to_same_categories(self):
        recorder = RecordingPostFn([ok_response(CANNED_FENCED_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.categories[0] == "Groceries"
        assert result.categories[5] == "Dining Out"

    def test_json_fence_summary_preserved(self):
        recorder = RecordingPostFn([ok_response(CANNED_FENCED_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.summary == "Mostly groceries, utilities and dining."

    def test_bare_fence_parses_correctly(self):
        """Triple-backtick without 'json' label is also stripped."""
        recorder = RecordingPostFn([ok_response(CANNED_BARE_FENCE_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.categories[0] == "Groceries"
        assert result.model_used == _DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Unknown category coerced to Other
# ---------------------------------------------------------------------------

class TestUnknownCategoryCoercion:
    """FR-25: LLM returns an unrecognised category → coerced to 'Other'."""

    def test_crypto_coerced_to_other(self):
        recorder = RecordingPostFn([ok_response(CANNED_UNKNOWN_CAT_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.categories[5] == "Other"

    def test_known_categories_unchanged_alongside_unknown(self):
        recorder = RecordingPostFn([ok_response(CANNED_UNKNOWN_CAT_CONTENT)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.categories[0] == "Groceries"
        assert result.categories[1] == "Housing"
        assert result.categories[2] == "Transport"

    def test_missing_row_index_defaults_to_other(self):
        """LLM response missing an entry for a row_index → that row defaults to 'Other'."""
        content = json.dumps({
            "categories": {
                "0": "Groceries", "1": "Housing", "2": "Transport",
                "3": "Subscriptions", "4": "Income",
                # row 5 intentionally absent
            },
            "summary": "",
            "flagged": [],
        })
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.categories[5] == "Other"

    def test_extra_row_indexes_in_llm_response_ignored(self):
        """LLM returns a row_index not in payload — it must not appear in categories."""
        content = json.dumps({
            "categories": {
                "0": "Groceries", "1": "Housing", "2": "Transport",
                "3": "Subscriptions", "4": "Income", "5": "Dining Out",
                "99": "Other",  # not in payload
            },
            "summary": "",
            "flagged": [],
        })
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert 99 not in result.categories
        assert result.categories[0] == "Groceries"

    def test_whitespace_category_coerced_to_other(self):
        """A category string that is only whitespace → coerced to 'Other'."""
        content = json.dumps({
            "categories": {"0": "   "},
            "summary": "",
            "flagged": [],
        })
        recorder = RecordingPostFn([ok_response(content)])
        sr = _make_sanitise_result(payload=(PAYLOAD[0],))
        result = categorise(sr, client=_make_client(recorder))

        assert result.categories[0] == "Other"


# ---------------------------------------------------------------------------
# Fallback state machine
# ---------------------------------------------------------------------------

class TestFallbackStateMachine:
    """FR-23: exactly one attempt per tier; fail-through on non-2xx / bad response."""

    def test_default_429_fallback_model_used(self):
        """Tier 1 returns 429 → tier 2 (fallback) used; model_used == fallback id."""
        recorder = RecordingPostFn([
            error_response(429),
            ok_response(CANNED_GOOD_CONTENT),
        ])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.model_used == _FALLBACK_MODEL

    def test_default_429_exactly_two_http_calls(self):
        recorder = RecordingPostFn([
            error_response(429),
            ok_response(CANNED_GOOD_CONTENT),
        ])
        categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert len(recorder.calls) == 2

    def test_default_and_fallback_fail_router_model_used(self):
        """Tier 1 + 2 fail → tier 3 (router) used; model_used == router id."""
        recorder = RecordingPostFn([
            error_response(429),
            error_response(500),
            ok_response(CANNED_GOOD_CONTENT),
        ])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.model_used == _ROUTER_MODEL

    def test_default_and_fallback_fail_exactly_three_http_calls(self):
        recorder = RecordingPostFn([
            error_response(429),
            error_response(500),
            ok_response(CANNED_GOOD_CONTENT),
        ])
        categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert len(recorder.calls) == 3

    def test_all_tiers_fail_raises_analyser_error(self):
        """All three tiers fail → AnalyserError raised."""
        recorder = RecordingPostFn([
            error_response(429),
            error_response(500),
            error_response(503),
        ])
        with pytest.raises(AnalyserError):
            categorise(_make_sanitise_result(), client=_make_client(recorder))

    def test_all_tiers_fail_exactly_three_http_calls(self):
        recorder = RecordingPostFn([
            error_response(429),
            error_response(500),
            error_response(503),
        ])
        with pytest.raises(AnalyserError):
            categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert len(recorder.calls) == 3

    def test_analyser_error_message_does_not_contain_api_key(self):
        recorder = RecordingPostFn([
            error_response(429),
            error_response(500),
            error_response(503),
        ])
        client = _make_client(recorder, api_key="test-key-xyz")

        with pytest.raises(AnalyserError) as exc_info:
            categorise(_make_sanitise_result(), client=client)

        assert "test-key-xyz" not in str(exc_info.value)

    def test_network_exception_triggers_tier_fallthrough(self):
        """A network-level exception on tier 1 falls through to tier 2."""
        call_count = 0

        def raising_then_ok(url, *, headers=None, json=None, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("simulated network failure")
            return ok_response(CANNED_GOOD_CONTENT)

        client = OpenRouterClient(
            api_key=_SYNTH_API_KEY,
            base_url="http://fake.invalid/v1",
            model=_DEFAULT_MODEL,
            fallback_model=_FALLBACK_MODEL,
            router_fallback=_ROUTER_MODEL,
            post_fn=raising_then_ok,
        )
        result = categorise(_make_sanitise_result(), client=client)

        assert result.model_used == _FALLBACK_MODEL
        assert call_count == 2

    def test_unparseable_json_content_triggers_fallthrough(self):
        """200 response with unparseable JSON content → falls through to next tier."""
        recorder = RecordingPostFn([
            ok_response("This is definitely not JSON {{ broken"),
            ok_response(CANNED_GOOD_CONTENT),
        ])
        result = categorise(_make_sanitise_result(), client=_make_client(recorder))

        assert result.model_used == _FALLBACK_MODEL

    def test_empty_content_triggers_fallthrough(self):
        """200 response with empty content string → falls through to next tier."""
        # Simulate empty content in the choices array
        class EmptyContentResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": ""}}]}

        call_count = 0

        def empty_then_ok(url, *, headers=None, json=None, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return EmptyContentResponse()
            return ok_response(CANNED_GOOD_CONTENT)

        client = OpenRouterClient(
            api_key=_SYNTH_API_KEY,
            base_url="http://fake.invalid/v1",
            model=_DEFAULT_MODEL,
            fallback_model=_FALLBACK_MODEL,
            router_fallback=_ROUTER_MODEL,
            post_fn=empty_then_ok,
        )
        result = categorise(_make_sanitise_result(), client=client)

        assert result.model_used == _FALLBACK_MODEL


# ---------------------------------------------------------------------------
# Empty payload → zero HTTP calls (FR-15)
# ---------------------------------------------------------------------------

class TestEmptyPayload:
    """FR-15: empty payload short-circuits before any HTTP call."""

    def test_empty_list_zero_http_calls(self):
        """Passing an empty list makes zero HTTP calls."""
        recorder = RecordingPostFn([])  # any call would raise AssertionError
        result = categorise([], client=_make_client(recorder))

        assert len(recorder.calls) == 0

    def test_empty_sanitise_result_zero_http_calls(self):
        sr = _make_sanitise_result(payload=(), dropped=())
        recorder = RecordingPostFn([])
        result = categorise(sr, client=_make_client(recorder))

        assert len(recorder.calls) == 0

    def test_empty_payload_with_dropped_zero_http_calls(self):
        """Dropped rows are handled locally — still no HTTP call with empty payload."""
        sr = _make_sanitise_result(payload=(), dropped=(7,))
        recorder = RecordingPostFn([])
        result = categorise(sr, client=_make_client(recorder))

        assert len(recorder.calls) == 0

    def test_empty_payload_categories_empty(self):
        result = categorise([], client=_make_client())
        assert result.categories == {}

    def test_empty_payload_with_dropped_categories_has_other(self):
        """Dropped row_indexes appear as 'Other' in categories even with empty payload."""
        sr = _make_sanitise_result(payload=(), dropped=(7,))
        result = categorise(sr, client=_make_client())

        assert result.categories == {7: "Other"}

    def test_empty_payload_category_totals_empty(self):
        result = categorise([], client=_make_client())
        assert result.category_totals == {}

    def test_empty_payload_summary_empty_string(self):
        result = categorise([], client=_make_client())
        assert result.summary == ""

    def test_empty_payload_flagged_empty_list(self):
        result = categorise([], client=_make_client())
        assert result.flagged == []

    def test_empty_payload_model_used_empty_string(self):
        """model_used is '' for the empty no-call case (FR-15)."""
        result = categorise([], client=_make_client())
        assert result.model_used == ""


# ---------------------------------------------------------------------------
# Dropped rows
# ---------------------------------------------------------------------------

class TestDroppedRows:
    """Dropped rows appear as 'Other' in categories but are excluded from totals."""

    def test_dropped_row_labelled_other(self):
        sr = _make_sanitise_result(payload=PAYLOAD[:2], dropped=(7,))
        content = json.dumps({
            "categories": {"0": "Groceries", "1": "Housing"},
            "summary": "",
            "flagged": [],
        })
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(sr, client=_make_client(recorder))

        assert result.categories[7] == "Other"

    def test_dropped_row_not_in_category_totals(self):
        """Row 7 is dropped → no amount → must not contribute to category_totals."""
        sr = _make_sanitise_result(payload=PAYLOAD[:2], dropped=(7,))
        content = json.dumps({
            "categories": {"0": "Groceries", "1": "Housing"},
            "summary": "",
            "flagged": [],
        })
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(sr, client=_make_client(recorder))

        # Only the two payload rows should have totals; the dropped row has no amount
        assert set(result.category_totals.keys()) <= {"Groceries", "Housing"}

    def test_payload_totals_correct_alongside_dropped(self):
        sr = _make_sanitise_result(payload=PAYLOAD[:2], dropped=(7,))
        content = json.dumps({
            "categories": {"0": "Groceries", "1": "Housing"},
            "summary": "",
            "flagged": [],
        })
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(sr, client=_make_client(recorder))

        assert result.category_totals["Groceries"] == "-72.40"
        assert result.category_totals["Housing"] == "-130.05"

    def test_multiple_dropped_rows_all_labelled_other(self):
        sr = _make_sanitise_result(payload=(PAYLOAD[0],), dropped=(7, 8, 9))
        content = json.dumps({
            "categories": {"0": "Groceries"},
            "summary": "",
            "flagged": [],
        })
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(sr, client=_make_client(recorder))

        assert result.categories[7] == "Other"
        assert result.categories[8] == "Other"
        assert result.categories[9] == "Other"


# ---------------------------------------------------------------------------
# Privacy regression — outgoing request body (BLOCKING)
# ---------------------------------------------------------------------------

class TestPrivacyRegression:
    """BLOCKING: outgoing request body must contain only row_index, cleaned_description, amount."""

    def _capture_body(self) -> dict:
        """Run categorise and return the json body that was POSTed."""
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        categorise(_make_sanitise_result(), client=_make_client(recorder))
        assert len(recorder.calls) == 1, "Expected exactly one HTTP call"
        return recorder.calls[0]["json"]

    def _get_user_items(self) -> list[dict]:
        """Extract the parsed transaction items from the user message content."""
        body = self._capture_body()
        messages = body["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        return json.loads(user_msg["content"])

    def test_user_message_items_have_exactly_three_keys(self):
        """Each item in the user prompt has exactly row_index, cleaned_description, amount."""
        items = self._get_user_items()

        assert isinstance(items, list)
        assert len(items) == len(PAYLOAD)
        for item in items:
            assert set(item.keys()) == {"row_index", "cleaned_description", "amount"}, (
                f"Unexpected keys in outgoing item: {set(item.keys())}"
            )

    def test_no_date_field_in_user_items(self):
        items = self._get_user_items()
        for item in items:
            assert "date" not in item, "date field must not appear in outgoing items"

    def test_no_bank_field_in_user_items(self):
        items = self._get_user_items()
        for item in items:
            assert "bank" not in item, "bank field must not appear in outgoing items"

    def test_no_balance_field_in_user_items(self):
        items = self._get_user_items()
        for item in items:
            assert "balance" not in item, "balance field must not appear in outgoing items"

    def test_no_commbank_or_westpac_in_serialised_body(self):
        """No bank identifier strings anywhere in the full serialised request body."""
        body = self._capture_body()
        serialised = json.dumps(body).lower()
        assert "commbank" not in serialised
        assert "westpac" not in serialised

    def test_transaction_class_name_not_in_body(self):
        """The string 'Transaction' (raw data class name) must not appear in the body."""
        body = self._capture_body()
        serialised = json.dumps(body)
        assert "Transaction" not in serialised

    def test_cleaned_descriptions_present_in_user_prompt(self):
        """The sanitised merchant descriptions must reach the user prompt."""
        items = self._get_user_items()
        descriptions = [item["cleaned_description"] for item in items]

        assert "WOOLWORTHS METRO" in descriptions
        assert "AGL ENERGY" in descriptions
        assert "ACME PAYROLL" in descriptions

    def test_amounts_present_in_user_prompt(self):
        """The signed amount strings must appear in the user prompt."""
        items = self._get_user_items()
        amounts = [item["amount"] for item in items]

        assert "-72.40" in amounts
        assert "3200.00" in amounts
        assert "-9.50" in amounts

    def test_row_indexes_correct_in_user_prompt(self):
        """row_index values must match the SanitisedTxn.row_index values in order."""
        items = self._get_user_items()
        row_indexes = [item["row_index"] for item in items]

        assert row_indexes == [txn.row_index for txn in PAYLOAD]

    def test_no_account_number_digit_run_in_user_items(self):
        """No 9+ digit run (account-number-like) appears in any cleaned_description."""
        items = self._get_user_items()
        for item in items:
            desc = item.get("cleaned_description", "")
            assert not re.search(r"\d{9,}", desc), (
                f"Account-number-like digit run in cleaned_description: {desc!r}"
            )

    def test_amount_is_string_not_float_in_body(self):
        """amount values in the outgoing body are strings (from amount_to_text), not floats."""
        items = self._get_user_items()
        for item in items:
            assert isinstance(item["amount"], str), (
                f"amount must be str in outgoing body, got {type(item['amount'])}"
            )


# ---------------------------------------------------------------------------
# API key handling
# ---------------------------------------------------------------------------

class TestApiKeyHandling:
    """API key appears only in Authorization header; never in results or error messages."""

    def test_api_key_appears_in_authorization_header(self):
        """The api_key is sent as 'Bearer <key>' in the Authorization header."""
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        client = _make_client(recorder, api_key="test-key-xyz")
        categorise(_make_sanitise_result(), client=client)

        assert len(recorder.calls) == 1
        auth = recorder.calls[0]["headers"].get("Authorization", "")
        assert auth == "Bearer test-key-xyz"

    def test_api_key_not_in_analysis_result_repr(self):
        """The api_key must not appear anywhere in the AnalysisResult repr."""
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        client = _make_client(recorder, api_key="test-key-xyz")
        result = categorise(_make_sanitise_result(), client=client)

        result_repr = repr(result)
        assert "test-key-xyz" not in result_repr

    def test_api_key_not_in_summary(self):
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        client = _make_client(recorder, api_key="test-key-xyz")
        result = categorise(_make_sanitise_result(), client=client)

        assert "test-key-xyz" not in result.summary

    def test_api_key_not_in_model_used(self):
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        client = _make_client(recorder, api_key="test-key-xyz")
        result = categorise(_make_sanitise_result(), client=client)

        assert "test-key-xyz" not in result.model_used

    def test_api_key_not_in_category_values(self):
        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        client = _make_client(recorder, api_key="test-key-xyz")
        result = categorise(_make_sanitise_result(), client=client)

        for cat in result.categories.values():
            assert "test-key-xyz" not in cat

    def test_api_key_not_in_analyser_error_message(self):
        """api_key must not appear in AnalyserError messages (all tiers fail path)."""
        recorder = RecordingPostFn([
            error_response(429),
            error_response(500),
            error_response(503),
        ])
        client = _make_client(recorder, api_key="test-key-xyz")

        with pytest.raises(AnalyserError) as exc_info:
            categorise(_make_sanitise_result(), client=client)

        assert "test-key-xyz" not in str(exc_info.value)

    def test_api_key_from_env_via_monkeypatch(self, monkeypatch):
        """When api_key arg is None, client resolves OPENROUTER_API_KEY from the environment."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-test-key-99")
        monkeypatch.setenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL", _FALLBACK_MODEL)
        monkeypatch.setenv("OPENROUTER_ROUTER_FALLBACK", _ROUTER_MODEL)
        # Prevent load_dotenv from loading the real .env file
        import backend.analyser.client as client_module
        monkeypatch.setattr(client_module, "load_dotenv", lambda *a, **kw: None)

        recorder = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        client = OpenRouterClient(
            api_key=None,
            base_url="http://fake.invalid/v1",
            post_fn=recorder,
        )
        categorise(_make_sanitise_result(), client=client)

        assert len(recorder.calls) == 1
        auth = recorder.calls[0]["headers"].get("Authorization", "")
        assert auth == "Bearer env-test-key-99"


# ---------------------------------------------------------------------------
# Missing API key → AnalyserError at construction
# ---------------------------------------------------------------------------

class TestMissingApiKey:
    """Missing or empty OPENROUTER_API_KEY → AnalyserError raised at construction time."""

    def test_missing_key_raises_analyser_error(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        import backend.analyser.client as client_module
        monkeypatch.setattr(client_module, "load_dotenv", lambda *a, **kw: None)

        with pytest.raises(AnalyserError):
            OpenRouterClient(
                api_key=None,
                base_url="http://fake.invalid/v1",
                model=_DEFAULT_MODEL,
                fallback_model=_FALLBACK_MODEL,
            )

    def test_empty_string_key_raises_analyser_error(self):
        """Explicitly passing an empty string api_key raises AnalyserError."""
        with pytest.raises(AnalyserError):
            OpenRouterClient(
                api_key="",
                base_url="http://fake.invalid/v1",
                model=_DEFAULT_MODEL,
            )

    def test_missing_key_error_before_any_network_call(self, monkeypatch):
        """AnalyserError is raised at construction — the post_fn is never called."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        import backend.analyser.client as client_module
        monkeypatch.setattr(client_module, "load_dotenv", lambda *a, **kw: None)

        call_count = 0

        def counting_post_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ok_response(CANNED_GOOD_CONTENT)

        with pytest.raises(AnalyserError):
            OpenRouterClient(
                api_key=None,
                base_url="http://fake.invalid/v1",
                model=_DEFAULT_MODEL,
                post_fn=counting_post_fn,
            )

        assert call_count == 0, "Network must not be called when API key is missing"

    def test_no_model_tiers_raises_analyser_error(self):
        """All model tier strings explicitly empty → AnalyserError at construction.

        Passing "" (not None) bypasses env resolution, so env vars cannot interfere.
        """
        with pytest.raises(AnalyserError):
            OpenRouterClient(
                api_key=_SYNTH_API_KEY,
                base_url="http://fake.invalid/v1",
                model="",
                fallback_model="",
                router_fallback="",
            )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Re-running categorise on unchanged input gives identical categories and totals."""

    def test_same_payload_same_categories(self):
        """Two runs on the same payload produce the same category mapping."""
        recorder1 = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        recorder2 = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])

        result1 = categorise(_make_sanitise_result(), client=_make_client(recorder1))
        result2 = categorise(_make_sanitise_result(), client=_make_client(recorder2))

        assert result1.categories == result2.categories

    def test_re_run_totals_not_doubled(self):
        """Totals from a second run match the first — they are not accumulated."""
        recorder1 = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        recorder2 = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])

        result1 = categorise(_make_sanitise_result(), client=_make_client(recorder1))
        result2 = categorise(_make_sanitise_result(), client=_make_client(recorder2))

        assert result1.category_totals == result2.category_totals

    def test_re_run_summary_identical(self):
        recorder1 = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])
        recorder2 = RecordingPostFn([ok_response(CANNED_GOOD_CONTENT)])

        result1 = categorise(_make_sanitise_result(), client=_make_client(recorder1))
        result2 = categorise(_make_sanitise_result(), client=_make_client(recorder2))

        assert result1.summary == result2.summary


# ---------------------------------------------------------------------------
# Flagged entry validation
# ---------------------------------------------------------------------------

class TestFlaggedValidation:
    """Flagged entries that are not in the payload index set are silently dropped."""

    def test_flagged_entry_outside_payload_dropped(self):
        """row_index 99 in 'flagged' but not in payload → not in result.flagged."""
        content = json.dumps({
            "categories": {"0": "Groceries", "1": "Housing"},
            "summary": "",
            "flagged": [99],
        })
        sr = _make_sanitise_result(payload=PAYLOAD[:2])
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(sr, client=_make_client(recorder))

        assert 99 not in result.flagged

    def test_flagged_non_int_entry_dropped(self):
        """A non-integer string in 'flagged' is silently ignored."""
        content = json.dumps({
            "categories": {"0": "Groceries"},
            "summary": "",
            "flagged": ["not-an-int", None],
        })
        sr = _make_sanitise_result(payload=(PAYLOAD[0],))
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(sr, client=_make_client(recorder))

        assert result.flagged == []

    def test_valid_flagged_entry_in_payload_kept(self):
        """A valid row_index present in the payload is retained in result.flagged."""
        content = json.dumps({
            "categories": {"4": "Income"},
            "summary": "",
            "flagged": [4],
        })
        sr = _make_sanitise_result(payload=(PAYLOAD[4],))
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(sr, client=_make_client(recorder))

        assert 4 in result.flagged

    def test_flagged_absent_from_response_gives_empty_list(self):
        """If the LLM response omits 'flagged', result.flagged is []."""
        content = json.dumps({
            "categories": {"0": "Groceries"},
            "summary": "ok",
            # 'flagged' key is absent
        })
        sr = _make_sanitise_result(payload=(PAYLOAD[0],))
        recorder = RecordingPostFn([ok_response(content)])
        result = categorise(sr, client=_make_client(recorder))

        assert result.flagged == []


# ---------------------------------------------------------------------------
# build_context_prompt — golden-string fixture (SYNTHETIC; shared cross-language
# pin with the JS mirror in frontend/src/categoryContext.test.js — keep both
# byte-identical).
# ---------------------------------------------------------------------------

# SYNTHETIC categories — invented hint strings, NOT the D2 defaults and NOT any
# transaction text. color/position values are irrelevant to build_context_prompt.
_GOLDEN_CATEGORIES = (
    CategoryContext(
        name="Alpha", color="#111111", hints="SYNTH GROCER A, SYNTH GROCER B", position=0
    ),
    CategoryContext(name="Beta", color="#222222", hints="   ", position=1),
    CategoryContext(
        name="Gamma", color="#333333", hints="SYNTH  MULTI   SPACE\n\nHINT", position=2
    ),
)

# Byte-identical to the JS golden fixture asserted in categoryContext.test.js.
_GOLDEN_PROMPT = (
    "TAXONOMY & CONTEXT\n"
    "------------------\n"
    "- Alpha\n"
    "    SYNTH GROCER A, SYNTH GROCER B\n"
    "\n"
    "- Beta\n"
    "    (no extra context)\n"
    "\n"
    "- Gamma\n"
    "    SYNTH MULTI SPACE HINT"
)


class TestBuildContextPromptGolden:
    """build_context_prompt golden-string assertion (SYNTHETIC fixture, D1/D2)."""

    def test_golden_string_matches_exactly(self):
        assert build_context_prompt(_GOLDEN_CATEGORIES) == _GOLDEN_PROMPT

    def test_empty_hints_become_no_extra_context(self):
        assert "(no extra context)" in build_context_prompt(_GOLDEN_CATEGORIES)

    def test_multi_space_and_newline_hints_collapsed_and_trimmed(self):
        result = build_context_prompt(_GOLDEN_CATEGORIES)
        assert "SYNTH MULTI SPACE HINT" in result
        assert "SYNTH  MULTI" not in result  # original double space must be gone
        assert "\n\nHINT" not in result      # original blank-line break must be gone

    def test_header_then_separator_then_first_entry(self):
        result = build_context_prompt(_GOLDEN_CATEGORIES)
        assert result.startswith("TAXONOMY & CONTEXT\n------------------\n- Alpha")

    def test_categories_joined_by_blank_line(self):
        result = build_context_prompt(_GOLDEN_CATEGORIES)
        assert "\n\n- Beta" in result
        assert "\n\n- Gamma" in result

    def test_empty_list_is_header_only_form(self):
        assert build_context_prompt([]) == "TAXONOMY & CONTEXT\n------------------\n"


# ---------------------------------------------------------------------------
# build_prompt — context_preamble prepend (analyser.py)
# ---------------------------------------------------------------------------


class TestBuildPromptContextPrepend:
    """build_prompt(payload, context_preamble=P) prepends P + blank line to system_prompt."""

    def test_preamble_prepended_with_blank_line(self):
        system_prompt, _ = build_prompt(PAYLOAD, context_preamble=_GOLDEN_PROMPT)
        assert system_prompt.startswith(_GOLDEN_PROMPT + "\n\n")

    def test_base_prompt_follows_preamble(self):
        system_prompt, _ = build_prompt(PAYLOAD, context_preamble=_GOLDEN_PROMPT)
        base_system, _ = build_prompt(PAYLOAD, context_preamble="")
        assert system_prompt == _GOLDEN_PROMPT + "\n\n" + base_system

    def test_empty_preamble_leaves_system_prompt_unchanged(self):
        with_empty, _ = build_prompt(PAYLOAD, context_preamble="")
        without_arg, _ = build_prompt(PAYLOAD)
        assert with_empty == without_arg

    def test_whitespace_only_preamble_leaves_system_prompt_unchanged(self):
        with_ws, _ = build_prompt(PAYLOAD, context_preamble="   \n  ")
        without_arg, _ = build_prompt(PAYLOAD)
        assert with_ws == without_arg

    def test_user_prompt_identical_regardless_of_preamble(self):
        _, user_with = build_prompt(PAYLOAD, context_preamble=_GOLDEN_PROMPT)
        _, user_without = build_prompt(PAYLOAD, context_preamble="")
        assert user_with == user_without

    def test_user_prompt_contains_only_three_allowed_keys(self):
        """BLOCKING: user_prompt still parses to only the sanctioned three keys."""
        _, user_prompt = build_prompt(PAYLOAD, context_preamble=_GOLDEN_PROMPT)
        items = json.loads(user_prompt)
        for item in items:
            assert set(item.keys()) == {"row_index", "cleaned_description", "amount"}
