"""Admin CRUD/publish/rollback API for the tribute CRM config tables."""

from __future__ import annotations

from tests.http.conftest import admin_headers, auth_headers

VALID_CAMPAIGN = {
    "slug": "friendship_day_2026",
    "display_name": "A Friendship Day Tribute",
    "message_card_copy": "Friends never say it. Say it once.",
    "archetype_extra_context": "This is a Friendship Day tribute.",
    "featured": True,
    "active_start": "2026-07-28",
    "active_end": "2026-08-03",
}

VALID_VISUAL_THEME = {
    "slug": "bright_polaroid",
    "display_name": "Bright Polaroid",
    "fonts": {"main_slug": "playfair_italic", "eyebrow_slug": "eb_garamond"},
    "ink": {"main_fill": "#204060", "eyebrow_fill": "#967648"},
    "audio_slug": "sentimental_piano",
}


async def test_requires_admin_token(client_with_db) -> None:
    resp = await client_with_db.get(
        "/admin/tribute_config/tribute_campaigns", headers=auth_headers()
    )
    assert resp.status_code == 401


async def test_unknown_table_404(client_with_db) -> None:
    resp = await client_with_db.get(
        "/admin/tribute_config/nope", headers=admin_headers()
    )
    assert resp.status_code == 404


async def test_full_campaign_lifecycle(client_with_db) -> None:
    h = admin_headers()
    # create -> draft
    resp = await client_with_db.post(
        "/admin/tribute_config/tribute_campaigns",
        json={"payload": VALID_CAMPAIGN},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    row_id = resp.json()["id"]

    # draft rows are invisible to the public campaign list
    pub = await client_with_db.get("/tribute-campaigns", headers=auth_headers())
    assert "friendship_day_2026" not in {
        c["slug"] for c in pub.json()["campaigns"]
    }

    # edit supersedes -> v2
    resp = await client_with_db.put(
        f"/admin/tribute_config/tribute_campaigns/{row_id}",
        json={"payload": {"display_name": "Friendship Day"}},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    v2_id = resp.json()["id"]
    assert v2_id != row_id and resp.json()["version"] == 2

    # publish (overlaps nothing featured in its window)
    resp = await client_with_db.post(
        f"/admin/tribute_config/tribute_campaigns/{v2_id}/publish", headers=h
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == []

    # now visible on the public surface
    pub = await client_with_db.get("/tribute-campaigns", headers=auth_headers())
    fd = next(
        c for c in pub.json()["campaigns"] if c["slug"] == "friendship_day_2026"
    )
    assert fd["display_name"] == "Friendship Day"

    # rollback to v1 content -> v3, published
    resp = await client_with_db.post(
        f"/admin/tribute_config/tribute_campaigns/{v2_id}/rollback",
        json={"to_row_id": row_id},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    v3_id = resp.json()["id"]

    listing = await client_with_db.get(
        "/admin/tribute_config/tribute_campaigns",
        params={"include_superseded": "true"},
        headers=h,
    )
    rows = [
        r for r in listing.json()["rows"] if r["slug"] == "friendship_day_2026"
    ]
    active = [r for r in rows if r["id"] == v3_id]
    assert len(rows) == 3 and len(active) == 1
    assert active[0]["display_name"] == "A Friendship Day Tribute"  # v1 content
    assert active[0]["version"] == 3 and active[0]["state"] == "published"

    # archive it so other tests see a clean surface
    resp = await client_with_db.post(
        f"/admin/tribute_config/tribute_campaigns/{v3_id}/archive", headers=h
    )
    assert resp.status_code == 200


async def test_invalid_profile_payload_422(client_with_db) -> None:
    bad = {
        "group_slug": "friend",
        "display_name": "Friend",
        "voice": {"energy_words": [], "narrator_stance": "", "emotion_rule": ""},
        "opener": {"style": "x", "examples": ["No placeholder here."]},
        "art": {"mood_words": ["bright"]},
        "fallback_opener": "Missing placeholder.",
        "fallback_closing": "Also missing.",
    }
    resp = await client_with_db.post(
        "/admin/tribute_config/relationship_profiles",
        json={"payload": bad},
        headers=admin_headers(),
    )
    assert resp.status_code == 422
    errors = resp.json()["detail"]["errors"]
    assert any("{name}" in e for e in errors)
    assert any("energy_words" in e for e in errors)


async def test_visual_theme_rejects_inline_image_bytes(client_with_db) -> None:
    payload = {**VALID_VISUAL_THEME, "template_image": "abc123"}
    resp = await client_with_db.post(
        "/admin/tribute_config/visual_themes",
        json={"payload": payload},
        headers=admin_headers(),
    )
    assert resp.status_code == 422
    assert any(
        "template_image" in e for e in resp.json()["detail"]["errors"]
    )


async def test_visual_theme_unknown_slugs_422(client_with_db) -> None:
    payload = {
        **VALID_VISUAL_THEME,
        "fonts": {"main_slug": "comic_sans", "eyebrow_slug": "eb_garamond"},
        "audio_slug": "dubstep",
    }
    resp = await client_with_db.post(
        "/admin/tribute_config/visual_themes",
        json={"payload": payload},
        headers=admin_headers(),
    )
    assert resp.status_code == 422
    errors = resp.json()["detail"]["errors"]
    assert any("fonts.main_slug" in e for e in errors)
    assert any("audio_slug" in e for e in errors)


async def test_other_profile_archive_is_409(client_with_db) -> None:
    h = admin_headers()
    listing = await client_with_db.get(
        "/admin/tribute_config/relationship_profiles", headers=h
    )
    other = next(
        r for r in listing.json()["rows"] if r["group_slug"] == "other"
    )
    resp = await client_with_db.post(
        f"/admin/tribute_config/relationship_profiles/{other['id']}/archive",
        headers=h,
    )
    assert resp.status_code == 409


async def test_image_404_for_builtin_theme(client_with_db) -> None:
    h = admin_headers()
    listing = await client_with_db.get(
        "/admin/tribute_config/visual_themes", headers=h
    )
    classic = next(
        r for r in listing.json()["rows"] if r["slug"] == "classic_keepsake"
    )
    assert classic["has_image"] is False
    resp = await client_with_db.get(
        f"/admin/visual_themes/{classic['id']}/image", headers=h
    )
    assert resp.status_code == 404


async def test_theme_ref_must_be_uuid_422_not_500(
    client_with_db, async_db_pool
) -> None:
    """Prod 2026-07-15: a slug pasted into visual_theme_id 500d with
    InvalidTextRepresentation. Must be a field-level 422; a real row id
    still works; empty string detaches."""
    h = admin_headers(user="attach@flashback")
    bad = await client_with_db.post(
        "/admin/tribute_config/tribute_campaigns",
        json={"payload": {"slug": "attach_bad", "display_name": "A",
                          "visual_theme_id": "test1"}},
        headers=h,
    )
    assert bad.status_code == 422
    assert any(
        e.startswith("visual_theme_id:")
        for e in bad.json()["detail"]["errors"]
    )

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id::text FROM tribute_visual_themes "
                "WHERE slug = 'classic_keepsake' AND status = 'active'"
            )
            (theme_id,) = await cur.fetchone()
    good = await client_with_db.post(
        "/admin/tribute_config/tribute_campaigns",
        json={"payload": {"slug": "attach_good", "display_name": "A",
                          "visual_theme_id": theme_id}},
        headers=h,
    )
    assert good.status_code == 200, good.text

    detached = await client_with_db.put(
        f"/admin/tribute_config/tribute_campaigns/{good.json()['id']}",
        json={"payload": {"visual_theme_id": ""}},
        headers=h,
    )
    assert detached.status_code == 200, detached.text


async def test_bad_date_format_is_422_not_500(client_with_db) -> None:
    h = admin_headers(user="dates@flashback")
    resp = await client_with_db.post(
        "/admin/tribute_config/tribute_campaigns",
        json={"payload": {"slug": "bad_date", "display_name": "B",
                          "active_start": "28-07-2026",
                          "active_end": "2026-08-03"}},
        headers=h,
    )
    assert resp.status_code == 422
    assert "detail" in resp.json()


async def test_delete_draft_frees_slug_and_purges_chain(client_with_db) -> None:
    """A never-published draft (junk candidate) hard-deletes: the whole
    edit chain goes away and the slug is immediately reusable."""
    h = admin_headers(user="cleanup@flashback")
    made = await client_with_db.post(
        "/admin/tribute_config/visual_themes",
        json={"payload": {**VALID_VISUAL_THEME, "slug": "junk_draft"}},
        headers=h,
    )
    assert made.status_code == 200, made.text
    # edit it once so the chain has a superseded row too
    edited = await client_with_db.put(
        f"/admin/tribute_config/visual_themes/{made.json()['id']}",
        json={"payload": {"display_name": "Junk v2"}},
        headers=h,
    )
    assert edited.status_code == 200, edited.text

    resp = await client_with_db.delete(
        f"/admin/tribute_config/visual_themes/{edited.json()['id']}",
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_rows"] == 2  # v1 + v2, whole chain

    listing = await client_with_db.get(
        "/admin/tribute_config/visual_themes",
        params={"include_superseded": "true", "include_archived": "true"},
        headers=h,
    )
    assert not any(r["slug"] == "junk_draft" for r in listing.json()["rows"])

    # slug is free again — no "already in use" 422
    again = await client_with_db.post(
        "/admin/tribute_config/visual_themes",
        json={"payload": {**VALID_VISUAL_THEME, "slug": "junk_draft"}},
        headers=h,
    )
    assert again.status_code == 200, again.text
    await client_with_db.delete(
        f"/admin/tribute_config/visual_themes/{again.json()['id']}", headers=h
    )


async def test_delete_published_is_409(client_with_db) -> None:
    h = admin_headers(user="cleanup@flashback")
    listing = await client_with_db.get(
        "/admin/tribute_config/visual_themes", headers=h
    )
    classic = next(
        r for r in listing.json()["rows"] if r["slug"] == "classic_keepsake"
    )
    resp = await client_with_db.delete(
        f"/admin/tribute_config/visual_themes/{classic['id']}", headers=h
    )
    assert resp.status_code == 409
    assert "archive" in resp.json()["detail"]


async def test_delete_unknown_or_malformed_id_is_404(client_with_db) -> None:
    h = admin_headers()
    resp = await client_with_db.delete(
        "/admin/tribute_config/visual_themes/not-a-uuid", headers=h
    )
    assert resp.status_code == 404
    resp = await client_with_db.delete(
        "/admin/tribute_config/visual_themes/"
        "00000000-0000-0000-0000-000000000000",
        headers=h,
    )
    assert resp.status_code == 404


async def test_asset_library(client_with_db) -> None:
    resp = await client_with_db.get(
        "/admin/asset-library", headers=admin_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "playfair_italic" in body["fonts"]
    assert "sentimental_piano" in body["audio"]
