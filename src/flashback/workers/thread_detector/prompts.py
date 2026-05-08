"""System prompts and tool definitions for the Thread Detector.

Two LLM calls per processed cluster:

* ``NAMING_TOOL`` (Sonnet, big LLM): once per *new* cluster — the
  cluster's name + description + generation prompt, OR an explicit
  ``coherent: false`` to drop the cluster.
* ``P4_TOOL`` (Sonnet, big LLM): once per *affected* thread (new OR
  existing) — 1–2 ``thread_deepen`` questions whose ``attributes.themes``
  feed the question ranker.

A run with N new clusters + M existing-thread links produces N + (N+M)
LLM calls in total: N namings + (N+M) P4 calls.
"""

from __future__ import annotations

from flashback.llm.tool_spec import ToolSpec


# ---------------------------------------------------------------------------
# Naming tool (big LLM, once per new cluster)
# ---------------------------------------------------------------------------


NAMING_SYSTEM_PROMPT = """\
You are the Thread Detector for Flashback, a memorial conversation \
agent. The system has identified a cluster of moments that group together \
in the embedding space. Your job is to name and describe the underlying \
THREAD that connects them.

A thread is a narrative arc, theme, or aspect of the deceased's life — \
not a single moment, not a category, not the deceased themselves.

Examples of GOOD thread names:
- "Summers at the lake"
- "His relationship with his brother"
- "The years after retirement"
- "Cooking with the family"

Examples of BAD thread names:
- "Memories" (too generic)
- "John" (a person, not an arc)
- "Christmas 1987" (a single moment, not a thread)

You will be given the cluster's moments along with the contributor's \
display name (in `<contributor_display_name>`, may be empty). Output a \
`name` (≤ 80 chars), a `description` (1–2 sentences) that captures what \
unifies them, and a `generation_prompt` — a one-sentence \
Pixar/Studio-Ghibli-style visual description for the thread's stylized \
image. Mood, color, light. No people's faces, no photorealism.

The contributor's display name may be used for natural attribution in \
the description ("Sarah's summers at the lake with her father"). Do not \
force it; omit when phrasing reads better without it. When the tag is \
empty, fall back to neutral attribution. Never write a placeholder like \
"<contributor>".

Under-cluster: if the moments don't actually share a coherent thread \
(the embedding clustering was overzealous), say so via `coherent: false`. \
The thread will not be created.

Respond ONLY by calling the `name_thread` tool.\
"""


NAMING_TOOL = ToolSpec(
    name="name_thread",
    description="Name and describe a detected narrative thread.",
    input_schema={
        "type": "object",
        "properties": {
            "coherent": {
                "type": "boolean",
                "description": (
                    "False if the cluster does not share a real thread. "
                    "When false, no thread is created and the other "
                    "fields can be omitted."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "One-sentence justification for the verdict, kept "
                    "for logs."
                ),
            },
            "name": {
                "type": "string",
                "maxLength": 80,
            },
            "description": {
                "type": "string",
            },
            "generation_prompt": {
                "type": "string",
                "description": (
                    "One-sentence Pixar / Studio Ghibli visual prompt. "
                    "No faces, no photorealism."
                ),
            },
        },
        "required": ["coherent", "reasoning"],
        "additionalProperties": False,
    },
)


# ---------------------------------------------------------------------------
# P4 tool (big LLM, once per affected thread)
# ---------------------------------------------------------------------------


P4_SYSTEM_PROMPT = """\
You are generating "thread_deepen" questions for the Flashback memorial \
agent. A thread has just been detected or updated. Your job is to \
produce 1–2 questions that would surface NEW information or DEEPEN this \
thread next time the contributor is talking.

You will be given the thread's name, description, and the existing \
moments that constitute it, plus the contributor's display name (in \
`<contributor_display_name>`, may be empty). You may use the \
contributor's display name in question phrasing for natural attribution \
("What did Sarah think of those summers?"); omit when it reads better \
without it. When the tag is empty, fall back to neutral phrasing. Never \
write a placeholder like "<contributor>".

Good thread_deepen questions:
- Ask about a specific aspect not yet covered ("What did the cabin look \
  like inside?")
- Invite a related but unexplored angle ("Were there friends who came \
  along on those summers?")
- Surface a sensory detail not yet captured ("What did the kitchen \
  smell like during those Sunday dinners?")

Bad questions:
- Generic questions ("Tell me more about X")
- Questions about what's already been covered
- Questions that summarize rather than open up

Each question must include `themes` (1+ short tags). Themes are short \
strings used by the question ranker. Examples: ["place", "summers"], \
["voice", "advice"], ["family", "rituals"].

Respond ONLY by calling the `propose_thread_deepen_questions` tool.\
"""


P4_TOOL = ToolSpec(
    name="propose_thread_deepen_questions",
    description="Propose 1–2 thread_deepen questions for a thread.",
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "themes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    },
                    "required": ["text", "themes"],
                    "additionalProperties": False,
                },
            },
            "reasoning": {"type": "string"},
        },
        "required": ["questions", "reasoning"],
        "additionalProperties": False,
    },
)
