"""parse.py — defensive JSON extraction for LLM responses (§7.6, PRD §12).

Pure functions, no network, no IO — safe to import at module level.

LLMs occasionally wrap their JSON output in markdown code fences or surround it
with prose despite being instructed not to.  extract_json_object strips those
decorations and returns the first well-formed JSON object found in the text.
"""
from __future__ import annotations

import json
import re


def extract_json_object(raw: str) -> dict:
    """Strip markdown fences and surrounding prose, return the parsed JSON object.

    Steps:
      1. raw.strip()
      2. Remove a leading ```json / ``` fence and a trailing ``` fence if present.
      3. Slice from the FIRST '{' to the LAST '}' inclusive.
      4. json.loads the slice.

    Raises ValueError if there is no object or it does not parse.

    Handles:
    - Clean JSON strings.
    - Responses wrapped in ```json ... ``` fences.
    - Responses wrapped in ``` ... ``` fences.
    - JSON with leading/trailing prose (the brace-trim in step 3 extracts the object).
    """
    text = raw.strip()

    # Step 2a: remove a leading ```json fence (with optional trailing whitespace/newline)
    text = re.sub(r"^```json\s*", "", text)
    # Step 2b: remove a leading ``` fence (only if still present after 2a)
    text = re.sub(r"^```\s*", "", text)
    # Step 2c: remove a trailing ``` fence (with optional leading whitespace)
    text = re.sub(r"\s*```$", "", text)

    text = text.strip()

    # Step 3: brace-trim — slice from FIRST '{' to LAST '}' inclusive
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError(
            "No JSON object found in LLM response (no matching braces)"
        )

    slice_ = text[start : end + 1]

    # Step 4: parse — raises json.JSONDecodeError (a ValueError subclass) on failure
    result = json.loads(slice_)

    if not isinstance(result, dict):
        raise ValueError(
            f"Expected a JSON object (dict) but got {type(result).__name__}"
        )

    return result
