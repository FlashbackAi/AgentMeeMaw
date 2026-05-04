"""P2 - underdeveloped-entity question producer."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import structlog

from flashback.llm.interface import call_with_tool

from .prompts import P2_SYSTEM_PROMPT, P2_TOOL
from .schema import GeneratedQuestion, ProducerLLMConfig, ProducerResult

log = structlog.get_logger("flashback.workers.producers.underdeveloped")


@dataclass
class UnderdevelopedEntity:
    id: UUID
    kind: str
    name: str
    description: str | None
    mention_count: int
    related_thread_names: list[str] = field(default_factory=list)


class P2Underdeveloped:
    name = "P2"
    source_tag = "underdeveloped_entity"

    async def produce(self, db_pool, person_id: UUID, settings) -> ProducerResult:
        entities = self._find_underdeveloped(db_pool, person_id, settings)
        if not entities:
            return ProducerResult(
                person_id=person_id,
                source_tag=self.source_tag,
                questions=[],
                overall_reasoning="no underdeveloped entities found",
            )
        return await self._call_llm(entities, person_id, settings)

    def _find_underdeveloped(
        self, db_pool, person_id: UUID, settings
    ) -> list[UnderdevelopedEntity]:
        """Find entities with fewer than 3 active moment mentions."""
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT en.id::text, en.kind, en.name, en.description,
                           (
                             SELECT count(*)
                               FROM active_edges e
                               JOIN active_moments m ON m.id = e.from_id
                              WHERE e.from_kind = 'moment'
                                AND e.to_kind   = 'entity'
                                AND e.to_id     = en.id
                                AND e.edge_type = 'involves'
                                AND m.person_id = %(pid)s
                           ) AS mention_count
                      FROM active_entities en
                     WHERE en.person_id = %(pid)s
                    """,
                    {"pid": str(person_id)},
                )
                rows = cur.fetchall()

        entities = [
            UnderdevelopedEntity(
                id=UUID(str(row[0])),
                kind=str(row[1]),
                name=str(row[2]),
                description=row[3],
                mention_count=int(row[4]),
            )
            for row in rows
            if int(row[4]) < 3
        ]
        entities.sort(key=lambda e: (e.mention_count, len(e.description or "")))
        entities = entities[: settings.p2_max_entities_per_run]

        for entity in entities:
            entity.related_thread_names = self._fetch_related_threads(
                db_pool, person_id=person_id, entity_id=entity.id
            )

        return entities

    def _fetch_related_threads(
        self, db_pool, *, person_id: UUID, entity_id: UUID
    ) -> list[str]:
        """Fetch threads directly or indirectly evidenced by this entity."""
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT t.name
                      FROM active_threads t
                      JOIN active_edges et
                        ON et.to_kind = 'thread'
                       AND et.to_id = t.id
                       AND et.edge_type = 'evidences'
                     WHERE t.person_id = %(pid)s
                       AND (
                            (et.from_kind = 'entity' AND et.from_id = %(eid)s)
                            OR
                            (
                              et.from_kind = 'moment'
                              AND EXISTS (
                                  SELECT 1
                                    FROM active_edges me
                                    JOIN active_moments m ON m.id = me.from_id
                                   WHERE me.from_kind = 'moment'
                                     AND me.to_kind = 'entity'
                                     AND me.edge_type = 'involves'
                                     AND me.to_id = %(eid)s
                                     AND m.id = et.from_id
                                     AND m.person_id = %(pid)s
                              )
                            )
                       )
                     ORDER BY t.name
                    """,
                    {"pid": str(person_id), "eid": str(entity_id)},
                )
                return [str(row[0]) for row in cur.fetchall()]

    async def _call_llm(
        self, entities: list[UnderdevelopedEntity], person_id: UUID, settings
    ) -> ProducerResult:
        cfg = ProducerLLMConfig(
            provider=settings.llm_producer_provider,
            model=settings.llm_producer_model,
            timeout=settings.llm_producer_timeout_seconds,
            max_tokens=settings.llm_producer_max_tokens,
        )
        args = await call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=P2_SYSTEM_PROMPT,
            user_message=_build_user_message(
                entities=entities,
                questions_per_entity=settings.p2_questions_per_entity,
            ),
            tool=P2_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
        allowed = {e.id for e in entities}
        questions: list[GeneratedQuestion] = []
        for item in args.get("questions", []) or []:
            q = GeneratedQuestion(
                text=item["text"],
                themes=item["themes"],
                attributes={},
                targets_entity_id=UUID(str(item["targets_entity_id"])),
            )
            if q.targets_entity_id in allowed:
                questions.append(q)
            else:
                log.warning(
                    "producer_p2.dropped_unknown_entity_target",
                    person_id=str(person_id),
                    target=str(q.targets_entity_id),
                )
        return ProducerResult(
            person_id=person_id,
            source_tag=self.source_tag,
            questions=questions,
            overall_reasoning=str(args.get("overall_reasoning", "")),
        )


def _build_user_message(
    *, entities: list[UnderdevelopedEntity], questions_per_entity: int
) -> str:
    lines = [
        f"<questions_per_entity>{questions_per_entity}</questions_per_entity>",
        "<under_developed_entities>",
    ]
    for entity in entities:
        lines.append(f"<entity id='{entity.id}'>")
        lines.append(f"kind: {entity.kind}")
        lines.append(f"name: {entity.name}")
        lines.append(f"description: {entity.description or ''}")
        lines.append(f"mention_count: {entity.mention_count}")
        if entity.related_thread_names:
            lines.append("related_threads: " + ", ".join(entity.related_thread_names))
        else:
            lines.append("related_threads: none")
        lines.append("</entity>")
    lines.append("</under_developed_entities>")
    return "\n".join(lines)

