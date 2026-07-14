"""POST /admin/tribute_preview — real assembly with draft config + sample page."""

from __future__ import annotations

import base64
import dataclasses
import io

from PIL import Image

from flashback.http.routes import admin_tribute_config as admin_route
from flashback.tribute import preview as preview_module
from flashback.tribute_video.book import Beat, Book
from tests.http.conftest import admin_headers

_BOOK = Book(
    cover_title="Partner in Crime",
    opener=Beat(line="Nobody warned anyone about Arjun.",
                art_direction="two bicycles leaning on a chai stall at noon"),
    beats=[Beat(line="Every exam eve ended at the same chai stall.",
                art_direction="steam over glasses of chai", moment_id="m1")],
    closing=Beat(line="Some friends are simply family.", art_direction=""),
    message="Thank you, idiot.",
)


async def _seed_person(pool, relationship: str = "best friend") -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name, relationship) "
                    "VALUES ('Arjun', %s) RETURNING id::text",
                    (relationship,),
                )
                person_id = (await cur.fetchone())[0]
                for i in range(2):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details) VALUES (%s, %s, 'n', 'chai steam')",
                        (person_id, f"m{i}"),
                    )
    return person_id


async def test_text_preview_resolves_profile_from_person(
    client_with_db, async_db_pool, monkeypatch
) -> None:
    captured: dict = {}

    async def fake_assemble(**kw):
        captured.update(kw)
        return _BOOK

    monkeypatch.setattr(
        preview_module, "assemble_storybook_video", fake_assemble
    )
    admin_route._BUCKETS.clear()
    person_id = await _seed_person(async_db_pool)
    resp = await client_with_db.post(
        "/admin/tribute_preview",
        json={"person_id": person_id},
        headers=admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["book"]["cover_title"] == "Partner in Crime"
    assert body["book"]["beats"][0]["moment_id"] == "m1"
    assert body["resolved"]["group_slug"] == "friend"
    assert body["resolved"]["candidate_count"] == 2
    assert body["sample_page_b64"] is None
    # the real assembler got the composed friend register + the preview tag
    assert "partner-in-crime" in captured["voice_block"]
    assert captured["feature"] == "tribute_preview"
    assert captured["subject_name"] == "Arjun"


async def test_inline_draft_profile_validated(client_with_db, async_db_pool) -> None:
    admin_route._BUCKETS.clear()
    person_id = await _seed_person(async_db_pool)
    resp = await client_with_db.post(
        "/admin/tribute_preview",
        json={
            "person_id": person_id,
            "profile_draft": {"group_slug": "friend", "display_name": "F"},
        },
        headers=admin_headers(),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"]


async def test_sample_page_503_without_gemini_key(
    client_with_db, async_db_pool
) -> None:
    admin_route._BUCKETS.clear()
    person_id = await _seed_person(async_db_pool)
    resp = await client_with_db.post(
        "/admin/tribute_preview",
        json={"person_id": person_id, "render_sample_page": True},
        headers=admin_headers(),
    )
    assert resp.status_code == 503


async def test_sample_page_renders_composited_jpeg(
    client_with_db, app_with_db, async_db_pool, monkeypatch
) -> None:
    app_with_db.state.http_config = dataclasses.replace(
        app_with_db.state.http_config, gemini_api_key="test-key"
    )

    async def fake_assemble(**kw):
        return _BOOK

    monkeypatch.setattr(
        preview_module, "assemble_storybook_video", fake_assemble
    )

    class FakeArtist:
        def illustrate(self, art_direction, gt_context, blend, **kw):
            assert "chai stall" in art_direction
            return Image.new("RGB", (400, 400), (230, 220, 200))

    monkeypatch.setattr(
        admin_route, "_make_artist", lambda s, feature=None: FakeArtist()
    )
    admin_route._BUCKETS.clear()
    person_id = await _seed_person(async_db_pool)
    resp = await client_with_db.post(
        "/admin/tribute_preview",
        json={"person_id": person_id, "render_sample_page": True},
        headers=admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    b64 = resp.json()["sample_page_b64"]
    assert b64
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.format == "JPEG"
    # real template geometry (899x1600 shipped page)
    assert img.size[0] < img.size[1]


async def test_preview_rate_limit_429(
    client_with_db, async_db_pool, monkeypatch
) -> None:
    async def fake_assemble(**kw):
        return _BOOK

    monkeypatch.setattr(
        preview_module, "assemble_storybook_video", fake_assemble
    )
    admin_route._BUCKETS.clear()
    person_id = await _seed_person(async_db_pool)
    h = admin_headers(user="previewlimit@flashback")
    for _ in range(6):
        ok = await client_with_db.post(
            "/admin/tribute_preview",
            json={"person_id": person_id},
            headers=h,
        )
        assert ok.status_code == 200
    resp = await client_with_db.post(
        "/admin/tribute_preview", json={"person_id": person_id}, headers=h
    )
    assert resp.status_code == 429
