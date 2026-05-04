"""Context assembly for the Trait Synthesizer.

Pulls the person's name, all active traits (with their current
``exemplifies``-edge counts), and all active threads (with their
``evidences``-edge moment counts). Formats the result into a single
string the LLM consumes.

Invariants honoured (CLAUDE.md §4):

* #1 (status='active'): all reads scope to ``active_traits`` /
  ``active_threads`` / ``active_moments`` views, with active edges.
* #2 (person_id scoping): every query carries the legacy ``person_id``;
  cross-legacy bleed is impossible.
"""

from __future__ import annotations

from typing import Iterable

from .schema import (
    ExistingTraitView,
    ThreadView,
    TraitSynthContext,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_context(db_pool, *, person_id: str) -> TraitSynthContext:
    """Read everything the synthesizer LLM needs for one person."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            person_name = _fetch_person_name(cur, person_id=person_id)
            existing_traits = _fetch_existing_traits(cur, person_id=person_id)
            threads = _fetch_threads(cur, person_id=person_id)

    return TraitSynthContext(
        person_id=person_id,
        person_name=person_name,
        existing_traits=existing_traits,
        threads=threads,
    )


def render_user_message(ctx: TraitSynthContext) -> str:
    """Format ``ctx`` into the user message passed to the LLM.

    The shape is intentionally simple — a tagged-section block per
    surface so the model can scan quickly. Counts are inlined so the
    model can weigh evidence without an extra reasoning round-trip.
    """
    lines: list[str] = []
    lines.append("<subject>")
    lines.append(f"Name: {ctx.person_name}")
    lines.append("</subject>")
    lines.append("")
    lines.append("<existing_traits>")
    if not ctx.existing_traits:
        lines.append("(none)")
    else:
        for trait in ctx.existing_traits:
            description = trait.description or ""
            lines.append(
                f"- [trait_id={trait.id}] {trait.name} "
                f"(strength={trait.strength}) — {description} "
                f"(currently exemplified by {trait.moment_count} moments)"
            )
    lines.append("</existing_traits>")
    lines.append("")
    lines.append("<threads>")
    if not ctx.threads:
        lines.append("(none)")
    else:
        for thread in ctx.threads:
            lines.append(
                f"- [thread_id={thread.id}] {thread.name}: "
                f"{thread.description} ({thread.moment_count} moments)"
            )
    lines.append("</threads>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fetch_person_name(cur, *, person_id: str) -> str:
    cur.execute(
        "SELECT name FROM persons WHERE id = %s",
        (person_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"person {person_id!r} not found")
    return str(row[0])


def _fetch_existing_traits(cur, *, person_id: str) -> list[ExistingTraitView]:
    """Active traits + their active ``exemplifies``-moment counts.

    The count uses ``LEFT JOIN`` against the active edges and active
    moments so a trait with zero exemplifying moments still appears
    (with ``moment_count=0``).
    """
    cur.execute(
        """
        SELECT t.id::text,
               t.name,
               t.description,
               t.strength,
               COUNT(DISTINCT m.id) AS moment_count
          FROM active_traits t
          LEFT JOIN active_edges e
                 ON e.from_kind = 'moment'
                AND e.to_kind   = 'trait'
                AND e.to_id     = t.id
                AND e.edge_type = 'exemplifies'
          LEFT JOIN active_moments m
                 ON m.id = e.from_id
                AND m.person_id = t.person_id
         WHERE t.person_id = %s
         GROUP BY t.id, t.name, t.description, t.strength, t.created_at
         ORDER BY t.created_at ASC
        """,
        (person_id,),
    )
    out: list[ExistingTraitView] = []
    for row in cur.fetchall():
        trait_id, name, description, strength, moment_count = row
        out.append(
            ExistingTraitView(
                id=str(trait_id),
                name=str(name),
                description=description if description is None else str(description),
                strength=str(strength),  # type: ignore[arg-type]
                moment_count=int(moment_count or 0),
            )
        )
    return out


def _fetch_threads(cur, *, person_id: str) -> list[ThreadView]:
    """Active threads + count of active moments evidencing each."""
    cur.execute(
        """
        SELECT th.id::text,
               th.name,
               th.description,
               COUNT(DISTINCT m.id) AS moment_count
          FROM active_threads th
          LEFT JOIN active_edges e
                 ON e.from_kind = 'moment'
                AND e.to_kind   = 'thread'
                AND e.to_id     = th.id
                AND e.edge_type = 'evidences'
          LEFT JOIN active_moments m
                 ON m.id = e.from_id
                AND m.person_id = th.person_id
         WHERE th.person_id = %s
         GROUP BY th.id, th.name, th.description, th.created_at
         ORDER BY th.created_at ASC
        """,
        (person_id,),
    )
    out: list[ThreadView] = []
    for row in cur.fetchall():
        thread_id, name, description, moment_count = row
        out.append(
            ThreadView(
                id=str(thread_id),
                name=str(name),
                description=str(description),
                moment_count=int(moment_count or 0),
            )
        )
    return out


def threads_by_id(threads: Iterable[ThreadView]) -> dict[str, ThreadView]:
    """Convenience for callers that want O(1) lookup by id."""
    return {t.id: t for t in threads}
