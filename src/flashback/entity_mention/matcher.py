"""Word-boundary matching of user utterances against cached entity names.

Substring scanning was rejected: a bare ``"pal" in text`` style match
fires on "hospital" and "palace". Regex with explicit word boundaries
keeps matches anchored to whole-word occurrences.

Strategy:
  * Build one alternation pattern per entry from ``name`` + ``aliases``
    (each escaped, joined with ``|``, wrapped in ``\\b...\\b``,
    case-insensitive).
  * Process entries in descending order of longest-name-length, so an
    entry named "Chaitanya Reddy" wins over a colliding "Chaitanya"
    alias on a turn that mentions the full name. Once an entry matches
    a region of the message, that region is masked out for the
    remaining entries.

Output: a list of ``EntityMatch`` (one per entry that hit at least
one span) and a derived ``ambiguous`` flag if two entries collided on
the same matched surface form.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from flashback.entity_mention.cache import EntityNameEntry


@dataclass(frozen=True, slots=True)
class EntityMatch:
    entity_id: UUID
    matched_text: str


def _name_length(entry: EntityNameEntry) -> int:
    longest_alias = max((len(a) for a in entry.aliases), default=0)
    return max(len(entry.name), longest_alias)


def _compile_pattern(entry: EntityNameEntry) -> re.Pattern[str]:
    forms = [entry.name, *entry.aliases]
    escaped = sorted(
        {re.escape(f.strip()) for f in forms if f and f.strip()},
        key=len,
        reverse=True,
    )
    if not escaped:
        return re.compile(r"(?!)")
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


def find_entity_mentions(
    user_message: str,
    entries: list[EntityNameEntry],
) -> tuple[list[EntityMatch], bool]:
    """Return matched entries and whether any surface form was ambiguous.

    ``ambiguous`` is True when at least one matched surface form (e.g.
    "Priya") resolves to two or more distinct entity ids. The caller
    is responsible for nudging the response generator into a
    disambiguating question on the next turn.
    """
    if not user_message or not entries:
        return [], False

    sorted_entries = sorted(entries, key=_name_length, reverse=True)
    matches: list[EntityMatch] = []
    surface_to_ids: dict[str, set[UUID]] = defaultdict(set)
    masked = list(user_message)

    for entry in sorted_entries:
        pattern = _compile_pattern(entry)
        scan_text = "".join(masked)
        hit_text: str | None = None
        for m in pattern.finditer(scan_text):
            hit_text = m.group(0)
            surface_to_ids[hit_text.lower()].add(entry.id)
            for i in range(m.start(), m.end()):
                masked[i] = "\x00"
            break  # one match per entry per turn is enough for context
        if hit_text is not None:
            matches.append(EntityMatch(entity_id=entry.id, matched_text=hit_text))

    ambiguous = any(len(ids) > 1 for ids in surface_to_ids.values())
    return matches, ambiguous
