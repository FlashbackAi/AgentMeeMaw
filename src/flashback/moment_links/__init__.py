"""SP5: same-event links + contradiction review."""

from .repository import (
    acknowledge_event_link_async,
    canonical_pair,
    dismiss_contradiction_async,
    insert_contradiction,
    insert_same_event_link,
    list_contradictions_async,
    list_event_links_async,
    repoint_records_on_supersession,
    unlink_event_link_async,
)
from .schema import ContradictionItem, SameEventLink

__all__ = [
    "canonical_pair",
    "insert_contradiction",
    "insert_same_event_link",
    "repoint_records_on_supersession",
    "list_event_links_async",
    "list_contradictions_async",
    "acknowledge_event_link_async",
    "unlink_event_link_async",
    "dismiss_contradiction_async",
    "ContradictionItem",
    "SameEventLink",
]
