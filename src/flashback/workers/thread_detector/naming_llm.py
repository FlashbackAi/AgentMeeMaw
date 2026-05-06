"""LLM wrapper for naming a freshly-detected thread cluster (Sonnet).

Mirrors the extraction worker's :mod:`extraction_llm` shape:

* sync surface backed by ``asyncio.run`` around the async
  :func:`call_with_tool`;
* validates output via :class:`NamingResult`.

If ``coherent`` is ``false`` the persistence layer rolls the cluster's
transaction back without writing a thread or any P4 questions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable

import structlog

from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text

from .prompts import NAMING_SYSTEM_PROMPT, NAMING_TOOL
from .schema import ClusterableMoment, NamingResult

log = structlog.get_logger("flashback.workers.thread_detector.naming_llm")
THREAD_NAMING_PROMPT_VERSION = "thread_naming.v1"


@dataclass
class NamingLLMConfig:
    provider: str
    model: str
    timeout: float
    max_tokens: int


def name_cluster(
    *,
    cfg: NamingLLMConfig,
    settings,
    person_name: str,
    member_moments: Iterable[ClusterableMoment],
) -> NamingResult:
    """Run the naming LLM for one cluster. Returns a typed result."""
    user_message = _build_user_message(
        person_name=person_name, member_moments=member_moments
    )
    args = asyncio.run(
        call_with_tool(
            provider=cfg.provider,  # type: ignore[arg-type]
            model=cfg.model,
            system_prompt=NAMING_SYSTEM_PROMPT,
            user_message=user_message,
            tool=NAMING_TOOL,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            settings=settings,
        )
    )
    result = NamingResult.model_validate(args)
    log.info(
        "thread_detector.naming_returned",
        coherent=result.coherent,
        name=result.name,
    )
    return result


def _build_user_message(
    *,
    person_name: str,
    member_moments: Iterable[ClusterableMoment],
) -> str:
    lines: list[str] = [
        f"<subject>{xml_text(person_name)}</subject>",
        "",
        "<cluster>",
    ]
    for m in member_moments:
        lines.append(f"<moment id='{m.id}'>")
        lines.append(f"title: {xml_text(m.title)}")
        lines.append(f"narrative: {xml_text(m.narrative)}")
        lines.append("</moment>")
    lines.append("</cluster>")
    return "\n".join(lines)
