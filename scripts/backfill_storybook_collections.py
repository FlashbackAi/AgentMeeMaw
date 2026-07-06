"""Backfill ``moments.storybook_collections`` for moments extracted before the
per-collection eligibility feature (design 2026-07-06).

Tags every active moment whose ``storybook_collections`` is NULL with the grid
collections it fits, using the same judgement the Extraction Worker now applies
inline. Writes ``'{}'`` when a moment fits nothing, so re-runs skip it —
idempotent, resumable, and safe to run repeatedly.

Usage::

    DATABASE_URL=... ANTHROPIC_API_KEY=... python scripts/backfill_storybook_collections.py
    python scripts/backfill_storybook_collections.py --dry-run
    python scripts/backfill_storybook_collections.py --person <uuid>

Run once at deploy so existing legacies show true per-collection eligibility.
"""

from __future__ import annotations

import argparse
import asyncio

import psycopg
import structlog

from flashback.config import ExtractionConfig
from flashback.storybook.tagging import tag_moments

log = structlog.get_logger("flashback.scripts.backfill_storybook_collections")

BATCH_SIZE = 15


def _persons_with_untagged(cur, person: str | None) -> list[tuple[str, str, str | None]]:
    """(person_id, name, relationship) for persons holding untagged moments."""
    if person is not None:
        cur.execute(
            "SELECT id::text, name, relationship FROM persons WHERE id = %s",
            (person,),
        )
        return list(cur.fetchall())
    cur.execute(
        """
        SELECT DISTINCT p.id::text, p.name, p.relationship
          FROM persons p
          JOIN moments m ON m.person_id = p.id
         WHERE m.status = 'active'
           AND m.storybook_collections IS NULL
         ORDER BY p.id::text
        """
    )
    return list(cur.fetchall())


def _untagged_moments(cur, person_id: str) -> list[dict]:
    cur.execute(
        """
        SELECT id::text, title, narrative
          FROM moments
         WHERE person_id = %s
           AND status = 'active'
           AND storybook_collections IS NULL
         ORDER BY created_at ASC
        """,
        (person_id,),
    )
    return [
        {"id": r[0], "title": r[1], "narrative": r[2]}
        for r in cur.fetchall()
    ]


def _write_tags(cur, moment_id: str, slugs: list[str]) -> None:
    cur.execute(
        "UPDATE moments SET storybook_collections = %s WHERE id = %s",
        (list(slugs), moment_id),
    )


async def _run(cfg: ExtractionConfig, *, person: str | None, dry_run: bool) -> int:
    tagged_total = 0
    with psycopg.connect(cfg.database_url) as conn:
        with conn.cursor() as cur:
            persons = _persons_with_untagged(cur, person)
        log.info("backfill.start", persons=len(persons), dry_run=dry_run)
        for pid, name, relationship in persons:
            with conn.cursor() as cur:
                moments = _untagged_moments(cur, pid)
            if not moments:
                continue
            log.info("backfill.person", person_id=pid, moments=len(moments))
            for start in range(0, len(moments), BATCH_SIZE):
                batch = moments[start:start + BATCH_SIZE]
                try:
                    tags = await tag_moments(
                        settings=cfg,
                        provider=cfg.llm_extraction_provider,
                        model=cfg.llm_extraction_model,
                        subject_name=name or "",
                        relationship=relationship,
                        moments=batch,
                    )
                except Exception as exc:  # noqa: BLE001 - skip batch, resume later
                    log.warning(
                        "backfill.batch_failed",
                        person_id=pid,
                        error_type=type(exc).__name__,
                        error=str(exc)[:200],
                    )
                    continue
                if dry_run:
                    for mid, slugs in tags.items():
                        print(f"{mid}\t{slugs}")
                    tagged_total += sum(1 for v in tags.values() if v)
                    continue
                with conn.transaction():
                    with conn.cursor() as cur:
                        for mid, slugs in tags.items():
                            _write_tags(cur, mid, slugs)
                tagged_total += sum(1 for v in tags.values() if v)
    log.info("backfill.done", tagged=tagged_total, dry_run=dry_run)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print tags without writing")
    parser.add_argument("--person", default=None,
                        help="backfill a single person id")
    args = parser.parse_args(argv)
    cfg = ExtractionConfig.from_env()
    return asyncio.run(_run(cfg, person=args.person, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
