"""Split the qualifying moment pool across the grid collections.

One global Sonnet pass assigns each moment to AT MOST ONE grid collection --
its single best fit -- so the books never share a scene when read side by
side (the spike's #1 "same story everywhere" fix). The chapter collection
(wisdom) is deliberately absent: it reads the WHOLE pool through a lens.

The LLM is asked for single-assignment, and ``dedupe_assignments`` enforces it
in code as a backstop: a moment that slips into two lists is kept where it
ranks highest (earliest position) and dropped elsewhere.
"""

from __future__ import annotations

from typing import Any

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec
from flashback.storybook.collections import COLLECTIONS, CURATED_SLUGS

log = structlog.get_logger("flashback.storybook.curation")

_CURATE_TOOL = ToolSpec(
    name="curate",
    description="Assign memories to the storybook collections they best fit.",
    input_schema={
        "type": "object",
        "properties": {
            "collections": {
                "type": "object",
                "properties": {
                    slug: {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            f"Memory indices for '{slug}', best fit first."
                        ),
                    }
                    for slug in CURATED_SLUGS
                },
                "required": list(CURATED_SLUGS),
                "additionalProperties": False,
            }
        },
        "required": ["collections"],
        "additionalProperties": False,
    },
)


def dedupe_assignments(raw: dict[str, list[int]]) -> dict[str, list[int]]:
    """Enforce at-most-one collection per moment (best rank wins)."""
    best: dict[int, tuple[int, str]] = {}  # moment_idx -> (rank, slug)
    for slug, idxs in raw.items():
        for rank, i in enumerate(idxs):
            if i not in best or rank < best[i][0]:
                best[i] = (rank, slug)
    return {
        slug: [i for i in idxs if best.get(i, (0, None))[1] == slug]
        for slug, idxs in raw.items()
    }


def _sys_prompt(subject_name: str, relationship: str | None) -> str:
    rel = f" ({relationship})" if relationship else ""
    char = "\n".join(
        f"  - {slug}: {COLLECTIONS[slug].theme_focus}" for slug in CURATED_SLUGS
    )
    return (
        f"You are curating memories of {xml_text(subject_name)}{xml_text(rel)} "
        f"into themed storybook collections, each a separate gift book that "
        f"families will read side by side. The collections:\n{char}\n\n"
        f"Assign each memory to AT MOST ONE collection -- its single best fit. "
        f"Rules:\n"
        f"1. NO memory may appear in two collections. The books are read "
        f"together; a story repeated across books is the #1 thing that makes "
        f"them feel the same. When a memory could fit two, pick the ONE where "
        f"it fits best and leave it out of the other.\n"
        f"2. Spread the vivid 'anchor' memories across DIFFERENT books -- do "
        f"not let the few dramatic stories cluster; give each to a different "
        f"collection so no two books share a centrepiece.\n"
        f"3. Aim for 6-11 memories per collection where material supports it; "
        f"return fewer for a genuinely thin collection -- a short distinct "
        f"book beats a padded repetitive one. Do NOT pad with weak fits.\n"
        f"4. Order each list best-fit / most-vivid first.\n"
        f"5. A memory may fit none -- that is fine, leave it out.\n"
        f"Call `curate` once."
    )


async def curate_moments(
    *,
    settings: Any,
    subject_name: str,
    relationship: str | None,
    moments: list[dict[str, Any]],
) -> dict[str, list[int]]:
    """One Sonnet pass -> {grid slug: [moment index, ...]}, single-assignment.

    Raises LLMError upward (the worker's redrive handles transient failures --
    a mis-curated book is worse than a retried render).
    """
    blocks = "\n".join(
        f'<m i="{i}"><t>{xml_text(m.get("title") or "")}</t>'
        f'<n>{xml_text((m.get("narrative") or "")[:300])}</n></m>'
        for i, m in enumerate(moments)
    )
    args = await call_with_tool(
        provider=settings.llm_big_provider,
        model=settings.llm_big_model,
        system_prompt=_sys_prompt(subject_name, relationship),
        user_message=f"<memories>\n{blocks}\n</memories>",
        tool=_CURATE_TOOL,
        max_tokens=4000,
        timeout=60.0,
        settings=settings,
    )
    raw = args.get("collections")
    if not isinstance(raw, dict):
        raise LLMError("curate tool returned no collections mapping")
    cleaned: dict[str, list[int]] = {}
    for slug in CURATED_SLUGS:
        idxs = raw.get(slug) or []
        seen: set[int] = set()
        keep: list[int] = []
        for i in idxs:
            if isinstance(i, int) and 0 <= i < len(moments) and i not in seen:
                keep.append(i)
                seen.add(i)
        cleaned[slug] = keep
    deduped = dedupe_assignments(cleaned)
    log.info(
        "storybook.curated",
        sizes={s: len(v) for s, v in deduped.items()},
        pool=len(moments),
    )
    return deduped
