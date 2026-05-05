"""
System prompts and tool definitions for the Extraction Worker.

Two LLM calls per message (typical):

* ``EXTRACTION_TOOL`` (Sonnet): one big call per segment that returns the
  full structured-data extraction.
* ``COMPATIBILITY_TOOL`` (gpt-5.1): one small call per refinement
  candidate found by vector search. Most segments fire it zero times.

The drift-detector test in ``tests/workers/extraction/test_prompts.py``
parses ``migrations/0001_initial_schema.up.sql`` and asserts the entity
``kind`` enum here matches the CHECK constraint exactly. If the migration
gains a new entity kind, that test fails until this file is updated.
"""

from __future__ import annotations

from flashback.llm.tool_spec import ToolSpec


ENTITY_KINDS: tuple[str, ...] = ("person", "place", "object", "organization")


# ---------------------------------------------------------------------------
# Extraction tool (big LLM)
# ---------------------------------------------------------------------------


EXTRACTION_TOOL = ToolSpec(
    name="extract_segment",
    description=(
        "Extract structured memory data from a closed conversation "
        "segment. Call exactly once. Under-extract if uncertain."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "moments": {
                "type": "array",
                "description": (
                    "0–3 distinct moments from this segment. A moment is a "
                    "single recalled episode. Skip if the segment is too thin "
                    "to anchor a moment cleanly."
                ),
                "minItems": 0,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "maxLength": 120},
                        "narrative": {"type": "string"},
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
                        "happened_at_entity_index": {
                            "type": "integer",
                            "minimum": 0,
                        },
                        "exemplifies_trait_indexes": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0},
                        },
                        "generation_prompt": {"type": "string"},
                    },
                    "required": ["title", "narrative", "generation_prompt"],
                    "additionalProperties": False,
                },
            },
            "entities": {
                "type": "array",
                "description": (
                    "Entities mentioned across the segment. Each is indexed by "
                    "position; moments reference them by index in "
                    "`involves_entity_indexes` etc. NEVER include the subject "
                    "of the legacy as an entity — they live in `persons`."
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
                        "related_to_entity_indexes": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0},
                        },
                        "generation_prompt": {"type": "string"},
                    },
                    "required": ["kind", "name", "generation_prompt"],
                    "additionalProperties": False,
                },
            },
            "traits": {
                "type": "array",
                "description": (
                    "Traits of the SUBJECT (the deceased), explicitly mentioned "
                    "in this segment. Strength is always 'mentioned_once' here "
                    "— the Trait Synthesizer upgrades later."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
            "dropped_references": {
                "type": "array",
                "description": (
                    "Named entities mentioned in passing but not explored. Each "
                    "becomes a `dropped_reference` question for later. Include "
                    "at most 3."
                ),
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "dropped_phrase": {"type": "string"},
                        "question_text": {"type": "string"},
                        "themes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    },
                    "required": [
                        "dropped_phrase",
                        "question_text",
                        "themes",
                    ],
                    "additionalProperties": False,
                },
            },
            "extraction_notes": {
                "type": "string",
                "description": (
                    "One or two sentences explaining what was extracted and "
                    "why. For logs only."
                ),
            },
        },
        "required": [
            "moments",
            "entities",
            "traits",
            "dropped_references",
            "extraction_notes",
        ],
        "additionalProperties": False,
    },
)


EXTRACTION_SYSTEM_PROMPT = """\
You are the Extraction Worker for Flashback. You are processing a closed \
conversation segment between a contributor and a memorial agent. The \
contributor is talking about a deceased person — the SUBJECT of the legacy. \
The subject's name is provided below.

Your job is to extract structured memory data from this segment.

Input shape:
- The subject's name and relationship to the contributor.
- The PRIOR rolling summary (compressed history of earlier segments).
- The CLOSED SEGMENT (the conversation turns to extract from).

Extract the following, in this order of priority:

1. MOMENTS — discrete recalled episodes. Title, narrative, sensory details, \
emotional tone, time anchor, life period, contributor perspective. Each moment \
must be anchored — vague reflections without a specific recalled scene are NOT \
moments. Aim for 0-2 moments per segment; up to 3 if the segment genuinely \
contains that many distinct episodes.

2. ENTITIES — people, places, objects, or organizations mentioned. Sub-typed \
via `kind`. NEVER include the subject themselves as an entity. The subject is \
in `persons`, not `entities`. Other people mentioned ARE entities.

3. TRAITS — character properties of the SUBJECT, explicitly stated or strongly \
implied in the contributor's words. Strength is always 'mentioned_once' here.

4. DROPPED_REFERENCES — named entities the contributor mentioned in passing \
but did not explore. Generate a question that would open them up next time.

CRITICAL RULES:
- UNDER-EXTRACT. If uncertain whether something is a moment, drop it. Better \
to miss material than to pollute the graph.
- Use the contributor's own words for narrative when reasonable — paraphrase \
only when needed for coherence.
- The subject of the legacy is NEVER an entity. They live in `persons`. Other \
people mentioned ARE entities.
- For places, populate `attributes.region` / `attributes.kind` if the \
contributor said anything about them.
- For person entities, populate `attributes.relationship` (their relationship \
to the SUBJECT, not to the contributor).
- For person entities with a known signature phrase or behavior, populate \
`attributes.saying` or `attributes.mannerism`. The Coverage Tracker credits \
the `voice` dimension when these attributes exist.
- Time anchors: be conservative. If the contributor said "the summer of '76" \
set year=1976. If they said "the 80s" set decade="1980s". If unclear, leave \
time_anchor blank.

For `generation_prompt` fields: produce a one-sentence visual description in \
present tense. Pixar/Studio Ghibli style. Focus on mood, color, light, \
composition. No people's faces, no photorealism. The worker code appends \
style guidance after.

Examples of good generation_prompts:
- "A wood-paneled kitchen at dawn, sunlight catching steam from a coffee cup."
- "An old red truck parked in a snowy driveway under a porch light."

Respond ONLY by calling the `extract_segment` tool.\
"""


# ---------------------------------------------------------------------------
# Compatibility check tool (small LLM)
# ---------------------------------------------------------------------------


COMPATIBILITY_TOOL = ToolSpec(
    name="judge_compatibility",
    description=(
        "Compare a newly-extracted moment against an existing one. Decide "
        "whether they are the same memory (refinement), contradict each "
        "other (contradiction), or are independent."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["refinement", "contradiction", "independent"],
            },
            "reasoning": {"type": "string"},
        },
        "required": ["verdict", "reasoning"],
        "additionalProperties": False,
    },
)


COMPATIBILITY_SYSTEM_PROMPT = """\
You are comparing two memorial-conversation moments to decide whether they \
describe the same underlying memory.

Verdicts:
- `refinement`: They describe the SAME underlying memory; the newer one adds \
detail or corrects the older. The system will supersede the older with the \
newer.
- `contradiction`: They describe overlapping but factually conflicting \
memories that cannot both be true. Both are preserved; the conflict is \
logged for later review.
- `independent`: They describe different memories that happen to share an \
entity or theme. Both stand on their own.

When in doubt, prefer `independent`. False refinements lose information; \
false contradictions are noise.

Respond ONLY by calling the `judge_compatibility` tool.\
"""
