"""Collection-voiced book script assembly (the spike-validated narrative prompt).

One big-LLM call writes the whole picture book: a cover title + exactly
``PAGE_COUNT`` pages, each page either three comic-panel beats (grid) or one
flowing chapter caption (chapter). The prompt carries every rule the spike
validated against a real legacy:

  * ONE story with an arc — open inside the theme, cause-and-effect middle
    (never a list of anecdotes), close on a concrete IMAGE, not a moral.
  * A SIGNATURE visual motif unique to this book, chosen from the subject's
    own memories per the collection's ``signature_hint`` (never hardcoded).
  * Two-tier content safety — ``tone="gentle"`` books get the child-safety
    block (no alcohol, no danger, loss handled softly).
  * ``age_stage`` on every panel so the subject is drawn at the right age.
  * Quotes must be spoken by a named, present person; the subject is the
    THROUGHLINE on every page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec
from flashback.storybook.collections import PAGE_COUNT, Collection

log = structlog.get_logger("flashback.storybook.script")

AGE_STAGES = ("child", "young", "mid", "old")
PANEL_KINDS = ("speech", "caption", "none")

_TOOL = ToolSpec(
    name="comic",
    description="Return the illustrated storybook script.",
    input_schema={
        "type": "object",
        "properties": {
            "cover_title": {"type": "string", "maxLength": 60},
            "characters": {
                "type": "array",
                "maxItems": 4,
                "description": (
                    "Every person OTHER than the subject who recurs in the "
                    "story. 'appearance' is ONE stable, AGE-NEUTRAL visual "
                    "description (hair, face shape, build) reused whenever "
                    "they appear, at whatever age the scene states -- it "
                    "keeps them recognisable across panels and clearly "
                    "distinct from the subject."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": 60},
                        "who": {"type": "string", "maxLength": 80},
                        "appearance": {"type": "string", "maxLength": 200},
                    },
                    "required": ["name", "who", "appearance"],
                    "additionalProperties": False,
                },
            },
            "pages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "panels": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "scene": {"type": "string", "maxLength": 300},
                                    "text": {"type": "string", "maxLength": 220},
                                    "kind": {
                                        "type": "string",
                                        "enum": list(PANEL_KINDS),
                                    },
                                    "age_stage": {
                                        "type": "string",
                                        "enum": list(AGE_STAGES),
                                        "description": (
                                            "The SUBJECT's life stage in THIS "
                                            "panel, so he or she is drawn at "
                                            "the right age: 'child' (~10, "
                                            "their own childhood), 'young' "
                                            "(~30, early adulthood -- their "
                                            "own children still young), 'mid' "
                                            "(~60, children grown / "
                                            "grandparent), 'old' (~75, final "
                                            "years). Judge from the scene's "
                                            "time cues."
                                        ),
                                    },
                                },
                                "required": ["scene", "text", "kind", "age_stage"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["panels"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["cover_title", "characters", "pages"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True)
class Panel:
    scene: str
    text: str
    kind: str
    age_stage: str


@dataclass(frozen=True)
class BookPage:
    panels: list[Panel] = field(default_factory=list)


@dataclass(frozen=True)
class Character:
    """A recurring non-subject person with one stable, age-neutral look."""

    name: str
    who: str
    appearance: str


@dataclass(frozen=True)
class BookScript:
    cover_title: str
    pages: list[BookPage] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cover_title": self.cover_title,
            "characters": [
                {"name": c.name, "who": c.who, "appearance": c.appearance}
                for c in self.characters
            ],
            "pages": [
                {
                    "panels": [
                        {
                            "scene": p.scene,
                            "text": p.text,
                            "kind": p.kind,
                            "age_stage": p.age_stage,
                        }
                        for p in page.panels
                    ]
                }
                for page in self.pages
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BookScript":
        pages: list[BookPage] = []
        for page in d.get("pages") or []:
            panels: list[Panel] = []
            for p in page.get("panels") or []:
                kind = (p.get("kind") or "caption").strip()
                stage = (p.get("age_stage") or "mid").strip()
                if kind not in PANEL_KINDS:
                    raise ValueError(f"invalid panel kind: {kind!r}")
                if stage not in AGE_STAGES:
                    raise ValueError(f"invalid age_stage: {stage!r}")
                panels.append(
                    Panel(
                        scene=(p.get("scene") or "").strip(),
                        text=(p.get("text") or "").strip(),
                        kind=kind,
                        age_stage=stage,
                    )
                )
            pages.append(BookPage(panels=panels))
        # Stored scripts predating the roster load fine with no characters.
        characters = [
            Character(
                name=(c.get("name") or "").strip(),
                who=(c.get("who") or "").strip(),
                appearance=(c.get("appearance") or "").strip(),
            )
            for c in d.get("characters") or []
            if (c.get("name") or "").strip()
        ]
        return cls(
            cover_title=(d.get("cover_title") or "").strip(),
            pages=pages,
            characters=characters,
        )


def _text_rule(chapter: bool) -> str:
    if chapter:
        return (
            "Each page is ONE illustration with a SHORT narration caption "
            "(kind='caption', 1-2 sentences, at most ~35 words). Each page's "
            "caption must continue directly from the page before -- one "
            "flowing story read aloud, never a fresh disconnected entry. No "
            "dialogue."
        )
    return (
        "Treat the THREE panels on a page as BEATS OF ONE SCENE -- a setup, "
        "then what happens, then the feeling -- NOT three unrelated pictures. "
        "For each panel give 'scene' (the visual: who, action, place, time of "
        "day) and 'text'. Read top-to-bottom, a page's captions form ONE "
        "flowing 1-3 sentence narration of that single scene (kind='caption', "
        "<=18 words each); use kind='speech' (<=12 words) for at most ONE "
        "panel per page when a spoken line brings the moment alive; use "
        "kind='none' (text='') when a panel needs no words."
    )


_GENTLE_RULE = (
    "AUDIENCE = SMALL CHILDREN. Keep every page safe and warm for a little "
    "one:\n"
    "- NEVER show a child drinking toddy, alcohol, or any intoxicant. If a "
    "memory involves toddy, reframe it innocently (sitting and listening with "
    "the elders, sipping sweet toddy-WATER / palm juice) or leave that beat "
    "out.\n"
    "- NO danger, weapons, threat, or armed strangers (no militants, no fear "
    "in the home). If a memory carries that, omit it -- do not "
    "soften-and-keep.\n"
    "- Handle loss GENTLY and briefly. You may say someone 'grew old' or "
    "'was gone', once, softly -- never dwell, never frighten, no medical "
    "peril, no 'born too soon', no baby in danger. Prefer warmth over sorrow "
    "on every page.\n"
    "- When in doubt, choose the lighter, happier memory. A gentle book that "
    "leaves a heavy memory out is exactly right.\n\n"
)


def _sys_prompt(
    collection: Collection, name: str, rel: str | None, counts: list[int]
) -> str:
    chapter = collection.layout == "chapter"
    rel_part = f" ({rel})" if rel else ""
    gentle = _GENTLE_RULE if collection.tone == "gentle" else ""
    return (
        f"You are writing a children's PICTURE BOOK ('{collection.display}') "
        f"about {name}{rel_part} -- a keepsake the family will read aloud to "
        f"little ones, in {collection.voice}.\n\n"
        f"THIS IS ONE STORY, NOT A LIST OF ANECDOTES. A child must be able "
        f"to follow it from the first page to the last like a single bedtime "
        f"tale that feels like magic. Build a clear arc:\n"
        f"- OPEN by gently setting the scene and introducing {name} (and, "
        f"where it fits, the loved one through whose eyes we watch them) so "
        f"a young reader knows who everyone is before the story moves.\n"
        f"- Let the MIDDLE pages flow as ONE journey with CAUSE AND EFFECT, "
        f"not a list. Each page should feel like it happens BECAUSE of the "
        f"page before -- a small change carries forward (a lesson learned, a "
        f"bond deepening, a season or the years turning). A soft transition "
        f"word alone is not enough; the reader should feel the story MOVING, "
        f"not restarting. Never cut cold between unrelated events. In "
        f"particular, do NOT write a string of separate memories that merely "
        f"share a place or a person -- that is the LIST trap; each page must "
        f"add something the last one set up.\n"
        f"- CLOSE on a final IMAGE, not a moral. End on one concrete picture "
        f"the child can see -- a gesture, an object, a look between two "
        f"people -- that lets the feeling land on its own. Do NOT end by "
        f"summarizing the lesson ('all of it was his blessing', 'that was "
        f"who he was'); show, never explain.\n\n"
        f"This book's subject: {collection.theme_focus}\n\n"
        f"SIGNATURE IMAGE (unique to THIS book): from the memories, choose "
        f"ONE concrete recurring visual motif -- {collection.signature_hint} "
        f"-- and thread it through the story: introduce it early, let it "
        f"return, and if it fits, close on it. This is what makes THIS book "
        f"look and feel different from the others; never borrow another "
        f"book's iconic image.\n\n"
        + gentle
        + f"Use exactly {len(counts)} pages, panel counts per page: {counts}. "
        f"Choose ONE clear scene per page and develop it; do not cram many "
        f"unrelated events onto a page. {_text_rule(chapter)}\n\n"
        f"RULES:\n"
        f"- Introduce each person by name and who they are the first time "
        f"they appear, and keep the SAME few people recurring so the child "
        f"is never lost about who is who.\n"
        f"- Simple, warm, vivid language a child understands -- short "
        f"sentences, concrete details that carry the feeling.\n"
        f"- Open INSIDE the theme with a scene that could ONLY belong to "
        f"THIS book. Do NOT open with a generic introduction of the person "
        f"(no 'In <place> there lived a <occupation> named {name}') -- these "
        f"books are read side by side and must not share an opening. The "
        f"subject's work is the backdrop of their life, not the subject of "
        f"every book; only foreground it where the theme calls for it.\n"
        f"- {name} is the THROUGHLINE: they must appear and be central on "
        f"EVERY page. Never spend a page (or two) on ancestors, family "
        f"backstory, or other people without them -- if older history "
        f"matters, fold it into ONE sentence INSIDE a page that features "
        f"{name}; never give it a standalone page. The reader must never "
        f"lose sight of {name}.\n"
        f"- Every spoken line must be said BY a specific named person who is "
        f"present in that scene, and sound like something they would really "
        f"say in that moment. Do NOT drop in cute free-floating quotes that "
        f"no one is clearly saying, and do NOT dress narration up as "
        f"dialogue.\n"
        f"- Stay anchored to THESE memories; draw every page from the "
        f"candidate material and never invent facts beyond it.\n"
        f"- CAST: fill `characters` with every person other than {name} who "
        f"recurs in the story (at most 4): their name, who they are to "
        f"{name}, and ONE stable appearance (hair, face shape, build, "
        f"typical clothing) that must NOT mention age -- the same "
        f"description is reused whenever they appear, at whatever age that "
        f"scene states. This is what keeps them recognisable and clearly "
        f"distinct from {name} in the art.\n"
        f"- AGES: panels are illustrated INDEPENDENTLY, so every scene "
        f"description must state the apparent age of EVERY person present "
        f"('his son, now about seventeen', 'a girl of ten') -- an unstated "
        f"age WILL be drawn wrong. Keep each person's age identical across "
        f"all panels of the same event, keep it consistent with that "
        f"memory's when-label, and never give an event an age the memory "
        f"contradicts.\n"
        f"- On EVERY panel set `age_stage` to how old {name} is in that "
        f"scene so they are drawn at the right age (the story may span a "
        f"whole life): 'child' (~10, their own childhood), 'young' (~30, "
        f"early adulthood), 'mid' (~60, grandparent), 'old' (~75, their "
        f"final years). Judge from each memory's when-label AND from who "
        f"shares the scene: when {name}'s own child appears as a young "
        f"child or teenager, {name} is 'young', NOT 'mid'; 'mid' fits "
        f"scenes with grown children or grandchildren. Do not fall back to "
        f"one stage on every panel when the when-labels show the story "
        f"spans years.\n"
        f"- Page i must have EXACTLY counts[i] panels. Call `comic` once."
    )


def _when_attr(m: dict[str, Any]) -> str:
    """The memory's time label as a ``when`` attribute, where one is known.

    Prefers the extraction's ``life_period`` estimate ("Late teens / college
    entrance age"), falling back to the ``time_anchor`` fields. This is what
    lets the assembler place events on the timeline and state ages instead of
    guessing them.
    """
    label = (m.get("life_period") or "").strip()
    if not label:
        ta = m.get("time_anchor") or {}
        if isinstance(ta, dict):
            for k in ("life_period", "year", "decade", "era"):
                v = ta.get(k)
                if v is not None and str(v).strip():
                    label = str(v).strip()
                    break
    return f' when="{xml_text(label)}"' if label else ""


async def assemble_script(
    *,
    settings: Any,
    collection: Collection,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    moments: list[dict[str, Any]],
    edit_instructions: list[str] | None = None,
) -> BookScript:
    """Write the whole book (cover title + PAGE_COUNT pages) in one call.

    Raises LLMError on model failure or malformed output — the render worker
    treats that as transient and lets SQS redrive.
    """
    chapter = collection.layout == "chapter"
    counts = [1 if chapter else 3] * PAGE_COUNT
    name = xml_text(subject_name)
    blocks = "\n".join(
        f"<m{_when_attr(m)}><t>{xml_text(m.get('title') or '')}</t>"
        f"<n>{xml_text((m.get('narrative') or '')[:400])}</n></m>"
        for m in moments
    )
    edits = ""
    if edit_instructions:
        lines = "\n".join(f"  - {xml_text(e)}" for e in edit_instructions if e)
        edits = (
            f"\n<family_edit_requests>\n{lines}\n</family_edit_requests>\n"
            f"Honour every request above when reshaping the book."
        )
    user = (
        f'<subject rel="{xml_text(relationship or "")}">{name}</subject>\n'
        f"<world>{gt_context}</world>\n"
        f"<panel_counts>{counts}</panel_counts>\n"
        f"<memories>\n{blocks}\n</memories>{edits}"
    )
    args = await call_with_tool(
        provider=settings.llm_big_provider,
        model=settings.llm_big_model,
        system_prompt=_sys_prompt(collection, name, relationship, counts),
        user_message=user,
        tool=_TOOL,
        max_tokens=6000,
        timeout=120.0,
        settings=settings,
        feature="storybook_script",
    )
    try:
        script = BookScript.from_dict(args)
    except ValueError as exc:
        raise LLMError(f"comic tool returned invalid script: {exc}") from exc
    if len(script.pages) != PAGE_COUNT:
        raise LLMError(
            f"comic tool returned {len(script.pages)} pages, "
            f"expected {PAGE_COUNT}"
        )
    want = 1 if chapter else 3
    for i, page in enumerate(script.pages, 1):
        if len(page.panels) != want:
            raise LLMError(
                f"page {i} has {len(page.panels)} panels, expected {want}"
            )
    log.info(
        "storybook.script_assembled",
        collection=collection.slug,
        cover_title=script.cover_title[:60],
        pages=len(script.pages),
    )
    return script
