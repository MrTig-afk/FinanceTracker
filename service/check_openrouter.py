#!/usr/bin/env python3
"""
Check OpenRouter credits, rate limits, and that FinanceTracker's models exist.

Reads OPENROUTER_API_KEY from the environment or a local .env — never hardcode the key.
Zero dependencies (stdlib only).

Usage:
    python service/check_openrouter.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://openrouter.ai/api/v1"
MODELS = [
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-4-31b-it:free",
    "openrouter/free",
]


def load_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (".env", os.path.join(here, "..", ".env")):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and line.startswith("OPENROUTER_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return None


def get(path: str, key: str) -> dict:
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main() -> None:
    key = load_key()
    if not key:
        sys.exit("No OPENROUTER_API_KEY found in environment or .env")

    try:
        info = get("/key", key)["data"]
    except urllib.error.HTTPError as e:
        sys.exit(f"Key check failed: HTTP {e.code} — {e.read().decode(errors='ignore')}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e}")

    print("== OpenRouter key ==")
    print(f"  label:          {info.get('label')}")
    print(f"  is_free_tier:   {info.get('is_free_tier')}")
    print(f"  usage (spent):  ${info.get('usage')}")
    lim = info.get("limit")
    print(f"  credit limit:   {('$' + str(lim)) if lim is not None else 'pay-as-you-go (no key cap)'}")
    if info.get("limit_remaining") is not None:
        print(f"  remaining:      ${info.get('limit_remaining')}")
    rl = info.get("rate_limit") or {}
    if rl:
        print(f"  rate limit:     {rl.get('requests')} requests / {rl.get('interval')}")

    try:
        c = get("/credits", key)["data"]
        total, used = c.get("total_credits"), c.get("total_usage")
        print("\n== Credits ==")
        print(f"  purchased:      ${total}")
        print(f"  used:           ${used}")
        if total is not None and used is not None:
            print(f"  balance:        ${round(total - used, 4)}")
    except Exception:
        pass

    print("\n== Free-model daily limit (policy) ==")
    print("  Free models: ~20 requests/min. Per day:")
    print("    <$10 ever purchased   -> 50/day")
    print("    >=$10 ever purchased  -> 1000/day  (one-time threshold; persists)")
    print("  You've purchased $10, so you should be on 1000/day. The only fully")
    print("  definitive test is making free calls and seeing when a 429 hits.")
    print("  (FinanceTracker uses ~1 call/month regardless.)")

    print("\n== Model availability ==")
    try:
        ids = {m["id"] for m in get("/models", key).get("data", [])}
        for m in MODELS:
            print(f"  {'OK     ' if m in ids else 'MISSING'}  {m}")
    except Exception as e:
        print(f"  (could not fetch model list: {e})")


if __name__ == "__main__":
    main()
