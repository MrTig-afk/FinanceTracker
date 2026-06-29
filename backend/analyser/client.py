"""client.py — OpenRouterClient: 3-tier LLM fallback (§7.6, FR-22, FR-23).

FR-26 — disable data retention in the OpenRouter account (account setting, not code).

This is the ONLY file in the repo allowed to import requests and the ONLY code
allowed to make the OpenRouter call — the sole sanctioned off-machine endpoint.

The outgoing request body contains ONLY row_index, cleaned_description, amount.
No date, no bank, no account/balance, nothing else can reach this layer because
SanitisedTxn has no other fields.

API key: never hardcoded, never logged, never included in error messages or results.
"""
from __future__ import annotations

import os
from typing import Any, Callable

import requests
from dotenv import load_dotenv

from .models import AnalyserError
from .parse import extract_json_object

# ---------------------------------------------------------------------------
# Type alias for the injectable HTTP post function
# ---------------------------------------------------------------------------

PostFn = Callable[..., Any]
# Called as: post_fn(url, headers=..., json=..., timeout=...)
# Returned object must expose: .status_code:int, .json()->dict (may raise), .text:str

# ---------------------------------------------------------------------------
# Module-level constants (public so tests can reference them)
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL: str = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT: float = 60.0


# ---------------------------------------------------------------------------
# OpenRouterClient
# ---------------------------------------------------------------------------

class OpenRouterClient:
    """3-tier fallback HTTP client for the OpenRouter chat-completions endpoint.

    Tier order: primary model → fallback model → router fallback.
    Each tier gets exactly one attempt; on any failure the next tier is tried.
    Only request.RequestException and HTTP/JSON errors trigger a tier-skip;
    a successfully parsed dict is returned immediately.

    Privacy invariant: the api_key is stored as an instance attribute and used
    only in request headers.  It is NEVER returned, logged, or included in any
    exception message raised by this class.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        router_fallback: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        post_fn: PostFn | None = None,
        http_referer: str | None = None,
        x_title: str | None = None,
    ) -> None:
        load_dotenv()  # no-op if already loaded; safe to call repeatedly

        # ---- api_key (required) ----
        resolved_key = api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY", "")
        if not resolved_key:
            raise AnalyserError(
                "OPENROUTER_API_KEY is missing or empty; "
                "set it in .env to enable LLM categorisation"
            )
        self._api_key: str = resolved_key  # private; never exposed in logs/errors/results

        # ---- base_url ----
        self._base_url: str = (
            base_url
            if base_url is not None
            else os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
        )

        # ---- optional headers ----
        self._http_referer: str | None = (
            http_referer
            if http_referer is not None
            else (os.getenv("OPENROUTER_HTTP_REFERER") or None)
        )
        self._x_title: str | None = (
            x_title
            if x_title is not None
            else (os.getenv("OPENROUTER_X_TITLE") or None)
        )

        # ---- timeout + post_fn ----
        self._timeout: float = timeout
        self._post_fn: PostFn = post_fn if post_fn is not None else requests.post

        # ---- tier list (resolve model strings from args/env, filter empties) ----
        resolved_model = model if model is not None else os.getenv("OPENROUTER_MODEL")
        resolved_fallback = (
            fallback_model
            if fallback_model is not None
            else os.getenv("OPENROUTER_FALLBACK_MODEL")
        )
        resolved_router = (
            router_fallback
            if router_fallback is not None
            else os.getenv("OPENROUTER_ROUTER_FALLBACK")
        )

        self._tiers: list[str] = [
            m for m in (resolved_model, resolved_fallback, resolved_router) if m
        ]
        if not self._tiers:
            raise AnalyserError(
                "No model tiers configured; "
                "set OPENROUTER_MODEL (and optionally FALLBACK/ROUTER) in .env"
            )

    # ------------------------------------------------------------------
    # Public method
    # ------------------------------------------------------------------

    def complete(self, *, system_prompt: str, user_prompt: str) -> tuple[dict, str]:
        """Run the 3-tier fallback. Return (parsed_json_object, model_used).

        Raises AnalyserError if every tier fails.
        """
        url = f"{self._base_url.rstrip('/')}/chat/completions"

        for model in self._tiers:
            # Build headers — api_key only in Authorization, never elsewhere
            headers: dict[str, str] = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            if self._http_referer:
                headers["HTTP-Referer"] = self._http_referer
            if self._x_title:
                headers["X-Title"] = self._x_title

            # Request body — ONLY model + messages + temperature + response_format
            body: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }

            # ---- attempt ----
            try:
                resp = self._post_fn(url, headers=headers, json=body, timeout=self._timeout)
            except Exception:
                # Network error, timeout, connection refused, etc. → try next tier
                continue

            # Non-2xx (includes 429 rate-limit) → try next tier
            if not (200 <= resp.status_code < 300):
                continue

            # Parse response envelope
            try:
                resp_json = resp.json()
            except Exception:
                continue

            if not isinstance(resp_json, dict):
                continue

            # Extract content from choices[0].message.content
            try:
                content: str = resp_json["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                continue

            if not content or not content.strip():
                continue

            # Defensively parse the JSON object from the content string
            try:
                parsed = extract_json_object(content)
            except ValueError:
                continue

            # Success — return immediately without trying further tiers
            return (parsed, model)

        # All tiers failed — do NOT include api_key or response bodies in the message
        raise AnalyserError("all model tiers failed")
