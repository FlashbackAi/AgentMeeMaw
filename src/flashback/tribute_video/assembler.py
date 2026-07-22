"""Assemble a storybook-video Book from a tribute flow.

Someone who knew the subject tells their story: an opener, {n} memory beats
(8-10 word lines + art direction), and a closing. Inputs come from the flow --
theme-tagged moments, the contributor's message (the emotional climax), and the
archetype answers (leads). Big-LLM via the shared call_with_tool; falls back to
title-derived beats so a render can always proceed.

The NARRATIVE FRAMING -- who the piece speaks to, how the pages are ordered,
and what it's ultimately about -- is authored per relationship in the CRM
(``ProfileConfig.narrative`` -> composed ``narrative_block``), NOT coded per
occasion. When no framing is authored, ``_DEFAULT_FRAMING`` reproduces the
original memorial "tell a stranger the arc of a life" behavior, so a new
occasion is pure config and existing renders are unchanged.

Separate from ``flashback.tribute.assembly`` (which emits the longer
``TributeScript`` for the Node renderer): this theme wants short, self-contained
lines + an opener/closing, so a focused assembler is clearer than overloading
that schema.
"""
from __future__ import annotations

from typing import Any

import structlog

from flashback.artifacts.people import figure_noun
from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

from .book import Beat, Book

log = structlog.get_logger("flashback.tribute_video.assembler")

_DEFAULT_VOICE_SLOT = """\
VOICE -- a loved one speaking, warm and proud:
Speak about the subject as "he"/"she" or by name. You MAY use "we/our/us/my"
sparingly to make it personal. Tender, admiring, plain-spoken, true. Never an
encyclopedia. Follow the FRAMING below for who you are speaking to."""

# Memorial default: reproduces the original hard-coded Father's Day framing so
# an un-authored profile / pre-0046 snapshot renders exactly as before.
_DEFAULT_FRAMING = """\
FRAMING -- who this is for and how it's shaped:
- AUDIENCE: a reader who never met them; keep the spotlight on the subject.
- ARC: one connected life arc -- early life + work, then family + character, then the late years.
- THROUGHLINE: their life and quiet greatness."""

_DEFAULT_OPENER_SLOT = """\
THE OPENING -- "Meet my {relationship}, ...": one warm sentence naming them and,
in a breath, who they were and why they mattered (~8-16 words). A dedication,
not a memory."""


def _voice_slot(voice_block: str | None) -> str:
    """CRM-composed register wrapped in the non-negotiable voice guardrails.

    Audience/spotlight framing lives in the FRAMING slot now (relationship-
    authored), so the voice slot is purely HOW to speak, not WHO to."""
    if not voice_block or not voice_block.strip():
        return _DEFAULT_VOICE_SLOT
    return (
        "VOICE:\n"
        + voice_block.strip()
        + "\nPlain-spoken, true; never an encyclopedia. Follow the FRAMING "
        "below for who you are speaking to and about."
    )


def _framing_slot(narrative_block: str | None) -> str:
    """Relationship-authored FRAMING, or the memorial default when unset."""
    if not narrative_block or not narrative_block.strip():
        return _DEFAULT_FRAMING
    return narrative_block.strip()


def _opener_slot(opener_style: str | None) -> str:
    if not opener_style or not opener_style.strip():
        return _DEFAULT_OPENER_SLOT
    return (
        "THE OPENING -- "
        + opener_style.strip()
        + " One sentence (~8-16 words) that names them. Not a memory."
    )


def _art_mood_slot(art_mood: str | None) -> str:
    if not art_mood or not art_mood.strip():
        return ""
    return "\n" + art_mood.strip() + " Ground every page's brief in this mood."


_SYSTEM = """\
{voice_slot}

{framing_slot}

You receive the subject (with their relationship to the teller), their world,
the candidate memories (each an id + a short memory), the archetype LEADS
already shared, and -- when present -- the contributor's own message. Build a
book: an OPENING page, exactly {n} memory pages, and a CLOSING page. Everything
below serves the FRAMING above -- who this speaks to, how it's ordered, and
what it's about.

{opener_slot}

EACH MEMORY PAGE -- one line, 8-10 words:
- One short, COMPLETE sentence (8 to 10 words; count them). Never a cryptic
  fragment -- a stranger must understand AND feel it.
- End on the turn (the word that lands the feeling goes LAST). One concrete
  thing carries the page. Show it; never name the emotion. No clichés. Specific
  to THIS person and THIS memory; never invent facts.

EACH PAGE ALSO GETS A `display` -- 2-4 words:
The video renders some pages as BOLD TYPOGRAPHY (a giant headline, a
handwritten scrapbook caption, art showing through one huge word), and those
pages show the `display`, not the line. Distill the line's core into 2-4
punchy words that stand alone ("Always In Motion", "Chai At Dawn", "Her
Quick Smile"). Concrete and specific -- never generic filler like "A Memory"
or "Good Times". The first display word should be strong (it may render
alone at giant size) -- never "The"/"A"/"She"/"He".

THE CLOSING -- one sentence that lands the THROUGHLINE (see FRAMING) in a breath
(~8-16 words). If a contributor message is present, it is the emotional climax
shown on its own page just before this closing -- let the closing follow
naturally from it; do NOT quote the message in a beat.

LEADS: the archetype answers are context for what to look for and how to open --
weave them in only where a real memory supports them. They are NOT facts to
state; never put a bare lead on a page.

FAMILY EDIT REQUESTS: when <family_edit_requests> is present, the family saw a
draft and asked for these adjustments. Honor them throughout -- tone, emphasis,
which memories to feature or downplay, and the feel of the ART DIRECTION --
while keeping every rule above. Later requests override earlier ones on
conflict. Never invent facts to satisfy a request; if a request asks for
something the memories don't support, lean the existing material toward its
spirit rather than fabricating.

LOGICAL FLOW: choose the {n} most vivid, distinct memories; drop weak/redundant
ones. Order them along the ARC described in FRAMING so each page follows from
the one before.

ART DIRECTION (every page incl. opener + closing): a vivid VISUAL brief -- what
we SEE: the action, the ONE concrete object, the place, the time of day, the
light (~20-35 words, grounded in that beat). Paint it; don't restate the line.{art_mood_slot}
NEVER a face or recognizable likeness -- render figures from behind, at a
distance, or implied (hands, silhouette, the thing they are doing). When the
subject's gender is known (the `<subject>` tag's `gender` attribute), depict
them with the matching gendered noun ("a man"/"a woman"); otherwise render
figures neutrally.

Also give a `cover_title` (2-6 words, Title Case). Call `compose_book` once.
"""

_PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "line": {"type": "string", "minLength": 1, "maxLength": 120},
        "display": {"type": "string", "minLength": 1, "maxLength": 32},
        "art_direction": {"type": "string", "maxLength": 340},
    },
    "required": ["line", "display", "art_direction"],
    "additionalProperties": False,
}

_TOOL = ToolSpec(
    name="compose_book",
    description="Return opener + ordered memory beats + closing. Once.",
    input_schema={
        "type": "object",
        "properties": {
            "cover_title": {"type": "string", "maxLength": 60},
            "opener": _PAGE_SCHEMA,
            "beats": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "moment_id": {"type": "string"},
                        "line": {"type": "string", "minLength": 1, "maxLength": 95},
                        "display": {"type": "string", "minLength": 1, "maxLength": 32},
                        "art_direction": {"type": "string", "maxLength": 340},
                    },
                    "required": ["moment_id", "line", "display", "art_direction"],
                    "additionalProperties": False,
                },
            },
            "closing": _PAGE_SCHEMA,
        },
        "required": ["opener", "beats", "closing"],
        "additionalProperties": False,
    },
)


def _xml(s: str) -> str:
    return xml_text(s or "")


def _user_message(*, subject_name: str, relationship: str | None,
                  gt_context: str, candidates: list[dict[str, Any]],
                  message_text: str, archetype_leads: list[str],
                  edit_instructions: list[str],
                  subject_gender: str | None = None) -> str:
    rel = f' relationship="{_xml(relationship)}"' if relationship else ""
    fig = figure_noun(subject_gender)
    gender_attr = f' gender="{_xml(fig)}"' if fig else ""
    blocks = []
    for m in candidates:
        body = (m.get("narrative") or m.get("title") or "").strip()
        sens = (m.get("sensory_details") or "").strip()
        extra = f"\n<sensory>{_xml(sens)}</sensory>" if sens else ""
        blocks.append(
            f'<memory id="{_xml(m["id"])}">\n'
            f"<title>{_xml((m.get('title') or '').strip())}</title>\n"
            f"<text>{_xml(body)}</text>{extra}\n</memory>")
    gt_block = f"<subject_world>{_xml(gt_context)}</subject_world>\n" if gt_context else ""
    msg = (message_text or "").strip()
    msg_block = (f"<message>{_xml(msg)}</message>\n" if msg
                 else "<message present=\"false\"/>\n")
    leads = [l for l in (archetype_leads or []) if l and l.strip()]
    leads_block = (
        "<leads>\n" + "\n".join(f"<lead>{_xml(l)}</lead>" for l in leads) + "\n</leads>\n"
        if leads else ""
    )
    edits = [e for e in (edit_instructions or []) if e and e.strip()]
    edits_block = (
        "<family_edit_requests>\n"
        + "\n".join(f"<request>{_xml(e)}</request>" for e in edits)
        + "\n</family_edit_requests>\n"
        if edits else ""
    )
    return (
        f"<subject{rel}{gender_attr}>{_xml(subject_name)}</subject>\n"
        f"{gt_block}{msg_block}{leads_block}{edits_block}"
        f"<memories>\n" + "\n".join(blocks) + "\n</memories>"
    )


def _beat(raw: dict[str, Any]) -> Beat:
    return Beat(
        line=(raw.get("line") or "").strip(),
        art_direction=(raw.get("art_direction") or "").strip(),
        moment_id=(raw.get("moment_id") or "").strip(),
        display=(raw.get("display") or "").strip(),
    )


class _SafeDict(dict):
    """format_map mapping that collapses unknown placeholders to ''."""

    def __missing__(self, key: str) -> str:  # noqa: D105
        return ""


def _fill_template(template: str, *, name: str, relationship: str) -> str:
    return template.format_map(_SafeDict(name=name, relationship=relationship))


def _fallback(candidates: list[dict[str, Any]], *, subject_name: str,
              relationship: str | None, message_text: str,
              n_pages: int, fallback_opener: str = "",
              fallback_closing: str = "") -> Book:
    """Title-derived degraded book. Every page still carries TEXT: an empty
    opener/closing line renders as an image-only page, which reads as a broken
    video rather than a plain one. Profile-authored {name}/{relationship}
    templates keep even the degraded book in the right register."""
    chosen = [c for c in candidates if c.get("id")][:n_pages] or [{"id": ""}]
    beats = [
        Beat(line=(c.get("title") or "A memory").strip(),
             art_direction=(c.get("narrative") or c.get("title") or "").strip(),
             moment_id=c.get("id", ""))
        for c in chosen
    ]
    name = (subject_name or "").strip() or "someone we love"
    rel = (relationship or "").strip()
    if fallback_opener.strip():
        opener_line = _fill_template(
            fallback_opener, name=name, relationship=rel.lower())
    else:
        opener_line = (f"Meet my {rel.lower()}, {name}." if rel
                       else f"This is the story of {name}.")
    if fallback_closing.strip():
        closing_line = _fill_template(
            fallback_closing, name=name, relationship=rel.lower())
    else:
        closing_line = f"Thank you for everything, {name}."
    return Book(
        cover_title=f"The Story of {name}"[:60],
        opener=Beat(line=opener_line, art_direction=""),
        beats=beats,
        closing=Beat(line=closing_line, art_direction=""),
        message=(message_text or "").strip(),
    )


async def assemble_storybook_video(
    *,
    settings,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    candidates: list[dict[str, Any]],
    subject_gender: str | None = None,
    message_text: str = "",
    archetype_leads: list[str] | None = None,
    edit_instructions: list[str] | None = None,
    n_pages: int = 15,
    voice_block: str | None = None,
    opener_style: str | None = None,
    art_mood: str | None = None,
    narrative_block: str | None = None,
    fallback_opener: str = "",
    fallback_closing: str = "",
    feature: str = "tribute_video",
) -> Book:
    usable = [c for c in candidates if c.get("id")]

    def fallback() -> Book:
        return _fallback(usable, subject_name=subject_name,
                         relationship=relationship, message_text=message_text,
                         n_pages=n_pages, fallback_opener=fallback_opener,
                         fallback_closing=fallback_closing)

    if not usable or settings is None:
        return fallback()

    by_id = {c["id"] for c in usable}
    user = _user_message(
        subject_name=subject_name, relationship=relationship,
        subject_gender=subject_gender,
        gt_context=gt_context, candidates=usable, message_text=message_text,
        archetype_leads=archetype_leads or [],
        edit_instructions=edit_instructions or [])
    system = (
        _SYSTEM
        .replace("{voice_slot}", _voice_slot(voice_block))
        .replace("{framing_slot}", _framing_slot(narrative_block))
        .replace("{opener_slot}", _opener_slot(opener_style))
        .replace("{art_mood_slot}", _art_mood_slot(art_mood))
        .replace("{n}", str(n_pages))
        .replace("{relationship}", relationship or "the subject")
    )
    if voice_block or opener_style or art_mood or narrative_block:
        # CRM opener examples carry {name}; ground them on THIS subject.
        system = system.replace("{name}", subject_name)
    try:
        args = await call_with_tool(
            provider=settings.llm_big_provider,
            model=settings.llm_big_model,
            system_prompt=system,
            user_message=user,
            tool=_TOOL,
            max_tokens=5000,
            # A full {n}-page book runs ~30s of generation on the big model;
            # 60s tripped LLMTimeout under load and silently shipped the
            # text-less fallback book. 120s matches the storybook assembler.
            timeout=120.0,
            settings=settings,
            feature=feature,
        )
    except LLMError as exc:
        log.warning("storybook_video.assembly_failed", error=str(exc))
        return fallback()
    except Exception as exc:  # defensive
        log.warning("storybook_video.assembly_unexpected",
                    error_type=type(exc).__name__, detail=str(exc))
        return fallback()

    if not isinstance(args, dict):
        return fallback()
    beats: list[Beat] = []
    for raw in (args.get("beats") or [])[:n_pages]:
        if not isinstance(raw, dict):
            continue
        b = _beat(raw)
        if b.moment_id in by_id and b.line:
            beats.append(b)
    if not beats:
        return fallback()
    return Book(
        cover_title=(args.get("cover_title") or "").strip(),
        opener=_beat(args.get("opener") or {}),
        beats=beats,
        closing=_beat(args.get("closing") or {}),
        message=(message_text or "").strip(),
    )
