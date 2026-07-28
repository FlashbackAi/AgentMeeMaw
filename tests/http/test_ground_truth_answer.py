import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from flashback.http.ground_truth_answer import persist_ground_truth_answer
from flashback.http.models import GroundTruthAnswerInput


def _pending(kind="ground_truth", field="region", question="Where?"):
    return json.dumps({"kind": kind, "field": field, "question_text": question})


def _wm(pending_json):
    wm = AsyncMock()
    wm.get_state.return_value = SimpleNamespace(
        signal_pending_gt_tap=pending_json
    )
    return wm


@pytest.mark.asyncio
async def test_no_pending_tap_ignores_answer():
    wm = _wm("")
    answer = GroundTruthAnswerInput(kind="ground_truth", field="region",
                                    option_label="Karimnagar")
    await persist_ground_truth_answer(
        session_id=uuid4(), person_id=uuid4(), answer=answer,
        wm=wm, db_pool=None,
    )
    wm.clear_pending_gt_tap.assert_not_awaited()


@pytest.mark.asyncio
async def test_skipped_marks_declined_and_clears():
    wm = _wm(_pending())
    answer = GroundTruthAnswerInput(kind="ground_truth", field="region",
                                    skipped=True)
    await persist_ground_truth_answer(
        session_id=uuid4(), person_id=uuid4(), answer=answer,
        wm=wm, db_pool=None,
    )
    wm.add_gt_declined_field.assert_awaited_once()
    wm.clear_pending_gt_tap.assert_awaited_once()


@pytest.mark.asyncio
async def test_segment_anchor_answer_goes_to_working_memory():
    wm = _wm(_pending(kind="segment_anchor", field=None,
                      question="About when was that?"))
    answer = GroundTruthAnswerInput(kind="segment_anchor",
                                    option_label="In the 1970s")
    await persist_ground_truth_answer(
        session_id=uuid4(), person_id=uuid4(), answer=answer,
        wm=wm, db_pool=None,
    )
    wm.set_segment_anchor.assert_awaited_once()
    kwargs = wm.set_segment_anchor.await_args.kwargs
    assert kwargs["answer"] == "In the 1970s"
    wm.clear_pending_gt_tap.assert_awaited_once()
