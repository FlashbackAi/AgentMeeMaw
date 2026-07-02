"""Back-compat shim: the Gemini artist moved to the shared page_render core.

Extracted per spec 2026-06-29 §4 so tribute + storybook render off one artist.
New code should import from ``flashback.page_render.art``.
"""
from flashback.page_render.art import (  # noqa: F401
    NEGATIVE,
    STYLE,
    Artist,
    GeminiError,
    build_prompt,
)
