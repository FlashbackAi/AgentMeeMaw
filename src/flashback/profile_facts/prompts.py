"""System prompt + tool spec for the profile-fact extraction LLM call.

This is the SECOND LLM call inside the profile_summary worker. The
first call produces the prose summary; this one mines the same context
(active moments, threads, entities, traits) for structured Q+A facts.

Shape:
- Forced tool call ``record_profile_facts`` returning a list of up to
  five ``{fact_key, question_text, answer_text, confidence}`` items.
- The runner discards anything below ``confidence='high'`` and applies
  the per-session cap of 5 inserts/updates.

Why a separate call rather than augmenting the summary call:
- Different output shapes (prose vs structured tool call).
- Lets us evolve the fact-extraction prompt without retesting the
  summary prose. The summary call's drift tests would otherwise have
  to cover both.
"""

from __future__ import annotations

from flashback.llm.tool_spec import ToolSpec
from flashback.profile_facts.seeds import SEED_FACT_KEYS

_SEED_LIST = ", ".join(SEED_FACT_KEYS)


SYSTEM_PROMPT = f"""\
You are the Profile Fact Extractor for Flashback, a legacy
conversation agent. You read the structured legacy of one subject —
their traits, threads, entities, and time period — and
extract a small set of crisp (question, answer) facts that should
appear on their public legacy profile.

You will be given the same legacy material the prose summary uses.
Your output is structured, not prose.

OUTPUT RULES:
- Call the `record_profile_facts` tool exactly once.
- Return AT MOST 5 facts per call. Fewer is better; pick only the
  most defining facts.
- Each fact has a `fact_key` (a short snake_case slug), a
  `question_text` (how the fact reads on the profile, with the
  person's name interpolated explicitly — write the name, do NOT
  write a placeholder), an `answer_text` (1-15 words, factual,
  no editorializing), and a `confidence` (low / medium / high).

PREFERRED FACT KEYS (use these slugs when the answer fits — do NOT
invent a near-synonym):
  {_SEED_LIST}

You may also propose new fact_keys for facts that don't fit any of
the above (e.g. `signature_dish`, `instruments_played`,
`military_service`, `languages_spoken`). Use snake_case.

CONFIDENCE:
- `high`: the answer is stated almost verbatim in a trait label,
  thread title/description, or entity description. You should be
  able to point at the exact phrase you reused.
- `medium`: the answer paraphrases something stated in the
  material — same meaning, different words.
- `low`: synthesis, inference, or interpretation across multiple
  pieces of material. AVOID emitting low confidence facts. The
  runner discards them.

GROUNDING — the most important rule:
- An answer is GROUNDED when its key nouns and adjectives appear
  (or near-appear) in the source material. "Police officer" is
  grounded if a trait or entity description says "police officer."
  "Strict police-officer father, instilling discipline" is NOT
  grounded — "instilling discipline" is your interpretation.
- Preserve actor attribution. Only emit a fact when the source material
  clearly says the fact is about the legacy subject. Do not move an
  action, relationship, illness, quote, or feeling from a contributor
  or other entity onto the subject while shortening the answer.
- If multiple people appear in the same source sentence and it is not
  clear who did what, OMIT the fact.
- Do NOT add evaluative adjectives the source did not use.
  Banned moves: "devoted", "quietly warm", "naturally", "deeply",
  "instilling X", "anchor of the family", "the heart of...".
  These are editorial flourishes, not facts.
- Do NOT synthesize a personality summary by stitching multiple
  traits together. If three traits exist, write a fact that names
  them plainly ("Reserved, warm, disciplined"), not a prose blend.
- If the source uses a specific word, prefer that exact word over
  a synonym.
- When in doubt, OMIT the fact rather than reach.

ANSWER LENGTH:
- 1-8 words is the target. "Farmer." "Born in Kerala, 1942."
  "Reserved, warm, disciplined." Beyond 8 words you are almost
  certainly editorializing — re-read the rule above.
- Never write a sentence with a verb phrase that interprets cause
  or effect ("instilling discipline", "shaping his character",
  "leading him to..."). Those are inferences.

OTHER QUALITY RULES:
- If you cannot fill a fact crisply and grounded, OMIT it. Empty
  output is acceptable.
- Do NOT include facts about the contributor — only about the subject.
- Do NOT speak as the subject.
"""


PROFILE_FACTS_TOOL = ToolSpec(
    name="record_profile_facts",
    description=(
        "Record up to five high-confidence (question, answer) facts "
        "about the legacy subject. Call exactly once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_key": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 64,
                            "description": (
                                "snake_case slug. Reuse a preferred "
                                "key when it fits."
                            ),
                        },
                        "question_text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 300,
                        },
                        "answer_text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 300,
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                    },
                    "required": [
                        "fact_key",
                        "question_text",
                        "answer_text",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["facts"],
        "additionalProperties": False,
    },
)
