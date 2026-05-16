"""Repository for the ``themes`` table.

Two sync surfaces (extraction worker, thread detector) and two async
surfaces (HTTP endpoints, onboarding/persons flows). Keep both honest by
sharing SQL where possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from flashback.themes.universal import UNIVERSAL_THEMES


# ---------------------------------------------------------------------------
# Universal seeding
# ---------------------------------------------------------------------------


_SEED_UNIVERSAL_THEME_SQL = """
INSERT INTO themes (person_id, kind, slug, display_name, state)
VALUES (%(person_id)s, 'universal', %(slug)s, %(display_name)s, 'locked')
ON CONFLICT (person_id, slug) WHERE status = 'active' DO NOTHING
"""


async def seed_universal_themes_async(cur, *, person_id: UUID | str) -> None:
    """Seed all five universal themes for a person inside the caller's tx.

    Idempotent: re-running on an already-seeded person is a no-op via
    the partial unique index.
    """
    pid = str(person_id)
    for theme in UNIVERSAL_THEMES:
        await cur.execute(
            _SEED_UNIVERSAL_THEME_SQL,
            {
                "person_id": pid,
                "slug": theme.slug,
                "display_name": theme.display_name,
            },
        )


def seed_universal_themes_sync(cur, *, person_id: UUID | str) -> None:
    """Sync variant (used by extraction worker if needed; mirrors async)."""
    pid = str(person_id)
    for theme in UNIVERSAL_THEMES:
        cur.execute(
            _SEED_UNIVERSAL_THEME_SQL,
            {
                "person_id": pid,
                "slug": theme.slug,
                "display_name": theme.display_name,
            },
        )


# ---------------------------------------------------------------------------
# Theme lookups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThemeRow:
    id: str
    person_id: str
    kind: str  # 'universal' | 'emergent'
    slug: str
    display_name: str
    description: str | None
    state: str  # 'locked' | 'unlocked'
    archetype_questions: list[dict[str, Any]] | None
    archetype_answers: list[dict[str, Any]] | None
    thread_id: str | None


def fetch_active_themes_for_person_sync(
    cur, *, person_id: str
) -> list[ThemeRow]:
    """Return all active themes for a person.

    Used by the extraction-worker prompt builder to know which emergent
    themes are taggable for this subject (universals are always taggable).
    """
    cur.execute(
        """
        SELECT id::text, person_id::text, kind, slug, display_name,
               description, state, archetype_questions, archetype_answers,
               thread_id::text
          FROM active_themes
         WHERE person_id = %s
         ORDER BY kind, slug
        """,
        (person_id,),
    )
    out: list[ThemeRow] = []
    for row in cur.fetchall():
        (
            tid,
            pid,
            kind,
            slug,
            display_name,
            description,
            state,
            archetype_questions,
            archetype_answers,
            thread_id,
        ) = row
        out.append(
            ThemeRow(
                id=tid,
                person_id=pid,
                kind=kind,
                slug=slug,
                display_name=display_name,
                description=description,
                state=state,
                archetype_questions=archetype_questions,
                archetype_answers=archetype_answers,
                thread_id=thread_id,
            )
        )
    return out


async def fetch_theme_by_id_async(
    cur, *, theme_id: str, person_id: str | None = None
) -> ThemeRow | None:
    """Fetch a single active theme. If ``person_id`` is given, scope to it."""
    if person_id is not None:
        await cur.execute(
            """
            SELECT id::text, person_id::text, kind, slug, display_name,
                   description, state, archetype_questions, archetype_answers,
                   thread_id::text
              FROM active_themes
             WHERE id = %s AND person_id = %s
            """,
            (theme_id, person_id),
        )
    else:
        await cur.execute(
            """
            SELECT id::text, person_id::text, kind, slug, display_name,
                   description, state, archetype_questions, archetype_answers,
                   thread_id::text
              FROM active_themes
             WHERE id = %s
            """,
            (theme_id,),
        )
    row = await cur.fetchone()
    if row is None:
        return None
    (
        tid,
        pid,
        kind,
        slug,
        display_name,
        description,
        state,
        archetype_questions,
        archetype_answers,
        thread_id,
    ) = row
    return ThemeRow(
        id=tid,
        person_id=pid,
        kind=kind,
        slug=slug,
        display_name=display_name,
        description=description,
        state=state,
        archetype_questions=archetype_questions,
        archetype_answers=archetype_answers,
        thread_id=thread_id,
    )


def fetch_theme_by_slug_sync(
    cur, *, person_id: str, slug: str
) -> ThemeRow | None:
    cur.execute(
        """
        SELECT id::text, person_id::text, kind, slug, display_name,
               description, state, archetype_questions, archetype_answers,
               thread_id::text
          FROM active_themes
         WHERE person_id = %s AND slug = %s
        """,
        (person_id, slug),
    )
    row = cur.fetchone()
    if row is None:
        return None
    (
        tid,
        pid,
        kind,
        slug_out,
        display_name,
        description,
        state,
        archetype_questions,
        archetype_answers,
        thread_id,
    ) = row
    return ThemeRow(
        id=tid,
        person_id=pid,
        kind=kind,
        slug=slug_out,
        display_name=display_name,
        description=description,
        state=state,
        archetype_questions=archetype_questions,
        archetype_answers=archetype_answers,
        thread_id=thread_id,
    )


def fetch_theme_slug_to_id_sync(cur, *, person_id: str) -> dict[str, str]:
    """Return a {slug: theme_id} map for all active themes for a person."""
    cur.execute(
        "SELECT slug, id::text FROM active_themes WHERE person_id = %s",
        (person_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


async def update_archetype_questions_async(
    cur, *, theme_id: str, questions: list[dict[str, Any]]
) -> None:
    await cur.execute(
        """
        UPDATE themes
           SET archetype_questions = %s
         WHERE id = %s
           AND status = 'active'
        """,
        (Json(questions), theme_id),
    )


def update_archetype_questions_sync(
    cur, *, theme_id: str, questions: list[dict[str, Any]]
) -> None:
    cur.execute(
        """
        UPDATE themes
           SET archetype_questions = %s
         WHERE id = %s
           AND status = 'active'
        """,
        (Json(questions), theme_id),
    )


async def unlock_theme_async(
    cur, *, theme_id: str, archetype_answers: list[dict[str, Any]]
) -> None:
    """Flip a theme to ``unlocked`` and persist the user's archetype answers.

    Idempotent: re-running for an already-unlocked theme updates the
    answers but keeps the original ``unlocked_at`` via COALESCE.
    """
    await cur.execute(
        """
        UPDATE themes
           SET state             = 'unlocked',
               archetype_answers = %s,
               unlocked_at       = COALESCE(unlocked_at, now())
         WHERE id = %s
           AND status = 'active'
        """,
        (Json(archetype_answers), theme_id),
    )


def insert_emergent_theme_sync(
    cur,
    *,
    person_id: str,
    slug: str,
    display_name: str,
    description: str | None,
    thread_id: str,
    archetype_questions: list[dict[str, Any]] | None,
    generation_prompt: str | None,
) -> str | None:
    """Insert a new emergent theme. Returns the new theme id, or ``None``
    if there's already an active theme with this slug for the person.

    Uses the partial-unique-index conflict to stay idempotent under
    re-runs of the Thread Detector cluster path.
    """
    cur.execute(
        """
        INSERT INTO themes (
            person_id, kind, slug, display_name, description, state,
            archetype_questions, thread_id, generation_prompt
        )
        VALUES (
            %s, 'emergent', %s, %s, %s, 'locked',
            %s, %s, %s
        )
        ON CONFLICT (person_id, slug) WHERE status = 'active' DO NOTHING
        RETURNING id::text
        """,
        (
            person_id,
            slug,
            display_name,
            description,
            Json(archetype_questions) if archetype_questions is not None else None,
            thread_id,
            generation_prompt,
        ),
    )
    row = cur.fetchone()
    return row[0] if row is not None else None
