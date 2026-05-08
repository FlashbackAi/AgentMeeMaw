"""LLM wrapper for P4 ``thread_deepen`` question generation (Sonnet).

Per ARCHITECTURE.md §3.16, P4 runs inline at the end of the Thread
Detector — once per *affected* thread (new OR existing) — and produces
1–2 questions whose ``attributes.themes`` feed the ranker. CLAUDE.md §4
invariant #9 requires every emitted question carry ``themes``; the
:class:`P4Result` Pydantic model enforces that with a min-length-1
constraint on each question.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable

import structlog

from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text

from .prompts import P4_SYSTEM_PROMPT, P4_TOOL
from .schema import ClusterableMoment, P4Result, ThreadSnapshot

log = structlog.get_logger("flashback.workers.thread_detector.p4_llm")
P4_PROMPT_VERSION = "thread_p4.v1"


@dataclass
class P4LLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int


def propose_thread_deepen_questions(
    *,
    cfg: P4LLMConfig,
    settings,
    person_name: str,
    thread: ThreadSnapshot,
    member_moments: Iterable[ClusterableMoment],
    contributor_display_name: str = "",
) -> P4Result:
    """Run the P4 LLM for one thread. Returns a typed result."""
    user_message = _build_user_message(
        person_name=person_name,
        thread=thread,
        member_moments=member_moments,
        contributor_display_name=contributor_display_name,
    )
    args = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=P4_SYSTEM_PROMPT,
            user_message=user_message,
            tool=P4_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )
    result = P4Result.model_validate(args)
    log.info(
        "thread_detector.p4_returned",
        thread_id=thread.id,
        question_count=len(result.questions),
    )
    return result


def _build_user_message(
    *,
    person_name: str,
    thread: ThreadSnapshot,
    member_moments: Iterable[ClusterableMoment],
    contributor_display_name: str = "",
) -> str:
    lines: list[str] = [
        f"<subject>{xml_text(person_name)}</subject>",
        f"<contributor_display_name>{xml_text(contributor_display_name or '')}"
        f"</contributor_display_name>",
        "",
        "<thread>",
        f"name: {xml_text(thread.name)}",
        f"description: {xml_text(thread.description)}",
        "</thread>",
        "",
        "<member_moments>",
    ]
    for m in member_moments:
        lines.append(f"<moment id='{m.id}'>")
        lines.append(f"title: {xml_text(m.title)}")
        lines.append(f"narrative: {xml_text(m.narrative)}")
        lines.append("</moment>")
    lines.append("</member_moments>")
    return "\n".join(lines)
