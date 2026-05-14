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
                    "Traits of the SUBJECT, each anchored to a specific "
                    "recalled behavior or recurring pattern in this segment. "
                    "EVERY trait MUST be referenced by at least one moment "
                    "via `exemplifies_trait_indexes` — orphan traits are "
                    "dropped by the worker and never persisted. Skip bare "
                    "adjectives that have no accompanying behavioral "
                    "evidence. Never extract a single recalled incident as "
                    "a trait (extract it as a moment and link). 0-2 typical, "
                    "max 3. Strength is always 'mentioned_once'; the Trait "
                    "Synthesizer upgrades later."
                ),
                "maxItems": 3,
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
conversation segment between a contributor and a legacy-preservation agent. \
The contributor is talking about the SUBJECT of the legacy, who may be \
living, deceased, or known through inherited family stories. The subject's \
name is provided below.

Your job is to extract structured memory data from this segment.

Input shape:
- The subject's name and relationship to the contributor.
- The contributor's display name (may be empty).
- Candidate answered question ids, when the prior assistant turn contained
  a structured tap or seeded question the contributor may be answering.
- The PRIOR rolling summary (compressed history of earlier segments).
- The CLOSED SEGMENT (the conversation turns to extract from).

When `<contributor_display_name>` is non-empty, USE that name for any \
first-person attribution to the contributor in moment narratives and \
entity descriptions ("Sarah recalls...", "John, Sarah's father, was a \
carpenter"). Do NOT write "the contributor" or "the contributor's" when \
a name is provided — use the name, or restructure into impersonal voice \
(passive, descriptive) if that reads better than any explicit attribution. \
The phrase "the contributor" is reserved for the empty-tag case; only \
then fall back to neutral attribution ("the contributor", or simply omit). \
Never write a placeholder like "<contributor>".

Extract the following, in this order of priority:

1. MOMENTS — discrete recalled or inherited episodes. Title, narrative, \
sensory details, emotional tone, time anchor, life period, contributor \
perspective. Each moment must be anchored — vague reflections without a \
specific scene are NOT moments. Aim for 0-2 moments per segment; up to 3 if \
the segment genuinely contains that many distinct episodes.

2. ENTITIES — people, places, objects, or organizations mentioned. Sub-typed \
via `kind`. NEVER include the subject themselves as an entity. The subject is \
in `persons`, not `entities`. Other people mentioned ARE entities.

3. TRAITS — STABLE character properties of the SUBJECT, anchored to behavior \
in this segment. A trait is a pattern ("patient explainer", "quick to laugh"), \
not a single event and not a bare adjective. Under-extract — drop on doubt.

Strict trait rules:
- REQUIRE behavioral anchoring. Drop any candidate trait unless this segment \
contains a specific recalled behavior or recurring pattern that exemplifies \
it. Bare adjectives in a list ("strong, handsome, kind") with NO accompanying \
instance are NOT yet traits. Drop them; a later session will earn them.
- REQUIRE an exemplifying MOMENT in this same extraction. Every trait you \
emit MUST be referenced by ≥1 moment via `exemplifies_trait_indexes`. Orphan \
traits (no exemplifying moment index) are dropped by the worker and never \
persisted. Do not emit them.
- ONE INCIDENT IS NOT A TRAIT. If a single recalled scene shows a property, \
extract it as a MOMENT and connect via `exemplifies_trait_indexes` to a \
pattern trait. Do NOT also emit the incident itself as a separate trait — \
the trait names the pattern, the moment IS the evidence.
- THE CONTRIBUTOR NEVER APPEARS IN A TRAIT DESCRIPTION — in ANY role. \
A trait lives on the subject's legacy and describes the SUBJECT's \
observed property. The contributor must not appear as speaker, narrator, \
witness, observer, listener, OR participant. The general \
contributor-name rule (used elsewhere in extraction) does NOT apply \
inside trait descriptions. Banned patterns include but are not limited \
to:
    (a) speaker attribution — "Described as kind by the contributor", \
"Described as kind by Priya", any "by <X>" tail
    (b) meta-narration — "the contributor noted...", "they noted...", \
"Priya recalls...", "according to the contributor...", any phrasing \
that names the act of describing rather than the property itself
    (c) contributor knowledge framing — "Despite the contributor not \
knowing much about his home life...", "the contributor wasn't sure, \
but...", "even though Priya rarely saw him at work..."
    (d) contributor as participant — "Chitanya would come to the \
contributor when stuck", "explained things to the contributor", \
"called Priya every Sunday". When the relationship between the subject \
and contributor IS part of the evidence, abstract it: "drew on close \
friends when stuck", "explained things at the listener's pace", "kept \
in touch with the people closest to him".

Examples:
    Bad:  "Described as kind by the contributor."
    Bad:  "The contributor noted Chitanya was talented."
    Bad:  "Despite the contributor not knowing much about his home \
life, they noted he looked out for people at home."
    Bad:  "When stuck on a problem, Chitanya would come to the \
contributor rather than struggle alone."
    Good: "Came across as kind from the first meeting — made time for \
a stranger's laptop questions without seeming bothered."
    Good: "Looked out for people at home — even the parts of his life \
others rarely saw."
    Good: "Drew on close friends when stuck on a problem rather than \
struggle alone, trusting the people around him."
- Name = short label (1-4 words), e.g., "Kind", "Patient explainer". \
Description = 1-2 sentences in observed-behavior voice that name the \
PROPERTY and a concrete behavior that shows it. No speaker attribution.
- Strength is always 'mentioned_once' here.

4. DROPPED_REFERENCES — named entities the contributor mentioned in passing \
but did not explore. Generate a question that would open them up next time.

CRITICAL RULES:
- UNDER-EXTRACT. If uncertain whether something is a moment, drop it. Better \
to miss material than to pollute the graph.
- Use the contributor's own words for narrative when reasonable — paraphrase \
only when needed for coherence.
- Preserve the contributor's tense and knowledge position. Do not force \
present-tense input into past tense, and do not turn inherited family stories \
into firsthand memories. Capture contributor_perspective faithfully (for \
example: firsthand witness, participant, family story, story heard from a \
parent, never met directly).
- Preserve actor attribution. The CLOSED SEGMENT is the source of truth for \
who did what; the PRIOR rolling summary is context only. When several people \
appear in the same or adjacent events, use explicit names and do not transfer \
an action, illness, relationship, quote, or feeling from one person to another.
- Keep separate events separate if merging them would blur the actor, place, \
relationship, or outcome.
- If `<candidate_answered_question_ids>` is present, use it only as context: \
the persistence layer may link extracted moments to those question rows when \
the user's answer addresses the prior assistant tap or seeded question.
- The subject of the legacy is NEVER an entity. They live in `persons`. Other \
people mentioned ARE entities.
- For places, populate `attributes.region` / `attributes.kind` if the \
contributor said anything about them.
- For person entities, populate `attributes.relationship` (their relationship \
to the SUBJECT, not to the contributor).
- If the contributor corrects an identity, extract ONE canonical entity with \
the corrected name and put the mistaken/prior label in `aliases`. Do not emit \
both as separate entities.
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
You are comparing two legacy-conversation moments to decide whether they \
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


# ---------------------------------------------------------------------------
# Trait-description merge tool (small LLM)
# ---------------------------------------------------------------------------


TRAIT_MERGE_TOOL = ToolSpec(
    name="merge_trait_description",
    description=(
        "Merge an existing trait description with newly-observed behavior "
        "from this segment into one cohesive 1-2 sentence description, "
        "in observed-behavior voice. No speaker attribution."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "merged_description": {
                "type": "string",
                "description": (
                    "The merged trait description. 1-2 sentences. Names "
                    "the property and concrete behaviors that show it. "
                    "No speaker attribution ('Described as X by Y' / "
                    "'the contributor noted...' are forbidden)."
                ),
            },
        },
        "required": ["merged_description"],
        "additionalProperties": False,
    },
)


TRAIT_MERGE_SYSTEM_PROMPT = """\
You are merging two descriptions of the SAME trait of a legacy subject. Both \
describe the same property; you fold them into \
one cohesive 1-2 sentence description that preserves the strongest concrete \
behavior from each.

Hard rules:
- THE CONTRIBUTOR NEVER APPEARS in the merged description, in ANY role: not \
as speaker, narrator, witness, observer, listener, or participant. Strip \
such framings from the inputs if present. Banned patterns include speaker \
attribution ("described as X by the contributor", "described as X by \
<name>"), meta-narration ("the contributor noted...", "they noted...", \
"<name> recalls..."), contributor-knowledge framings ("despite the \
contributor not knowing...", "even though <name> rarely saw..."), and \
contributor-as-participant ("came to the contributor when stuck", \
"explained things to <name>"). When the contributor is structurally part \
of the evidence, abstract them into a neutral group noun: "close friends", \
"those around him", "people he trusted", "the listener".
- 1-2 sentences total. Concise, behavior-focused, present-tense recall.
- Preserve the strongest concrete behavior from EACH input. If one input is \
behavior-grounded and the other is bare adjective filler, weight the \
behavior-grounded side.
- Do NOT invent details. Use only what is in the inputs.
- Match the trait NAME — the merged description must describe THAT property, \
not drift to an adjacent one.

Examples:
  Trait: Kind
  Existing: "Made room for a stranger's laptop questions without seeming bothered."
  New:      "Covered a colleague's surgery costs quietly when no one asked."
  Merged:   "Made room for people who needed help — from a stranger's laptop \
questions to a colleague's surgery costs he quietly covered."

  Trait: Patient
  Existing: "Described as patient by the contributor."          # bad input — strip the meta-commentary
  New:      "Explained the same recipe to his daughter three times without sighing."
  Merged:   "Explained the same recipe three times without sighing — patient \
with people who needed the long version."

Respond ONLY by calling the `merge_trait_description` tool.\
"""
