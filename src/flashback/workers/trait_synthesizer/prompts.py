"""System prompt and tool definition for the Trait Synthesizer.

The synthesizer makes ONE LLM call per person. The single tool covers
both decisions: what to do with each existing trait, and what new
traits the accumulated thread evidence supports.

The system prompt biases hard toward ``keep`` to keep the trait set
short and accurate. Upgrades require multiple thread-level evidence;
downgrades are rare and must be justified.

The tool schema's strength enum and action enum mirror
:data:`flashback.workers.trait_synthesizer.schema.STRENGTH_LADDER` and
the ``Action`` Literal — kept in sync via a drift test in
``tests/workers/trait_synthesizer/test_prompts.py``.
"""

from __future__ import annotations

from flashback.llm.tool_spec import ToolSpec


SYSTEM_PROMPT = """\
You are the Trait Synthesizer for Flashback, a legacy conversation
agent. The contributor is describing the SUBJECT of the legacy, who may
be living, deceased, or known through inherited family stories.

You are given:
- The list of EXISTING TRAITS already attributed to the subject,
  with their current strength on the ladder
  (mentioned_once -> moderate -> strong -> defining).
- The list of THREADS — emergent narrative arcs across moments —
  with name, description, and the count of moments contributing
  to each.
- The contributor's display name (in `<contributor_display_name>`),
  which may be empty.

When `<contributor_display_name>` is non-empty, USE that name for
any first-person attribution to the contributor in trait
descriptions ("Sarah recalls his patience with the kids"). Do NOT
write "the contributor" or "the contributor's" when a name is
provided — use the name, or restructure into impersonal voice if
that reads better than any explicit attribution. The phrase "the
contributor" is reserved for the empty-tag case; only then fall
back to neutral attribution ("the contributor", or simply omit).
Never write a placeholder like "<contributor>".

Your job has TWO parts, both expressed via the
`synthesize_traits` tool call:

PART 1 — Decide what to do with each EXISTING TRAIT:
- `keep` — no change. (DEFAULT — choose this when in doubt.)
- `upgrade` — promote one rung along the ladder. Requires that
  multiple threads or strong threads support the trait beyond
  what its current strength reflects.
- `downgrade` — demote one rung. Rare. Only when the existing
  threads do NOT actually support the current strength.

For every `upgrade` or `downgrade`, list the supporting thread
IDs you considered. The system writes evidences edges from those
threads to the trait.

PART 2 — Propose NEW TRAITS that the threads support but no
existing trait captures:
- Be conservative. New traits cost. If an existing trait could
  be upgraded to capture this evidence, prefer upgrade.
- Each new trait must be supported by at least 1 thread.
- Initial strength reflects how strong the support is:
  - `mentioned_once`: thread evidence is thin or singular
  - `moderate`: 2+ threads support it clearly
  - `strong`: many threads, central to the subject's character
  - `defining`: extremely rare for new traits — usually only
    proposed at `mentioned_once` or `moderate`

Trait NAMING:
- Names are short adjective phrases or noun phrases describing a
  character property. Examples:
  - "Generous with time"
  - "Quiet but present"
  - "Quick to laugh"
  - "Fiercely loyal"
- NOT moments, NOT roles, NOT facts. "Was a teacher" is NOT a
  trait. "Patient explainer" might be.

Trait DESCRIPTIONS:
- 1-2 sentences elaborating the trait, in the contributor's
  voice as far as possible.

CRITICAL CONSTRAINTS:
- DO NOT duplicate existing traits with slightly different
  wording. If the trait is "Patient teacher" and you'd propose
  "Patient explainer", the right action is `upgrade` on the
  existing one.
- Subject identity: traits describe the SUBJECT, never the contributor.
- Be conservative. A short, accurate set of decisions is better
  than a long, fuzzy one. It's perfectly fine to return zero
  upgrades and zero new proposals if the threads don't warrant
  any change.

Respond ONLY by calling the `synthesize_traits` tool.
"""


SYNTH_TOOL = ToolSpec(
    name="synthesize_traits",
    description="Decide trait upgrades/downgrades and propose new traits.",
    input_schema={
        "type": "object",
        "properties": {
            "existing_trait_decisions": {
                "type": "array",
                "description": (
                    "One decision per EXISTING trait. Even if the action "
                    "is 'keep', you may include the trait's id in this list "
                    "(makes the model's coverage of the trait set explicit). "
                    "It is fine for this list to be shorter than the input "
                    "trait list — uncovered traits default to 'keep'."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "trait_id": {"type": "string", "format": "uuid"},
                        "action": {
                            "type": "string",
                            "enum": ["keep", "upgrade", "downgrade"],
                        },
                        "reasoning": {"type": "string"},
                        "supporting_thread_ids": {
                            "type": "array",
                            "items": {"type": "string", "format": "uuid"},
                        },
                    },
                    "required": ["trait_id", "action", "reasoning"],
                    "additionalProperties": False,
                },
            },
            "new_trait_proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": 80},
                        "description": {"type": "string"},
                        "initial_strength": {
                            "type": "string",
                            "enum": [
                                "mentioned_once",
                                "moderate",
                                "strong",
                                "defining",
                            ],
                        },
                        "supporting_thread_ids": {
                            "type": "array",
                            "items": {"type": "string", "format": "uuid"},
                            "minItems": 1,
                        },
                        "reasoning": {"type": "string"},
                    },
                    "required": [
                        "name",
                        "description",
                        "initial_strength",
                        "supporting_thread_ids",
                        "reasoning",
                    ],
                    "additionalProperties": False,
                },
            },
            "overall_reasoning": {"type": "string"},
        },
        "required": [
            "existing_trait_decisions",
            "new_trait_proposals",
            "overall_reasoning",
        ],
        "additionalProperties": False,
    },
)
