"""Shared Anthropic Messages client for the LLM-backed validators.

Stdlib only (urllib) — no SDK dependency. Every caller degrades to a heuristic
when `available()` is False (no key) or `call()` returns an error, so the whole
platform runs offline.

Uses the current API surface: claude-opus-4-8, adaptive thinking, and (when a
schema is supplied) structured JSON output via output_config.format.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-4-8"


def available(ctx) -> bool:
    return bool(ctx.env.get("ANTHROPIC_API_KEY"))


def call(ctx, system: str, user: str, schema: dict | None = None,
         model: str | None = None, max_tokens: int = 6000,
         timeout: float = 120):
    """Return (result, error). result is a parsed dict when schema is given,
    otherwise the response text. On any failure returns (None, reason)."""
    api_key = ctx.env.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "no ANTHROPIC_API_KEY"

    body = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if schema is not None:
        body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}

    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200] if e.fp else str(e)
        return None, f"HTTP {e.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"network error: {e}"
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"bad response: {e}"

    if payload.get("stop_reason") == "refusal":
        return None, "model refused"

    text = None
    for block in payload.get("content") or []:
        if block.get("type") == "text" and block.get("text", "").strip():
            text = block["text"]
            break
    if text is None:
        return None, "no text block in response"

    usage = payload.get("usage") or {}
    meta = {"input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "model": payload.get("model")}

    if schema is None:
        return {"text": text, "_usage": meta}, None
    try:
        parsed = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return None, "model output was not valid JSON"
    parsed["_usage"] = meta
    return parsed, None


# Reusable structured-review schema (score + findings) for the LLM validators.
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string",
                                 "enum": ["critical", "high", "medium", "low", "info"]},
                    "message": {"type": "string"},
                    "file": {"type": "string"},
                },
                "required": ["severity", "message", "file"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["score", "summary", "findings"],
    "additionalProperties": False,
}
