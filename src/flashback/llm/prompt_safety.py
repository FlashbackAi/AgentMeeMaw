"""Small prompt-rendering helpers for user-derived XML-ish blocks."""

from __future__ import annotations

from html import escape


def xml_text(value: object) -> str:
    """Escape user-derived text before embedding in XML-style prompts."""
    return escape("" if value is None else str(value), quote=False)


def tagged(name: str, value: object) -> str:
    return f"<{name}>{xml_text(value)}</{name}>"
