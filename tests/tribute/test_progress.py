"""DB tests for the tribute_status view via fetch_tribute_progress_sync.

Walks a tribute from 0% (empty graph) to 100%/ready by filling each
checklist slot, asserting the view's weighted percent at each step.
"""

from __future__ import annotations

import json

from flashback.tribute.progress import fetch_tribute_progress_sync
from flashback.tribute.repository import insert_tribute_sync, set_message_sync


def _slot(progress, key: str):
    return next(s for s in progress.slots if s.key == key)


def _add_qualifying_moment(cur, person_id: str, title: str) -> None:
    # sensory_details non-empty => qualifying.
    cur.execute(
        """
        INSERT INTO moments (person_id, title, narrative, sensory_details)
        VALUES (%s, %s, %s, %s)
        """,
        (person_id, title, "a narrative", "the smell of diesel and rain"),
    )


def _set_appearance_ground_truth(cur, person_id: str) -> None:
    gt = {
        "region": {"value": "South India", "provenance": "tap",
                   "confidence": "high", "updated_at": "2026-06-14T00:00:00Z"},
        "birth_era": {"value": "1950s", "provenance": "onboarding",
                      "confidence": "medium", "updated_at": "2026-06-14T00:00:00Z"},
        "attire": {"value": "white cotton shirt", "provenance": "inferred",
                   "confidence": "medium", "updated_at": "2026-06-14T00:00:00Z"},
    }
    cur.execute(
        "UPDATE persons SET ground_truth = %s WHERE id = %s",
        (json.dumps(gt), person_id),
    )


def _add_trait(cur, person_id: str) -> None:
    cur.execute(
        "INSERT INTO traits (person_id, name, description, status) "
        "VALUES (%s, 'patient', NULL, 'active')",
        (person_id,),
    )


def test_progress_empty_tribute_is_zero(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    assert progress.percent == 0
    assert progress.ready is False
    assert all(s.filled is False for s in progress.slots)


def test_progress_fills_each_slot_to_ready(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)

            # memories: 3 qualifying moments => +40
            for i in range(3):
                _add_qualifying_moment(cur, person_id, f"Memory {i}")
            # appearance: ground_truth => +20
            _set_appearance_ground_truth(cur, person_id)
            # signature: one active trait => +10
            _add_trait(cur, person_id)
            # message: => +30
            set_message_sync(
                cur, tribute_id=tribute_id, message_text="Thank you, Dad."
            )
            conn.commit()

            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    assert _slot(progress, "memories").filled is True
    assert _slot(progress, "appearance").filled is True
    assert _slot(progress, "signature").filled is True
    assert _slot(progress, "message").filled is True
    assert progress.percent == 100
    assert progress.ready is True


def test_progress_partial_memories_scale_weight(db_pool, make_person) -> None:
    # 2 of 3 memories => 2/3 * 40 ~= 27, no other slots => not ready.
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            _add_qualifying_moment(cur, person_id, "Memory A")
            _add_qualifying_moment(cur, person_id, "Memory B")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    assert _slot(progress, "memories").filled is False  # needs 3
    assert progress.percent == 27  # floor(2/3 * 40)
    assert progress.ready is False
