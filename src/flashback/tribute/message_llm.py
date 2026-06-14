"""Polish a contributor's raw message into the tribute's message_text.

Spec choice (2026-06-14): LLM-polished from the user's own words --
tighter and more lyrical while keeping their specifics. Best-effort:
on any failure, return the cleaned raw text so the slot still fills.
"""

from __future__ import annotations

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

log = structlog.get_logger("flashback.tribute.message_llm")


_MESSAGE_SYSTEM = """\
You polish a contributor's spoken message to a loved one into a short,
heartfelt written message for a shareable tribute.

Rules:
- Keep THEIR specifics -- names of feelings, concrete details, the exact
  thing they wanted to say. Never invent new facts, memories, or names.
- Tighten and warm the phrasing. Fix stumbles and filler. Keep it in
  the contributor's own first-person voice ("I", "you").
- 1-4 sentences. No greeting/sign-off scaffolding ("Dear ...",
  "Love, ..."). Just the message.
- Never address the reader or narrate -- output only the message itself.

Call the `polish_message` tool exactly once.
"""

_MESSAGE_TOOL = ToolSpec(
    name="polish_message",
    description="Return the polished message. Call exactly once.",
    input_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string", "minLength": 1, "maxLength": 600},
        },
        "required": ["message"],
        "additionalProperties": False,
    },
)


async def polish_message(
    *,
    settings,
    raw_text: str,
    person_name: str,
    person_relationship: str | None = None,
) -> str:
    """Return a polished message; falls back to the cleaned raw text."""
    cleaned = (raw_text or "").strip()
    if settings is None or not cleaned:
        return cleaned

    rel_attr = (
        f' relationship="{xml_text(person_relationship)}"'
        if person_relationship
        else ""
    )
    user_block = (
        f"<subject{rel_attr}>{xml_text(person_name)}</subject>\n"
        f"<raw_message>{xml_text(cleaned)}</raw_message>"
    )

    try:
        args = await call_with_tool(
            provider=settings.llm_small_provider,
            model=settings.llm_intent_model,
            system_prompt=_MESSAGE_SYSTEM,
            user_message=user_block,
            tool=_MESSAGE_TOOL,
            max_tokens=300,
            timeout=12.0,
            settings=settings,
        )
    except LLMError as exc:
        log.warning("message_polish.llm_failed", error=str(exc))
        return cleaned
    except Exception as exc:  # defensive -- never lose the user's words
        log.warning(
            "message_polish.unexpected_failure",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return cleaned

    polished = args.get("message") if isinstance(args, dict) else None
    if isinstance(polished, str) and polished.strip():
        return polished.strip()
    return cleaned
