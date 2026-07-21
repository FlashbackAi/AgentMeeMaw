"""Admin API for the tribute CRM config tables (spec 2026-07-14 §5).

Node's CRM screens proxy here (dashboard-admin gated on their side, the
X-Admin-Service-Token pair on ours — no auth in this service, CLAUDE.md §3).
Uniform CRUD + lifecycle over the three 0039 tables; validation errors are
human-readable strings the CRM shows next to the field. Template image
bytes enter ONLY through the generation endpoint (Task 10), never through
CRUD payloads.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from psycopg import errors as pg_errors
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from flashback.config import HttpConfig
from flashback.http.auth import (
    require_admin_service_token,
    require_service_token,
)
from flashback.http.deps import get_db_pool, get_http_config
from flashback.llm.errors import LLMError
from flashback.tribute import config_repository as repo
from flashback.tribute.config_llm import generate_config_draft
from flashback.tribute.config_schema import (
    validate_campaign_payload,
    validate_ink,
    validate_profile_payload,
)
from flashback.tribute_video.sequencer import (
    LAYOUT_CATALOG,
    MOTION_PRESETS,
    PINNABLE_ROLES,
)
from flashback.tribute.preview import (
    build_preview,
    campaign_from_payload,
    profile_from_payload,
    render_sample_page,
)
from flashback.tribute.relationships import ensure_relationship_group
from flashback.tribute_video.style import (
    AUDIO_REGISTRY,
    FONT_REGISTRY,
    kit_from_style_dict,
)
from flashback.tribute_video.template_gen import generate_template_candidates

router = APIRouter(
    prefix="/admin",
    dependencies=[
        Depends(require_service_token),
        Depends(require_admin_service_token),
    ],
)
log = structlog.get_logger("flashback.http.admin_tribute_config")

_TABLES: dict[str, repo.ConfigTable] = {
    "relationship_profiles": "relationship_profiles",
    "tribute_campaigns": "tribute_campaigns",
    "visual_themes": "tribute_visual_themes",
}


def _table_or_404(table: str) -> repo.ConfigTable:
    resolved = _TABLES.get(table)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"unknown table {table!r}")
    return resolved


def admin_user(x_admin_user: str | None = Header(default=None)) -> str:
    return (x_admin_user or "").strip() or "unknown"


# --- in-process rate limiter (per admin identity) ---------------------------
# Generation/preview endpoints burn real LLM/image calls; CRUD is free.
_BUCKETS: dict[str, deque] = defaultdict(deque)


def allow(key: str, per_minute: int, *, now: float | None = None) -> bool:
    ts = time.monotonic() if now is None else now
    bucket = _BUCKETS[key]
    while bucket and ts - bucket[0] > 60.0:
        bucket.popleft()
    if len(bucket) >= per_minute:
        return False
    bucket.append(ts)
    return True


# --- payload validation ------------------------------------------------------


_LAYOUT_SLUGS = {layout["slug"] for layout in LAYOUT_CATALOG}


def validate_recipe(payload: dict) -> list[str]:
    """Errors for a visual theme's Remotion recipe (all fields optional).

    Empty/absent is valid (the render falls back to the code-side default).
    Slugs/roles/presets are checked against the catalog so a typo surfaces in
    the CRM instead of silently degrading a render.
    """
    errors: list[str] = []
    palette = payload.get("layout_palette")
    if palette is not None:
        if not isinstance(palette, list) or not all(isinstance(s, str) for s in palette):
            errors.append("layout_palette: must be a list of layout slugs")
        else:
            unknown = [s for s in palette if s not in _LAYOUT_SLUGS]
            if unknown:
                errors.append(
                    f"layout_palette: unknown slug(s) {unknown} (see /flashback/layouts)")
    pins = payload.get("layout_pins")
    if pins is not None:
        if not isinstance(pins, dict):
            errors.append("layout_pins: must be an object {role: slug}")
        else:
            for role, slug in pins.items():
                if role not in PINNABLE_ROLES:
                    errors.append(
                        f"layout_pins: unknown role '{role}' (allowed: {PINNABLE_ROLES})")
                elif slug not in _LAYOUT_SLUGS:
                    errors.append(f"layout_pins.{role}: unknown slug '{slug}'")
    pacing = payload.get("pacing")
    if pacing is not None:
        if not isinstance(pacing, dict):
            errors.append("pacing: must be an object {hold, transition}")
        else:
            for key in ("hold", "transition"):
                val = pacing.get(key)
                if val is not None and (not isinstance(val, (int, float)) or isinstance(val, bool)):
                    errors.append(f"pacing.{key}: must be a number (seconds)")
    motion = payload.get("motion_preset")
    if motion not in (None, "") and motion not in MOTION_PRESETS:
        errors.append(
            f"motion_preset: unknown preset '{motion}' (allowed: {MOTION_PRESETS})")
    return errors


def _validate(table: repo.ConfigTable, payload: dict) -> list[str]:
    if table == "relationship_profiles":
        return validate_profile_payload(payload)
    if table == "tribute_campaigns":
        return validate_campaign_payload(payload)
    # visual themes
    errors: list[str] = []
    if "template_image" in payload or "template_mime" in payload:
        errors.append(
            "template_image: image bytes enter only via /admin/visual_themes/"
            "generate, never CRUD payloads"
        )
    if not isinstance(payload.get("slug"), str) or not payload["slug"].strip():
        errors.append("slug: required")
    if (
        not isinstance(payload.get("display_name"), str)
        or not payload["display_name"].strip()
    ):
        errors.append("display_name: required")
    fonts = payload.get("fonts")
    if not isinstance(fonts, dict):
        errors.append("fonts: required object {main_slug, eyebrow_slug}")
    else:
        for key in ("main_slug", "eyebrow_slug"):
            if fonts.get(key) not in FONT_REGISTRY:
                errors.append(
                    f"fonts.{key}: unknown font slug (see /admin/asset-library)"
                )
    errors.extend(validate_ink(payload.get("ink")))
    if payload.get("audio_slug") not in AUDIO_REGISTRY:
        errors.append("audio_slug: unknown track slug (see /admin/asset-library)")
    errors.extend(validate_recipe(payload))
    return errors


def _row_as_payload(table: repo.ConfigTable, row: dict) -> dict:
    payload = {
        k: v
        for k, v in row.items()
        # template_image/template_mime are row-derived, never CRM-supplied;
        # keeping them here made publish's full-row re-validation reject
        # every generated candidate ("image bytes enter only via generate").
        if k not in ("id", "state", "status", "version", "updated_by",
                     "updated_at", "has_image", "template_image",
                     "template_mime")
    }
    if table == "tribute_campaigns":
        for key in ("active_start", "active_end"):
            if payload.get(key) is not None:
                payload[key] = str(payload[key])
    return payload


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


async def _resolve_theme_ref(cur, payload: dict) -> dict:
    """Guard ``visual_theme_id``: must be a theme ROW ID (or empty = detach).

    A non-UUID here (prod 2026-07-15: someone pasted a slug — `invalid
    input syntax for type uuid: "test1"`) becomes a field-level 422 the
    CRM can render, never a DB-level 500. Slugs are deliberately NOT
    resolved — the dashboard's theme dropdown supplies ids; keeping the
    API strict keeps the contract single-shaped.
    """
    v = payload.get("visual_theme_id")
    if not isinstance(v, str):
        return payload
    v = v.strip()
    if not v:
        return {**payload, "visual_theme_id": None}
    if _UUID_RE.match(v):
        return payload
    raise HTTPException(
        status_code=422,
        detail={"errors": [
            "visual_theme_id: must be a visual theme's row id (UUID) — "
            "pick it from the theme selector, not the slug"
        ]},
    )


async def _fetch_row_or_404(cur, table: repo.ConfigTable, row_id: str) -> dict:
    rows = await repo.list_rows(cur, table, include_archived=True)
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="no active row")
    return row


# --- models -------------------------------------------------------------------


class ConfigPayloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_row_id: str = Field(min_length=1)


class ConfigGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["profile", "campaign"]
    relationship_group: str | None = Field(None, max_length=40)
    occasion: str | None = Field(None, max_length=80)
    brief: str = Field(min_length=1, max_length=1000)


# --- generate-first authoring (registered BEFORE /tribute_config/{table} so
# the literal path wins over the parametrized one) ----------------------------


@router.post("/tribute_config/generate")
async def generate_config(
    body: ConfigGenerateRequest,
    settings: HttpConfig = Depends(get_http_config),
    updated_by: str = Depends(admin_user),
) -> dict:
    """A brief -> a validated structured draft. Never stored; the CRM lands
    it in the form for tuning and saves via the normal create endpoint."""
    if not allow(f"gen:{updated_by}", 4):
        raise HTTPException(status_code=429, detail="generation rate limited")
    try:
        payload = await generate_config_draft(
            settings,
            kind=body.kind,
            relationship_group=body.relationship_group,
            occasion=body.occasion,
            brief=body.brief,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"draft failed: {exc}")
    if body.kind == "profile":
        if body.relationship_group:
            payload.setdefault("group_slug", body.relationship_group)
        errors = validate_profile_payload(payload)
    else:
        if body.occasion:
            payload.setdefault(
                "slug",
                body.occasion.strip().lower().replace(" ", "_")[:64],
            )
        errors = validate_campaign_payload(payload)
    return {"payload": payload, "errors": errors}


# --- CRUD + lifecycle ----------------------------------------------------------


@router.get("/tribute_config/{table}")
async def list_config(
    table: str,
    include_archived: bool = Query(False),
    include_superseded: bool = Query(False),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> dict:
    resolved = _table_or_404(table)
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            rows = await repo.list_rows(
                cur,
                resolved,
                include_archived=include_archived,
                include_superseded=include_superseded,
            )
    return {"rows": rows}


@router.post("/tribute_config/{table}")
async def create_config(
    table: str,
    body: ConfigPayloadRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    updated_by: str = Depends(admin_user),
) -> dict:
    resolved = _table_or_404(table)
    errors = _validate(resolved, body.payload)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    try:
        async with db_pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    payload = await _resolve_theme_ref(cur, body.payload)
                    new_id = await repo.create_row(
                        cur, resolved, payload, updated_by=updated_by
                    )
    except pg_errors.UniqueViolation:
        # Validation-shaped so the CRM renders it inline on the slug field.
        raise HTTPException(
            status_code=422,
            detail={"errors": ["slug: already in use — pick a different slug"]},
        )
    except (pg_errors.DataError, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"errors": [
                "payload: a field has the wrong format "
                "(dates are YYYY-MM-DD; ids are UUIDs or known slugs)"
            ]},
        )
    log.info("tribute_config.created", table=resolved, row_id=new_id,
             updated_by=updated_by)
    return {"id": new_id}


@router.put("/tribute_config/{table}/{row_id}")
async def edit_config(
    table: str,
    row_id: str,
    body: ConfigPayloadRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    updated_by: str = Depends(admin_user),
) -> dict:
    resolved = _table_or_404(table)
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                # Validate the MERGED row (payload over current fields) so a
                # partial edit can't sneak an invalid combination past the
                # per-field checks.
                current = await _fetch_row_or_404(cur, resolved, row_id)
                payload = await _resolve_theme_ref(cur, body.payload)
                merged = {**_row_as_payload(resolved, current), **payload}
                errors = _validate(resolved, merged)
                if errors:
                    raise HTTPException(
                        status_code=422, detail={"errors": errors}
                    )
                try:
                    new_id = await repo.supersede_edit(
                        cur, resolved, row_id, payload,
                        updated_by=updated_by,
                    )
                except LookupError as exc:
                    raise HTTPException(status_code=404, detail=str(exc))
                except pg_errors.UniqueViolation:
                    raise HTTPException(
                        status_code=422,
                        detail={"errors": [
                            "slug: already in use — pick a different slug"
                        ]},
                    )
                except (pg_errors.DataError, ValueError):
                    raise HTTPException(
                        status_code=422,
                        detail={"errors": [
                            "payload: a field has the wrong format "
                            "(dates are YYYY-MM-DD; ids are UUIDs or "
                            "known slugs)"
                        ]},
                    )
                await cur.execute(
                    f"SELECT version FROM {resolved} WHERE id = %s", (new_id,)
                )
                version = (await cur.fetchone())[0]
    log.info("tribute_config.edited", table=resolved, row_id=new_id,
             version=version, updated_by=updated_by)
    return {"id": new_id, "version": version}


async def _campaign_overlap_warnings(cur, row_id: str) -> list[str]:
    await cur.execute(
        """
        SELECT other.slug FROM tribute_campaigns me
        JOIN tribute_campaigns other
          ON other.id != me.id
         AND other.status = 'active' AND other.state = 'published'
         AND other.featured
         AND other.active_start IS NOT NULL AND me.active_start IS NOT NULL
         AND other.active_start <= me.active_end
         AND other.active_end >= me.active_start
        WHERE me.id = %s AND me.featured
        """,
        (row_id,),
    )
    return [
        f"featured window overlaps campaign '{r[0]}'"
        for r in await cur.fetchall()
    ]


@router.post("/tribute_config/{table}/{row_id}/publish")
async def publish_config(
    table: str,
    row_id: str,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    updated_by: str = Depends(admin_user),
) -> dict:
    resolved = _table_or_404(table)
    warnings: list[str] = []
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                # Full-row re-validation before anything goes live.
                row = await _fetch_row_or_404(cur, resolved, row_id)
                errors = _validate(resolved, _row_as_payload(resolved, row))
                if errors:
                    raise HTTPException(
                        status_code=422, detail={"errors": errors}
                    )
                try:
                    await repo.set_state(
                        cur, resolved, row_id, "published",
                        updated_by=updated_by,
                    )
                except LookupError as exc:
                    raise HTTPException(status_code=404, detail=str(exc))
                if resolved == "tribute_campaigns":
                    warnings = await _campaign_overlap_warnings(cur, row_id)
    log.info("tribute_config.published", table=resolved, row_id=row_id,
             updated_by=updated_by, warnings=warnings)
    return {"id": row_id, "state": "published", "warnings": warnings}


@router.post("/tribute_config/{table}/{row_id}/archive")
async def archive_config(
    table: str,
    row_id: str,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    updated_by: str = Depends(admin_user),
) -> dict:
    resolved = _table_or_404(table)
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                try:
                    await repo.set_state(
                        cur, resolved, row_id, "archived", updated_by=updated_by
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=409, detail=str(exc))
                except LookupError as exc:
                    raise HTTPException(status_code=404, detail=str(exc))
    return {"id": row_id, "state": "archived"}


@router.delete("/tribute_config/{table}/{row_id}")
async def delete_config(
    table: str,
    row_id: str,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    updated_by: str = Depends(admin_user),
) -> dict:
    """Hard-delete a never-published draft — the whole slug chain.

    The CRM's junk-draft cleanup (abandoned candidates, test rows): frees
    the slug and drops the stored template bytes. Anything that has been
    published (or archived) 409s — that history stays append-only and is
    hidden via archive instead.
    """
    resolved = _table_or_404(table)
    if not _UUID_RE.match(row_id):
        raise HTTPException(status_code=404, detail="no active row")
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                try:
                    deleted = await repo.delete_draft_chain(
                        cur, resolved, row_id
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=409, detail=str(exc))
                except LookupError as exc:
                    raise HTTPException(status_code=404, detail=str(exc))
    log.info("tribute_config.deleted", table=resolved, row_id=row_id,
             rows=deleted, updated_by=updated_by)
    return {"id": row_id, "deleted_rows": deleted}


@router.post("/tribute_config/{table}/{row_id}/rollback")
async def rollback_config(
    table: str,
    row_id: str,
    body: RollbackRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    updated_by: str = Depends(admin_user),
) -> dict:
    """Republish a superseded version's content as a fresh active row.

    ``row_id`` in the path is the CURRENT row (kept for URL symmetry);
    ``to_row_id`` is the historical row whose content comes back.
    """
    resolved = _table_or_404(table)
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                try:
                    new_id = await repo.rollback_to(
                        cur, resolved, body.to_row_id, updated_by=updated_by
                    )
                except LookupError as exc:
                    raise HTTPException(status_code=404, detail=str(exc))
    log.info("tribute_config.rolled_back", table=resolved,
             restored_from=body.to_row_id, new_id=new_id,
             updated_by=updated_by)
    return {"id": new_id}


# --- assets ------------------------------------------------------------------


@router.get("/visual_themes/{row_id}/image")
async def get_visual_theme_image(
    row_id: str,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
) -> Response:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            found = await repo.fetch_visual_theme_image(cur, row_id)
    if found is None:
        raise HTTPException(
            status_code=404, detail="no template image (built-in kit)"
        )
    image_bytes, mime = found
    return Response(content=image_bytes, media_type=mime)


@router.get("/asset-library")
async def get_asset_library() -> dict:
    return {
        "fonts": sorted(FONT_REGISTRY.keys()),
        "audio": sorted(AUDIO_REGISTRY.keys()),
    }


# --- visual-theme candidate generation ----------------------------------------


class VisualThemeGenerateRequest(BaseModel):
    """Lenient on ``fonts``/``ink`` shapes (Postel's law for the CRM).

    Canonical: ``fonts={"main_slug", "eyebrow_slug"}``,
    ``ink={"main_fill", "eyebrow_fill"}``. Also accepted — because the
    dashboard's single-input UX sends them — ``fonts="caveat"`` or
    ``fonts=["caveat"]`` (main font; eyebrow keeps the classic default)
    and ``ink="#1a1a19"`` (main ink; eyebrow default).
    """

    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=1, max_length=1000)
    slug: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    n_candidates: int = Field(3, ge=1, le=4)
    fonts: dict | list | str | None = None
    ink: dict | str | None = None
    audio_slug: str | None = None

    def fonts_dict(self) -> dict | None:
        v = self.fonts
        if v is None or isinstance(v, dict):
            return v or None
        slugs = [s for s in (v if isinstance(v, list) else [v])
                 if isinstance(s, str) and s.strip()]
        if not slugs:
            return None
        out = {"main_slug": slugs[0].strip()}
        out["eyebrow_slug"] = (
            slugs[1].strip() if len(slugs) > 1 else "eb_garamond"
        )
        return out

    def ink_dict(self) -> dict | None:
        v = self.ink
        if v is None or isinstance(v, dict):
            return v or None
        s = v.strip()
        if not s:
            return None
        return {"main_fill": s, "eyebrow_fill": "#967648"}


@router.post("/visual_themes/generate")
async def generate_visual_themes(
    body: VisualThemeGenerateRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    settings: HttpConfig = Depends(get_http_config),
    updated_by: str = Depends(admin_user),
) -> dict:
    """Generate <=4 page-template candidates as DRAFT visual-theme rows.

    Fonts/ink/audio default to the classic kit; the CRM fetches each
    candidate's image via GET /admin/visual_themes/{id}/image, the content
    person picks one and publishes it (the rest stay drafts/archived).
    """
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503, detail="GEMINI_API_KEY not configured"
        )
    if not allow(f"visual:{updated_by}", 4):
        raise HTTPException(status_code=429, detail="generation rate limited")

    fonts = body.fonts_dict() or {
        "main_slug": "playfair_italic", "eyebrow_slug": "eb_garamond",
    }
    ink = body.ink_dict() or {"main_fill": "#3a2c1c", "eyebrow_fill": "#967648"}
    audio_slug = body.audio_slug or "sentimental_piano"
    base_payload = {
        "display_name": body.display_name,
        "fonts": fonts,
        "ink": ink,
        "audio_slug": audio_slug,
    }
    errors = _validate("tribute_visual_themes", {**base_payload, "slug": body.slug})
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    try:
        artist = _make_artist(settings)
        images = await asyncio.to_thread(
            generate_template_candidates,
            artist, brief=body.brief, n=body.n_candidates,
        )
    except Exception as exc:
        # Full traceback to the logs — the client gets a clean 502, never a
        # bare internal_server_error.
        log.exception("visual_theme.generation_failed", brief=body.brief[:80])
        raise HTTPException(
            status_code=502, detail=f"template generation failed: {exc}"
        )

    candidates = []
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                for i, image_bytes in enumerate(images, start=1):
                    # Redo-safe slugs: regenerating with the same slug
                    # REPLACES prior draft candidates (supersede + reuse);
                    # a published/archived row never gets clobbered — the
                    # new candidate takes a _vN suffix instead.
                    slug = f"{body.slug}_c{i}"
                    state = await repo.active_slug_state(
                        cur, "tribute_visual_themes", slug
                    )
                    if state == "draft":
                        await repo.supersede_active_slug(
                            cur, "tribute_visual_themes", slug,
                            updated_by=updated_by,
                        )
                    elif state is not None:
                        n = 2
                        while await repo.active_slug_state(
                            cur, "tribute_visual_themes", f"{slug}_v{n}"
                        ) is not None:
                            n += 1
                        slug = f"{slug}_v{n}"
                    payload = {
                        **base_payload,
                        "slug": slug,
                        "template_image": image_bytes,
                        "template_mime": "image/jpeg",
                    }
                    new_id = await repo.create_row(
                        cur, "tribute_visual_themes", payload,
                        updated_by=updated_by,
                    )
                    candidates.append({"id": new_id, "slug": slug})
    log.info("visual_theme.candidates_generated", count=len(candidates),
             updated_by=updated_by)
    return {"candidates": candidates}


def _make_artist(settings: HttpConfig, feature: str = "tribute_template_generate"):
    """Factory kept separate so tests can monkeypatch it."""
    from flashback.page_render.art import Artist

    return Artist(
        api_key=settings.gemini_api_key,
        model=settings.gemini_image_model,
        feature=feature,
    )


# --- preview -------------------------------------------------------------------


class TributePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: str = Field(min_length=1)
    profile_id: str | None = None
    profile_draft: dict | None = None
    campaign_id: str | None = None
    campaign_draft: dict | None = None
    visual_theme_id: str | None = None
    render_sample_page: bool = False
    sample_page_role: Literal["opener", "beat", "closing"] = "opener"
    sample_beat_index: int = Field(0, ge=0, le=15)


@router.post("/tribute_preview")
async def tribute_preview(
    body: TributePreviewRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    settings: HttpConfig = Depends(get_http_config),
    updated_by: str = Depends(admin_user),
) -> dict:
    """Real assembly over a real legacy with draft/persisted config.

    Drafts work inline — nothing must be saved to be tried (spec §5).
    Text-only by default; ``render_sample_page`` composites ONE page through
    the real compositor (separate button in the CRM — cheap vs expensive
    loop).
    """
    if not allow(f"preview:{updated_by}", 6):
        raise HTTPException(status_code=429, detail="preview rate limited")
    if body.render_sample_page and not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY not configured for sample-page rendering",
        )

    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            # --- profile: draft > id > resolved-from-person ------------------
            if body.profile_draft is not None:
                errors = validate_profile_payload(body.profile_draft)
                if errors:
                    raise HTTPException(
                        status_code=422, detail={"errors": errors}
                    )
                profile = profile_from_payload(body.profile_draft)
            elif body.profile_id:
                profile = await repo.fetch_profile_by_id(cur, body.profile_id)
                if profile is None:
                    raise HTTPException(status_code=404, detail="no profile")
            else:
                group = await ensure_relationship_group(
                    cur, settings=settings, person_id=body.person_id
                )
                profile = await repo.fetch_profile_by_group(cur, group)
                if profile is None:
                    profile = await repo.fetch_profile_by_group(cur, "other")
                if profile is None:
                    raise HTTPException(
                        status_code=404, detail="no published profiles"
                    )

            # --- campaign: draft > id > neutral -------------------------------
            if body.campaign_draft is not None:
                errors = validate_campaign_payload(body.campaign_draft)
                if errors:
                    raise HTTPException(
                        status_code=422, detail={"errors": errors}
                    )
                campaign = campaign_from_payload(body.campaign_draft)
            elif body.campaign_id:
                campaign = await repo.fetch_campaign_by_id(cur, body.campaign_id)
                if campaign is None:
                    raise HTTPException(status_code=404, detail="no campaign")
            else:
                from flashback.tribute.config_schema import NEUTRAL_CAMPAIGN

                campaign = NEUTRAL_CAMPAIGN

            payload, book, directives = await build_preview(
                settings, cur, person_id=body.person_id,
                profile=profile, campaign=campaign,
            )

            sample_b64: str | None = None
            if body.render_sample_page:
                theme_id = body.visual_theme_id or directives.visual_theme_id
                style_dict = None
                template_path = None
                if theme_id:
                    vt = await repo.fetch_visual_theme_by_id(cur, theme_id)
                    if vt is not None:
                        style_dict = {
                            "visual_theme_id": vt.id,
                            "fonts": vt.fonts,
                            "ink": vt.ink,
                            "audio_slug": vt.audio_slug,
                        }
                        found = await repo.fetch_visual_theme_image(cur, vt.id)
                        if found is not None:
                            import tempfile

                            image_bytes, _mime = found
                            tmp = tempfile.NamedTemporaryFile(
                                suffix=".img", delete=False
                            )
                            tmp.write(image_bytes)
                            tmp.close()
                            template_path = tmp.name
                kit = kit_from_style_dict(
                    style_dict, template_override_path=template_path
                )
                artist = _make_artist(settings, feature="tribute_preview")
                try:
                    import asyncio
                    import base64

                    sample_bytes = await asyncio.to_thread(
                        render_sample_page,
                        artist, book=book, kit=kit,
                        role=body.sample_page_role,
                        beat_index=body.sample_beat_index,
                        art_mood=directives.art_mood or None,
                    )
                    sample_b64 = base64.b64encode(sample_bytes).decode("ascii")
                finally:
                    if template_path:
                        import os

                        try:
                            os.unlink(template_path)
                        except OSError:
                            pass

    payload["sample_page_b64"] = sample_b64
    log.info("tribute_preview.served", person_id=body.person_id,
             sample=body.render_sample_page, updated_by=updated_by)
    return payload
