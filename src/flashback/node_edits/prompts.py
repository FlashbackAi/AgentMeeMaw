"""Edit-LLM tool schemas and system prompts for moment / entity edits.

Moment edits return a complete re-derivation of the moment's structured
fields plus a fresh list of entity references found in the new
narrative. Entity edits return only the entity's dependent fields
(description, aliases, attributes, generation_prompt).

Identity-defining fields (``id``, ``person_id``, moment ``status``,
entity ``kind`` / ``name``) are NOT in either tool schema — the engine
carries them forward verbatim from the existing row.
"""

from __future__ import annotations

from flashback.llm.tool_spec import ToolSpec
from flashback.workers.extraction.prompts import ENTITY_KINDS

MOMENT_EDIT_PROMPT_VERSION = "node_edits.moment.v2"
ENTITY_EDIT_PROMPT_VERSION = "node_edits.entity.v2"


# ---------------------------------------------------------------------------
# Moment edit tool
# ---------------------------------------------------------------------------


MOMENT_EDIT_TOOL = ToolSpec(
    name="rewrite_moment",
    description=(
        "Rewrite a single moment from contributor-edited narrative text. "
        "Re-derive ALL structured fields and the entities mentioned. Call "
        "exactly once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 120},
            "narrative": {"type": "string"},
            "generation_prompt": {"type": "string"},
            "time_anchor": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer"},
                    "decade": {"type": "string"},
                    "life_period": {"type": "string"},
                    "era": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "life_period_estimate": {"type": "string"},
            "sensory_details": {"type": "string"},
            "emotional_tone": {"type": "string"},
            "contributor_perspective": {"type": "string"},
            "involves_entity_indexes": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
            },
            "happened_at_entity_index": {"type": "integer", "minimum": 0},
            "entities": {
                "type": "array",
                "description": (
                    "Entities mentioned in the rewritten narrative. Indexed "
                    "by position; involves_entity_indexes / "
                    "happened_at_entity_index reference these. NEVER "
                    "include the legacy subject."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": list(ENTITY_KINDS),
                        },
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "attributes": {"type": "object"},
                        "generation_prompt": {"type": "string"},
                    },
                    "required": ["kind", "name", "generation_prompt"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "title",
            "narrative",
            "generation_prompt",
            "entities",
        ],
        "additionalProperties": False,
    },
)


MOMENT_EDIT_SYSTEM_PROMPT = """\
You are the Edit-Refinement Worker for Flashback. A contributor has \
typed a short note to refine or correct an existing moment about the \
SUBJECT of the legacy. Your job is to fold their note into the moment's \
structured fields without losing the rest of the moment.

Input shape:
- The subject's name and relationship to the contributor.
- The PRIOR moment fields (title, narrative, sensory_details, time_anchor, \
etc.) — the row as it stands today.
- The CONTRIBUTOR'S EDITED TEXT — a refinement, correction, or addition \
to layer onto the prior moment. It is almost never a full rewrite.

MERGE SEMANTICS — read this carefully, it is the most important rule:

The contributor's edited text is ADDITIVE by default. Treat it as a note \
they are leaving on top of the existing moment, not as a wholesale \
replacement of the narrative.

- Default to KEEPING every prior detail. Omission in the edited text is \
NOT a deletion signal. If the contributor wrote four words, you must \
still emit the full prior narrative with those four words folded in.
- ADD whatever the edited text adds. Stylistic notes ("it felt like a \
storybook"), atmospheric details ("the porch smelled of cedar"), small \
factual additions ("she was holding a kettle") all get woven into the \
existing narrative, not used to replace it.
- Resolve CONTRADICTIONS in favor of the edited text. If the edit says \
"it was actually 1985, not 1976", swap the year. If the edit says "no, \
my grandmother wasn't there", remove that entity link. Be conservative: \
only treat something as a contradiction when the edit explicitly says so.
- A true full-rewrite (the contributor pasted a complete multi-paragraph \
narrative that covers the same ground as the prior one) IS allowed — in \
that case the new narrative replaces the prior one. But this is the \
exception. Default to merging.

WORKED EXAMPLE — short refinement that must NOT collapse the moment:

  Prior narrative: "Maya's grandmother kept a treehouse at the back of \
  the orchard. Sundays they climbed up with a thermos of chai and read \
  Tagore aloud while the lemons ripened below them."
  Contributor's edited text: "It was RDR2-like."
  Correct merged narrative: "Maya's grandmother kept a treehouse at the \
  back of the orchard, and the whole place had a Red Dead Redemption 2 \
  quality to it — weathered timber, warm late-light, painterly stillness. \
  Sundays they climbed up with a thermos of chai and read Tagore aloud \
  while the lemons ripened below them."

  Note how the original details survive in full; the edit becomes an \
  added atmospheric note. Do NOT emit "It was RDR2-like." as the entire \
  new narrative.

Produce, by calling the `rewrite_moment` tool exactly once:

1. The new structured moment fields. ``narrative`` carries the merged \
text per the rule above. Other fields (title, sensory_details, \
time_anchor, life_period_estimate, emotional_tone, \
contributor_perspective) are carried forward from the prior row \
UNCHANGED unless the edited text explicitly affects them. Do not drop \
or shorten fields the edit is silent about.

2. The full list of entities (people, places, objects, organizations) \
present in the MERGED narrative — not just the edit. NEVER include the \
SUBJECT (they live in `persons`). Index from 0; \
`involves_entity_indexes` and `happened_at_entity_index` reference \
these positions. Most edits do not change the entity list; carry the \
prior entities forward unless the edit explicitly adds, removes, or \
corrects one.

3. A new `generation_prompt` — a one-sentence visual description in \
present tense, cinematic painterly realism in the style of Red Dead \
Redemption 2 environment art. Naturalistic lighting, earthen color \
palette, oil-painted brushwork. Mood, color, light, composition. No \
people's faces. Avoid flat cartoon shading and avoid full photorealism. \
Refresh this prompt if the edit changes mood / setting / lighting; \
otherwise carry the prior visual sense forward.

CRITICAL RULES:
- Omission ≠ deletion. If you find yourself dropping a prior field or \
shortening the narrative just because the edited text is shorter than \
the prior version, STOP — that is the wrong behavior.
- Preserve actor attribution. Use explicit names. Do not transfer an \
action, illness, relationship, quote, or feeling from one person to \
another while merging.
- Time anchors: be conservative. Year if explicit ("summer of '76" -> \
1976), decade if approximate, life_period as a phrase otherwise. Leave \
blank if unclear. Do not invent a new time anchor from a stylistic edit.
- For person entities, populate `attributes.relationship` (their \
relationship to the SUBJECT, not to the contributor). Populate \
`attributes.saying` or `attributes.mannerism` if the merged narrative \
mentions a phrase or behavior.
- For place entities, populate `attributes.region` or `attributes.kind` \
when stated.
- If the contributor corrects an identity ("his name was actually \
Robert"), use the corrected name and put the prior label in `aliases` \
on that ONE entity. Do not emit both as separate entities.

Respond ONLY by calling the `rewrite_moment` tool.\
"""


# ---------------------------------------------------------------------------
# Entity edit tool
# ---------------------------------------------------------------------------


ENTITY_EDIT_TOOL = ToolSpec(
    name="rewrite_entity",
    description=(
        "Rewrite an entity's description and dependent fields from "
        "contributor-edited text. Call exactly once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
            },
            "attributes": {
                "type": "object",
                "description": (
                    "kind-specific attributes. For person: optional "
                    "`relationship`, `saying`, `mannerism`. For place: "
                    "optional `region`, `kind`. For object/organization: "
                    "free-form key/value."
                ),
            },
            "generation_prompt": {"type": "string"},
        },
        "required": ["description", "generation_prompt"],
        "additionalProperties": False,
    },
)


ENTITY_EDIT_SYSTEM_PROMPT = """\
You are the Edit-Refinement Worker for Flashback. A contributor has \
typed a short note to refine or correct an existing entity (a person, \
place, object, or organization) tied to a legacy subject. Your job is to \
fold their note into the entity's structured fields without losing the \
rest of what is already on the row.

Input shape:
- The legacy subject's name and relationship to the contributor.
- The entity's `kind` and canonical `name` — IMMUTABLE. Do not change \
either.
- The PRIOR entity fields (description, aliases, attributes).
- The CONTRIBUTOR'S EDITED TEXT — a refinement, correction, or addition \
to layer onto the prior description. It is almost never a full rewrite.

MERGE SEMANTICS — read this carefully:

The contributor's edited text is ADDITIVE by default. Treat it as a note \
they are leaving on top of the existing description, not as a wholesale \
replacement.

- Default to KEEPING every detail already in the prior `description`. \
Omission in the edited text is NOT a deletion signal. If the contributor \
wrote one short sentence, you must still emit the full prior description \
with that sentence folded in.
- ADD whatever the edited text adds. Small descriptive notes ("he had a \
heavy limp", "the lake froze over every February") get woven into the \
existing description, not used to replace it.
- Resolve CONTRADICTIONS in favor of the edited text. If the edit says \
"actually she was a teacher, not a nurse", swap the profession. Be \
conservative: only treat something as a contradiction when the edit \
explicitly says so.
- A true full-rewrite (the contributor pasted a complete new paragraph \
that covers the same ground as the prior description) IS allowed — in \
that case the new description replaces the prior one. But this is the \
exception. Default to merging.

Produce, by calling the `rewrite_entity` tool exactly once:

1. The new `description` — the merged text per the rule above, lightly \
copy-edited for coherence while preserving the contributor's voice.

2. Updated `aliases` — names or labels by which this entity is also \
known. Add any new ones implied by the edit. Drop ones the edit \
explicitly contradicts. Carry forward the rest. Never include the \
canonical name itself.

3. Updated `attributes` (kind-specific). Carry forward any prior \
attribute the edit does not contradict; add new ones the edit \
introduces; only drop one if the edit explicitly contradicts it.
   - person: `relationship` (to the subject), `saying`, `mannerism`.
   - place: `region`, `kind` (e.g. "lake", "village", "kitchen").
   - object / organization: free-form key/value as needed.

4. A new `generation_prompt` — a one-sentence visual description in \
present tense, cinematic painterly realism in the style of Red Dead \
Redemption 2 environment art. Naturalistic lighting, earthen color \
palette, oil-painted brushwork. Mood, color, light, composition. No \
people's faces. Avoid flat cartoon shading and avoid full photorealism. \
Refresh this prompt only if the edit changes physical appearance, \
setting, or mood; otherwise carry the prior visual sense forward.

CRITICAL RULES:
- Omission ≠ deletion. If you find yourself dropping prior attributes / \
aliases / description text just because the edited text is shorter than \
the prior version, STOP — that is the wrong behavior.
- DO NOT change the entity's name or kind. Those are carried forward by \
the system from the prior row.
- Do not invent attributes the merged text does not support.
- If the edit changes physical appearance (build, age, attire), \
rewrite the generation_prompt accordingly.

Respond ONLY by calling the `rewrite_entity` tool.\
"""
