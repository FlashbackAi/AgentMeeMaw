"""Disposition policy for verified identity-merge candidates.

Maps a verifier (verdict, confidence) onto one of three actions:

  * ``auto_merge`` — apply the merge silently and notify the user
    (reversible via unmerge). Reserved for near-certainty.
  * ``ask``        — write a pending suggestion for explicit review.
  * ``drop``       — write nothing.

Policy (conservative; see the 2026-06-06 design doc §5.4):

  same_identity + high   -> auto_merge
  same_identity + medium -> ask
  same_identity + low    -> drop   (too weak to even ask about)
  unsure (any conf)      -> ask    (unsure means "needs a human")
  different_identity      -> drop
  cross-kind candidates never reach here — the candidate gate excludes
  them — but if one did, it is treated as different_identity.

Kept as a tiny pure function so the policy is trivially unit-testable
without a database or an LLM.
"""

from __future__ import annotations

from typing import Literal

Verdict = Literal["same_identity", "different_identity", "unsure"]
Confidence = Literal["low", "medium", "high"]
Disposition = Literal["auto_merge", "ask", "drop"]


def decide_disposition(verdict: str, confidence: str) -> Disposition:
    """Return the action for a verified candidate. See module docstring."""
    if verdict == "different_identity":
        return "drop"
    if verdict == "unsure":
        return "ask"
    if verdict == "same_identity":
        if confidence == "high":
            return "auto_merge"
        if confidence == "medium":
            return "ask"
        return "drop"  # same_identity + low confidence
    # Unknown verdict — be conservative.
    return "drop"
