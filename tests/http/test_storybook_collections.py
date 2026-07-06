"""GET /storybook-collections — the fixed chooser registry."""

from __future__ import annotations

_HEADERS = {"X-Service-Token": "test-token"}


async def test_lists_six_collections(client) -> None:
    r = await client.get("/storybook-collections", headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 6
    assert {c["slug"] for c in body} == {
        "childhood",
        "interesting",
        "nostalgia",
        "festivals",
        "adventurous",
        "wisdom",
    }
    assert all(c["page_count"] == 7 for c in body)
    layouts = {c["slug"]: c["layout"] for c in body}
    assert layouts["wisdom"] == "chapter"
    assert layouts["childhood"] == "grid"


async def test_requires_service_token(client) -> None:
    r = await client.get("/storybook-collections")
    assert r.status_code in (401, 403)


async def test_person_scoped_eligibility_badges(
    client_with_db, async_db_pool
) -> None:
    async with async_db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name, relationship) "
                    "VALUES ('Dad', 'father') RETURNING id::text"
                )
                pid = (await cur.fetchone())[0]
                # 5 childhood-tagged (eligible), 2 festivals (not).
                for tags in (
                    ["childhood"], ["childhood"], ["childhood"],
                    ["childhood"], ["childhood"], ["festivals"], ["festivals"],
                ):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details, storybook_collections) "
                        "VALUES (%s, 'm', 'n', 'rain', %s)",
                        (pid, tags),
                    )
    r = await client_with_db.get(
        f"/storybook-collections?person_id={pid}", headers=_HEADERS
    )
    assert r.status_code == 200
    by_slug = {c["slug"]: c for c in r.json()}
    assert by_slug["childhood"]["tagged_count"] == 5
    assert by_slug["childhood"]["eligible"] is True
    assert by_slug["festivals"]["tagged_count"] == 2
    assert by_slug["festivals"]["eligible"] is False
    # wisdom = whole qualifying pool (7), eligible at 3.
    assert by_slug["wisdom"]["tagged_count"] == 7
    assert by_slug["wisdom"]["eligible"] is True


async def test_bare_registry_has_no_eligibility_fields(client) -> None:
    r = await client.get("/storybook-collections", headers=_HEADERS)
    assert r.status_code == 200
    for c in r.json():
        assert c["tagged_count"] is None
        assert c["eligible"] is None
