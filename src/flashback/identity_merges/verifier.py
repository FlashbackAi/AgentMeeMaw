"""Small-LLM verifier for identity merge candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from flashback.llm import Provider, ToolSpec, call_with_tool


SYSTEM_PROMPT = """You verify whether two extracted entity rows refer to the same real-world identity.

Be conservative. Return same_identity only when the evidence says the two labels are the same person, place, object, or concept. Do not merge entities merely because they are related, friends, family, coworkers, romantically involved, or appear in the same memory.

Good same-identity evidence includes:
- The contributor explicitly corrected one label into the other.
- One row's aliases or description says it is also known by the other row's name.
- The rows have the same name and compatible descriptions.

Return unsure when the evidence is thin or could describe two related but separate entities.
"""


VERIFY_TOOL = ToolSpec(
    name="verify_identity_merge",
    description="Decide whether two extracted entity rows should be merged.",
    input_schema={
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["same_identity", "different_identity", "unsure"],
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "reasoning": {"type": "string"},
        },
        "required": ["verdict", "confidence", "reasoning"],
        "additionalProperties": False,
    },
)


class IdentityMergeVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["same_identity", "different_identity", "unsure"]
    confidence: Literal["low", "medium", "high"]
    reasoning: str


@dataclass(frozen=True)
class IdentityMergeVerifier:
    """LLM-backed verifier used after deterministic candidate gating."""

    settings: object
    provider: Provider
    model: str
    timeout: float
    max_tokens: int

    async def verify(self, candidate) -> IdentityMergeVerification:
        args = await call_with_tool(
            provider=self.provider,
            model=self.model,
            system_prompt=SYSTEM_PROMPT,
            user_message=_render_candidate(candidate),
            tool=VERIFY_TOOL,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            settings=self.settings,
        )
        return IdentityMergeVerification.model_validate(args)


def _render_candidate(candidate) -> str:
    return "\n".join(
        [
            "<candidate>",
            f"heuristic: {candidate.reason_kind}",
            f"embedding_distance: {candidate.embedding_distance}",
            "",
            "<source_entity>",
            f"id: {candidate.source_id}",
            f"kind: {candidate.kind}",
            f"name: {candidate.source_name}",
            f"aliases: {', '.join(candidate.source_aliases) if candidate.source_aliases else '(none)'}",
            f"description: {candidate.source_description or '(none)'}",
            "</source_entity>",
            "",
            "<target_entity>",
            f"id: {candidate.target_id}",
            f"kind: {candidate.kind}",
            f"name: {candidate.target_name}",
            f"aliases: {', '.join(candidate.target_aliases) if candidate.target_aliases else '(none)'}",
            f"description: {candidate.target_description or '(none)'}",
            "</target_entity>",
            "</candidate>",
        ]
    )
