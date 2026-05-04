"""Transactional persistence for Question Producers P2/P3/P5."""

from __future__ import annotations

from dataclasses import dataclass, field

from psycopg.types.json import Json

from flashback.db.edges import validate_edge

from .schema import ProducerResult


@dataclass
class PersistResult:
    question_ids: list[str] = field(default_factory=list)

    @property
    def questions_written(self) -> int:
        return len(self.question_ids)

    def summary(self) -> dict:
        return {"questions_written": self.questions_written}


def persist_producer_result(cursor, *, result: ProducerResult) -> PersistResult:
    """Insert all questions and P2 target edges for one producer run."""
    out = PersistResult()
    for q in result.questions:
        attributes = dict(q.attributes)
        attributes["themes"] = list(q.themes)
        question_id = _insert_question(
            cursor,
            person_id=str(result.person_id),
            text=q.text,
            source=result.source_tag,
            attributes=attributes,
        )
        out.question_ids.append(question_id)

        if q.targets_entity_id is not None:
            _assert_active_entity_for_person(
                cursor,
                entity_id=str(q.targets_entity_id),
                person_id=str(result.person_id),
            )
            _insert_validated_edge(
                cursor,
                from_kind="question",
                from_id=question_id,
                to_kind="entity",
                to_id=str(q.targets_entity_id),
                edge_type="targets",
            )

    return out


def _assert_active_entity_for_person(
    cursor, *, entity_id: str, person_id: str
) -> None:
    """Caller-side scope/existence guard for P2 target edges."""
    cursor.execute(
        """
        SELECT 1
          FROM active_entities
         WHERE id = %s
           AND person_id = %s
        """,
        (entity_id, person_id),
    )
    if cursor.fetchone() is None:
        raise ValueError(
            f"target entity {entity_id!r} is not active for person {person_id!r}"
        )


def push_question_embeddings(
    *,
    embedding_sender,
    result: ProducerResult,
    question_ids: list[str],
    embedding_model: str,
    embedding_model_version: str,
) -> None:
    """Push one embedding job per newly inserted question after commit."""
    for question_id, question in zip(question_ids, result.questions, strict=True):
        embedding_sender.send(
            record_type="question",
            record_id=question_id,
            source_text=question.text,
            embedding_model=embedding_model,
            embedding_model_version=embedding_model_version,
        )


def _insert_question(
    cursor,
    *,
    person_id: str,
    text: str,
    source: str,
    attributes: dict,
) -> str:
    cursor.execute(
        """
        INSERT INTO questions (person_id, text, source, attributes)
        VALUES                (%s,        %s,   %s,     %s)
        RETURNING id::text
        """,
        (person_id, text, source, Json(attributes)),
    )
    return cursor.fetchone()[0]


def _insert_validated_edge(
    cursor,
    *,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    edge_type: str,
) -> None:
    validate_edge(from_kind, to_kind, edge_type)
    cursor.execute(
        """
        INSERT INTO edges (from_kind, from_id, to_kind, to_id,
                           edge_type, attributes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (from_kind, from_id, to_kind, to_id, edge_type)
        DO NOTHING
        """,
        (from_kind, from_id, to_kind, to_id, edge_type, Json({})),
    )
