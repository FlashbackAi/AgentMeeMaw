"""
Node Edits — generic edit surface for canonical-graph nodes.

Lets a contributor (via Node) submit revised free text for an existing
``moment`` or ``entity`` row. The edit-LLM re-derives the structured
fields, the engine writes them transactionally, and the change fans
out to the embedding and ``artifact_generation`` queues.

Per CLAUDE.md s3 / s4 the agent service is the only writer for the
canonical graph. This module is a sibling to
:mod:`flashback.profile_facts` and :mod:`flashback.identity_merges` —
same shape: free-form input -> small structured graph mutation, with
embedding + artifact lifecycle handled in code.

Public surface:

* :class:`NodeEditConfig`, :data:`REGISTRY` — per-type knobs.
* :class:`NodeEditRequest`, :class:`NodeEditResponse` — HTTP shapes.
* :func:`edit_node` — engine entry point (async).
"""

from .engine import edit_node
from .registry import REGISTRY, NodeEditConfig
from .schema import NodeEditRequest, NodeEditResponse, NodeEditResult

__all__ = [
    "NodeEditConfig",
    "NodeEditRequest",
    "NodeEditResponse",
    "NodeEditResult",
    "REGISTRY",
    "edit_node",
]
