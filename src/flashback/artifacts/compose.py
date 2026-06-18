"""Scene-art prompt composition for moments / entities / threads.

Portraits live in :mod:`flashback.profile_picture` because they have a
fixed compositional recipe. Scene artifacts share this helper: take the
LLM-emitted base ``generation_prompt`` already stored on the row, layer
cumulative user edit instructions on top (oldest first), then append the
active preset's stylistic modifier.

The base prompt was authored by the extraction worker (or thread
detector) under the RDR2 painterly-realism style instruction — we don't
re-emit it on edit, we just decorate it.
"""

from __future__ import annotations

from flashback.artifacts.presets import apply_preset

SCENE_NEGATIVE_PROMPT = (
    "flat cartoon shading, cel-shaded anime, Pixar 3D look, exaggerated "
    "cartoon proportions, plastic surfaces, hyperrealistic photograph, "
    "harsh digital sharpening, deepfake likeness of a real specific "
    "living person, visible faces of named subjects, text, watermark, "
    "signature, blurry, low quality, distorted, uncanny"
)

# Cover-only negative for the tribute prime-years portrait. The contributor
# uploads a photo of the consented subject and asks us to stylize it, so the
# no-likeness / no-visible-faces bans are intentionally DROPPED here -- a scoped
# exception to CLAUDE.md §1/§3, cover only. Page/scene art keeps the full
# SCENE_NEGATIVE_PROMPT (likeness ban intact).
COVER_PORTRAIT_NEGATIVE_PROMPT = (
    "flat cartoon shading, cel-shaded anime, Pixar 3D look, exaggerated "
    "cartoon proportions, plastic surfaces, hyperrealistic photograph, harsh "
    "digital sharpening, text, watermark, signature, blurry, low quality, "
    "distorted, uncanny"
)


def compose_scene_prompt(
    *,
    base_prompt: str,
    prior_instructions: list[str] | None = None,
    instructions: str | list[str] | None = None,
    preset: str | None = None,
    ground_truth_context: str | None = None,
) -> str:
    """Compose a scene prompt for moment / entity / thread artifact regen + edit.

    ``base_prompt`` is the LLM-emitted ``generation_prompt`` already on the row.
    ``prior_instructions`` carries the cumulative edit history Node tracks per
    record in its Dynamo edit-log, oldest first. ``instructions`` is the newest
    edit (None for a plain regenerate). ``preset`` selects a stylistic modifier
    via :func:`flashback.artifacts.presets.apply_preset`.

    Empty / whitespace-only instructions are dropped silently. Unknown preset
    slugs raise ``ValueError``.
    """
    parts: list[str] = []
    base = (base_prompt or "").strip()
    if base:
        parts.append(base)
    for fragment in _normalize_instructions(prior_instructions):
        parts.append(fragment)
    for fragment in _normalize_instructions(instructions):
        parts.append(fragment)
    if ground_truth_context and ground_truth_context.strip():
        # Subject-world grounding (region/era/setting), read at compose
        # time — grounds even old generic base prompts on regenerate.
        parts.append(ground_truth_context.strip())
    composed = ", ".join(parts)
    return apply_preset(composed, preset)


def _normalize_instructions(
    value: str | list[str] | None,
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            continue
        stripped = entry.strip()
        if stripped:
            out.append(stripped)
    return out
