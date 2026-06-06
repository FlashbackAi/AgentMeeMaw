"""User-approved entity merge workflow."""

from .disposition import decide_disposition
from .repository import (
    acknowledge_auto_merge_async,
    approve_merge_async,
    auto_merge_async,
    list_auto_merged_async,
    list_suggestions_async,
    reject_merge_async,
    unmerge_async,
)
from .scanner import IdentityMergeCandidate, scan_identity_merge_suggestions_async
from .schema import (
    AutoMergeNotification,
    IdentityMergeActionResponse,
    IdentityMergeScanRequest,
    IdentityMergeScanResponse,
    IdentityMergeSuggestion,
    UnmergeResponse,
)
from .verifier import IdentityMergeVerifier, IdentityMergeVerification

__all__ = [
    "AutoMergeNotification",
    "IdentityMergeActionResponse",
    "IdentityMergeCandidate",
    "IdentityMergeScanRequest",
    "IdentityMergeScanResponse",
    "IdentityMergeVerification",
    "IdentityMergeVerifier",
    "IdentityMergeSuggestion",
    "UnmergeResponse",
    "acknowledge_auto_merge_async",
    "approve_merge_async",
    "auto_merge_async",
    "decide_disposition",
    "list_auto_merged_async",
    "list_suggestions_async",
    "reject_merge_async",
    "scan_identity_merge_suggestions_async",
    "unmerge_async",
]
