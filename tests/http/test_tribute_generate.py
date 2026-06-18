"""POST /tributes/{id}/generate — gating + happy path against the test DB.

Uses the shared client_with_db + async_db_pool fixtures (tests/http/conftest).
The assembler is stubbed so no live LLM call is made; we assert the gate,
the status flip, and the keyed latest_generation_context write.
"""

from __future__ import annotations

import json

import flashback.http.routes.tributes as route
from flashback.http.models import TributeGenerateRequest
from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.assembly import Scene, TributeScript
from flashback.tribute.repository import ensure_open_tribute_async, set_message_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

_HEADERS = {"X-Service-Token": "test-token"}


def test_request_accepts_prime_photo_key() -> None:
    req = TributeGenerateRequest(
        person_id="00000000-0000-0000-0000-000000000001",
        artifact_kind="storybook",
        campaign="fathers_day_2026",
        prime_photo_s3_key="uploads/p/prime.jpg",
    )
    assert req.prime_photo_s3_key == "uploads/p/prime.jpg"


def test_request_prime_photo_defaults_none() -> None:
    req = TributeGenerateRequest(
        person_id="00000000-0000-0000-0000-000000000001",
    )
    assert req.prime_photo_s3_key is None


async def _seed(pool, *, ready: bool) -> tuple[str, str, str | None]:
    """Return (person_id, tribute_id, a_real_moment_id_or_None)."""
    moment_id: str | None = None
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                theme_id = await ensure_tribute_theme_async(
                    cur,
                    person_id=person_id,
                    slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME,
                    description=TRIBUTE_DESCRIPTION,
                )
                tribute_id = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                if ready:
                    gt = json.dumps(
                        {
                            "region": {"value": "South India"},
                            "birth_era": {"value": "1950s"},
                            "attire": {"value": "white shirt"},
                        }
                    )
                    await cur.execute(
                        "UPDATE persons SET ground_truth = %s WHERE id = %s",
                        (gt, person_id),
                    )
                    for i in range(3):
                        await cur.execute(
                            "INSERT INTO moments (person_id, title, narrative, "
                            "sensory_details) VALUES (%s, %s, %s, %s) "
                            "RETURNING id::text",
                            (person_id, f"m{i}", "n", "the smell of rain"),
                        )
                        if moment_id is None:
                            moment_id = (await cur.fetchone())[0]
                    await cur.execute(
                        "INSERT INTO traits (person_id, name, status) "
                        "VALUES (%s, 'patient', 'active')",
                        (person_id,),
                    )
                    await set_message_async(
                        cur, tribute_id=tribute_id, message_text="Thank you, Dad."
                    )
    return person_id, tribute_id, moment_id


async def test_video_409_when_not_ready(client_with_db, async_db_pool) -> None:
    person_id, tribute_id, _ = await _seed(async_db_pool, ready=False)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json={"person_id": person_id, "artifact_kind": "tribute_video"},
        headers=_HEADERS,
    )
    assert resp.status_code == 409


async def test_video_200_flips_status_and_writes_keyed_context(
    client_with_db, async_db_pool, monkeypatch
) -> None:
    person_id, tribute_id, moment_id = await _seed(async_db_pool, ready=True)

    async def _fake_assemble(**kwargs):
        return TributeScript(
            scenes=[Scene(moment_id=moment_id, caption="a memory")],
            opening_caption="open",
            closing_caption="close",
            message_text=kwargs["message_text"],
        )

    monkeypatch.setattr(route, "assemble_tribute_script", _fake_assemble)
    resp = await client_with_db.post(
        f"/tributes/{tribute_id}/generate",
        json={"person_id": person_id, "artifact_kind": "tribute_video"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["artifact_kind"] == "tribute_video"
    assert body["ready"] is True

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, (latest_generation_context ? 'tribute_video') "
                "FROM tributes WHERE id = %s",
                (tribute_id,),
            )
            status_val, has_video_ctx = await cur.fetchone()
    assert status_val == "generating"
    assert has_video_ctx is True
