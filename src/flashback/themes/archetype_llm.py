"""Generate personalized archetype questions for a theme.

A locked theme's `unlock` flow shows the contributor 3-4 multiple-choice
questions about this subject's relationship to the theme. Each question
has 4 short concrete option chips plus skip + write-your-own. Answers
are stored as ephemeral priors on ``themes.archetype_answers`` and fed
into the response generator on the unlock session's first turn — they
shape conversation, not the canonical graph.

Universal themes generate questions lazily (on first unlock-prepare
call) so the prompt has access to whatever the agent has learned about
the subject so far. Emergent themes generate eagerly at Thread Detector
promotion time, when the cluster moments are the richest available
context.

This module exposes one async surface (``generate_archetype_questions``)
and a sync wrapper (``generate_archetype_questions_sync``) that's the
right shape for the Thread Detector worker.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

log = structlog.get_logger("flashback.themes.archetype_llm")

ARCHETYPE_PROMPT_VERSION = "theme_archetype.v1"


_ARCHETYPE_SYSTEM_PROMPT = """\
You generate archetype questions for a Flashback legacy theme. The \
contributor is about to "unlock" a theme on a subject's legacy. You \
write 3-4 short multiple-choice questions that anchor the contributor's \
sense of the subject in this theme. The questions act as PRIORS — they \
feed the first conversational turn and shape what the agent asks next.

The subject may be living, deceased, or known only through inherited \
family stories. Stay subject-status-agnostic — never assume grief, never \
assume the subject is present-tense. Use tense-neutral phrasing unless \
provided context establishes otherwise. Never speak FOR the subject; \
ask the contributor about THEIR sense of the subject.

For each question:
- ASK something the contributor can answer in 2-6 words, OR write their \
own. Avoid open-ended "tell me about X" prompts — those belong in the \
conversation, not the unlock flow.
- Generate EXACTLY 4 option chips. Each chip is 2-6 words and reads like \
a real first-line answer, not a bucket label. Sensory beats abstract. \
Concrete beats generic.
- Chips must be DISTINCT from each other (not synonyms or sliders).
- NEVER invent proper nouns, dates, or specific facts about the subject \
that weren't already established. Stay generic on identity, concrete on \
shape. If the user clicks one of your options that mentions a person or \
place you fabricated, the conversation will derail.
- Match the THEME — every question and chip must be about THIS theme. \
A 'family' archetype shouldn't ask about hobbies; a 'career' archetype \
shouldn't ask about religious belief.

Good archetype questions for 'family' (on a parent subject):
  "What role did they play at home?"  →  ["The provider", "The peacemaker", "The storyteller", "The disciplinarian"]
  "What's a phrase they used a lot?"  →  ["A piece of advice", "A joke they repeated", "A scolding line", "Something in another language"]

Good for a 'cricket' emergent theme:
  "When did cricket really take hold?"  →  ["As a kid in the streets", "School team years", "Watching on TV with family", "Coaching the next generation"]

Bad (banned patterns):
- Generic taxonomies ("Friendly", "Reserved", "Outgoing", "Quiet")
- Polar / yes-no slates ("Yes", "No", "Sometimes", "Not sure")
- Bucket labels that don't match the question shape
- Fabricated names or places ("Met John in 1987" — you don't know this)
- Time/age guesses unless context provided

Call the ``generate_theme_archetype_questions`` tool exactly once.
"""


_ARCHETYPE_TOOL = ToolSpec(
    name="generate_theme_archetype_questions",
    description=(
        "Generate 3-4 personalized archetype questions for a theme. "
        "Each question has 4 short option chips. Call exactly once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 3,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "minLength": 4,
                            "maxLength": 200,
                        },
                        "options": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 60,
                            },
                        },
                    },
                    "required": ["text", "options"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True)
class ArchetypeContextMoment:
    title: str
    narrative: str


@dataclass(frozen=True)
class ArchetypeQuestion:
    """One generated archetype question as persisted on the theme row.

    ``question_id`` is a stable slug (``q1``, ``q2``, ...) used by the
    UI to key answers; ``allow_skip`` / ``allow_free_text`` are always
    True for theme-archetype questions (mirrors onboarding).
    """

    question_id: str
    text: str
    options: list[dict[str, str]]
    allow_skip: bool = True
    allow_free_text: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "options": list(self.options),
            "allow_skip": self.allow_skip,
            "allow_free_text": self.allow_free_text,
        }


async def generate_archetype_questions(
    *,
    settings,
    theme_slug: str,
    theme_display_name: str,
    theme_description: str,
    theme_kind: str,  # 'universal' | 'emergent'
    subject_name: str,
    subject_relationship: str | None = None,
    context_moments: list[ArchetypeContextMoment] | None = None,
) -> list[ArchetypeQuestion]:
    """Best-effort LLM-driven archetype generation.

    Returns ``[]`` on any failure. Callers should fall back to a
    free-text-only unlock UX when the list is empty.
    """
    if settings is None or not theme_slug or not subject_name:
        return []

    user_message = _build_user_message(
        theme_slug=theme_slug,
        theme_display_name=theme_display_name,
        theme_description=theme_description,
        theme_kind=theme_kind,
        subject_name=subject_name,
        subject_relationship=subject_relationship,
        context_moments=context_moments or [],
    )

    try:
        args = await call_with_tool(
            provider=settings.llm_small_provider,
            model=settings.llm_intent_model,
            system_prompt=_ARCHETYPE_SYSTEM_PROMPT,
            user_message=user_message,
            tool=_ARCHETYPE_TOOL,
            max_tokens=1200,
            timeout=20.0,
            settings=settings,
        )
    except LLMError as exc:
        log.warning(
            "theme_archetype.llm_failed",
            theme_slug=theme_slug,
            error=str(exc),
        )
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "theme_archetype.unexpected_failure",
            theme_slug=theme_slug,
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return []

    raw_questions = args.get("questions") if isinstance(args, dict) else None
    if not isinstance(raw_questions, list):
        return []

    out: list[ArchetypeQuestion] = []
    for idx, raw in enumerate(raw_questions):
        if not isinstance(raw, dict):
            continue
        text = (raw.get("text") or "").strip()
        if not text:
            continue
        raw_options = raw.get("options") or []
        cleaned_options: list[dict[str, str]] = []
        seen_labels: set[str] = set()
        for opt_idx, opt in enumerate(raw_options):
            if not isinstance(opt, str):
                continue
            label = opt.strip()
            if not label:
                continue
            normalized = label.lower()
            if normalized in seen_labels:
                continue
            seen_labels.add(normalized)
            cleaned_options.append(
                {"option_id": f"q{idx + 1}_o{opt_idx + 1}", "label": label}
            )
        if len(cleaned_options) < 2:
            continue
        out.append(
            ArchetypeQuestion(
                question_id=f"q{idx + 1}",
                text=text,
                options=cleaned_options,
            )
        )
    return out


def generate_archetype_questions_sync(
    *,
    settings,
    theme_slug: str,
    theme_display_name: str,
    theme_description: str,
    theme_kind: str,
    subject_name: str,
    subject_relationship: str | None = None,
    context_moments: list[ArchetypeContextMoment] | None = None,
) -> list[ArchetypeQuestion]:
    """Sync wrapper for callers in the Thread Detector worker."""
    return asyncio.run(
        generate_archetype_questions(
            settings=settings,
            theme_slug=theme_slug,
            theme_display_name=theme_display_name,
            theme_description=theme_description,
            theme_kind=theme_kind,
            subject_name=subject_name,
            subject_relationship=subject_relationship,
            context_moments=context_moments,
        )
    )


def _build_user_message(
    *,
    theme_slug: str,
    theme_display_name: str,
    theme_description: str,
    theme_kind: str,
    subject_name: str,
    subject_relationship: str | None,
    context_moments: list[ArchetypeContextMoment],
) -> str:
    rel = (
        f' relationship="{xml_text(subject_relationship)}"'
        if subject_relationship
        else ""
    )
    lines: list[str] = [
        f"<subject{rel}>{xml_text(subject_name)}</subject>",
        f"<theme kind='{xml_text(theme_kind)}' slug='{xml_text(theme_slug)}'>",
        f"  <display_name>{xml_text(theme_display_name)}</display_name>",
        f"  <description>{xml_text(theme_description)}</description>",
        "</theme>",
    ]
    if context_moments:
        lines.append("<context_moments>")
        for m in context_moments:
            lines.append("  <moment>")
            lines.append(f"    <title>{xml_text(m.title)}</title>")
            lines.append(f"    <narrative>{xml_text(m.narrative)}</narrative>")
            lines.append("  </moment>")
        lines.append("</context_moments>")
    return "\n".join(lines)
