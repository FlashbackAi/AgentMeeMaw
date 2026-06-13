"""DB round-trip tests for the tributes repository."""

from __future__ import annotations

from flashback.tribute.repository import (
    fetch_tribute_sync,
    insert_tribute_sync,
    set_message_sync,
    set_status_sync,
)


def test_insert_and_fetch_draft_tribute(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            conn.commit()
            row = fetch_tribute_sync(cur, tribute_id=tribute_id)

    assert row is not None
    assert row.person_id == person_id
    assert row.status == "draft"
    assert row.message_text is None
    assert row.theme_id is None


def test_set_message_persists_text_and_source_turns(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            set_message_sync(
                cur,
                tribute_id=tribute_id,
                message_text="I never said it, but thank you.",
                source_turns=[{"role": "user", "text": "raw words"}],
            )
            conn.commit()
            row = fetch_tribute_sync(cur, tribute_id=tribute_id)

    assert row is not None
    assert row.message_text == "I never said it, but thank you."


def test_set_status_advances_lifecycle(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            set_status_sync(cur, tribute_id=tribute_id, status="ready")
            conn.commit()
            row = fetch_tribute_sync(cur, tribute_id=tribute_id)

    assert row is not None
    assert row.status == "ready"


def test_fetch_missing_tribute_returns_none(db_pool) -> None:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            row = fetch_tribute_sync(
                cur, tribute_id="00000000-0000-0000-0000-000000000000"
            )
    assert row is None
