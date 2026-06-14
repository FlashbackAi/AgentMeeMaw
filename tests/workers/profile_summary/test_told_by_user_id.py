"""Unit tests for told_by_user_id provenance in ProfileSummaryMessage and worker.

No DB required. All tests are pure model/schema/unit tests.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from flashback.workers.profile_summary.schema import ProfileSummaryMessage
from flashback.workers.profile_summary.sqs_client import ReceivedProfileSummaryMessage


# ---------------------------------------------------------------------------
# ProfileSummaryMessage schema
# ---------------------------------------------------------------------------


def test_profile_summary_message_parses_told_by_user_id():
    """ProfileSummaryMessage round-trips told_by_user_id."""
    uid = uuid4()
    msg = ProfileSummaryMessage.model_validate(
        {
            "person_id": str(uuid4()),
            "told_by_user_id": str(uid),
        }
    )
    assert msg.told_by_user_id == uid


def test_profile_summary_message_told_by_user_id_defaults_none():
    """Absent told_by_user_id parses to None."""
    msg = ProfileSummaryMessage.model_validate({"person_id": str(uuid4())})
    assert msg.told_by_user_id is None


def test_profile_summary_message_null_told_by_user_id_is_none():
    """Explicit null told_by_user_id parses to None."""
    msg = ProfileSummaryMessage.model_validate(
        {"person_id": str(uuid4()), "told_by_user_id": None}
    )
    assert msg.told_by_user_id is None
