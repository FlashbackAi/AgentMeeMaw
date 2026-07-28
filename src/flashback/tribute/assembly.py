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


# Coarse life-stage ranking (0 = earliest). Matched as substrings against a
# moment's time_anchor.life_period / .era, lower-cased. Longer/more specific
# phrases are checked first so "young adult" beats "adult".
_LIFE_RANK_TERMS: tuple[tuple[str, int], ...] = (
    ("infancy", 0), ("infant", 0), ("newborn", 0), ("baby", 0),
    ("young adult", 3), ("college", 3), ("university", 3),
    ("middle age", 5), ("midlife", 5), ("mid-life", 5),
    ("later life", 6), ("old age", 6), ("retirement", 6),
    ("childhood", 1), ("child", 1), ("boyhood", 1), ("girlhood", 1),
    ("adolescence", 2), ("teenage", 2), ("teen", 2), ("youth", 2),
    ("twenties", 3),
    ("adulthood", 4), ("adult", 4), ("thirties", 4), ("forties", 4),
    ("fifties", 5), ("sixties", 6), ("elderly", 6), ("senior", 6),
)


def _life_rank(ta: dict[str, Any]) -> int | None:
    """A coarse 0-6 life-stage rank from a time_anchor's life_period / era."""
    text = " ".join(
        str(ta.get(k) or "") for k in ("life_period", "era")
    ).lower()
    if not text.strip():
        return None
    for term, rank in _LIFE_RANK_TERMS:
        if term in text:
            return rank
    return None


def _year_from_anchor(ta: dict[str, Any]) -> int | None:
    """A sortable year from a time_anchor's year, else its decade."""
    year = ta.get("year")
    if isinstance(year, int):
        return year
    if isinstance(year, str) and year.strip().isdigit():
        return int(year.strip())
    decade = ta.get("decade")
    if isinstance(decade, str):
        digits = "".join(ch for ch in decade if ch.isdigit())
        if len(digits) >= 4:
            return int(digits[:4])
    return None


def _when_label(ta: dict[str, Any]) -> str:
    """A short human time label for the LLM, most specific field first."""
    year = ta.get("year")
    if isinstance(year, (int, str)) and str(year).strip():
        return str(year).strip()
    for k in ("decade", "life_period", "era"):
        v = ta.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def chronological_sort(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order candidates along the subject's life so the assembler (and the
    fallback) start from a coherent spine instead of reverse-extraction order.

    Sort key: dated moments first, ordered by life stage then year; moments
    with only a year (no life stage) trail the staged ones by year; fully
    undated moments keep their original order at the tail. The subject's DOB
    is deliberately not stored, so absolute year and relative life stage can't
    be merged precisely -- this is a baseline hint; the assembler refines it
    with the per-scene ``when`` labels it also receives.
    """
    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
        idx, c = item
        ta = c.get("time_anchor") or {}
        rank = _life_rank(ta)
        year = _year_from_anchor(ta)
        undated = rank is None and year is None
        return (
            1 if undated else 0,
            rank if rank is not None else 7,
            year if year is not None else 9999,
            idx,
        )

    return [c for _, c in sorted(enumerate(candidates), key=key)]


def _scene_block(c: dict[str, Any]) -> str:
    """Render one candidate as a <scene> block, carrying its time label so the
    assembler can place it on the subject's timeline."""
    when = _when_label(c.get("time_anchor") or {})
    when_attr = f' when="{xml_text(when)}"' if when else ""
    body = xml_text((c.get("narrative") or c.get("title") or "").strip())
    return f'<scene id="{xml_text(c["id"])}"{when_attr}>{body}</scene>'


@dataclass(frozen=True)
class Scene:
    moment_id: str
    caption: str
    accent: str = ""  # short chapter eyebrow / scene label
    pull_quote: str = ""  # optional <=12-word quotable line, often empty
    layout: str = ""  # optional "spread" | "hero" | "quote" treatment
    art_direction: str = ""  # visual direction for THIS beat's image (drives the picture)


@dataclass(frozen=True)
class TributeScript:
    scenes: list[Scene]
    opening_caption: str
    closing_caption: str
    message_text: str  # placed as the climax, before the closing
    cover_title: str = ""  # short evocative book title for the storybook cover
    cover_prompt: str = ""  # dramatic establishing-scene concept for the cover image
    defining_phrase: str = ""  # cover line: who he IS at core (confession, brief §2.3)
    hero_line: str = ""  # story-gated "fork in the road" line (brief §2.4)
    tags: tuple[str, ...] = ()  # emotional tag slugs (storybook only; see storybook.tags)


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

Voice -- a letter, first person, spoken straight to them:
The whole book is the contributor speaking DIRECTLY TO the subject, in
first person. Use "I" for the contributor and "you" for the subject
everywhere -- the opening, every single page, and the closing. It reads
like a handwritten letter folded into a book of pictures: "You laughed the
whole way down while I gripped the bar." NEVER narrate in third person --
no "he", no "she", no "his father", never the contributor's name. Always
"you" and "I". When the contributor was not in a memory, still speak to the
subject directly ("You faced down a bear once, and never once told me you
were scared."). Warm, intimate, unguarded -- the things you'd only say to
someone you love.

Emotional impact -- this is the whole point, so earn it:
- End on the turn. Put the line that lands the feeling LAST, so the page
  closes on the ache, not on setup. The final clause should be the one they
  feel in their chest.
- One small concrete thing carries more than any summary -- the flour on
  your hands, the one extra block, the radio left on. Choose the tiny
  physical detail that holds the whole relationship and let it do the work.
- Trust subtext. Don't name the emotion ("it was so special", "I'll always
  cherish") -- show the thing and let them feel it. Restraint hits harder
  than sentiment; understate and let silence do the rest.
- Imply the loss without stating it. The tenderness comes from what's
  unspoken -- that this is being written down because it matters now.
- No clichés, no greeting-card lines, no abstractions ("you taught me so
  much", "forever in my heart"). Every line must be specific to THIS person
  and this memory, or cut it.

Produce:
- An ordered subset of scenes (3 to {max_scenes}). Pick the most vivid,
  emotionally distinct moments; drop weak or redundant ones. ORDER it like a
  film, not an archive. Anchor the spine in the subject's life chronology:
  each scene carries a `when` attribute (a year, a decade, or a life stage),
  and the candidates already arrive roughly in life order. Open on an
  establishing beat, build through the middle of their life, and let the most
  powerful beat land near the end. Deviate from chronological order ONLY for a
  deliberate dramatic effect (a brief flash-back or flash-forward that earns
  its place), never at random -- the reader, even one who never knew this
  person, should feel a clear beginning, a build, and a close, never a
  shuffle. Scenes without a `when` carry no fixed date: place them where they
  make the most narrative sense.
- A caption for each chosen scene: 1-2 SHORT sentences, about 15-35 words --
  no more. Say less and mean more: find the single truest image or feeling
  in the memory and let it land clean. Cut every word that merely explains,
  sets up, or pads -- the picture carries the scene, the words carry the
  heart. Aim for the line that would make them stop and feel it, not a full
  retelling. STRONG page-to-page continuity still matters: a caption may
  quietly pick up the thread of the one before ("Even then...", "You did
  that a lot..."), so the book reads as one unbroken arc. Concrete and
  specific over abstract or poetic. Never invent facts; draw only on the
  scene's own memory text.
- An `accent` for each chosen scene: a short scene label / chapter eyebrow
  (2-6 words, no ending punctuation), e.g. "One · The Drop Ride" or "A theme
  park, dusk". Evocative shorthand for the beat, never a full sentence. Draw
  only on the scene's own memory.
- An `art_direction` for each chosen scene (ALWAYS): a vivid VISUAL brief for
  the illustrator painting THIS beat -- what we actually SEE. Name the subject's
  action, the ONE concrete object that anchors the memory, the place, the time
  of day, and the emotional quality of the light (e.g. "a boy on his father's
  shoulders at dusk, reaching for a kite, low gold backlight, warmth and ache").
  ~20-40 words, concrete and grounded only in this scene's memory. It must PAINT
  the moment, not restate the caption. Never a face or a recognizable likeness
  of a specific person; render figures from behind, at distance, or implied.
- A `pull_quote` for a scene ONLY when the beat has a genuinely quotable,
  punchy line (<= 12 words) worth setting alone on its own page. Omit it on
  every scene that isn't truly quotable -- most scenes have none. Never
  invent it; it must be grounded in the scene's memory.
- A `layout` for a scene ONLY when a beat clearly wants a specific
  treatment: "hero" for the single most climactic beat, or "quote" for a
  beat whose pull_quote should stand alone. Leave it unset for ordinary
  beats -- the renderer alternates layouts on its own.
- An opening line: a short dedication, first person, spoken straight to the
  subject as "you" (1 sentence, ~15 words) -- the first thing they read when
  they open the gift, tender and unmistakably personal. A closing line:
  first person, to "you" (1 short sentence, ~12-20 words) that lands the
  whole book in one breath and reads as the contributor's parting words --
  the last thing they read. Make both ache a little. Neither may invent
  facts.
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


_CONFESSION_SYSTEM = """\
You compose a Father's Day "confession" storybook/video that a contributor is
sharing WITH THE WORLD about their father. You receive candidate scenes (each
an id + a short memory), the subject (the father, with relationship to the
contributor), and -- when present -- the contributor's own closing message.

This is not an archive or a biography. It is a child finally saying out loud
what they feel about their father -- the soft things most families never say.
Find the through-line across the chosen memories (what he had, what he gave,
the quiet cost) and let every page serve it.

Voice -- first person, ABOUT him, spoken to the world, the father is "he":
The narrator is the contributor speaking to a friend ABOUT their father. Use
"I" for the contributor and "he"/"him" for the father everywhere -- the
opening, every page, the closing. It reads like someone showing you a photo and
telling you who their father really is: "He sold the house he built with his
own hands." NEVER write it as a letter addressed TO the father ("you did...",
"you gave...") and NEVER third-person-detached with names ("Vinay's father...").
The ONE exception: you MAY turn and address him directly as "you" on the SINGLE
climax line -- the contributor's message page (or, when no message is present,
the very last scene). One direct-address spike, then the closing pulls back to
"he". Everywhere else: "he".

Emotional impact -- this is the whole point, so earn it:
- Two sentences, maximum impact. One or two SHORT sentences per scene -- think
  aphorism, not description. Set the thing he had against the thing he gave, and
  stop. Cut every word that merely explains, sets up, or pads.
- One small concrete thing carries more than any summary -- the plain cloth, the
  4 a.m. street, the unbought tea. Choose the tiny physical detail that holds
  the whole sacrifice and let it land.
- Trust subtext. Never name the emotion and NEVER use the word "sacrifice" --
  show the thing and let them feel it. Understatement hits hardest.
- No clichés, no greeting-card lines. Every line specific to THIS father and
  this memory, or cut it. Never invent facts beyond the scene's own memory.

Produce:
- An ordered subset of scenes (3 to {max_scenes}). Pick the most vivid,
  emotionally distinct moments; drop weak or redundant ones. ORDER it like a
  film, not an archive. Anchor the spine in his life chronology: each scene
  carries a `when` attribute (a year, a decade, or a life stage), and the
  candidates already arrive roughly in life order. Open on an establishing
  beat, build through the middle of his life, and let the most powerful beat
  land near the end. Deviate from chronological order ONLY for a deliberate
  dramatic effect (a brief flash-back or flash-forward that earns its place),
  never at random -- a listener who never knew him should feel a clear
  beginning, a build, and a close, never a shuffle. Scenes without a `when`
  carry no fixed date: place them where they make the most narrative sense.
- A `caption` for each chosen scene: 1-2 SHORT sentences, maximum impact (aim
  ~12-30 words, never more). Concrete and specific over abstract or poetic.
  Strong page-to-page continuity still matters. Never invent facts.
- An `accent` for each scene: a short scene label / chapter eyebrow (2-6 words,
  no ending punctuation), e.g. "One · The Sold House". Evocative shorthand for
  the beat, never a full sentence. Draw only on the scene's own memory.
- An `art_direction` for each scene (ALWAYS): a vivid VISUAL brief for the
  illustrator painting THIS beat -- what we actually SEE. Name his action, the
  ONE concrete object that holds the memory (the plain cloth, the 4 a.m. street,
  the half-built house), the place, the time of day, and the quality of the
  light (e.g. "a young man at a village tailor's choosing the cheapest bolt of
  cloth, lamplit dusk, quiet resolve"). ~20-40 words, concrete and grounded only
  in this scene's memory. It must PAINT the moment, not restate the caption.
  Never a face or recognizable likeness; render him from behind, at distance, or
  implied -- hands, silhouette, the thing he is doing.
- A `pull_quote` for a scene ONLY when the beat has a genuinely quotable, punchy
  line (<= 12 words). Omit it on most scenes. Never invent it.
- A `layout` for a scene ONLY when a beat clearly wants "hero" (the single most
  climactic beat) or "quote". Leave unset for ordinary beats.
- An opening line: first person, ABOUT him ("he"), 1 sentence (~15 words) -- the
  first thing the reader sees, tender and unmistakably personal. A closing line:
  first person, ABOUT him, 1 short sentence (~12-20 words) that lands the whole
  book in one breath. Make both ache a little. Neither may invent facts.
- A `cover_title` for the cover (2-6 words, Title Case, no ending punctuation).
  Names the through-line, not a literal event.
- A `cover_prompt`: one vivid, atmospheric ESTABLISHING scene for a fallback
  cover image -- a place/scene drawn from his world (era, places, objects),
  dramatic light. NOT a portrait, never a face or recognizable likeness. Draw
  only on the memories provided. (A prime-years portrait may be composited
  separately from an uploaded photo; this is only the fallback.)
- A `defining_phrase` (ALWAYS): one line for who he IS at his core, stripped of
  all the sacrifice -- the man, not his cost. Goes on the cover. <= 14 words,
  first person about him is fine ("A man who spent himself so we'd never have
  to.").
- A `hero_line` (STORY-GATED): a single "fork in the road" line -- what he could
  have been versus what he chose. Emit it ONLY when the candidate scenes reveal
  a CONCRETE given-up alternative (a sold home, abandoned land, a dropped degree,
  a trade walked away from, money he had but didn't spend on himself). Write it
  fresh, grounded in THIS father's specifics ("He could have owned half that
  valley. He traded it for a report card."). If the scenes do NOT clearly show a
  given-up path, leave hero_line EMPTY. Never force it; never use a generic
  template.

If a contributor message is provided, it is the climax -- you do NOT rewrite it;
it is inserted verbatim after the last scene and before your closing line, so
your closing line should follow naturally from it. If no message is provided,
your closing line is the final word of the book; make it land.

Call the `assemble` tool exactly once.
"""

# Appended to the system prompt ONLY when a tag catalog is supplied (storybook
# path). Tribute calls pass no catalog, so their prompt is unchanged.
_TAG_DIRECTIVE = """\

Emotional tags:
- A <emotional_tags> catalog is provided. Choose the 1-3 slugs that best
  capture the dominant emotional register of THIS set of memories and return
  them in `tags`, most dominant first. Use ONLY slugs that appear in the
  catalog; never invent one.
- Let the chosen register shape the TONE of every caption and the opening +
  closing lines -- a "happiness" book reads light and warm, a "grief" book
  quiet and tender. The register colours the words; it never invents facts.\
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
                        "art_direction": {"type": "string", "maxLength": 320},
                    },
                    "required": ["moment_id", "caption"],
                    "additionalProperties": False,
                },
            },
            "opening_caption": {"type": "string", "maxLength": 240},
            "closing_caption": {"type": "string", "maxLength": 240},
            "cover_title": {"type": "string", "maxLength": 60},
            "cover_prompt": {"type": "string", "maxLength": 400},
            "defining_phrase": {"type": "string", "maxLength": 120},
            "hero_line": {"type": "string", "maxLength": 160},
            "tags": {
                "type": "array",
                "maxItems": 3,
                "items": {"type": "string", "maxLength": 40},
            },
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
    confession: bool = False,
    tag_catalog: str | None = None,
    style_directive: str | None = None,
    edit_directive: str | None = None,
) -> TributeScript:
    """Return an assembled script. Falls back to chronological on failure.

    ``confession=True`` selects the Father's Day "confession" voice (first
    person ABOUT him, addressed to the world, ``he``) and asks for a
    ``defining_phrase`` + story-gated ``hero_line``. Default ``False`` keeps the
    shipped "letter to you" voice byte-for-byte.

    Storybook-only knobs (all default off, so tribute behaviour is unchanged):
      * ``tag_catalog`` -- a rendered ``<emotional_tags>`` block. When set, the
        assembler is asked to pick 1-3 slugs (returned in ``script.tags``) and
        tone the prose to match.
      * ``style_directive`` -- an extra system instruction appended to the
        voice prompt (e.g. forcing a register on edit). Free text.
      * ``edit_directive`` -- cumulative user edit instructions, rendered as an
        ``<editorial_notes>`` block in the user message so the reshape honours
        them.
    """
    usable = [c for c in candidates if c.get("id")]
    if not usable:
        return TributeScript([], "", "", message_text)
    # Order candidates along the subject's life before anything downstream, so
    # both the LLM prompt and the fallback start from a coherent spine instead
    # of reverse-extraction order.
    usable = chronological_sort(usable)
    if settings is None:
        return _fallback_script(
            usable, message_text=message_text, max_scenes=max_scenes
        )

    by_id = {c["id"]: c for c in usable}
    scene_blocks = "\n".join(_scene_block(c) for c in usable)
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
    catalog_block = f"{tag_catalog}\n" if tag_catalog else ""
    edit_note = (edit_directive or "").strip()
    edit_block = (
        f"<editorial_notes>{xml_text(edit_note)}</editorial_notes>\n"
        if edit_note
        else ""
    )
    user_block = (
        f"<subject{rel}>{xml_text(person_name)}</subject>\n"
        f"{message_line}"
        f"{catalog_block}"
        f"{edit_block}"
        f"<candidate_scenes>\n{scene_blocks}\n</candidate_scenes>"
    )

    system = _CONFESSION_SYSTEM if confession else _ASSEMBLY_SYSTEM
    if tag_catalog:
        system = system + _TAG_DIRECTIVE
    extra = (style_directive or "").strip()
    if extra:
        system = system + "\n\n" + extra
    try:
        args = await call_with_tool(
            provider=settings.llm_big_provider,
            model=settings.llm_big_model,
            system_prompt=system.replace("{max_scenes}", str(max_scenes)),
            user_message=user_block,
            tool=_ASSEMBLY_TOOL,
            # Room for up to ~12 scenes, each with caption + accent +
            # art_direction (+ optional pull_quote) plus the cover/closing
            # fields. 1500 truncated the richer per-scene output.
            max_tokens=3200,
            timeout=45.0,
            settings=settings,
            feature="tribute_assembly",
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
                    art_direction=(raw.get("art_direction") or "").strip(),
                )
            )
    if not scenes:
        return _fallback_script(
            usable, message_text=message_text, max_scenes=max_scenes
        )

    # Raw tag slugs, lower-cased + de-duped + capped. Validation against the
    # registry happens in the storybook layer (keeps this module registry-
    # agnostic and avoids a tribute -> storybook import cycle).
    raw_tags = args.get("tags") if isinstance(args, dict) else None
    tags: tuple[str, ...] = ()
    if isinstance(raw_tags, list):
        seen: list[str] = []
        for t in raw_tags:
            slug = (t or "").strip().lower() if isinstance(t, str) else ""
            if slug and slug not in seen:
                seen.append(slug)
            if len(seen) >= 3:
                break
        tags = tuple(seen)

    return TributeScript(
        scenes=scenes,
        opening_caption=(args.get("opening_caption") or "").strip(),
        closing_caption=(args.get("closing_caption") or "").strip(),
        message_text=message_text,
        cover_title=(args.get("cover_title") or "").strip(),
        cover_prompt=(args.get("cover_prompt") or "").strip(),
        defining_phrase=(args.get("defining_phrase") or "").strip(),
        hero_line=(args.get("hero_line") or "").strip(),
        tags=tags,
    )
