"""Small-LLM check: is a chat reply the tribute message itself?

Runs at most ONCE per invitation (the turn right after the card is
offered — see maybe_capture_typed_message). Deliberately conservative:
a message reads as words spoken TO the subject or a heartfelt declaration
about them; questions, stories, deflections, and conversation
continuations are not captures. Any doubt -> False.
"""

from __future__ import annotations

from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

_TOOL = ToolSpec(
    name="classify_reply",
    description="Decide whether the reply IS the tribute message. Once.",
    input_schema={
        "type": "object",
        "properties": {"is_message": {"type": "boolean"}},
        "required": ["is_message"],
        "additionalProperties": False,
    },
)

_SYSTEM = """\
A contributor was just shown a card inviting them to say one thing straight
to a loved one (the tribute message). Instead of using the card, they typed
a chat reply. Decide: is the reply ITSELF that message?

is_message=true ONLY when the reply reads as the message — words addressed
to the person ("I never said it, but you were my hero") or a complete
heartfelt declaration meant for them. is_message=false for everything
else: questions, stories or memories ABOUT them, reactions ("that's a good
question"), deflections ("maybe later"), instructions, or fragments. When
unsure, false. Call classify_reply once."""


async def is_direct_message(
    settings, *, invitation_copy: str, user_reply: str
) -> bool:
    """True only on a confident is_message verdict; raises on LLM failure."""
    args = await call_with_tool(
        provider=settings.llm_small_provider,
        model=settings.llm_small_model,
        system_prompt=_SYSTEM,
        user_message=(
            f"<invitation>{xml_text(invitation_copy)}</invitation>\n"
            f"<reply>{xml_text(user_reply)}</reply>"
        ),
        tool=_TOOL,
        max_tokens=200,
        timeout=6.0,
        settings=settings,
        feature="tribute_message_capture",
    )
    return bool(isinstance(args, dict) and args.get("is_message") is True)
