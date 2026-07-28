"""Regression test for QuestionDecisionRepository.record.

The original implementation superseded + inserted in ONE statement (a
data-modifying CTE). Postgres runs WITH sub-statements against the same
snapshot, so the partial unique index ``idx_question_decisions_active``
still saw the old active row when the INSERT was checked — the second
decision on the same question raised UniqueViolation in production.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from flashback.question_decisions import QuestionDecisionRepository

pytestmark = pytest.mark.asyncio


async def _seed_person_and_question(pool) -> tuple:
    person_id, question_id = uuid4(), uuid4()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO persons (id, name, relationship) "
                "VALUES (%s, 'Maya', 'mother')",
                (person_id,),
            )
            await cur.execute(
                "INSERT INTO questions (id, person_id, text, source, attributes) "
                "VALUES (%s, %s, 'What did she do for work?', "
                "'dropped_reference', '{\"themes\": [\"career\"]}')",
                (question_id, person_id),
            )
    return person_id, question_id


async def test_second_decision_on_same_question_supersedes(async_db_pool):
    person_id, question_id = await _seed_person_and_question(async_db_pool)
    repo = QuestionDecisionRepository(async_db_pool)

    first = await repo.record(
        person_id=person_id, question_id=question_id, action="skip"
    )
    assert first.action == "skip"
    assert first.status == "active"

    # The production bug: this second record() raised UniqueViolation.
    second = await repo.record(
        person_id=person_id, question_id=question_id, action="defer"
    )
    assert second.action == "defer"
    assert second.status == "active"

    active = await repo.list_active(person_id)
    assert [d.action for d in active] == ["defer"]

    history = await repo.list_history(person_id, question_id)
    assert len(history) == 2
    assert {d.status for d in history} == {"active", "superseded"}


async def test_third_decision_still_works(async_db_pool):
    person_id, question_id = await _seed_person_and_question(async_db_pool)
    repo = QuestionDecisionRepository(async_db_pool)

    for action in ("skip", "defer", "suppress"):
        decision = await repo.record(
            person_id=person_id, question_id=question_id, action=action
        )
        assert decision.action == action

    active = await repo.list_active(person_id)
    assert [d.action for d in active] == ["suppress"]
