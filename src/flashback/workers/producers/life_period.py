"""P3 - life-period gap question producer."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from flashback.llm.interface import call_with_tool
from flashback.workers.profile_summary.time_period import LIFE_PERIOD_ORDER

from .prompts import P3_SYSTEM_PROMPT, P3_TOOL
from .schema import GeneratedQuestion, ProducerLLMConfig, ProducerResult


@dataclass(frozen=True)
class LifePeriodGap:
    kind: str
    label: str


class P3LifePeriodGap:
    name = "P3"
    source_tag = "life_period_gap"

    async def produce(self, db_pool, person_id: UUID, settings) -> ProducerResult:
        gaps = self._find_gaps(db_pool, person_id, settings)
        if not gaps:
            return ProducerResult(
                person_id=person_id,
                source_tag=self.source_tag,
                questions=[],
                overall_reasoning="no life-period gaps detected",
            )
        return await self._call_llm(gaps, person_id, settings)

    def _find_gaps(self, db_pool, person_id: UUID, settings) -> list[LifePeriodGap]:
        """Find missing decades, falling back to life-period labels."""
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT CASE
                             WHEN time_anchor ? 'year'
                              AND time_anchor->>'year' ~ '^[0-9]{3,4}$'
                             THEN (time_anchor->>'year')::int
                             ELSE NULL
                           END AS year,
                           life_period_estimate
                      FROM active_moments
                     WHERE person_id = %s
                    """,
                    (str(person_id),),
                )
                rows = cur.fetchall()
                covered = _life_periods_already_covered(cur, person_id=person_id)

        years = sorted({int(row[0]) for row in rows if row[0] is not None})
        if years:
            min_decade = (years[0] // 10) * 10
            max_decade = (years[-1] // 10) * 10
            represented = {(year // 10) * 10 for year in years}
            gaps = [
                LifePeriodGap(kind="decade", label=f"{decade}s")
                for decade in range(min_decade, max_decade + 10, 10)
                if decade not in represented and f"{decade}s" not in covered
            ]
            return gaps[: settings.p3_max_gaps_per_run]

        present_periods = {
            str(row[1]) for row in rows if row[1] is not None and str(row[1]) != ""
        }
        gaps = [
            LifePeriodGap(kind="life_period", label=period)
            for period in LIFE_PERIOD_ORDER
            if period not in present_periods and period not in covered
        ]
        return gaps[: settings.p3_max_gaps_per_run]

    async def _call_llm(
        self, gaps: list[LifePeriodGap], person_id: UUID, settings
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
            system_prompt=P3_SYSTEM_PROMPT,
            user_message=_build_user_message(
                gaps=gaps,
                questions_per_gap=settings.p3_questions_per_gap,
            ),
            tool=P3_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
        allowed = {gap.label for gap in gaps}
        questions: list[GeneratedQuestion] = []
        for item in args.get("questions", []) or []:
            label = str(item["life_period"])
            if label not in allowed:
                continue
            questions.append(
                GeneratedQuestion(
                    text=item["text"],
                    themes=item["themes"],
                    attributes={"life_period": label},
                )
            )
        return ProducerResult(
            person_id=person_id,
            source_tag=self.source_tag,
            questions=questions,
            overall_reasoning=str(args.get("overall_reasoning", "")),
        )


def _life_periods_already_covered(cur, *, person_id: UUID) -> set[str]:
    """life_period labels that already have an active life_period_gap question.

    A label is "covered" when an active question for it is still
    outstanding (unanswered) OR has been suppressed by the user. Re-minting
    for such a label would either stack a duplicate on top of an open
    question or, worse, escape a user's suppress (which is keyed on the
    old question_id). Mirrors the P2 entity guard and extraction's
    dropped_phrase guard — under-mint rather than pile up (invariant #6).
    """
    cur.execute(
        """
        SELECT DISTINCT q.attributes->>'life_period'
          FROM active_questions q
         WHERE q.person_id = %(pid)s
           AND q.source    = 'life_period_gap'
           AND q.attributes->>'life_period' IS NOT NULL
           AND (
                 NOT EXISTS (
                   SELECT 1 FROM active_edges ae
                    WHERE ae.from_kind = 'question'
                      AND ae.from_id   = q.id
                      AND ae.edge_type = 'answered_by'
                      AND ae.to_kind   = 'moment'
                 )
                 OR EXISTS (
                   SELECT 1 FROM active_question_decisions sd
                    WHERE sd.question_id = q.id
                      AND sd.person_id   = %(pid)s
                      AND sd.action      = 'suppress'
                 )
               )
        """,
        {"pid": str(person_id)},
    )
    return {str(row[0]) for row in cur.fetchall() if row[0] is not None}


def _build_user_message(
    *, gaps: list[LifePeriodGap], questions_per_gap: int
) -> str:
    lines = [
        f"<questions_per_gap>{questions_per_gap}</questions_per_gap>",
        "<life_period_gaps>",
    ]
    for gap in gaps:
        lines.append(f"<gap kind='{gap.kind}'>{gap.label}</gap>")
    lines.append("</life_period_gaps>")
    return "\n".join(lines)

