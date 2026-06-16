"""Assemble an ordered tribute script from candidate scene moments.

Big-LLM (Sonnet) selects + orders the strongest moments and writes a
connected story arc: a concrete, self-explanatory 1-2 sentence caption per
scene that picks up the thread page to page, plus an opening + closing line.
The polished message is placed as the climax (just before the closing).
Best-effort: on any LLM failure, fall back to chronological order with
title-derived captions so a tribute can always be assembled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

log = structlog.get_logger("flashback.tribute.assembly")


@dataclass(frozen=True)
class Scene:
    moment_id: str
    caption: str


@dataclass(frozen=True)
class TributeScript:
    scenes: list[Scene]
    opening_caption: str
    closing_caption: str
    message_text: str  # placed as the climax, before the closing
    cover_title: str = ""  # short evocative book title for the storybook cover
    cover_prompt: str = ""  # dramatic establishing-scene concept for the cover image


_ASSEMBLY_SYSTEM = """\
You arrange a short tribute video/storybook from a contributor's memories
of a loved one. You receive candidate scenes (each an id + a short memory)
and the contributor's own closing message.

The pages must read as ONE connected story, like chapters -- not a pile of
disconnected captions. Find the through-line across the chosen memories
(what this person was like, what they cared about, how they made people
feel) and let every caption serve it.

Produce:
- An ordered subset of scenes (3 to {max_scenes}). Pick the most vivid,
  emotionally distinct moments; drop weak or redundant ones. Order them so
  the story builds -- not strictly chronological, but emotionally coherent,
  each page following naturally from the one before.
- A caption for each chosen scene: 2-4 warm sentences (about 30-50 words)
  that actually tell what happened on this page, so it reads as a real
  storybook beat and is self-explanatory on its own. Make the sequence
  cohere -- a caption may quietly pick up the thread of the previous page
  ("Even then...", "That same care showed up...") so the reader feels a
  continuous arc. Concrete and specific over abstract or poetic. Never
  invent facts; draw only on the scene's own memory text.
- An opening line that introduces who this person was and sets up the
  through-line (1-2 sentences). A closing line that lands the theme the
  pages built toward (1-2 sentences). Neither may invent facts.
- A short, evocative `cover_title` for the book cover (2-6 words, e.g.
  "A Quiet Builder", "The Long Way Home"). It names the through-line, not a
  literal event. Title Case, no ending punctuation.
- A `cover_prompt`: one vivid, atmospheric ESTABLISHING scene for the cover
  image -- dramatic light, a wide evocative setting drawn from the person's
  world (their era, places, the objects around them). It sets a mood; it is
  NOT a portrait. Describe a place/scene, never a face or a recognizable
  likeness of the person. Draw only on the memories provided.

The contributor's message is the climax -- you do NOT rewrite it; it is
inserted verbatim after the last scene and before your closing line.

Call the `assemble` tool exactly once.
"""

_ASSEMBLY_TOOL = ToolSpec(
    name="assemble",
    description="Return the ordered scenes + captions + opening/closing. Once.",
    input_schema={
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "moment_id": {"type": "string"},
                        "caption": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 340,
                        },
                    },
                    "required": ["moment_id", "caption"],
                    "additionalProperties": False,
                },
            },
            "opening_caption": {"type": "string", "maxLength": 240},
            "closing_caption": {"type": "string", "maxLength": 240},
            "cover_title": {"type": "string", "maxLength": 60},
            "cover_prompt": {"type": "string", "maxLength": 400},
        },
        "required": ["scenes", "opening_caption", "closing_caption"],
        "additionalProperties": False,
    },
)


def _fallback_script(
    candidates: list[dict[str, Any]], *, message_text: str, max_scenes: int
) -> TributeScript:
    chosen = candidates[:max_scenes]
    scenes = [
        Scene(moment_id=c["id"], caption=(c.get("title") or "A memory").strip())
        for c in chosen
    ]
    return TributeScript(
        scenes=scenes,
        opening_caption="",
        closing_caption="",
        message_text=message_text,
    )


async def assemble_tribute_script(
    *,
    settings,
    candidates: list[dict[str, Any]],
    message_text: str,
    person_name: str,
    person_relationship: str | None,
    max_scenes: int,
) -> TributeScript:
    """Return an assembled script. Falls back to chronological on failure."""
    usable = [c for c in candidates if c.get("id")]
    if not usable:
        return TributeScript([], "", "", message_text)
    if settings is None:
        return _fallback_script(
            usable, message_text=message_text, max_scenes=max_scenes
        )

    by_id = {c["id"]: c for c in usable}
    scene_blocks = "\n".join(
        f'<scene id="{xml_text(c["id"])}">'
        f"{xml_text((c.get('narrative') or c.get('title') or '').strip())}"
        f"</scene>"
        for c in usable
    )
    rel = (
        f' relationship="{xml_text(person_relationship)}"'
        if person_relationship
        else ""
    )
    user_block = (
        f"<subject{rel}>{xml_text(person_name)}</subject>\n"
        f"<message>{xml_text(message_text)}</message>\n"
        f"<candidate_scenes>\n{scene_blocks}\n</candidate_scenes>"
    )

    try:
        args = await call_with_tool(
            provider=settings.llm_big_provider,
            model=settings.llm_big_model,
            system_prompt=_ASSEMBLY_SYSTEM.replace("{max_scenes}", str(max_scenes)),
            user_message=user_block,
            tool=_ASSEMBLY_TOOL,
            max_tokens=1500,
            timeout=30.0,
            settings=settings,
        )
    except LLMError as exc:
        log.warning("tribute_assembly.llm_failed", error=str(exc))
        return _fallback_script(
            usable, message_text=message_text, max_scenes=max_scenes
        )
    except Exception as exc:  # defensive
        log.warning(
            "tribute_assembly.unexpected_failure",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return _fallback_script(
            usable, message_text=message_text, max_scenes=max_scenes
        )

    raw_scenes = args.get("scenes") if isinstance(args, dict) else None
    if not isinstance(raw_scenes, list) or not raw_scenes:
        return _fallback_script(
            usable, message_text=message_text, max_scenes=max_scenes
        )

    scenes: list[Scene] = []
    for raw in raw_scenes[:max_scenes]:
        if not isinstance(raw, dict):
            continue
        mid = raw.get("moment_id")
        caption = (raw.get("caption") or "").strip()
        if mid in by_id and caption:
            scenes.append(Scene(moment_id=mid, caption=caption))
    if not scenes:
        return _fallback_script(
            usable, message_text=message_text, max_scenes=max_scenes
        )

    return TributeScript(
        scenes=scenes,
        opening_caption=(args.get("opening_caption") or "").strip(),
        closing_caption=(args.get("closing_caption") or "").strip(),
        message_text=message_text,
        cover_title=(args.get("cover_title") or "").strip(),
        cover_prompt=(args.get("cover_prompt") or "").strip(),
    )
