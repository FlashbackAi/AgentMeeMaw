"""Shared page-render core: the Gemini artist + Pillow compositing primitives.

Extracted from ``tribute_video`` (spec 2026-06-29 §4) so the tribute and the
storybook pipelines build on the same battle-tested pieces. ``tribute_video``
keeps back-compat shims; new code imports from here.
"""
from flashback.page_render.art import Artist, GeminiError  # noqa: F401
