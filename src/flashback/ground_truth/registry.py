"""Ground-truth field registry (design 2026-06-11, CLAUDE.md invariant #26).

Each field is a stable fact about the SUBJECT, stored under its key in
``persons.ground_truth``. ``askable`` fields may be asked via contextual
tap cards; inferred-only fields fill exclusively from extraction
observations; ``era_span`` is derived in code from moment time anchors.

Complexion / ethnicity is deliberately NOT a field — prompts derive it
from region + birth_era + cultural_context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ValueType = Literal["text", "list"]


@dataclass(frozen=True)
class GroundTruthField:
    key: str
    description: str  # shown to the selection + extraction LLMs
    askable: bool
    value_type: ValueType
    example_question: str  # seed phrasing; the selection LLM rephrases contextually


REGISTRY: tuple[GroundTruthField, ...] = (
    GroundTruthField(
        key="region",
        description=(
            "Where most of the subject's life happened — town/city, state, "
            "country (e.g. 'Karimnagar, Telangana, India')."
        ),
        askable=True,
        value_type="text",
        example_question="Where did most of their life happen?",
    ),
    GroundTruthField(
        key="birth_era",
        description=(
            "Decade the subject was born, approximately (e.g. '1950s'). "
            "Never a date of birth."
        ),
        askable=True,
        value_type="text",
        example_question="Roughly when were they born?",
    ),
    GroundTruthField(
        key="setting_type",
        description=(
            "The kind of place their life happened: village, small town, "
            "city, or farm."
        ),
        askable=True,
        value_type="text",
        example_question="What kind of place was that?",
    ),
    GroundTruthField(
        key="attire",
        description=(
            "What the subject usually wore (e.g. 'cotton saree', "
            "'shirt and lungi', 'always in a suit')."
        ),
        askable=True,
        value_type="text",
        example_question="What did they usually wear?",
    ),
    GroundTruthField(
        key="distinctive_features",
        description=(
            "Always-there physical details: glasses, mustache, braided "
            "hair, a walking stick."
        ),
        askable=True,
        value_type="list",
        example_question="When you picture them, is anything always there?",
    ),
    GroundTruthField(
        key="build",
        description=(
            "Overall physical impression: tall, slight, heavyset, wiry."
        ),
        askable=True,
        value_type="text",
        example_question="How would you picture them standing in a room?",
    ),
    GroundTruthField(
        key="cultural_context",
        description=(
            "Cultural / community background as it naturally surfaced "
            "(e.g. 'Telugu Hindu family'). Inferred only — NEVER asked."
        ),
        askable=False,
        value_type="text",
        example_question="",
    ),
    GroundTruthField(
        key="era_span",
        description=(
            "Decades the recalled memories span (e.g. ['1960s','1970s']). "
            "Derived from moment time anchors — never asked or LLM-emitted."
        ),
        askable=False,
        value_type="list",
        example_question="",
    ),
    GroundTruthField(
        key="languages",
        description="Languages the subject spoke at home / daily.",
        askable=True,
        value_type="list",
        example_question="Which language was home for them?",
    ),
)

REGISTRY_BY_KEY: dict[str, GroundTruthField] = {f.key: f for f in REGISTRY}
ASKABLE_KEYS: tuple[str, ...] = tuple(f.key for f in REGISTRY if f.askable)
# Everything the extraction LLM may observe. era_span is code-derived only.
INFERRABLE_KEYS: tuple[str, ...] = tuple(
    f.key for f in REGISTRY if f.key != "era_span"
)
