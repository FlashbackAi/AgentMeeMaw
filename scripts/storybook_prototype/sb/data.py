"""Read-only reads from the prod canonical graph. SELECT only — never writes.

Reuses the service's connection factory and the same 'qualifying moment'
definition the tribute path uses (sensory_details OR time_anchor OR an
involves edge).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from flashback.db.connection import make_pool
from flashback.ground_truth.render import render_ground_truth_block

from . import config  # noqa: F401  (import side effect: loads .env.production)

_MOMENTS_SQL = """
SELECT m.id::text, m.title, m.narrative,
       m.generation_prompt, m.sensory_details, m.time_anchor
  FROM active_moments m
 WHERE m.person_id = %(person_id)s
   AND (
        m.sensory_details IS NOT NULL
     OR m.time_anchor IS NOT NULL
     OR EXISTS (SELECT 1 FROM edges ie
                 WHERE ie.from_kind='moment' AND ie.from_id=m.id
                   AND ie.edge_type='involves' AND ie.status='active')
   )
 ORDER BY m.created_at DESC
 LIMIT %(limit)s
"""


@dataclass(frozen=True)
class Subject:
    person_id: str
    name: str
    relationship: str | None
    ground_truth: dict[str, Any]
    scene_subject_context: str  # rendered GT block for image prompts


def fetch_subject_and_moments(
    person_id: str, *, limit: int = 80
) -> tuple[Subject, list[dict[str, Any]]]:
    pool = make_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, relationship, ground_truth "
                    "FROM persons WHERE id=%s",
                    (person_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise SystemExit(f"No person found for {person_id}")
                name, relationship, gt = row[0], row[1], (row[2] or {})
                subject = Subject(
                    person_id=person_id,
                    name=name,
                    relationship=relationship,
                    ground_truth=gt,
                    scene_subject_context=render_ground_truth_block(
                        gt, "scene_subject"
                    ),
                )
                cur.execute(_MOMENTS_SQL, {"person_id": person_id, "limit": limit})
                moments = [
                    {
                        "id": r[0],
                        "title": r[1],
                        "narrative": r[2],
                        "generation_prompt": r[3],
                        "sensory_details": r[4],
                        "time_anchor": r[5],
                    }
                    for r in cur.fetchall()
                ]
        return subject, moments
    finally:
        pool.close()
