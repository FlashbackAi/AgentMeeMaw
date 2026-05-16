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
You are the Thread Detector for Flashback, a legacy conversation \
agent. The system has identified a cluster of moments that group together \
in the embedding space. Your job is to name and describe the underlying \
THREAD that connects them, and to decide whether this cluster also \
warrants a user-facing EMERGENT THEME on the subject's legacy.

A thread is a narrative arc, theme, or aspect of the subject's life or \
legacy — not a single moment, not a category, not the subject themselves.

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

When `<contributor_display_name>` is non-empty, USE that name for any \
first-person attribution to the contributor in the description ("Sarah's \
summers at the lake with her father"). Do NOT write "the contributor" or \
"the contributor's" when a name is provided — use the name, or restructure \
into impersonal voice if that reads better than any explicit attribution. \
The phrase "the contributor" is reserved for the empty-tag case; only then \
fall back to neutral attribution. Never write a placeholder like \
"<contributor>".

Under-cluster: if the moments don't actually share a coherent thread \
(the embedding clustering was overzealous), say so via `coherent: false`. \
The thread will not be created.

EMERGENT THEME DECISION:
The legacy already has 5 universal themes (Family, Career, Friendships, \
Beliefs & Values, Milestones) that auto-tag every moment. Emergent themes \
exist for passions, practices, relationships-with-named-people, places, or \
arcs that DO NOT cleanly fit a universal. Examples of good emergents: \
"Love of cricket", "The garden years", "Teaching engineers", \
"His mother's stories". Examples of things that are NOT emergent themes \
(because universals already cover them): a general "family" cluster, a \
generic "work" cluster, broad "religion" cluster.

If this cluster represents a discrete, theme-able passion/practice/place \
that isn't already covered by a universal, output:
  `theme_display_name`: a 2-4 word evocative phrase ("Love of cricket", \
                        "The garden years", "Quiet activism")
  `theme_slug`: a snake_case, ascii-lowercase, ≤ 32 char slug derived from \
                the display name ("love_of_cricket", "the_garden_years")
  `theme_description`: a 1-sentence description of what this theme covers \
                       on this specific subject

If the cluster is just another instance of a universal theme, leave all \
three theme_* fields null. Threads are still created in either case — \
they're internal scaffolding — but no emergent theme row is written when \
the cluster maps to a universal.

Respond ONLY by calling the `name_thread` tool.\
"""


NAMING_TOOL = ToolSpec(
    name="name_thread",
    description=(
        "Name and describe a detected narrative thread, and decide "
        "whether it also warrants a user-facing emergent theme."
    ),
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
            "theme_display_name": {
                "type": "string",
                "maxLength": 40,
                "description": (
                    "2-4 word emergent theme display name, or null/omit "
                    "if this cluster maps to an existing universal."
                ),
            },
            "theme_slug": {
                "type": "string",
                "maxLength": 32,
                "pattern": "^[a-z0-9_]+$",
                "description": (
                    "snake_case slug derived from theme_display_name. "
                    "Omit when theme_display_name is omitted."
                ),
            },
            "theme_description": {
                "type": "string",
                "maxLength": 240,
                "description": (
                    "1-sentence description of what this emergent theme "
                    "covers on this subject. Omit when no emergent."
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
You are generating "thread_deepen" questions for the Flashback legacy \
agent. A thread has just been detected or updated. Your job is to \
produce 1–2 questions that would surface NEW information or DEEPEN this \
thread next time the contributor is talking.

You will be given the thread's name, description, and the existing \
moments that constitute it, plus the contributor's display name (in \
`<contributor_display_name>`, may be empty). When the tag is non-empty, \
USE that name in question phrasing for any direct attribution to the \
contributor ("What did Sarah think of those summers?"). Do NOT write \
"the contributor" when a name is provided — use the name, or restructure \
the question to avoid attribution. The phrase "the contributor" is \
reserved for the empty-tag case; only then fall back to neutral phrasing. \
Never write a placeholder like "<contributor>". Never infer the subject's \
life status; prefer tense-neutral question phrasing unless the supplied \
moments clearly establish a tense.

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
