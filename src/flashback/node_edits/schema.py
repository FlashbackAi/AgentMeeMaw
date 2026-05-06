"""HTTP request / response shapes and engine result type for node_edits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NodeType = Literal["moment", "entity"]


class NodeEditRequest(BaseModel):
    """Body of ``POST /nodes/{node_type}/{id}/edit``.

    ``free_text`` is the contributor's revised prose for the node's
    primary text field (``narrative`` for moments, ``description`` for
    entities). ``person_id`` is required so the engine can verify the
    row belongs to that legacy and refuse cross-legacy edits.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    free_text: str = Field(min_length=1, max_length=8000)


class NodeEditResponse(BaseModel):
    """HTTP response for the edit endpoint."""

    node_type: NodeType
    node_id: UUID
    superseded_id: UUID | None = None
    new_entity_ids: list[UUID] = Field(default_factory=list)
    edges_added: int = 0
    edges_removed: int = 0
    artifact_queued: bool = False
    embedding_jobs_pushed: int = 0


@dataclass(frozen=True)
class NodeEditResult:
    """Engine's internal result. Converted to :class:`NodeEditResponse`."""

    node_type: str
    node_id: str
    superseded_id: str | None = None
    new_entity_ids: list[str] = field(default_factory=list)
    edges_added: int = 0
    edges_removed: int = 0
    artifact_queued: bool = False
    embedding_jobs_pushed: int = 0
