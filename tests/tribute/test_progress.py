"""DB tests for the tribute_status view via fetch_tribute_progress_sync.

Walks a tribute from 0% (empty graph) to 100%/ready by filling each
checklist slot, asserting the view's weighted percent at each step.
"""

from __future__ import annotations

import json

from flashback.tribute.campaigns import resolve_campaign
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


def _add_deep_moment(cur, person_id: str, title: str) -> None:
    # >80 chars of sensory + a year time_anchor => depth bonuses (0030).
    long_sensory = (
        "the smell of diesel and rain on the platform, his cracked hands, "
        "the cold steel bench, the 4 a.m. dark before the first train"
    )
    cur.execute(
        """
        INSERT INTO moments (person_id, title, narrative, sensory_details,
                             time_anchor)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (person_id, title, "a narrative", long_sensory, json.dumps({"year": 1974})),
    )


def _make_tribute_theme(cur, person_id: str, answers: list[dict]) -> str:
    cur.execute(
        """
        INSERT INTO themes (person_id, kind, slug, display_name, state,
                            status, archetype_answers)
        VALUES (%s, 'tribute', 'tribute', 'A Tribute', 'unlocked', 'active', %s)
        RETURNING id::text
        """,
        (person_id, json.dumps(answers)),
    )
    return cur.fetchone()[0]


def test_answer_floor_lifts_percent_without_moments(db_pool, make_person) -> None:
    # Rich archetype answers but an empty graph: the meter reflects captured
    # intent (off zero) yet stays NOT ready -- answers are leads, not facts.
    person_id = make_person("Dad")
    answers = [
        {"question_id": "q10", "option_label": "Sold a home"},
        {"question_id": "q11", "option_label": "Skipped meals"},
        {"question_id": "q14", "free_text": "I love you"},
        {"question_id": "q9", "skipped": True},  # no choice -> not counted
    ]
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            theme_id = _make_tribute_theme(cur, person_id, answers)
            tribute_id = insert_tribute_sync(
                cur, person_id=person_id, theme_id=theme_id
            )
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    assert progress.answered_layers == 3  # the skipped one doesn't count
    assert progress.percent == 9  # round(3/14 * 40)
    assert progress.ready is False
    assert _slot(progress, "memories").filled is False


def test_answer_floor_caps_at_16(db_pool, make_person) -> None:
    # All 14 layers answered => floor caps at 0.4 * 40 = 16, never higher.
    person_id = make_person("Dad")
    answers = [{"question_id": f"q{i}", "option_label": "x"} for i in range(1, 15)]
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            theme_id = _make_tribute_theme(cur, person_id, answers)
            tribute_id = insert_tribute_sync(
                cur, person_id=person_id, theme_id=theme_id
            )
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.answered_layers == 14
    assert progress.percent == 16  # capped floor, no other slots


def test_depth_weighting_two_vivid_moments_fill_memories(db_pool, make_person) -> None:
    # Two depth-weighted moments (score 2.0 each) saturate the memories %
    # contribution (40) even though only 2 stories exist -- but READY still
    # needs 3 raw qualifying moments, so it stays not ready.
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            _add_deep_moment(cur, person_id, "Deep A")
            _add_deep_moment(cur, person_id, "Deep B")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    assert progress.percent == 40  # depth-weighted memories maxed
    assert _slot(progress, "memories").filled is False  # only 2 raw stories
    assert progress.ready is False


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


def test_progress_exposes_next_count_and_hint(db_pool, make_person) -> None:
    # Granular fields: next points at the first unfilled slot, memories
    # carries count/target, every slot carries actionable hint copy.
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            _add_qualifying_moment(cur, person_id, "Memory A")
            _add_qualifying_moment(cur, person_id, "Memory B")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress is not None
    # memories is the first unfilled slot (2 of 3).
    assert progress.next_key == "memories"
    mem = _slot(progress, "memories")
    assert (mem.count, mem.target) == (2, 3)
    assert all(s.hint for s in progress.slots)
    # Non-memory slots are binary -- no count/target.
    assert _slot(progress, "message").count is None
    assert _slot(progress, "message").target is None
    # Neutral default title when no campaign skin is supplied.
    assert progress.title == "A Tribute"


def test_progress_campaign_skins_title_and_message_hint(db_pool, make_person) -> None:
    # The Father's Day skin overrides the meter title and the message hint;
    # other hints stay skin-neutral.
    person_id = make_person("Dad")
    campaign = resolve_campaign("fathers_day_2026")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            conn.commit()
            progress = fetch_tribute_progress_sync(
                cur, tribute_id=tribute_id, campaign=campaign
            )

    assert progress is not None
    assert progress.title == campaign.display_name
    assert _slot(progress, "message").hint == campaign.message_card_copy
    # A non-message slot keeps the neutral checklist copy.
    assert _slot(progress, "appearance").hint == "A few details so we can picture them."
