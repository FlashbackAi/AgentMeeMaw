"""Config repository: CRUD, supersession, publish/rollback, resolution."""

from __future__ import annotations

from datetime import date

import pytest

from flashback.tribute import config_repository as repo
from flashback.tribute.config_schema import NEUTRAL_CAMPAIGN

pytestmark = pytest.mark.asyncio


async def _cur(pool):
    return pool.connection()


async def test_resolve_seeded_fd_campaign(async_pool) -> None:
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            c = await repo.resolve_campaign_db(cur, "fathers_day_2026")
    assert c.slug == "fathers_day_2026"
    assert c.display_name == "A Letter to Dad"
    assert len(c.archetype_bank_override) == 22
    assert c.deage_cover_override is True


async def test_resolve_unknown_or_empty_is_neutral(async_pool) -> None:
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            assert (await repo.resolve_campaign_db(cur, None)) is NEUTRAL_CAMPAIGN
            assert (await repo.resolve_campaign_db(cur, "default")) is NEUTRAL_CAMPAIGN
            assert (await repo.resolve_campaign_db(cur, "nope")) is NEUTRAL_CAMPAIGN


async def test_featured_window(async_pool) -> None:
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            inside = await repo.active_featured_campaign_db(cur, date(2026, 6, 15))
            outside = await repo.active_featured_campaign_db(cur, date(2026, 7, 14))
    assert inside is not None and inside.slug == "fathers_day_2026"
    assert outside is None


async def test_profile_fetch_by_group(async_pool) -> None:
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            p = await repo.fetch_profile_by_group(cur, "friend")
    assert p is not None
    assert p.group_slug == "friend"
    assert len(p.archetype_bank) == 10
    assert "playful" in p.voice["energy_words"]


class _Rollback(Exception):
    pass


async def test_supersede_edit_bumps_version_single_active(async_pool) -> None:
    async with async_pool.connection() as conn:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    p = await repo.fetch_profile_by_group(cur, "cousin")
                    new_id = await repo.supersede_edit(
                        cur,
                        "relationship_profiles",
                        p.id,
                        {"display_name": "Cousin!"},
                        updated_by="tester@flashback",
                    )
                    assert new_id != p.id
                    await cur.execute(
                        "SELECT count(*), max(version) FROM relationship_profiles "
                        "WHERE group_slug='cousin' AND status='active'"
                    )
                    count, version = await cur.fetchone()
                    assert count == 1 and version == 2
                    p2 = await repo.fetch_profile_by_group(cur, "cousin")
                    assert p2.display_name == "Cousin!"
                    # untouched fields carried over
                    assert p2.fallback_opener == p.fallback_opener
                raise _Rollback()
        except _Rollback:
            pass


async def test_rollback_restores_old_content(async_pool) -> None:
    async with async_pool.connection() as conn:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    p1 = await repo.fetch_profile_by_group(cur, "mentor")
                    await repo.supersede_edit(
                        cur,
                        "relationship_profiles",
                        p1.id,
                        {"display_name": "Changed"},
                        updated_by="tester",
                    )
                    restored_id = await repo.rollback_to(
                        cur, "relationship_profiles", p1.id, updated_by="tester"
                    )
                    p3 = await repo.fetch_profile_by_group(cur, "mentor")
                    assert p3.id == restored_id
                    assert p3.display_name == p1.display_name
                    assert p3.version == 3
                raise _Rollback()
        except _Rollback:
            pass


async def test_archive_other_profile_is_protected(async_pool) -> None:
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            p = await repo.fetch_profile_by_group(cur, "other")
            with pytest.raises(ValueError):
                await repo.set_state(
                    cur,
                    "relationship_profiles",
                    p.id,
                    "archived",
                    updated_by="tester",
                )


async def test_create_row_starts_draft(async_pool) -> None:
    async with async_pool.connection() as conn:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    new_id = await repo.create_row(
                        cur,
                        "tribute_campaigns",
                        {
                            "slug": "raksha_bandhan_2026",
                            "display_name": "For My Sibling",
                        },
                        updated_by="tester",
                    )
                    # draft rows are invisible to runtime resolution
                    c = await repo.resolve_campaign_db(cur, "raksha_bandhan_2026")
                    assert c is NEUTRAL_CAMPAIGN
                    rows = await repo.list_rows(cur, "tribute_campaigns")
                    assert any(r["id"] == new_id and r["state"] == "draft" for r in rows)
                    # publish makes it resolvable
                    await repo.set_state(
                        cur, "tribute_campaigns", new_id, "published",
                        updated_by="tester",
                    )
                    c2 = await repo.resolve_campaign_db(cur, "raksha_bandhan_2026")
                    assert c2.display_name == "For My Sibling"
                raise _Rollback()
        except _Rollback:
            pass


async def test_editing_a_theme_repoints_profile_and_campaign(async_pool) -> None:
    """Live config references follow supersession: editing a visual theme
    must not orphan the profiles/campaigns that attached it."""
    async with async_pool.connection() as conn:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    vt = await repo.fetch_visual_theme_by_slug(
                        cur, "classic_keepsake"
                    )
                    # a campaign pointing at the theme
                    camp_id = await repo.create_row(
                        cur, "tribute_campaigns",
                        {"slug": "repoint_test", "display_name": "R",
                         "visual_theme_id": vt.id},
                        updated_by="t",
                    )
                    new_theme_id = await repo.supersede_edit(
                        cur, "tribute_visual_themes", vt.id,
                        {"display_name": "Classic v2"}, updated_by="t",
                    )
                    assert new_theme_id != vt.id
                    # campaign followed the edit
                    camp = await repo.fetch_campaign_by_slug(
                        cur, "repoint_test", published_only=False
                    )
                    assert camp.visual_theme_id == new_theme_id
                    # seeded profiles followed too
                    friend = await repo.fetch_profile_by_group(cur, "friend")
                    assert friend.visual_theme_id == new_theme_id
                raise _Rollback()
        except _Rollback:
            pass
    _ = camp_id


async def test_editing_a_campaign_repoints_open_tributes(async_pool) -> None:
    from flashback.themes.repository import ensure_tribute_theme_async
    from flashback.tribute.repository import ensure_open_tribute_async
    from flashback.tribute.theme import (
        TRIBUTE_DESCRIPTION,
        TRIBUTE_DISPLAY_NAME,
        TRIBUTE_SLUG,
    )

    async with async_pool.connection() as conn:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    camp = await repo.fetch_campaign_by_slug(
                        cur, "fathers_day_2026"
                    )
                    await cur.execute(
                        "INSERT INTO persons (name) VALUES ('T') "
                        "RETURNING id::text"
                    )
                    person_id = (await cur.fetchone())[0]
                    theme_id = await ensure_tribute_theme_async(
                        cur, person_id=person_id, slug=TRIBUTE_SLUG,
                        display_name=TRIBUTE_DISPLAY_NAME,
                        description=TRIBUTE_DESCRIPTION,
                    )
                    tribute_id = await ensure_open_tribute_async(
                        cur, person_id=person_id, theme_id=theme_id
                    )
                    await cur.execute(
                        "UPDATE tributes SET campaign_id = %s WHERE id = %s",
                        (camp.id, tribute_id),
                    )
                    new_camp_id = await repo.supersede_edit(
                        cur, "tribute_campaigns", camp.id,
                        {"display_name": "A Letter to Dad (v2)"},
                        updated_by="t",
                    )
                    await cur.execute(
                        "SELECT campaign_id::text FROM tributes WHERE id = %s",
                        (tribute_id,),
                    )
                    assert (await cur.fetchone())[0] == new_camp_id
                raise _Rollback()
        except _Rollback:
            pass


async def test_publishing_same_slug_theme_repoints_attachments(
    async_pool,
) -> None:
    """Candidate redo replaces a same-slug row (no repoint — replacement is
    a draft); PUBLISHING the replacement must pull live attachments over,
    or campaigns keep rendering the stale template forever."""
    async with async_pool.connection() as conn:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    old_id = await repo.create_row(
                        cur, "tribute_visual_themes",
                        {"slug": "redo_target", "display_name": "V1",
                         "fonts": {"main_slug": "playfair_italic",
                                   "eyebrow_slug": "eb_garamond"},
                         "ink": {"main_fill": "#204060",
                                 "eyebrow_fill": "#967648"},
                         "audio_slug": "sentimental_piano"},
                        updated_by="t",
                    )
                    await repo.set_state(
                        cur, "tribute_visual_themes", old_id, "published",
                        updated_by="t",
                    )
                    camp_id = await repo.create_row(
                        cur, "tribute_campaigns",
                        {"slug": "redo_attach_test", "display_name": "R",
                         "visual_theme_id": old_id},
                        updated_by="t",
                    )
                    # candidate redo: same slug superseded, new DRAFT row
                    await repo.supersede_active_slug(
                        cur, "tribute_visual_themes", "redo_target",
                        updated_by="t",
                    )
                    new_id = await repo.create_row(
                        cur, "tribute_visual_themes",
                        {"slug": "redo_target", "display_name": "V2",
                         "fonts": {"main_slug": "caveat",
                                   "eyebrow_slug": "nunito"},
                         "ink": {"main_fill": "#1f6f8b",
                                 "eyebrow_fill": "#e07a5f"},
                         "audio_slug": "sentimental_piano"},
                        updated_by="t",
                    )
                    # while draft: attachment must NOT move (live skin safe)
                    camp = await repo.fetch_campaign_by_slug(
                        cur, "redo_attach_test", published_only=False
                    )
                    assert camp.visual_theme_id == old_id
                    # publish -> attachment follows
                    await repo.set_state(
                        cur, "tribute_visual_themes", new_id, "published",
                        updated_by="t",
                    )
                    camp = await repo.fetch_campaign_by_slug(
                        cur, "redo_attach_test", published_only=False
                    )
                    assert camp.visual_theme_id == new_id
                raise _Rollback()
        except _Rollback:
            pass


async def test_visual_theme_image_none_for_classic(async_pool) -> None:
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            vt = await repo.fetch_visual_theme_by_slug(cur, "classic_keepsake")
            assert vt is not None and vt.has_image is False
            img = await repo.fetch_visual_theme_image(cur, vt.id)
    assert img is None


async def test_list_rows_visual_themes_never_returns_bytes(async_pool) -> None:
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            rows = await repo.list_rows(cur, "tribute_visual_themes")
    assert rows and all("template_image" not in r for r in rows)
    assert all("has_image" in r for r in rows)
