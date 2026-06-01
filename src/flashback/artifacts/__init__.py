"""Shared infrastructure for image / video artifact prompts.

Used by the profile-picture path (portraits) and the moment / entity /
thread artifact-edit path (scenes). Defines the preset registry, the
scene compose helper, and the scene-side negative prompt.

Portraits stay in :mod:`flashback.profile_picture` because they have a
fixed compositional recipe; scenes share the helper here because their
base prompt is LLM-emitted and only needs to be layered with stacked
user instructions plus the active preset.
"""

from flashback.artifacts.compose import SCENE_NEGATIVE_PROMPT, compose_scene_prompt
from flashback.artifacts.context import build_generation_context
from flashback.artifacts.persistence import (
    write_latest_generation_context_async,
    write_latest_generation_context_sync,
)
from flashback.artifacts.presets import (
    DEFAULT_PRESET_SLUG,
    apply_preset,
    list_presets,
    resolve_preset,
)

__all__ = [
    "DEFAULT_PRESET_SLUG",
    "SCENE_NEGATIVE_PROMPT",
    "apply_preset",
    "build_generation_context",
    "compose_scene_prompt",
    "list_presets",
    "resolve_preset",
    "write_latest_generation_context_async",
    "write_latest_generation_context_sync",
]
