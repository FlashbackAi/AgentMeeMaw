"""P5 - universal-dimension coverage question producer.

The keyword map is a v1 heuristic. It is intentionally simple and will
need tuning as we observe the Extraction Worker output; future versions
may add explicit extraction-time tags or a small classifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from flashback.llm.interface import call_with_tool

from .prompts import P5_SYSTEM_PROMPT, P5_TOOL
from .schema import GeneratedQuestion, ProducerLLMConfig, ProducerResult


UNIVERSAL_DIMENSIONS: tuple[str, ...] = (
    "childhood",
    "family",
    "education",
    "work",
    "marriage",
    "parenthood",
    "hobbies",
    "fears",
    "joys",
    "regrets",
    "advice",
    "daily_routines",
    "food",
    "beliefs",
    "memorable_phrases",
    "faiths",
    "big_losses",
)


UNIVERSAL_DIMENSION_KEYWORDS: dict[str, frozenset[str]] = {
    "childhood": frozenset({"child", "kid", "young", "growing up", "school"}),
    "family": frozenset({"family", "mother", "father", "sibling", "brother", "sister"}),
    "education": frozenset({"school", "college", "university", "teacher", "study"}),
    "work": frozenset({"work", "job", "career", "office", "boss"}),
    "marriage": frozenset({"marriage", "wedding", "spouse", "wife", "husband"}),
    "parenthood": frozenset({"parent", "child", "raising", "fatherhood", "motherhood"}),
    "hobbies": frozenset({"hobby", "loved", "enjoyed", "passion", "collected"}),
    "fears": frozenset({"afraid", "fear", "worried", "scared", "anxious"}),
    "joys": frozenset({"joy", "happy", "loved", "delighted", "excited"}),
    "regrets": frozenset({"regret", "wish", "should have", "missed"}),
    "advice": frozenset({"told me", "always said", "advice", "lesson"}),
    "daily_routines": frozenset({"every day", "morning", "evening", "always", "routine"}),
    "food": frozenset({"cook", "meal", "kitchen", "recipe", "ate", "favorite food"}),
    "beliefs": frozenset({"believe", "faith", "spirit", "values"}),
    "memorable_phrases": frozenset({"used to say", "always said", "phrase", "expression"}),
    "faiths": frozenset({"church", "temple", "synagogue", "mosque", "prayer", "religion"}),
    "big_losses": frozenset({"lost", "death", "passed away", "grief", "funeral"}),
}


@dataclass(frozen=True)
class UnderCoveredDimension:
    name: str
    coverage_count: int


class P5UniversalCoverage:
    name = "P5"
    source_tag = "universal_dimension"

    async def produce(self, db_pool, person_id: UUID, settings) -> ProducerResult:
        under_covered = self._find_under_covered(db_pool, person_id, settings)
        if not under_covered:
            return ProducerResult(
                person_id=person_id,
                source_tag=self.source_tag,
                questions=[],
                overall_reasoning="all universal dimensions sufficiently covered",
            )
        return await self._call_llm(under_covered, person_id, settings)

    def _find_under_covered(
        self, db_pool, person_id: UUID, settings
    ) -> list[UnderCoveredDimension]:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT lower(coalesce(title, '') || ' ' || coalesce(narrative, ''))
                      FROM active_moments
                     WHERE person_id = %s
                    """,
                    (str(person_id),),
                )
                moment_texts = [str(row[0]) for row in cur.fetchall()]
                cur.execute(
                    """
                    SELECT lower(coalesce(name, '') || ' ' || coalesce(description, ''))
                      FROM active_threads
                     WHERE person_id = %s
                    """,
                    (str(person_id),),
                )
                thread_texts = [str(row[0]) for row in cur.fetchall()]

        all_texts = moment_texts + thread_texts
        under_covered: list[UnderCoveredDimension] = []
        for dimension in UNIVERSAL_DIMENSIONS:
            keywords = UNIVERSAL_DIMENSION_KEYWORDS[dimension]
            count = sum(
                1
                for text in all_texts
                if any(keyword in text for keyword in keywords)
            )
            if count < settings.p5_dimension_coverage_threshold:
                under_covered.append(
                    UnderCoveredDimension(name=dimension, coverage_count=count)
                )

        under_covered.sort(key=lambda item: item.coverage_count)
        return under_covered[: settings.p5_max_dimensions_per_run]

    async def _call_llm(
        self,
        under_covered: list[UnderCoveredDimension],
        person_id: UUID,
        settings,
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
            system_prompt=P5_SYSTEM_PROMPT,
            user_message=_build_user_message(
                under_covered=under_covered,
                questions_per_dimension=settings.p5_questions_per_dimension,
            ),
            tool=P5_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
        allowed = {dim.name for dim in under_covered}
        questions: list[GeneratedQuestion] = []
        for item in args.get("questions", []) or []:
            dimension = str(item["dimension"])
            if dimension not in allowed:
                continue
            questions.append(
                GeneratedQuestion(
                    text=item["text"],
                    themes=item["themes"],
                    attributes={"dimension": dimension},
                )
            )
        return ProducerResult(
            person_id=person_id,
            source_tag=self.source_tag,
            questions=questions,
            overall_reasoning=str(args.get("overall_reasoning", "")),
        )


def _build_user_message(
    *,
    under_covered: list[UnderCoveredDimension],
    questions_per_dimension: int,
) -> str:
    lines = [
        f"<questions_per_dimension>{questions_per_dimension}</questions_per_dimension>",
        "<under_covered_dimensions>",
    ]
    for dimension in under_covered:
        lines.append(
            f"<dimension coverage_count='{dimension.coverage_count}'>"
            f"{dimension.name}</dimension>"
        )
    lines.append("</under_covered_dimensions>")
    return "\n".join(lines)

