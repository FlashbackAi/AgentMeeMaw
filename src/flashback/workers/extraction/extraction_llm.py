"""
Extraction LLM wrapper.

The wrapper exists so the worker has a single sync surface for the big
extraction call. The underlying :func:`flashback.llm.interface.call_with_tool`
is async — we run it via ``asyncio.run`` per call, which is fine for a
worker that processes one segment at a time.

Pydantic validation runs immediately after the tool call returns. Tool
arguments that violate the schema raise :class:`pydantic.ValidationError`,
which the worker treats as an extraction failure (no SQS ack).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable

import structlog

from flashback.artifacts.people import figure_noun
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import tagged, xml_text

from .prompts import EXTRACTION_SYSTEM_PROMPT, EXTRACTION_TOOL
from .schema import ExtractionResult, SegmentAnchor, SegmentTurn

log = structlog.get_logger("flashback.workers.extraction.extraction_llm")
EXTRACTION_PROMPT_VERSION = "extraction.v1"


@dataclass
class ExtractionLLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int


@dataclass(frozen=True)
class ThemeCatalogEntry:
    """One row of the theme catalog passed into the extraction prompt."""

    slug: str
    display_name: str
    description: str


@dataclass(frozen=True)
class EntityCatalogEntry:
    """One existing active entity shown to the extraction LLM.

    Prevention layer 2 (2026-06-06 design §5.2): the LLM reuses an existing
    entity's canonical name (adding the new surface form as an alias)
    instead of coining a duplicate. Covers the different-name identity case
    ("Mom" = "Ishita") with conversation context. All kinds are listed.
    """

    name: str
    kind: str
    aliases: tuple[str, ...]
    description: str


def run_extraction(
    *,
    cfg: ExtractionLLMConfig,
    settings,
    subject_name: str,
    subject_relationship: str | None,
    subject_gender: str | None = None,
    contributor_gender: str | None = None,
    prior_rolling_summary: str,
    segment_turns: Iterable[SegmentTurn],
    contributor_display_name: str = "",
    candidate_question_ids: Iterable[str] = (),
    theme_catalog: Iterable[ThemeCatalogEntry] = (),
    entity_catalog: Iterable[EntityCatalogEntry] = (),
    ground_truth_block: str = "",
    segment_anchor: SegmentAnchor | None = None,
) -> ExtractionResult:
    """
    Synchronous entry point. Returns a validated :class:`ExtractionResult`.

    Raises whatever :func:`call_with_tool` raises (LLMTimeout, LLMError,
    LLMMalformedResponse) plus ``pydantic.ValidationError`` on bad shapes.
    """
    user_message = _build_user_message(
        subject_name=subject_name,
        subject_relationship=subject_relationship,
        subject_gender=subject_gender,
        contributor_gender=contributor_gender,
        prior_rolling_summary=prior_rolling_summary,
        segment_turns=segment_turns,
        contributor_display_name=contributor_display_name,
        candidate_question_ids=candidate_question_ids,
        theme_catalog=theme_catalog,
        entity_catalog=entity_catalog,
        ground_truth_block=ground_truth_block,
        segment_anchor=segment_anchor,
    )

    args = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_message=user_message,
            tool=EXTRACTION_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )
    result = ExtractionResult.model_validate(args)
    log.info(
        "extraction.llm_returned",
        moments=len(result.moments),
        entities=len(result.entities),
        traits=len(result.traits),
        dropped_references=len(result.dropped_references),
    )
    return result


def _render_people_in_scenes(
    *,
    subject_name: str,
    subject_relationship: str | None,
    subject_gender: str | None,
    contributor_display_name: str,
    contributor_gender: str | None,
) -> str:
    """Tell the LLM how to depict the people who recur in moment scenes.

    The subject and (when they appear in a memory, e.g. "my father and I on
    a bike") the contributor are rendered with gender-correct figures instead
    of letting the image model default to one. Faces still stay turned/distant
    per the no-faces rule in the system prompt; this only fixes presentation.
    Emits nothing when no gender is known — silence beats a wrong guess.
    """
    rows: list[str] = []
    subject_fig = figure_noun(subject_gender)
    if subject_fig:
        rel = f", the contributor's {subject_relationship}" if subject_relationship else ""
        rows.append(f"- The subject ({subject_name}{rel}) is {subject_fig}.")
    contributor_fig = figure_noun(contributor_gender)
    if contributor_fig:
        who = contributor_display_name or "the contributor"
        rows.append(
            f"- The contributor ({who}, the one telling these stories) is "
            f"{contributor_fig}."
        )
    if not rows:
        return ""
    return (
        "<people_in_scenes>\n"
        "When a generation_prompt depicts human figures, render them with "
        "the correct gender presentation below. Use a matching noun (\"a "
        "man\", \"a woman\", \"a young boy\", \"an elderly woman\") rather "
        "than a neutral \"figure\". Faces stay turned away or distant per "
        "the no-faces rule.\n"
        + "\n".join(rows)
        + "\n</people_in_scenes>"
    )


def _build_user_message(
    *,
    subject_name: str,
    subject_relationship: str | None,
    subject_gender: str | None = None,
    contributor_gender: str | None = None,
    prior_rolling_summary: str,
    segment_turns: Iterable[SegmentTurn],
    contributor_display_name: str = "",
    candidate_question_ids: Iterable[str] = (),
    theme_catalog: Iterable[ThemeCatalogEntry] = (),
    entity_catalog: Iterable[EntityCatalogEntry] = (),
    ground_truth_block: str = "",
    segment_anchor: SegmentAnchor | None = None,
) -> str:
    """
    Render subject / prior summary / segment turns into a single prompt.

    The shape mirrors the segment_detector user-message format so the
    contributor sees the same structure across LLM calls.
    """
    rel = (
        f" (the contributor's {subject_relationship})"
        if subject_relationship
        else ""
    )
    lines: list[str] = [
        tagged("subject", f"{subject_name}{rel}"),
    ]
    contributor_name = (contributor_display_name or "").strip()
    lines.append(tagged("contributor_display_name", contributor_name))

    people_block = _render_people_in_scenes(
        subject_name=subject_name,
        subject_relationship=subject_relationship,
        subject_gender=subject_gender,
        contributor_display_name=contributor_name,
        contributor_gender=contributor_gender,
    )
    if people_block:
        lines.extend(["", people_block])
    candidate_ids = [qid for qid in candidate_question_ids if qid]
    if candidate_ids:
        lines.append(tagged("candidate_answered_question_ids", "\n".join(candidate_ids)))

    catalog = list(theme_catalog)
    if catalog:
        lines.append("")
        lines.append("<theme_catalog>")
        for entry in catalog:
            lines.append(
                f"- slug: {xml_text(entry.slug)} | "
                f"display: {xml_text(entry.display_name)} | "
                f"covers: {xml_text(entry.description)}"
            )
        lines.append("</theme_catalog>")

    entities = list(entity_catalog)
    if entities:
        lines.append("")
        lines.append("<entity_catalog>")
        for ent in entities:
            alias_str = ", ".join(a for a in ent.aliases if a)
            alias_part = f" | aka: {xml_text(alias_str)}" if alias_str else ""
            desc_part = f" | {xml_text(ent.description)}" if ent.description else ""
            lines.append(
                f"- {xml_text(ent.name)} [{xml_text(ent.kind)}]"
                f"{alias_part}{desc_part}"
            )
        lines.append("</entity_catalog>")

    if ground_truth_block.strip():
        lines.extend(
            [
                "",
                "<subject_ground_truth>",
                xml_text(ground_truth_block),
                "</subject_ground_truth>",
            ]
        )

    if segment_anchor is not None and segment_anchor.answer.strip():
        lines.extend(
            [
                "",
                "<segment_time_anchor>",
                f"question: {xml_text(segment_anchor.question_text)}",
                f"answer: {xml_text(segment_anchor.answer)}",
                "</segment_time_anchor>",
            ]
        )

    lines.extend(
        [
            "",
            "<prior_rolling_summary>",
            xml_text(prior_rolling_summary or ""),
            "</prior_rolling_summary>",
            "",
            "<closed_segment>",
        ]
    )
    for turn in segment_turns:
        lines.append(f"{turn.role}: {xml_text(turn.content)}")
    lines.append("</closed_segment>")
    return "\n".join(lines)
