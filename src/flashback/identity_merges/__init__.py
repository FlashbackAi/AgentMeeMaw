"""User-approved entity merge workflow."""

from .repository import (
    approve_merge_async,
    create_entity_merge_suggestions,
    list_suggestions_async,
    reject_merge_async,
)
from .scanner import IdentityMergeCandidate, scan_identity_merge_suggestions_async
from .schema import (
    IdentityMergeActionResponse,
    IdentityMergeScanRequest,
    IdentityMergeScanResponse,
    IdentityMergeSuggestion,
)
from .verifier import IdentityMergeVerifier, IdentityMergeVerification

__all__ = [
    "IdentityMergeActionResponse",
    "IdentityMergeCandidate",
    "IdentityMergeScanRequest",
    "IdentityMergeScanResponse",
    "IdentityMergeVerification",
    "IdentityMergeVerifier",
    "IdentityMergeSuggestion",
    "approve_merge_async",
    "create_entity_merge_suggestions",
    "list_suggestions_async",
    "reject_merge_async",
    "scan_identity_merge_suggestions_async",
]
