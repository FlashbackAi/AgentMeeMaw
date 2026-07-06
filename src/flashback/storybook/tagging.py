"""Tag moments with the storybook grid collections they fit (design 2026-07-06).

This is the same judgement the Extraction Worker applies inline via the
``<collection_catalog>`` in its prompt, factored out so the one-time backfill
(``scripts/backfill_storybook_collections.py``) can apply it to moments
extracted before the feature existed. Grid slugs only — ``wisdom`` lenses the
whole pool and is never tagged.
"""

from __future__ import annotations

from typing import Any

import structlog

from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec
from flashback.storybook.collections import TAGGABLE_SLUGS, grid_tag_catalog

log = structlog.get_logger("flashback.storybook.tagging")

_TAG_TOOL = ToolSpec(
    name="tag_collections",
    description=(
        "Tag each memory with the storybook collections it genuinely fits."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "moments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "minimum": 0},
                        "collections": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(TAGGABLE_SLUGS),
                            },
                        },
                    },
                    "required": ["index", "collections"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["moments"],
        "additionalProperties": False,
    },
)


def _sys_prompt(subject_name: str, relationship: str | None) -> str:
    rel = f" ({relationship})" if relationship else ""
    catalog = "\n".join(
        f"  - {row['slug']}: {row['tag_description']}"
        for row in grid_tag_catalog()
    )
    return (
        f"You are tagging memories of {xml_text(subject_name)}{xml_text(rel)} "
        f"with the storybook collections each memory genuinely belongs in. "
        f"The collections:\n{catalog}\n\n"
        f"For every memory, return the slugs it truly fits. Rules:\n"
        f"1. ACCURACY MATTERS — these tags gate which keepsake books a family "
        f"can make. Tag a collection only when the memory clearly fits its "
        f"description.\n"
        f"2. Multi-label is expected — a Diwali memory from childhood is BOTH "
        f"'festivals' AND 'childhood'.\n"
        f"3. Do NOT stretch. A quiet dinner is not an 'adventure'; an ordinary "
        f"errand is not an 'interesting' story. Most everyday memories fit "
        f"NONE — return an empty list for them. An empty list is the correct, "
        f"common answer.\n"
        f"4. Return one entry per memory index you were given. Call "
        f"`tag_collections` once."
    )


def _validate(slugs: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in slugs or ():
        s = (s or "").strip() if isinstance(s, str) else ""
        if s in TAGGABLE_SLUGS and s not in seen:
            seen.add(s)
            out.append(s)
    return out


async def tag_moments(
    *,
    settings: Any,
    provider: str,
    model: str,
    subject_name: str,
    relationship: str | None,
    moments: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Tag one batch of moments → ``{moment_id: [valid grid slugs]}``.

    ``moments`` is a list of ``{id, title, narrative}``. ``provider``/``model``
    pick the LLM (the big model, for parity with extraction); ``settings``
    carries the API credentials for ``call_with_tool``. Every input id is
    present in the result (``[]`` when the LLM tagged it with nothing), so the
    caller can write ``'{}'`` and mark the row processed. Raises ``LLMError``
    upward (the caller decides whether to skip the batch or abort)."""
    if not moments:
        return {}
    blocks = "\n".join(
        f'<m i="{i}"><t>{xml_text(m.get("title") or "")}</t>'
        f'<n>{xml_text((m.get("narrative") or "")[:400])}</n></m>'
        for i, m in enumerate(moments)
    )
    args = await call_with_tool(
        provider=provider,
        model=model,
        system_prompt=_sys_prompt(subject_name, relationship),
        user_message=f"<memories>\n{blocks}\n</memories>",
        tool=_TAG_TOOL,
        max_tokens=4000,
        timeout=90.0,
        settings=settings,
    )
    by_index: dict[int, list[str]] = {}
    for row in args.get("moments") or ():
        if not isinstance(row, dict):
            continue
        idx = row.get("index")
        if isinstance(idx, int) and 0 <= idx < len(moments):
            by_index[idx] = _validate(row.get("collections"))
    result = {
        str(m["id"]): by_index.get(i, []) for i, m in enumerate(moments)
    }
    log.info(
        "storybook.tagged_batch",
        batch=len(moments),
        tagged=sum(1 for v in result.values() if v),
    )
    return result
