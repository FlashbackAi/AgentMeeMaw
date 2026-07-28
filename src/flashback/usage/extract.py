from __future__ import annotations

from typing import Any

_ZERO = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def usage_from_anthropic(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return dict(_ZERO)
    return {
        "input_tokens": _int(getattr(usage, "input_tokens", 0)),
        "output_tokens": _int(getattr(usage, "output_tokens", 0)),
        "cache_read_tokens": _int(getattr(usage, "cache_read_input_tokens", 0)),
        "cache_write_tokens": _int(getattr(usage, "cache_creation_input_tokens", 0)),
    }


def usage_from_openai(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return dict(_ZERO)
    prompt = _int(getattr(usage, "prompt_tokens", 0))
    details = getattr(usage, "prompt_tokens_details", None)
    cached = _int(getattr(details, "cached_tokens", 0)) if details is not None else 0
    # OpenAI's prompt_tokens is inclusive of cached tokens; split so the cached
    # portion is priced at the cache-read rate and the rest at full input rate.
    return {
        "input_tokens": max(prompt - cached, 0),
        "output_tokens": _int(getattr(usage, "completion_tokens", 0)),
        "cache_read_tokens": cached,
        "cache_write_tokens": 0,
    }
