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
    importance_score: int = 0
    importance_reason: str = ""
    related_thread_names: list[str] = field(default_factory=list)


class P2Underdeveloped:
    name = "P2"
    source_tag = "underdeveloped_entity"

    async def produce(self, db_pool, person_id: UUID, settings) -> ProducerResult:
        subject_name = self._fetch_subject_name(db_pool, person_id)
        entities = self._find_underdeveloped(
            db_pool, person_id, settings, subject_name=subject_name
        )
        if not entities:
            return ProducerResult(
                person_id=person_id,
                source_tag=self.source_tag,
                questions=[],
                overall_reasoning="no underdeveloped entities found",
            )
        return await self._call_llm(entities, person_id, settings, subject_name=subject_name)

    def _find_underdeveloped(
        self, db_pool, person_id: UUID, settings, *, subject_name: str
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

        entities: list[UnderdevelopedEntity] = []
        for row in rows:
            if int(row[4]) >= 3:
                continue
            entity = UnderdevelopedEntity(
                id=UUID(str(row[0])),
                kind=str(row[1]),
                name=str(row[2]),
                description=row[3],
                mention_count=int(row[4]),
            )
            entity.importance_score, entity.importance_reason = _importance_score(
                entity,
                subject_name=subject_name,
            )
            if entity.importance_score >= 2:
                entities.append(entity)

        entities.sort(
            key=lambda e: (-e.importance_score, e.mention_count, len(e.description or ""))
        )
        entities = entities[: settings.p2_max_entities_per_run]

        for entity in entities:
            entity.related_thread_names = self._fetch_related_threads(
                db_pool, person_id=person_id, entity_id=entity.id
            )

        return entities

    def _fetch_subject_name(self, db_pool, person_id: UUID) -> str:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM persons WHERE id = %s",
                    (str(person_id),),
                )
                row = cur.fetchone()
        return str(row[0]) if row else "the subject"

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
        self,
        entities: list[UnderdevelopedEntity],
        person_id: UUID,
        settings,
        *,
        subject_name: str,
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
                subject_name=subject_name,
            ),
            tool=P2_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
        allowed = {e.id for e in entities}
        questions: list[GeneratedQuestion] = []
        per_entity_counts: dict[UUID, int] = {}
        max_supporting_questions = 2
        for item in args.get("questions", []) or []:
            target_id = UUID(str(item["targets_entity_id"]))
            if target_id not in allowed:
                log.warning(
                    "producer_p2.dropped_unknown_entity_target",
                    person_id=str(person_id),
                    target=str(target_id),
                )
                continue
            if per_entity_counts.get(target_id, 0) >= 1:
                continue
            if len(questions) >= max_supporting_questions:
                continue
            text = str(item["text"])
            if not _is_subject_centered(text, subject_name=subject_name):
                log.info(
                    "producer_p2.dropped_not_subject_centered",
                    person_id=str(person_id),
                    text=text,
                )
                continue
            if _has_poetic_framing(text):
                log.info(
                    "producer_p2.dropped_poetic_question",
                    person_id=str(person_id),
                    text=text,
                )
                continue
            q = GeneratedQuestion(
                text=text,
                themes=item["themes"],
                attributes={
                    "subject_centered": True,
                    "supporting_entity": True,
                },
                targets_entity_id=target_id,
            )
            questions.append(q)
            per_entity_counts[target_id] = per_entity_counts.get(target_id, 0) + 1
        return ProducerResult(
            person_id=person_id,
            source_tag=self.source_tag,
            questions=questions,
            overall_reasoning=str(args.get("overall_reasoning", "")),
        )


def _build_user_message(
    *,
    entities: list[UnderdevelopedEntity],
    questions_per_entity: int,
    subject_name: str,
) -> str:
    lines = [
        f"<questions_per_entity>{questions_per_entity}</questions_per_entity>",
        "<subject>",
        f"name: {subject_name}",
        "</subject>",
        "<rules>",
        "Generate at most 2 questions total.",
        "Generate at most 1 question for any one supporting entity.",
        "Only ask if the answer would teach us something about the subject.",
        "Use plain concrete wording; avoid interpretive or poetic phrasing.",
        "</rules>",
        "<under_developed_entities>",
    ]
    for entity in entities:
        lines.append(f"<entity id='{entity.id}'>")
        lines.append(f"kind: {entity.kind}")
        lines.append(f"name: {entity.name}")
        lines.append(f"description: {entity.description or ''}")
        lines.append(f"mention_count: {entity.mention_count}")
        lines.append(f"importance_score: {entity.importance_score}")
        lines.append(f"importance_reason: {entity.importance_reason}")
        if entity.related_thread_names:
            lines.append("related_threads: " + ", ".join(entity.related_thread_names))
        else:
            lines.append("related_threads: none")
        lines.append("</entity>")
    lines.append("</under_developed_entities>")
    return "\n".join(lines)


def _importance_score(
    entity: UnderdevelopedEntity,
    *,
    subject_name: str,
) -> tuple[int, str]:
    description = (entity.description or "").lower()
    name = entity.name.lower()
    subject = subject_name.lower()
    score = 0
    reasons: list[str] = []

    if entity.mention_count > 0:
        score += 1
        reasons.append("mentioned in active moment")
    if subject and subject in description:
        score += 1
        reasons.append("description references subject")
    if any(
        word in description
        for word in (
            "friendship",
            "friend",
            "conversation starter",
            "training",
            "lunch",
            "modelling",
            "modeling",
            "worked",
            "lived",
        )
    ):
        score += 1
        reasons.append("linked to subject context")
    if entity.kind in {"object", "place"} and any(
        word in description for word in ("starter", "where", "city", "training", "model")
    ):
        score += 1
        reasons.append("object/place anchors concrete subject detail")
    if entity.kind == "person" and subject not in description and entity.mention_count <= 1:
        score -= 1
        reasons.append("supporting person appears incidental")
    if name == subject:
        score -= 99
        reasons.append("entity is the subject")

    return max(0, score), ", ".join(reasons) or "thin context"


def _is_subject_centered(text: str, *, subject_name: str) -> bool:
    lowered = text.lower()
    subject = subject_name.lower()
    return subject in lowered or any(
        pronoun in lowered.split()
        for pronoun in ("he", "she", "they", "him", "her", "them", "his", "their")
    )


def _has_poetic_framing(text: str) -> bool:
    lowered = text.lower()
    banned = (
        "what did ",
        "mean in",
        "symbolize",
        "represent",
        "essence",
        "world",
        "belonging",
        "center",
        "thread",
        "carried with",
        "opened the door",
    )
    if "what did " in lowered and " mean" in lowered:
        return True
    return any(term in lowered for term in banned[1:])
