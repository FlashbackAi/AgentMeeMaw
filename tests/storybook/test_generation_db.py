"""DB-backed tests for the storybook count-gate + mint at Session Wrap.

settings=None makes the assembler fall back (no live LLM); artifact_queue=None
skips the SQS push. We assert: no-op below threshold, mint above it, the
watermark stamp, and idempotence on a second wrap with no new moments.
"""

from __future__ import annotations

from flashback.storybook.generation import maybe_generate_storybook
from flashback.storybook.repository import STORYBOOK_NEW_MOMENTS_THRESHOLD


async def _make_person(pool, name: str = "Dad") -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES (%s) RETURNING id::text",
                    (name,),
                )
                return (await cur.fetchone())[0]


async def _add_qualifying_moments(pool, person_id: str, n: int) -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                for i in range(n):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details) VALUES (%s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", "the smell of rain"),
                    )


async def _count_storybooks(pool, person_id: str) -> int:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM storybooks WHERE person_id = %s", (person_id,)
            )
            return int((await cur.fetchone())[0])


async def test_gate_no_op_below_threshold(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, person_id, 3)  # < threshold

    result = await maybe_generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )

    assert result.generated is False
    assert await _count_storybooks(async_pool, person_id) == 0


async def test_gate_mints_above_threshold_and_stamps_watermark(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(
        async_pool, person_id, STORYBOOK_NEW_MOMENTS_THRESHOLD
    )

    result = await maybe_generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )

    assert result.generated is True
    assert result.storybook_id is not None
    assert await _count_storybooks(async_pool, person_id) == 1

    # Row is 'generating' with a full storybook context and a watermark stamp.
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, (latest_generation_context ? 'pages'), moments_count "
                "FROM storybooks WHERE id = %s",
                (result.storybook_id,),
            )
            status_val, has_pages, moments_count = await cur.fetchone()
            await cur.execute(
                "SELECT moments_at_last_storybook_run FROM persons WHERE id = %s",
                (person_id,),
            )
            watermark = (await cur.fetchone())[0]
    assert status_val == "generating"
    assert has_pages is True
    assert moments_count == STORYBOOK_NEW_MOMENTS_THRESHOLD
    assert watermark == STORYBOOK_NEW_MOMENTS_THRESHOLD


async def test_second_wrap_no_op_without_new_moments(async_pool) -> None:
    person_id = await _make_person(async_pool)
    await _add_qualifying_moments(
        async_pool, person_id, STORYBOOK_NEW_MOMENTS_THRESHOLD
    )

    first = await maybe_generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )
    second = await maybe_generate_storybook(
        db_pool=async_pool, settings=None, artifact_queue=None, person_id=person_id
    )

    assert first.generated is True
    assert second.generated is False  # watermark advanced; delta now 0
    assert await _count_storybooks(async_pool, person_id) == 1
