"""Per-type registry of editable nodes.

Each entry declares everything the generic engine needs to route an
edit through the right strategy: which table, the free-text input
field, which fields the LLM may rewrite, the embedding ``record_type``
the embedding worker expects, the edge handling strategy, the
mutation strategy (supersede vs in-place), and whether an edit
triggers artifact regeneration.

Adding a new editable node type = add a :class:`NodeEditConfig` entry
plus a prompt block in :mod:`flashback.node_edits.prompts`. No engine
changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from flashback.llm.tool_spec import ToolSpec

from .prompts import (
    ENTITY_EDIT_PROMPT_VERSION,
    ENTITY_EDIT_SYSTEM_PROMPT,
    ENTITY_EDIT_TOOL,
    MOMENT_EDIT_PROMPT_VERSION,
    MOMENT_EDIT_SYSTEM_PROMPT,
    MOMENT_EDIT_TOOL,
)

MutationStrategy = Literal["supersede", "in_place"]
EdgeStrategy = Literal["re_extract_references", "none"]
ArtifactKind = Literal["image", "video"]


@dataclass(frozen=True)
class NodeEditConfig:
    """Knobs for one editable node type.

    Field semantics:

    * ``primary_text_field`` — the column whose contents the contributor
      is editing (``narrative`` for moments, ``description`` for
      entities).
    * ``immutable_fields`` — columns the edit must not touch. The
      LLM tool schema does NOT expose these, and the persistence
      strategy carries them forward verbatim from the prior row.
    * ``embedded_record_type`` — string the embedding worker expects
      in the SQS body (``moment`` / ``entity``).
    * ``edge_strategy`` — what happens to outbound edges. Moments
      re-extract entity references; entities leave edges alone (a
      description change should not repoint who-references-this).
    * ``mutation_strategy`` — ``supersede`` (insert new + flip old to
      ``superseded``, repoint inbound edges; the moment pattern) vs
      ``in_place`` (UPDATE columns + clear embedding fields; the
      identity-merge survivor pattern).
    * ``artifact_regen`` — push a fresh job onto
      ``artifact_generation`` after commit. We always regen on edit:
      even small description changes (e.g. physical appearance) can
      shift the visual.
    * ``artifact_kind`` — ``video`` for moments, ``image`` for
      entities. ``None`` skips the artifact push entirely.
    """

    node_type: str
    table: str
    primary_text_field: str
    immutable_fields: tuple[str, ...]
    embedded_record_type: str
    edge_strategy: EdgeStrategy
    mutation_strategy: MutationStrategy
    artifact_regen: bool
    artifact_kind: ArtifactKind | None
    llm_tool: ToolSpec
    llm_system_prompt: str
    prompt_version: str


MOMENT_EDIT_CONFIG = NodeEditConfig(
    node_type="moment",
    table="moments",
    primary_text_field="narrative",
    immutable_fields=(
        "id",
        "person_id",
        "status",
        "superseded_by",
        "narrative_embedding",
        "embedding_model",
        "embedding_model_version",
        "video_url",
        "thumbnail_url",
        "created_at",
    ),
    embedded_record_type="moment",
    edge_strategy="re_extract_references",
    mutation_strategy="supersede",
    artifact_regen=True,
    artifact_kind="video",
    llm_tool=MOMENT_EDIT_TOOL,
    llm_system_prompt=MOMENT_EDIT_SYSTEM_PROMPT,
    prompt_version=MOMENT_EDIT_PROMPT_VERSION,
)


ENTITY_EDIT_CONFIG = NodeEditConfig(
    node_type="entity",
    table="entities",
    primary_text_field="description",
    immutable_fields=(
        "id",
        "person_id",
        "kind",
        "name",
        "status",
        "merged_into",
        "description_embedding",
        "embedding_model",
        "embedding_model_version",
        "image_url",
        "thumbnail_url",
        "created_at",
    ),
    embedded_record_type="entity",
    edge_strategy="none",
    mutation_strategy="in_place",
    artifact_regen=True,
    artifact_kind="image",
    llm_tool=ENTITY_EDIT_TOOL,
    llm_system_prompt=ENTITY_EDIT_SYSTEM_PROMPT,
    prompt_version=ENTITY_EDIT_PROMPT_VERSION,
)


REGISTRY: dict[str, NodeEditConfig] = {
    "moment": MOMENT_EDIT_CONFIG,
    "entity": ENTITY_EDIT_CONFIG,
}
