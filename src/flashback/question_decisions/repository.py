"""Async repository over the question_decisions table.

One active row per (person_id, question_id). Recording a new decision
supersedes the prior active row first, then inserts the new one — as
two statements inside one transaction. They must NOT share a single
statement (the original used a data-modifying CTE): sub-statements in
a WITH run against the same snapshot, so the partial unique index
`idx_question_decisions_active` still saw the old active row when the
INSERT was checked, and the second decision on the same question blew
up with UniqueViolation.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from psycopg_pool import AsyncConnectionPool

from flashback.question_decisions.schema import Action, QuestionDecision

_SUPERSEDE = """
UPDATE question_decisions
   SET status = 'superseded'
 WHERE person_id   = %(person_id)s
   AND question_id = %(question_id)s
   AND status      = 'active'
RETURNING id
"""

_INSERT = """
INSERT INTO question_decisions (id, question_id, person_id, action)
VALUES (%(new_id)s, %(question_id)s, %(person_id)s, %(action)s)
RETURNING id, question_id, person_id, action, decided_at, status
"""

# superseded_by is stamped AFTER the insert — the FK requires the new
# row to exist before anything can reference it.
_STAMP_SUPERSEDED_BY = """
UPDATE question_decisions
   SET superseded_by = %(new_id)s
 WHERE id = ANY(%(old_ids)s)
"""

_LIST_ACTIVE = """
SELECT id, question_id, person_id, action, decided_at, status
  FROM active_question_decisions
 WHERE person_id = %(person_id)s
 ORDER BY decided_at DESC
"""

_LIST_HISTORY = """
SELECT id, question_id, person_id, action, decided_at, status
  FROM question_decisions
 WHERE person_id   = %(person_id)s
   AND question_id = %(question_id)s
 ORDER BY created_at DESC
"""


class QuestionDecisionRepository:
    def __init__(self, db_pool: AsyncConnectionPool) -> None:
        self._pool = db_pool

    async def record(
        self,
        person_id: UUID,
        question_id: UUID,
        action: Action,
    ) -> QuestionDecision:
        new_id = uuid4()
        params = {
            "new_id": new_id,
            "person_id": person_id,
            "question_id": question_id,
            "action": action,
        }
        async with self._pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(_SUPERSEDE, params)
                    old_ids = [r[0] for r in await cur.fetchall()]
                    await cur.execute(_INSERT, params)
                    row = await cur.fetchone()
                    if old_ids:
                        await cur.execute(
                            _STAMP_SUPERSEDED_BY,
                            {"new_id": new_id, "old_ids": old_ids},
                        )
        return _row_to_model(row)

    async def list_active(self, person_id: UUID) -> list[QuestionDecision]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_LIST_ACTIVE, {"person_id": person_id})
                rows = await cur.fetchall()
        return [_row_to_model(r) for r in rows]

    async def list_history(
        self, person_id: UUID, question_id: UUID
    ) -> list[QuestionDecision]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    _LIST_HISTORY,
                    {"person_id": person_id, "question_id": question_id},
                )
                rows = await cur.fetchall()
        return [_row_to_model(r) for r in rows]


def _row_to_model(row) -> QuestionDecision:
    return QuestionDecision(
        id=row[0],
        question_id=row[1],
        person_id=row[2],
        action=row[3],
        decided_at=row[4],
        status=row[5],
    )
