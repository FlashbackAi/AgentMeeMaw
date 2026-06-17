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
    accent: str = ""  # short chapter eyebrow / scene label
    pull_quote: str = ""  # optional <=12-word quotable line, often empty
    layout: str = ""  # optional "spread" | "hero" | "quote" treatment


@dataclass(frozen=True)
class TributeScript:
    scenes: list[Scene]
    opening_caption: str
    closing_caption: str
    message_text: str  # placed as the climax, before the closing
    cover_title: str = ""  # short evocative book title for the storybook cover
    cover_prompt: str = ""  # dramatic establishing-scene concept for the cover image


_ASSEMBLY_SYSTEM = """\
You compose a tribute storybook/video that a contributor is GIVING to a
loved one as a gift -- a keepsake of the person it is about. You receive
candidate scenes (each an id + a short memory), the subject (the person
this is about, with their relationship to the contributor, e.g. "father"),
and -- when present -- the contributor's own closing message.

This is not an archive or a biography. It is a gift, made by hand, from one
person to someone they love. It must FEEL that way: warm, intimate, and
emotionally true. Imagine the contributor placing this book in their loved
one's hands. Find the through-line across the chosen memories (who this
person was, what they cared about, how they made people feel) and let every
page serve it.

Voice -- hybrid, and this matters:
- The opening line and the closing line are spoken DIRECTLY TO the subject,
  in second person, as the contributor's own voice -- a dedication and a
  goodbye. Use "you". Address them as the relationship implies (a daughter
  to her "father" speaks the way a daughter would). These two lines carry
  the gift's emotional weight; let them be tender and personal, never
  generic ("To the man who...", "You taught me...", "I still see you...").
- The page captions narrate the memories in warm, close THIRD person ("he",
  "she", "they") -- you are showing the loved one these scenes, not lecturing
  them. Intimate and observed, like someone who was there and remembers it
  fondly.

Produce:
- An ordered subset of scenes (3 to {max_scenes}). Pick the most vivid,
  emotionally distinct moments; drop weak or redundant ones. Order them so
  the story builds -- not strictly chronological, but emotionally coherent,
  each page following naturally from the one before.
- A caption for each chosen scene: 1-3 rich sentences (about 40-80 words)
  that actually tell what happened on this page, so it reads as a real
  storybook beat and is self-explanatory on its own. Write with texture and
  warmth -- the page renders the text in a designed editorial layout, so it
  has room to breathe; don't clip it to a bare line, but don't pad past 80
  words either. STRONG page-to-page continuity is essential: every caption
  after the first must quietly pick up the emotional thread of the page
  before it ("Even then...", "That same patience...", "Years later, the same
  hands..."), so the book reads as one unbroken arc rather than separate
  entries. Concrete and specific over abstract or poetic. Never invent facts;
  draw only on the scene's own memory text.
- An `accent` for each chosen scene: a short scene label / chapter eyebrow
  (2-6 words, no ending punctuation), e.g. "One · The Drop Ride" or "A theme
  park, dusk". Evocative shorthand for the beat, never a full sentence. Draw
  only on the scene's own memory.
- A `pull_quote` for a scene ONLY when the beat has a genuinely quotable,
  punchy line (<= 12 words) worth setting alone on its own page. Omit it on
  every scene that isn't truly quotable -- most scenes have none. Never
  invent it; it must be grounded in the scene's memory.
- A `layout` for a scene ONLY when a beat clearly wants a specific
  treatment: "hero" for the single most climactic beat, or "quote" for a
  beat whose pull_quote should stand alone. Leave it unset for ordinary
  beats -- the renderer alternates layouts on its own.
- An opening line: a dedication spoken directly to the subject (second
  person, 1-2 sentences) that names who they are to the contributor and
  opens the through-line -- the first thing they read when they open the
  gift. A closing line: spoken directly to the subject (second person, 1-2
  sentences) that lands the theme the pages built toward and reads as the
  contributor's parting words -- the last thing they read. Neither may
  invent facts.
- A short, evocative `cover_title` for the book cover (2-6 words, e.g.
  "A Quiet Builder", "The Long Way Home"). It names the through-line, not a
  literal event. Title Case, no ending punctuation.
- A `cover_prompt`: one vivid, atmospheric ESTABLISHING scene for the cover
  image -- dramatic light, a wide evocative setting drawn from the person's
  world (their era, places, the objects around them). It sets a mood; it is
  NOT a portrait. Describe a place/scene, never a face or a recognizable
  likeness of the person. Draw only on the memories provided.

If a contributor message is provided, it is the climax -- you do NOT rewrite
it; it is inserted verbatim after the last scene and before your closing
line, so your closing line should follow naturally from it. If no message is
provided, your closing line is the final word of the book; make it land.

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
                            "maxLength": 600,
                        },
                        "accent": {"type": "string", "maxLength": 60},
                        "pull_quote": {"type": "string", "maxLength": 90},
                        "layout": {
                            "type": "string",
                            "enum": ["spread", "hero", "quote"],
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
    msg = (message_text or "").strip()
    message_line = (
        f"<message>{xml_text(msg)}</message>\n"
        if msg
        else "<message present=\"false\"/>\n"
    )
    user_block = (
        f"<subject{rel}>{xml_text(person_name)}</subject>\n"
        f"{message_line}"
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
            layout = (raw.get("layout") or "").strip().lower()
            if layout not in ("spread", "hero", "quote"):
                layout = ""
            scenes.append(
                Scene(
                    moment_id=mid,
                    caption=caption,
                    accent=(raw.get("accent") or "").strip(),
                    pull_quote=(raw.get("pull_quote") or "").strip(),
                    layout=layout,
                )
            )
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
