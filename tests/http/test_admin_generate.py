"""Generate-first authoring endpoints (config drafts + template candidates)."""

from __future__ import annotations

import dataclasses
import io

from PIL import Image

from flashback.http.routes import admin_tribute_config as admin_route
from tests.http.conftest import admin_headers

FRIEND_DRAFT = {
    "group_slug": "friend",
    "display_name": "Friend",
    "synonyms": ["friend", "bestie"],
    "voice": {
        "energy_words": ["playful", "loyal"],
        "narrator_stance": "their partner-in-crime",
        "emotion_rule": "sincerity only at the end",
        "never": ["formal introductions"],
    },
    "opener": {
        "style": "open like a party story",
        "examples": ["Nobody warned me about {name}.", "Lucky me: {name}."],
    },
    "art": {"mood_words": ["bright", "candid", "daylight"], "avoid": ["posed"]},
    "fallback_opener": "Some people get lucky. I got {name}.",
    "fallback_closing": "Thank you, {name}.",
    "archetype_bank": [
        {"question": f"Q{i}?", "options": ["a", "b", "c", "d"]}
        for i in range(1, 9)
    ],
    "message_invitation_copy": "Say the one thing.",
}


async def test_generate_profile_draft_validates(client_with_db, monkeypatch) -> None:
    async def fake_draft(settings, **kw):
        assert kw["kind"] == "profile"
        assert kw["relationship_group"] == "friend"
        return dict(FRIEND_DRAFT)

    monkeypatch.setattr(admin_route, "generate_config_draft", fake_draft)
    resp = await client_with_db.post(
        "/admin/tribute_config/generate",
        json={
            "kind": "profile",
            "relationship_group": "friend",
            "brief": "fun, teasing, the friend you got in trouble with",
        },
        headers=admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["errors"] == []
    assert body["payload"]["group_slug"] == "friend"
    assert all("{name}" in ex for ex in body["payload"]["opener"]["examples"])


async def test_generate_reports_validation_errors_not_500(
    client_with_db, monkeypatch
) -> None:
    async def bad_draft(settings, **kw):
        broken = dict(FRIEND_DRAFT)
        broken["opener"] = {"style": "x", "examples": ["No placeholder."]}
        return broken

    monkeypatch.setattr(admin_route, "generate_config_draft", bad_draft)
    resp = await client_with_db.post(
        "/admin/tribute_config/generate",
        json={"kind": "profile", "relationship_group": "friend", "brief": "b"},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    assert any("{name}" in e for e in resp.json()["errors"])


async def test_visual_generate_503_without_gemini_key(client_with_db) -> None:
    resp = await client_with_db.post(
        "/admin/visual_themes/generate",
        json={"brief": "warm doodles", "slug": "warm_doodle",
              "display_name": "Warm Doodle"},
        headers=admin_headers(),
    )
    assert resp.status_code == 503


async def test_visual_generate_creates_draft_rows_with_images(
    client_with_db, app_with_db, monkeypatch
) -> None:
    app_with_db.state.http_config = dataclasses.replace(
        app_with_db.state.http_config, gemini_api_key="test-key"
    )

    class FakeArtist:
        def raw(self, prompt, aspect):
            assert "PAGE BACKGROUND TEMPLATE" in prompt
            assert "polaroid corners" in prompt  # the brief rides the prompt
            assert aspect == "9:16"
            return Image.new("RGB", (9, 16), (240, 230, 210))

    monkeypatch.setattr(admin_route, "_make_artist", lambda s: FakeArtist())

    resp = await client_with_db.post(
        "/admin/visual_themes/generate",
        json={
            "brief": "bright, polaroid corners, friendship-bracelet border",
            "slug": "friendship_bright",
            "display_name": "Friendship Bright",
            "n_candidates": 2,
        },
        headers=admin_headers(),
    )
    assert resp.status_code == 200, resp.text
    candidates = resp.json()["candidates"]
    assert len(candidates) == 2
    assert candidates[0]["slug"] == "friendship_bright_c1"

    # each candidate's image is retrievable and decodes as a JPEG
    img_resp = await client_with_db.get(
        f"/admin/visual_themes/{candidates[0]['id']}/image",
        headers=admin_headers(),
    )
    assert img_resp.status_code == 200
    assert img_resp.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(img_resp.content))
    assert img.format == "JPEG"

    # candidates are drafts: invisible to runtime lists until published
    listing = await client_with_db.get(
        "/admin/tribute_config/visual_themes", headers=admin_headers()
    )
    row = next(
        r for r in listing.json()["rows"] if r["slug"] == "friendship_bright_c1"
    )
    assert row["state"] == "draft" and row["has_image"] is True


async def test_generate_rate_limit_429(client_with_db, monkeypatch) -> None:
    async def fake_draft(settings, **kw):
        return dict(FRIEND_DRAFT)

    monkeypatch.setattr(admin_route, "generate_config_draft", fake_draft)
    # isolate the limiter bucket for this test's admin identity
    admin_route._BUCKETS.clear()
    h = admin_headers(user="ratelimit@flashback")
    for _ in range(4):
        ok = await client_with_db.post(
            "/admin/tribute_config/generate",
            json={"kind": "profile", "relationship_group": "friend", "brief": "b"},
            headers=h,
        )
        assert ok.status_code == 200
    resp = await client_with_db.post(
        "/admin/tribute_config/generate",
        json={"kind": "profile", "relationship_group": "friend", "brief": "b"},
        headers=h,
    )
    assert resp.status_code == 429
