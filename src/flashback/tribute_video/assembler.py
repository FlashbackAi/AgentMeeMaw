"""Assemble a storybook-video Book from the Father's Day tribute flow.

A loved one tells the subject's life + greatness: an opener ("Meet my
{relationship}"), {n} memory beats (8-10 word lines + art direction), and a
closing conclusion. Inputs come from the FD flow -- theme-tagged moments, the
contributor's message (the emotional climax), and the archetype answers (leads).
Big-LLM via the shared call_with_tool; falls back to title-derived beats so a
render can always proceed.

Separate from ``flashback.tribute.assembly`` (which emits the longer
``TributeScript`` for the Node renderer): this theme wants short, self-contained
lines + an opener/closing, so a focused assembler is clearer than overloading
that schema.
"""
from __future__ import annotations

from typing import Any

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

from .book import Beat, Book

log = structlog.get_logger("flashback.tribute_video.assembler")

_SYSTEM = """\
You are someone in the family who loved this person -- a child, a grandchild --
telling the story of their life and quiet greatness to a reader who never met
them. You receive the subject (with their family relationship), their world, the
candidate memories (each an id + a short memory), the archetype LEADS the family
already shared, and -- when present -- the contributor's own message. Build a
book: an OPENING page, exactly {n} memory pages, and a CLOSING page.

VOICE -- a loved one speaking, warm and proud:
Speak about the subject as "he"/"she" or by name. You MAY use "we/our/us/my"
sparingly to make it personal, but keep the spotlight on THEM and their
greatness. Tender, admiring, plain-spoken, true. Never an encyclopedia.

THE OPENING -- "Meet my {relationship}, ...": one warm sentence naming them and,
in a breath, who they were and why they mattered (~8-16 words). A dedication,
not a memory.

EACH MEMORY PAGE -- one line, 8-10 words:
- One short, COMPLETE sentence (8 to 10 words; count them). Never a cryptic
  fragment -- a stranger must understand AND feel it.
- End on the turn (the word that lands the feeling goes LAST). One concrete
  thing carries the page. Show it; never name the emotion. No clichés. Specific
  to THIS person and THIS memory; never invent facts.

THE CLOSING -- one sentence landing the whole life in a breath (~8-16 words).
If a contributor message is present, it is the emotional climax shown on its own
page just before this closing -- let the closing follow naturally from it; do
NOT quote the message in a beat.

LEADS: the archetype answers are context for what to look for and how to open --
weave them in only where a real memory supports them. They are NOT facts to
state; never put a bare lead on a page.

LOGICAL FLOW: choose the {n} most vivid, distinct memories; drop weak/redundant
ones. Order them as one connected life arc (early life + work -> family +
character -> the late years) so each page follows from the one before.

ART DIRECTION (every page incl. opener + closing): a vivid VISUAL brief -- what
we SEE: the action, the ONE concrete object, the place, the time of day, the
light (~20-35 words, grounded in that beat). Paint it; don't restate the line.
NEVER a face or recognizable likeness -- render figures from behind, at a
distance, or implied (hands, silhouette, the thing they are doing).

Also give a `cover_title` (2-6 words, Title Case). Call `compose_book` once.
"""

_PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "line": {"type": "string", "minLength": 1, "maxLength": 120},
        "art_direction": {"type": "string", "maxLength": 340},
    },
    "required": ["line", "art_direction"],
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
                        "art_direction": {"type": "string", "maxLength": 340},
                    },
                    "required": ["moment_id", "line", "art_direction"],
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
                  message_text: str, archetype_leads: list[str]) -> str:
    rel = f' relationship="{_xml(relationship)}"' if relationship else ""
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
    return (
        f"<subject{rel}>{_xml(subject_name)}</subject>\n"
        f"{gt_block}{msg_block}{leads_block}"
        f"<memories>\n" + "\n".join(blocks) + "\n</memories>"
    )


def _beat(raw: dict[str, Any]) -> Beat:
    return Beat(
        line=(raw.get("line") or "").strip(),
        art_direction=(raw.get("art_direction") or "").strip(),
        moment_id=(raw.get("moment_id") or "").strip(),
    )


def _fallback(candidates: list[dict[str, Any]], *, message_text: str,
              n_pages: int) -> Book:
    chosen = [c for c in candidates if c.get("id")][:n_pages] or [{"id": ""}]
    beats = [
        Beat(line=(c.get("title") or "A memory").strip(),
             art_direction=(c.get("narrative") or c.get("title") or "").strip(),
             moment_id=c.get("id", ""))
        for c in chosen
    ]
    return Book(
        cover_title="",
        opener=Beat(line="", art_direction=""),
        beats=beats,
        closing=Beat(line="", art_direction=""),
        message=(message_text or "").strip(),
    )


async def assemble_storybook_video(
    *,
    settings,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    candidates: list[dict[str, Any]],
    message_text: str = "",
    archetype_leads: list[str] | None = None,
    n_pages: int = 15,
) -> Book:
    usable = [c for c in candidates if c.get("id")]
    if not usable or settings is None:
        return _fallback(usable, message_text=message_text, n_pages=n_pages)

    by_id = {c["id"] for c in usable}
    user = _user_message(
        subject_name=subject_name, relationship=relationship,
        gt_context=gt_context, candidates=usable, message_text=message_text,
        archetype_leads=archetype_leads or [])
    system = _SYSTEM.replace("{n}", str(n_pages)).replace(
        "{relationship}", relationship or "grandfather")
    try:
        args = await call_with_tool(
            provider=settings.llm_big_provider,
            model=settings.llm_big_model,
            system_prompt=system,
            user_message=user,
            tool=_TOOL,
            max_tokens=5000,
            timeout=60.0,
            settings=settings,
        )
    except LLMError as exc:
        log.warning("storybook_video.assembly_failed", error=str(exc))
        return _fallback(usable, message_text=message_text, n_pages=n_pages)
    except Exception as exc:  # defensive
        log.warning("storybook_video.assembly_unexpected",
                    error_type=type(exc).__name__, detail=str(exc))
        return _fallback(usable, message_text=message_text, n_pages=n_pages)

    if not isinstance(args, dict):
        return _fallback(usable, message_text=message_text, n_pages=n_pages)
    beats: list[Beat] = []
    for raw in (args.get("beats") or [])[:n_pages]:
        if not isinstance(raw, dict):
            continue
        b = _beat(raw)
        if b.moment_id in by_id and b.line:
            beats.append(b)
    if not beats:
        return _fallback(usable, message_text=message_text, n_pages=n_pages)
    return Book(
        cover_title=(args.get("cover_title") or "").strip(),
        opener=_beat(args.get("opener") or {}),
        beats=beats,
        closing=_beat(args.get("closing") or {}),
        message=(message_text or "").strip(),
    )
