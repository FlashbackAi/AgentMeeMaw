"""The storybook collections manifest (spec 2026-06-29 §5).

Six fixed collections, each a separate gift book: five curated "grid"
collections (3 comic panels per page) and one "chapter" collection (a single
rich illustration per page) that reads the WHOLE life through a lens instead
of a curated slice. Every book is exactly ``PAGE_COUNT`` pages + a cover, so
Node can mint presigned PUT URLs up front.

Two content tiers (validated in the spike):
  * ``tone="gentle"`` -- read to small children; the assembler applies the
    child-safety rules (no alcohol, no danger, loss handled softly).
  * ``tone="full"``   -- keepsake weight allowed; real history stays.

``theme_focus`` steers curation + assembly (what the book is ABOUT and how it
must OPEN); ``signature_hint`` tells the assembler what KIND of recurring
visual motif to pick from the subject's own memories -- the concrete motif is
always chosen per legacy, never hardcoded (a motif like "the mango orchard"
belongs to one subject's life, not to the product).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PAGE_COUNT = 7

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")


@dataclass(frozen=True)
class Collection:
    slug: str
    display: str
    art_style: str
    voice: str
    layout: str  # "grid" | "chapter"
    tone: str    # "gentle" | "full"
    theme_focus: str
    signature_hint: str
    # A clean one-line description of what BELONGS in this collection, used by
    # the extraction / backfill tagger. Distinct from ``theme_focus`` (which
    # also carries "Open on ..." assembly directives that would confuse a
    # tagger). Empty for the chapter collection (``wisdom`` is never tagged —
    # it lenses the whole pool).
    tag_description: str = ""


COLLECTIONS: dict[str, Collection] = {
    "childhood": Collection(
        slug="childhood",
        display="Childhood Memories",
        art_style=(
            "soft watercolour storybook illustration, warm and nostalgic, "
            "gentle ink linework, golden-hour palette, tender"
        ),
        voice=(
            "a warm, nostalgic childhood-memories voice -- gentle, fond, a "
            "little wistful"
        ),
        layout="grid",
        tone="gentle",
        theme_focus=(
            "Childhood memories -- the small, formative world of growing up: "
            "the subject's own childhood AND especially the grandchildren's "
            "childhood spent with them (play, mischief, lessons, summers). "
            "Open on a child's-eye moment of play or wonder."
        ),
        signature_hint=(
            "a beloved childhood place or plaything from these memories (a "
            "tree, a yard, a pond, a favourite spot)"
        ),
        tag_description=(
            "the small, formative world of growing up — the subject's own "
            "childhood OR a grandchild's/child's childhood spent with them: "
            "play, mischief, school, lessons learned as a kid, summers, "
            "growing-up scenes."
        ),
    ),
    "interesting": Collection(
        slug="interesting",
        display="Interesting Stories",
        art_style=(
            "light watercolour storybook illustration, bright and lively, "
            "soft cool palette, gentle line work, cozy detail"
        ),
        voice=(
            "a curious, lively storyteller voice -- vivid, a touch playful, "
            "savouring the surprising detail"
        ),
        layout="grid",
        tone="full",
        theme_focus=(
            "The remarkable, surprising stories -- the ones you tell a "
            "stranger: hidden talents, unusual events, brushes with history, "
            "the unexpected. Open on a hook that makes the reader lean in "
            "('Not many people knew that...')."
        ),
        signature_hint=(
            "the one object or skill at the heart of the subject's most "
            "surprising side (a book, a tool, a craft)"
        ),
        tag_description=(
            "the remarkable, surprising stories you'd tell a stranger — "
            "hidden talents, unusual events, brushes with history, quirks, "
            "the unexpected side of the subject. Not everyday routine."
        ),
    ),
    "nostalgia": Collection(
        slug="nostalgia",
        display="Nostalgia",
        art_style=(
            "soft painterly watercolour illustration, hazy warm light, "
            "tender domestic scenes, muted timeless palette"
        ),
        voice=(
            "a lyrical, reflective voice -- unhurried, sensory, full of "
            "quiet longing and tenderness"
        ),
        layout="grid",
        tone="full",
        theme_focus=(
            "Quiet, tender, sensory everyday textures -- domestic rhythms, "
            "small rituals, the ache of remembering; gentle and unhurried, "
            "not dramatic. Open on a sensory memory (a sound, a smell, the "
            "light at dawn)."
        ),
        signature_hint=(
            "a small sensory detail worn or repeated daily in these memories "
            "(a piece of jewellery, a morning ritual, a familiar sound)"
        ),
        tag_description=(
            "quiet, tender, sensory everyday textures — domestic rhythms, "
            "small daily rituals, familiar sounds/smells, gentle ordinary "
            "moments that ache with remembering. Not dramatic or eventful."
        ),
    ),
    "festivals": Collection(
        slug="festivals",
        display="Festivals & Special Days",
        art_style=(
            "warm cinematic storybook illustration, rich painterly light, "
            "festive glow, golden and amber palette, celebratory"
        ),
        voice=(
            "a celebratory, vivid voice -- evoking the warmth, colour and "
            "togetherness of special days"
        ),
        layout="grid",
        tone="gentle",
        theme_focus=(
            "Festivals, ceremonies and special days -- celebrations, "
            "blessings, milestones and rites the family marked together. "
            "Open inside a celebration, mid-colour-and-noise."
        ),
        signature_hint=(
            "one physical emblem of celebration recurring in these memories "
            "(a flag, a lamp, a garland, a shared dish)"
        ),
        tag_description=(
            "festivals, ceremonies, holidays and special days — religious or "
            "cultural celebrations, weddings, blessings, milestones and rites "
            "the family marked together."
        ),
    ),
    "adventurous": Collection(
        slug="adventurous",
        display="Adventures",
        art_style=(
            "cinematic semi-realistic illustration, dramatic natural light, "
            "rich detail, painterly depth, evocative"
        ),
        voice=(
            "a vivid, cinematic voice -- a sense of motion, place and the "
            "feeling of the moment"
        ),
        layout="grid",
        tone="gentle",
        theme_focus=(
            "Physical, outdoor, daring moments -- journeys, feats, risk and "
            "motion; the body in the world. Open in motion, mid-action, out "
            "in the wild -- never with a still portrait."
        ),
        signature_hint=(
            "the tool or terrain of the subject's boldest doing (an axe, a "
            "road, a river, a forest)"
        ),
        tag_description=(
            "physical, outdoor, daring moments — journeys, travel, feats, "
            "risk and motion, the body out in the world (hikes, rivers, "
            "roads, sport, expeditions). Not indoor or sedentary scenes."
        ),
    ),
    "wisdom": Collection(
        slug="wisdom",
        display="Wisdom & Lessons",
        art_style=(
            "delicate watercolour storybook illustration, soft warm light, "
            "fine ink detail, single richly-detailed scene, literary"
        ),
        voice=(
            "a warm, reflective elder-wisdom voice -- gentle and knowing, as "
            "if passing a lesson down to a grandchild, one flowing paragraph "
            "a page"
        ),
        layout="chapter",
        tone="full",
        theme_focus=(
            "The values and lessons the subject lived and passed down -- what "
            "they taught, by word and (mostly) by example: how they treated "
            "people, what they believed, the quiet rules they lived by. Draw "
            "the lesson OUT of a concrete memory each page (a thing they "
            "did), never preach it in the abstract. Open on a loved one "
            "realising something the subject taught without ever 'teaching' "
            "it."
        ),
        signature_hint=(
            "a physical embodiment of how the subject taught by doing (their "
            "working hands, a gesture, a daily act)"
        ),
    ),
}

# Grid collections draw on their tagged slice; the chapter collection
# (``wisdom``) draws a lens over the whole pool and is never tagged / gated
# per collection. ``CURATED_SLUGS`` keeps its name for compatibility but now
# means "the tag-gated grid collections".
CURATED_SLUGS = [s for s, c in COLLECTIONS.items() if c.layout == "grid"]

# The collection slugs the extraction / backfill tagger may assign. Exactly the
# grid slugs — ``wisdom`` is excluded by design (it lenses the whole pool).
TAGGABLE_SLUGS: tuple[str, ...] = tuple(CURATED_SLUGS)


def is_grid(slug: str) -> bool:
    """True for a tag-gated grid collection; False for the chapter lens."""
    c = COLLECTIONS.get(slug)
    return c is not None and c.layout == "grid"


def grid_tag_catalog() -> list[dict[str, str]]:
    """The ``<collection_catalog>`` rows shown to the tagger.

    One entry per grid slug: ``slug`` + ``tag_description`` (what belongs).
    ``wisdom`` is absent by design.
    """
    return [
        {"slug": s, "tag_description": COLLECTIONS[s].tag_description}
        for s in TAGGABLE_SLUGS
    ]


def asset_dir(slug: str) -> str:
    return os.path.join(_ASSETS, slug)


def public_collections(
    eligibility: dict[str, tuple[int, bool]] | None = None,
) -> list[dict]:
    """The GET /storybook-collections surface (chooser + URL-mint counts).

    When ``eligibility`` is supplied (a per-slug ``(tagged_count, eligible)``
    map, computed against one person's pool), each entry additionally carries
    ``tagged_count`` + ``eligible`` so Node can render locked "3/5 stories"
    cards. Without it the response is the bare registry (unchanged).
    """
    rows: list[dict] = []
    for c in COLLECTIONS.values():
        row = {
            "slug": c.slug,
            "display_name": c.display,
            "layout": c.layout,
            "page_count": PAGE_COUNT,
        }
        if eligibility is not None:
            count, eligible = eligibility.get(c.slug, (0, False))
            row["tagged_count"] = count
            row["eligible"] = eligible
        rows.append(row)
    return rows
