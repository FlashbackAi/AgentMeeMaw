"""
Pure key-naming helpers for Working Memory.

Four keys per session, all scoped by session_id:

    wm:session:{session_id}:transcript    LIST   rolling buffer (trimmed)
    wm:session:{session_id}:segment       LIST   turns since last boundary
    wm:session:{session_id}:state         HASH   everything else
    wm:session:{session_id}:asked         LIST   recent seeded questions

The functions here do NOT touch Valkey; they only build strings.
``session_id`` is validated to be a non-empty string with no Valkey
delimiter chars. This defends against an upstream caller that hands us
something pathological.
"""

from __future__ import annotations

KEY_PREFIX = "wm:session"


class InvalidSessionIdError(ValueError):
    """Raised when a session_id would produce an unsafe Valkey key."""


def _validate(session_id: str) -> None:
    if not isinstance(session_id, str):
        raise InvalidSessionIdError(
            f"session_id must be str, got {type(session_id).__name__}"
        )
    if not session_id:
        raise InvalidSessionIdError("session_id must not be empty")
    # Prevent injection of additional key segments; UUIDs never contain
    # spaces, newlines, or colons in the rendered form, so any of these
    # is a sign of a bad input.
    for ch in (" ", "\n", "\r", "\t"):
        if ch in session_id:
            raise InvalidSessionIdError(
                f"session_id must not contain whitespace; got {session_id!r}"
            )


def transcript_key(session_id: str) -> str:
    _validate(session_id)
    return f"{KEY_PREFIX}:{session_id}:transcript"


def segment_key(session_id: str) -> str:
    _validate(session_id)
    return f"{KEY_PREFIX}:{session_id}:segment"


def state_key(session_id: str) -> str:
    _validate(session_id)
    return f"{KEY_PREFIX}:{session_id}:state"


def asked_key(session_id: str) -> str:
    _validate(session_id)
    return f"{KEY_PREFIX}:{session_id}:asked"


def all_keys(session_id: str) -> tuple[str, str, str, str]:
    """Return all per-session keys, useful for bulk DEL / EXPIRE."""
    return (
        transcript_key(session_id),
        segment_key(session_id),
        state_key(session_id),
        asked_key(session_id),
    )
