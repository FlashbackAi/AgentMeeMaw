"""Render structured response-generation context into prompt text."""

from __future__ import annotations

from flashback.response_generator.schema import StarterContext, TurnContext


def render_turn_context(ctx: TurnContext) -> str:
    sections: list[str] = [_render_subject(ctx.person_name, ctx.person_relationship)]

    if ctx.rolling_summary.strip():
        sections.append(_block("rolling_summary", ctx.rolling_summary.strip()))

    if ctx.recent_turns:
        lines = [f"{turn.role}: {turn.content}" for turn in ctx.recent_turns]
        sections.append(_block("recent_turns", "\n".join(lines)))

    sections.append(
        f"<emotional_temperature>{ctx.emotional_temperature}</emotional_temperature>"
    )

    retrieval_sections: list[str] = []
    if ctx.related_moments:
        lines = []
        for moment in ctx.related_moments:
            similarity = ""
            if moment.similarity_score is not None:
                similarity = f"  (similarity: {moment.similarity_score:.2f})"
            lines.append(f"- {moment.title}: {moment.narrative}{similarity}")
        retrieval_sections.append(_block("moments", "\n".join(lines)))

    if ctx.related_entities:
        lines = []
        for entity in ctx.related_entities:
            description = entity.description or ""
            lines.append(f"- {entity.kind} {entity.name}: {description}".rstrip())
        retrieval_sections.append(_block("entities", "\n".join(lines)))

    if ctx.related_threads:
        lines = [
            f"- {thread.name}: {thread.description}"
            for thread in ctx.related_threads
        ]
        retrieval_sections.append(_block("threads", "\n".join(lines)))

    if retrieval_sections:
        sections.append(_block("retrieved_context", "\n".join(retrieval_sections)))

    if ctx.seeded_question_text:
        sections.append(_block("seeded_question", ctx.seeded_question_text))

    return "\n\n".join(sections)


def render_starter_context(ctx: StarterContext) -> str:
    sections = [_render_subject(ctx.person_name, ctx.person_relationship)]
    sections.append(
        "\n".join(
            [
                f'<anchor_question dimension="{ctx.anchor_dimension}">',
                ctx.anchor_question_text,
                "</anchor_question>",
            ]
        )
    )
    if ctx.prior_session_summary and ctx.prior_session_summary.strip():
        sections.append(
            _block("prior_session_summary", ctx.prior_session_summary.strip())
        )
    return "\n\n".join(sections)


def _render_subject(name: str, relationship: str | None) -> str:
    lines = ["<subject>", f"Name: {name}"]
    if relationship:
        lines.append(f"Relationship to contributor: {relationship}")
    lines.append("</subject>")
    return "\n".join(lines)


def _block(name: str, content: str) -> str:
    return "\n".join([f"<{name}>", content, f"</{name}>"])
