"""Spike-local story assembly (Sonnet): a loving family member tells the story
of one person's life and greatness.

Produces an OPENING ("Meet my grandfather ..."), {n} content beats (one per
memory), and a CLOSING conclusion -- each with a short contextual line + a
painterly art-direction. Separate from flashback.tribute.assembly on purpose:
this theme wants short, self-contained, emotionally legible lines.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anthropic

from . import config

_SYSTEM = """\
You are someone in the family who loved this person -- a grandchild, a child --
telling the story of their life to a reader who never met them. You are showing
a stranger who this person was and the quiet greatness in him. You receive the
subject (with their family relationship), their world, and a set of candidate
memories (each an id + a short memory). Build a book: an OPENING page, exactly
{n} memory pages, and a CLOSING page.

VOICE -- a loved one speaking, warm and proud:
Speak about the subject as "he"/"him" or by name. You MAY use "we", "our",
"us", "my" sparingly to make it personal and present ("He never let us go
without."), but keep the spotlight on HIM and his greatness. Tender, admiring,
plain-spoken, true. Never address him as "you". Never sound like an
encyclopedia or a survey.

THE OPENING -- introduce him, invite the reader in:
One warm sentence in the spirit of "Meet my {relationship}, ..." -- name him,
say in one breath who he was and why he mattered. ~8-16 words. This is a
dedication, NOT a memory.

EACH MEMORY PAGE -- one line, 8-10 words:
- One short, COMPLETE sentence. 8 to 10 words -- count every word; if it runs to
  11+, cut words until it fits, but NEVER become a cryptic fragment. A stranger
  who knows nothing must understand it AND feel it. ("He sealed the poison away
  to keep the children safe.")
- End on the turn -- the word that lands the feeling goes LAST.
- One concrete thing carries the page (the buffalo's name, the coconut cup, the
  4 a.m. milk). Show it; never name the emotion ("so loving", "we cherished
  him"). Subtext over sentiment. No clichés, no greeting-card lines.
- Every line specific to THIS person and THIS memory. Never invent facts.

THE CLOSING -- the conclusion:
One sentence that lands his whole life in a breath -- what he left in the people
who loved him, the ache of his absence implied, not stated. ~8-16 words. NOT a
memory.

LOGICAL FLOW:
Choose the {n} most vivid, distinct memories; drop weak or redundant ones.
Order them as one connected life story -- roughly early life and work -> family
and character -> the late years -- so each page follows naturally from the one
before and the book builds. The opening sets him up; the closing pays him off.

EYEBROW (small-caps label above the line):
- Memory pages: an evocative 1-3 word label for the beat ("THE BUFFALO",
  "BEFORE DAWN"). NOT a section/summary header.
- The OPENING and CLOSING eyebrows MUST be empty strings -- those pages land
  clean, with no label.

ART DIRECTION (every page, including opening + closing):
A vivid VISUAL brief for the illustrator -- what we SEE. Name the action, the
ONE concrete object, the place, the time of day, the quality of light. ~20-35
words, grounded in that beat (for opening/closing, his world: his land, tools,
village, a quiet reflective scene). Paint it; don't restate the line. NEVER a
face or recognizable likeness -- render figures from behind, at a distance, or
implied (hands, silhouette, the thing they are doing).

Also give a `cover_title` (2-6 words, Title Case). Call `compose_book` once.
"""

_PAGE = {
    "type": "object",
    "properties": {
        "line": {"type": "string", "minLength": 1, "maxLength": 120},
        "art_direction": {"type": "string", "maxLength": 340},
        "eyebrow": {"type": "string", "maxLength": 40},
    },
    "required": ["line", "art_direction"],
    "additionalProperties": False,
}

_TOOL = {
    "name": "compose_book",
    "description": "Return opening + ordered memory pages + closing. Call once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cover_title": {"type": "string", "maxLength": 60},
            "opening": _PAGE,
            "beats": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "moment_id": {"type": "string"},
                        "eyebrow": {"type": "string", "maxLength": 40},
                        "line": {"type": "string", "minLength": 1, "maxLength": 95},
                        "art_direction": {"type": "string", "maxLength": 340},
                    },
                    "required": ["moment_id", "eyebrow", "line", "art_direction"],
                    "additionalProperties": False,
                },
            },
            "closing": _PAGE,
        },
        "required": ["opening", "beats", "closing"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class Beat:
    eyebrow: str
    line: str
    art_direction: str
    moment_id: str = ""


@dataclass(frozen=True)
class Book:
    cover_title: str
    opening: Beat
    beats: list[Beat]
    closing: Beat


def _xml(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _user_message(subject, moments: list[dict[str, Any]]) -> str:
    rel = f' relationship="{_xml(subject.relationship)}"' if subject.relationship else ""
    blocks = []
    for m in moments:
        body = (m.get("narrative") or m.get("title") or "").strip()
        sens = (m.get("sensory_details") or "").strip()
        extra = f"\n<sensory>{_xml(sens)}</sensory>" if sens else ""
        blocks.append(
            f'<memory id="{_xml(m["id"])}">\n'
            f"<title>{_xml((m.get('title') or '').strip())}</title>\n"
            f"<text>{_xml(body)}</text>{extra}\n</memory>"
        )
    gt = subject.scene_subject_context or ""
    gt_block = f"<subject_world>{_xml(gt)}</subject_world>\n" if gt else ""
    return (
        f"<subject{rel}>{_xml(subject.name)}</subject>\n"
        f"{gt_block}"
        f"<memories>\n" + "\n".join(blocks) + "\n</memories>"
    )


def _page(raw: dict[str, Any]) -> Beat:
    return Beat(
        eyebrow=(raw.get("eyebrow") or "").strip(),
        line=(raw.get("line") or "").strip(),
        art_direction=(raw.get("art_direction") or "").strip(),
        moment_id=(raw.get("moment_id") or "").strip(),
    )


def compose_book(subject, moments: list[dict[str, Any]], *, n_pages: int = 15) -> Book:
    client = anthropic.Anthropic(api_key=config.env("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model=config.STORY_MODEL,
        max_tokens=5000,
        system=_SYSTEM.replace("{n}", str(n_pages)).replace(
            "{relationship}", subject.relationship or "grandfather"
        ),
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "compose_book"},
        messages=[{"role": "user", "content": _user_message(subject, moments)}],
    )
    data: dict[str, Any] | None = None
    for block in resp.content:
        if block.type == "tool_use" and block.name == "compose_book":
            data = block.input
            break
    if not data:
        raise SystemExit("Sonnet did not return a compose_book tool call")

    by_id = {m["id"] for m in moments}
    beats: list[Beat] = []
    for raw in (data.get("beats") or [])[:n_pages]:
        b = _page(raw)
        if b.moment_id in by_id and b.line:
            beats.append(b)
    if not beats:
        raise SystemExit("compose_book returned no usable beats")
    return Book(
        cover_title=(data.get("cover_title") or "").strip(),
        opening=_page(data.get("opening") or {}),
        beats=beats,
        closing=_page(data.get("closing") or {}),
    )


def save_book(book: Book, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "cover_title": book.cover_title,
                "opening": book.opening.__dict__,
                "beats": [b.__dict__ for b in book.beats],
                "closing": book.closing.__dict__,
            },
            f, ensure_ascii=False, indent=2,
        )


def load_book(path: str) -> Book:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return Book(
        cover_title=d.get("cover_title", ""),
        opening=Beat(**d["opening"]),
        beats=[Beat(**b) for b in d["beats"]],
        closing=Beat(**d["closing"]),
    )
