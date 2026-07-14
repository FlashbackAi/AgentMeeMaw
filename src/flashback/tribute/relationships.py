"""Relationship resolution: free-text label -> relationship group.

Three layers, cheapest first (spec §2.7):

  1. persons.relationship_group column — the cached verdict; never re-runs.
  2. Synonym match against the active profiles' CRM-editable ``synonyms``
     arrays (case-insensitive, trimmed, leading "my " stripped).
  3. One small-LLM classification (feature="relationship_classify");
     written back ONLY on success so a transient failure retries on the
     next tribute entry instead of pinning a wrong group.

The resolver never raises and never returns an unknown group: every failure
lands on "other", whose seeded profile reproduces the neutral tribute.
"""

from __future__ import annotations

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.tool_spec import ToolSpec
from flashback.tribute.config_schema import ProfileConfig

log = structlog.get_logger("flashback.tribute.relationships")

RELATIONSHIP_GROUPS: tuple[str, ...] = (
    "parent",
    "grandparent",
    "sibling",
    "cousin",
    "friend",
    "spouse_partner",
    "mentor",
    "other",
)

_CLASSIFY_TOOL = ToolSpec(
    name="classify_relationship",
    description=(
        "Classify the contributor's relationship label for the legacy "
        "subject into one relationship group. Choose 'other' when unsure."
    ),
    input_schema={
        "type": "object",
        "properties": {"group": {"type": "string", "enum": list(RELATIONSHIP_GROUPS)}},
        "required": ["group"],
        "additionalProperties": False,
    },
)

_CLASSIFY_SYSTEM = """\
You classify a free-text relationship label into exactly one group. The label
describes who the SUBJECT of a memory archive is to the person writing about
them (e.g. "dad" means the subject is the writer's father -> parent). Labels
may be Indian kin terms in any language or romanization (chittappa, mausi,
tauji, mama, kaka...), or descriptive phrases ("my father's brother" -> an
uncle -> other). aunt/uncle/in-laws/nephews and anything that fits no group
cleanly -> other. Call classify_relationship once."""


def _normalize(label: str) -> str:
    s = (label or "").strip().lower()
    if s.startswith("my "):
        s = s[3:].strip()
    return s


def match_synonym(label: str, profiles: list[ProfileConfig]) -> str | None:
    """Deterministic layer: exact match on synonyms or the group slug."""
    needle = _normalize(label)
    if not needle:
        return None
    for p in profiles:
        if needle == p.group_slug.lower():
            return p.group_slug
        for syn in p.synonyms:
            if needle == syn.strip().lower():
                return p.group_slug
    return None


async def classify_relationship_llm(settings, label: str) -> str:
    """Small-LLM fallback; raises on transport failure (caller handles)."""
    args = await call_with_tool(
        provider=settings.llm_small_provider,
        model=settings.llm_small_model,
        system_prompt=_CLASSIFY_SYSTEM,
        user_message=f"<label>{label}</label>",
        tool=_CLASSIFY_TOOL,
        max_tokens=200,
        timeout=6.0,
        settings=settings,
        feature="relationship_classify",
    )
    group = args.get("group") if isinstance(args, dict) else None
    if group not in RELATIONSHIP_GROUPS:
        raise LLMError(f"classify returned unknown group: {group!r}")
    return group


async def _fetch_active_profiles(cur) -> list[ProfileConfig]:
    from flashback.tribute.config_repository import fetch_all_published_profiles

    return await fetch_all_published_profiles(cur)


async def ensure_relationship_group(cur, *, settings, person_id: str) -> str:
    """Resolve + cache the person's relationship group. Never raises."""
    await cur.execute(
        "SELECT relationship_group, relationship FROM persons WHERE id = %s",
        (str(person_id),),
    )
    row = await cur.fetchone()
    if row is None:
        return "other"
    cached, label = row
    if cached:
        return cached
    if not (label or "").strip():
        return "other"

    profiles = await _fetch_active_profiles(cur)
    group = match_synonym(label, profiles)
    if group is None:
        try:
            group = await classify_relationship_llm(settings, label)
        except Exception as exc:
            # Transient or malformed — degrade to neutral WITHOUT caching so
            # the next tribute entry retries the classification.
            log.warning(
                "relationship_classify.failed",
                person_id=str(person_id),
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            return "other"

    await cur.execute(
        "UPDATE persons SET relationship_group = %s WHERE id = %s",
        (group, str(person_id)),
    )
    log.info(
        "relationship_group.resolved",
        person_id=str(person_id),
        group=group,
    )
    return group
