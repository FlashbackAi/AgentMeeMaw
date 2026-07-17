"""Per-campaign archetype answers (migration 0042).

A completed campaign's answers must not bleed into a new campaign's
tribute, merges are keyed by question_text, and the meter prefers the
tribute row's answers over the shared theme row.
"""

from __future__ import annotations

import pytest
from psycopg.types.json import Json

from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import (
    ensure_open_tribute_async,
    fetch_tribute_archetype_answers_async,
    merge_tribute_archetype_answers_async,
)
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

pytestmark = pytest.mark.asyncio


async def _seed(pool) -> tuple[str, str]:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') "
                    "RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                theme_id = await ensure_tribute_theme_async(
                    cur, person_id=person_id, slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )
    return person_id, theme_id


def _ans(text: str, label: str) -> dict:
    return {"question_id": "q1", "question_text": text,
            "option_labels": [label]}


async def test_merge_accumulates_and_new_wins(async_pool) -> None:
    person_id, theme_id = await _seed(async_pool)
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                tid = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                await merge_tribute_archetype_answers_async(
                    cur, tribute_id=tid,
                    answers=[_ans("Favourite trip?", "The hills")],
                )
                await merge_tribute_archetype_answers_async(
                    cur, tribute_id=tid,
                    answers=[_ans("Favourite trip?", "The coast"),
                             _ans("His best joke?", "The parrot one")],
                )
                got = await fetch_tribute_archetype_answers_async(
                    cur, tribute_id=tid
                )
    by_text = {a["question_text"]: a for a in got}
    assert len(got) == 2
    assert by_text["Favourite trip?"]["option_labels"] == ["The coast"]
    assert by_text["His best joke?"]["option_labels"] == ["The parrot one"]


async def test_new_campaign_tribute_does_not_inherit_committed_answers(
    async_pool,
) -> None:
    """The FD tribute's answers stay on the FD tribute; a friendship-day
    tribute starts with its own empty set (the meter may still fall back
    to theme answers for warmth, but the per-row truth is separate)."""
    person_id, theme_id = await _seed(async_pool)
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO tribute_campaigns (slug, display_name, "
                    "state) VALUES ('answers_a_test', 'A', 'published') "
                    "RETURNING id::text"
                )
                (camp_a,) = await cur.fetchone()
                await cur.execute(
                    "INSERT INTO tribute_campaigns (slug, display_name, "
                    "state) VALUES ('answers_b_test', 'B', 'published') "
                    "RETURNING id::text"
                )
                (camp_b,) = await cur.fetchone()
                try:
                    ta = await ensure_open_tribute_async(
                        cur, person_id=person_id, theme_id=theme_id,
                        campaign_id=camp_a,
                    )
                    await merge_tribute_archetype_answers_async(
                        cur, tribute_id=ta,
                        answers=[_ans("FD question?", "FD answer")],
                    )
                    await cur.execute(
                        "UPDATE tributes SET status = 'complete' "
                        "WHERE id = %s", (ta,))
                    tb = await ensure_open_tribute_async(
                        cur, person_id=person_id, theme_id=theme_id,
                        campaign_id=camp_b,
                    )
                    assert tb != ta
                    assert await fetch_tribute_archetype_answers_async(
                        cur, tribute_id=tb
                    ) == []
                    # and A's answers survive B's entry
                    got_a = await fetch_tribute_archetype_answers_async(
                        cur, tribute_id=ta
                    )
                    assert got_a and got_a[0]["question_text"] == "FD question?"
                finally:
                    await cur.execute(
                        "DELETE FROM tributes WHERE person_id = %s",
                        (person_id,))
                    await cur.execute(
                        "DELETE FROM tribute_campaigns WHERE id IN (%s, %s)",
                        (camp_a, camp_b))


async def test_latest_same_campaign_answers_backfill_after_completion(
    async_pool,
) -> None:
    """Re-entering a campaign after completing its video prefills from the
    completed tribute's answers (same campaign only)."""
    from flashback.tribute.repository import (
        fetch_latest_tribute_answers_async,
    )

    person_id, theme_id = await _seed(async_pool)
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO tribute_campaigns (slug, display_name, "
                    "state) VALUES ('prefill_test', 'P', 'published') "
                    "RETURNING id::text"
                )
                (camp,) = await cur.fetchone()
                try:
                    tid = await ensure_open_tribute_async(
                        cur, person_id=person_id, theme_id=theme_id,
                        campaign_id=camp,
                    )
                    await merge_tribute_archetype_answers_async(
                        cur, tribute_id=tid,
                        answers=[_ans("How did you meet?", "College")],
                    )
                    await cur.execute(
                        "UPDATE tributes SET status = 'complete' "
                        "WHERE id = %s", (tid,))
                    got = await fetch_latest_tribute_answers_async(
                        cur, person_id=person_id, theme_id=theme_id,
                        campaign_id=camp,
                    )
                    assert got and got[0]["question_text"] == "How did you meet?"
                    # a different campaign never sees them
                    other = await fetch_latest_tribute_answers_async(
                        cur, person_id=person_id, theme_id=theme_id,
                        campaign_id=None,
                    )
                    assert other == []
                finally:
                    await cur.execute(
                        "DELETE FROM tributes WHERE person_id = %s",
                        (person_id,))
                    await cur.execute(
                        "DELETE FROM tribute_campaigns WHERE id = %s",
                        (camp,))


async def test_meter_prefers_tribute_row_answers(async_pool) -> None:
    """answered_layers counts the tribute row's answers when present,
    falling back to the theme row for pre-0042 tributes — and counts
    multi-select (option_labels) answers."""
    person_id, theme_id = await _seed(async_pool)
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                # legacy shape: three answers on the THEME row
                await cur.execute(
                    "UPDATE themes SET archetype_answers = %s WHERE id = %s",
                    (Json([_ans(f"t{i}?", "x") for i in range(3)]), theme_id),
                )
                tid = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                await cur.execute(
                    "SELECT answered_layers FROM tribute_status WHERE id = %s",
                    (tid,))
                assert (await cur.fetchone())[0] == 3  # theme fallback
                # one answer on the tribute row -> row wins outright
                await merge_tribute_archetype_answers_async(
                    cur, tribute_id=tid, answers=[_ans("fresh?", "yes")],
                )
                await cur.execute(
                    "SELECT answered_layers FROM tribute_status WHERE id = %s",
                    (tid,))
                assert (await cur.fetchone())[0] == 1
