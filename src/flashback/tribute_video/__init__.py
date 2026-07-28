"""Python-owned tribute video + PDF render.

Graduated from the spike at ``scripts/storybook_prototype``. Builds the
watercolour storybook pages (Gemini illustration + 8-10 word line composited
into a fixed template), then renders them to an MP4 (layered reveal + ken burns
+ ink-bleed transitions) and a print PDF. Consumed by the ``tribute_render``
worker; see ``docs/superpowers/specs/2026-06-20-tribute-video-pipeline-design.md``.
"""
