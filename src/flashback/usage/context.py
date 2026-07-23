"""Ambient per-request usage attribution context.

Cost attribution (``person_id`` / ``session_id`` on ``usage_events``) is a
cross-cutting concern: a single turn or background job fans out to many LLM,
embedding, and image-render calls scattered across ~30 modules, and threading
an attribution pair through every one of those signatures is both invasive and
easy to get silently wrong — a missed call site keeps writing ``person_id =
NULL`` with no error.

Instead the request/job boundary binds the pair once via
:func:`bind_usage_context`, and the recorder reads it as the default when a
call site does not pass ``person_id`` explicitly. Because ``ContextVar`` is
copied into ``asyncio`` tasks and propagated across ``asyncio.to_thread`` (the
hop the async recorder uses to run its sync insert), a binding on the event
loop is visible to the nested sync insert without any further plumbing.

An explicit ``person_id`` passed to the recorder always wins over the ambient
binding.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class UsageContext:
    person_id: str | None = None
    session_id: str | None = None


_EMPTY = UsageContext()

_usage_context: ContextVar[UsageContext] = ContextVar(
    "flashback_usage_context", default=_EMPTY
)


def _coerce(value) -> str | None:
    """Normalize UUID / str / None to ``str | None`` for storage."""
    if value is None:
        return None
    return str(value)


def set_usage_context(*, person_id=None, session_id=None) -> Token:
    """Bind the attribution pair and return a reset token.

    Nested binds are additive: a field left ``None`` inherits the value from
    the enclosing binding rather than clearing it, so an inner scope that only
    knows ``session_id`` does not wipe an outer ``person_id``. Pair every call
    with :func:`reset_usage_context` in a ``finally`` (or use the
    :func:`bind_usage_context` context manager, which does that for you).
    """
    outer = _usage_context.get()
    ctx = UsageContext(
        person_id=_coerce(person_id) or outer.person_id,
        session_id=_coerce(session_id) or outer.session_id,
    )
    return _usage_context.set(ctx)


def reset_usage_context(token: Token) -> None:
    _usage_context.reset(token)


@contextmanager
def bind_usage_context(*, person_id=None, session_id=None) -> Iterator[None]:
    """Context-manager form of :func:`set_usage_context`."""
    token = set_usage_context(person_id=person_id, session_id=session_id)
    try:
        yield
    finally:
        reset_usage_context(token)


def current_usage_context() -> UsageContext:
    """The attribution pair bound for the current context, or an empty pair."""
    return _usage_context.get()
