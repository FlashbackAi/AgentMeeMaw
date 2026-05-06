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

MOMENT_EDIT_PROMPT_VERSION = "node_edits.moment.v1"
ENTITY_EDIT_PROMPT_VERSION = "node_edits.entity.v1"


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
edited the narrative text of an existing moment about a deceased loved \
one (the SUBJECT of the legacy). Your job is to translate their edited \
text into the moment's structured fields and the list of entities \
mentioned.

Input shape:
- The subject's name and relationship to the contributor.
- The PRIOR moment fields (title, narrative, sensory_details, time_anchor, \
etc.) — these are the row as it stands today.
- The CONTRIBUTOR'S EDITED TEXT — the new narrative they want stored.

Produce, by calling the `rewrite_moment` tool exactly once:

1. The new structured moment fields, derived from the edited text. Use \
the prior fields as scaffolding — keep what still applies, change what \
the new text changes, drop what the new text removes.

2. The full list of entities (people, places, objects, organizations) \
mentioned in the edited text. NEVER include the SUBJECT — they live in \
`persons`, not entities. Index from 0; `involves_entity_indexes` and \
`happened_at_entity_index` reference these positions.

3. A new `generation_prompt` — a one-sentence visual description in \
present tense, Pixar/Studio Ghibli style. Mood, color, light, \
composition. No people's faces. No photorealism.

CRITICAL RULES:
- The contributor's edited text is the source of truth. Do not silently \
re-introduce details from the prior version that the contributor removed.
- Preserve actor attribution. Use explicit names. Do not transfer an \
action, illness, relationship, quote, or feeling from one person to \
another.
- Time anchors: be conservative. Year if explicit ("summer of '76" -> \
1976), decade if approximate, life_period as a phrase otherwise. Leave \
blank if unclear.
- For person entities, populate `attributes.relationship` (their \
relationship to the SUBJECT, not to the contributor). Populate \
`attributes.saying` or `attributes.mannerism` if the new text mentions \
a phrase or behavior.
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
edited the description of an existing entity (a person, place, object, \
or organization) tied to a deceased loved one's legacy. Your job is to \
translate their edited text into the entity's structured fields.

Input shape:
- The legacy subject's name and relationship to the contributor.
- The entity's `kind` and canonical `name` — IMMUTABLE. Do not change \
either.
- The PRIOR entity fields (description, aliases, attributes).
- The CONTRIBUTOR'S EDITED TEXT — the new description.

Produce, by calling the `rewrite_entity` tool exactly once:

1. The new `description` text — usually the contributor's edit, lightly \
copy-edited for coherence. Preserve their voice.

2. Updated `aliases` — names or labels by which this entity is also \
known. Add any new ones implied by the edit. Drop ones the edit \
explicitly contradicts. Never include the canonical name itself.

3. Updated `attributes` (kind-specific):
   - person: `relationship` (to the subject), `saying`, `mannerism`.
   - place: `region`, `kind` (e.g. "lake", "village", "kitchen").
   - object / organization: free-form key/value as needed.

4. A new `generation_prompt` — a one-sentence visual description in \
present tense, Pixar/Studio Ghibli style. Mood, color, light, \
composition. No people's faces. No photorealism. The visual should \
reflect what this edited description establishes.

CRITICAL RULES:
- DO NOT change the entity's name or kind. Those are carried forward by \
the system from the prior row.
- The contributor's edited text is the source of truth. Do not invent \
attributes the text does not support.
- If the edit changes physical appearance (build, age, attire), \
rewrite the generation_prompt accordingly.

Respond ONLY by calling the `rewrite_entity` tool.\
"""
