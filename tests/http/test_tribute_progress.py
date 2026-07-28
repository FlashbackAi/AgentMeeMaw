"""GET /tributes/{id}/progress — the standalone decorated meter read.

Shares the client_with_db + async_db_pool fixtures (tests/http/conftest).
Asserts owner-scoping, the decorated payload shape, and the campaign skin
override, mirroring what the /turn `tribute_progress` block emits.
"""

from __future__ import annotations

_HEADERS = {"X-Service-Token": "test-token"}


async def _seed_tribute(pool) -> tuple[str, str]:
    """A bare tribute with a polished message (message slot filled => not 0%)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                await cur.execute(
                    "INSERT INTO tributes (person_id, message_text) "
                    "VALUES (%s, %s) RETURNING id::text",
                    (person_id, "Thank you, Dad."),
                )
                tribute_id = (await cur.fetchone())[0]
    return person_id, tribute_id


async def test_progress_returns_decorated_shape(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_tribute(async_db_pool)

    resp = await client_with_db.get(
        f"/tributes/{tribute_id}/progress",
        params={"person_id": person_id},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Same shape as the /turn tribute_progress block.
    assert set(body) == {"percent", "ready", "title", "next", "slots"}
    assert body["title"] == "A Tribute"  # neutral default, no campaign
    assert body["ready"] is False
    # Message slot is filled; it carries decorated copy.
    msg = next(s for s in body["slots"] if s["key"] == "message")
    assert msg["filled"] is True
    assert msg["hint"]
    # next points at the first unfilled slot, not message.
    assert body["next"] != "message"


async def test_progress_404_for_wrong_owner(
    client_with_db, async_db_pool
) -> None:
    _person_id, tribute_id = await _seed_tribute(async_db_pool)
    other_person = "00000000-0000-0000-0000-0000000000ff"

    resp = await client_with_db.get(
        f"/tributes/{tribute_id}/progress",
        params={"person_id": other_person},
        headers=_HEADERS,
    )
    assert resp.status_code == 404


async def test_progress_404_for_missing_tribute(
    client_with_db, async_db_pool
) -> None:
    missing = "00000000-0000-0000-0000-0000000000aa"
    resp = await client_with_db.get(
        f"/tributes/{missing}/progress",
        params={"person_id": "00000000-0000-0000-0000-0000000000bb"},
        headers=_HEADERS,
    )
    assert resp.status_code == 404


async def test_progress_campaign_skins_title(
    client_with_db, async_db_pool
) -> None:
    person_id, tribute_id = await _seed_tribute(async_db_pool)

    resp = await client_with_db.get(
        f"/tributes/{tribute_id}/progress",
        params={"person_id": person_id, "campaign": "fathers_day_2026"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The skin overrides the neutral "A Tribute" title.
    assert body["title"] != "A Tribute"


async def test_progress_requires_person_id(
    client_with_db, async_db_pool
) -> None:
    _person_id, tribute_id = await _seed_tribute(async_db_pool)
    resp = await client_with_db.get(
        f"/tributes/{tribute_id}/progress", headers=_HEADERS
    )
    # person_id is a required query param.
    assert resp.status_code == 422
