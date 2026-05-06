"""Async wrapper for the per-type edit-LLM call.

Mirrors :mod:`flashback.workers.extraction.extraction_llm` but stays
async (the HTTP route is async). The user message renders the
subject, the prior row's structured fields, and the contributor's
edited free text in XML-tagged blocks.
"""

from __future__ import annotations

from typing import Any, cast

import structlog

from flashback.llm.interface import Provider, call_with_tool
from flashback.llm.prompt_safety import tagged, xml_text

from .registry import NodeEditConfig

log = structlog.get_logger("flashback.node_edits.llm")


async def run_edit_llm(
    *,
    config: NodeEditConfig,
    settings,
    provider: str,
    model: str,
    timeout: float,
    max_tokens: int,
    subject_name: str,
    subject_relationship: str | None,
    prior_row: dict[str, Any],
    edited_text: str,
) -> dict[str, Any]:
    """Run the type-specific edit-LLM call. Returns parsed tool args."""
    user_message = _build_user_message(
        node_type=config.node_type,
        subject_name=subject_name,
        subject_relationship=subject_relationship,
        prior_row=prior_row,
        edited_text=edited_text,
    )
    args = await call_with_tool(
        provider=cast(Provider, provider),
        model=model,
        system_prompt=config.llm_system_prompt,
        user_message=user_message,
        tool=config.llm_tool,
        max_tokens=max_tokens,
        timeout=timeout,
        settings=settings,
    )
    log.info(
        "node_edits.llm_returned",
        node_type=config.node_type,
        prompt_version=config.prompt_version,
    )
    return args


def _build_user_message(
    *,
    node_type: str,
    subject_name: str,
    subject_relationship: str | None,
    prior_row: dict[str, Any],
    edited_text: str,
) -> str:
    rel = (
        f" (the contributor's {subject_relationship})"
        if subject_relationship
        else ""
    )
    lines: list[str] = [
        tagged("subject", f"{subject_name}{rel}"),
        "",
        f"<prior_{node_type}>",
    ]
    for key, value in prior_row.items():
        if value in (None, "", [], {}):
            continue
        lines.append(f"  <{key}>{xml_text(_render(value))}</{key}>")
    lines.append(f"</prior_{node_type}>")
    lines.append("")
    lines.append("<contributor_edited_text>")
    lines.append(xml_text(edited_text))
    lines.append("</contributor_edited_text>")
    return "\n".join(lines)


def _render(value: Any) -> str:
    """Render a prior-row column for prompt embedding."""
    if isinstance(value, dict):
        parts = [f"{k}={v}" for k, v in value.items() if v not in (None, "")]
        return "; ".join(parts)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v not in (None, ""))
    return str(value)
