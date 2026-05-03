"""Database access layer for the agent service."""

from .edges import (
    VALID_EDGE_PATTERNS,
    VALID_KINDS,
    EdgeValidationError,
    allowed_sources,
    allowed_targets,
    validate_edge,
)

__all__ = [
    "VALID_EDGE_PATTERNS",
    "VALID_KINDS",
    "EdgeValidationError",
    "allowed_sources",
    "allowed_targets",
    "validate_edge",
]
