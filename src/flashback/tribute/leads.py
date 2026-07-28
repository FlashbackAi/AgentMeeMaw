"""Turn tribute archetype answers into in-session conversation *leads*.

An archetype answer ("he sold a home", "he lost his mother young") is a
LEAD, not a fact (design 2026-06-19). It is never written to the graph
(invariant #22 stays intact); instead it steers the interview so the agent
draws out the real story behind it, and the *extracted moment* becomes the
durable record.

This module derives ranked leads from the committed archetype answers, and
owns the JSON shape stored in Working Memory (``tribute_leads``) plus the
soft hint text handed to the response generator. Leads are surfaced highest
narrative-value first and marked ``pursued`` once shown so the agent doesn't
circle the same thread twice in a session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any

# Father's Day bank (tribute/theme.py) question_ids by narrative value. The
# "what he gave up / went without" layer is the spine of the confession, so it
# ranks highest; the mirror pairs + lost-a-parent + the confession line are
# mid; plain background is low. Unknown ids (non-FD tributes) default to mid.
_FD_HIGH = frozenset({"q9", "q10", "q11", "q12", "q13"})
_FD_MID = frozenset({"q4", "q5", "q6", "q7", "q8", "q14"})
_FD_LOW = frozenset({"q1", "q2", "q3"})

# Don't carry more than this many leads into a session -- the strongest few.
_MAX_LEADS = 8


@dataclass(frozen=True)
class Lead:
    label: str  # stable id for pursued-tracking (the answer's question_id)
    question: str
    answer: str
    value: int
    pursued: bool = False


def _answer_text(ans: dict[str, Any]) -> str:
    """The contributor's actual choice.

    Multi-select answers join their chip labels; typed free text is the
    most personal signal so it leads when present, with the chips kept
    alongside for context.
    """
    raw_labels = ans.get("option_labels") or ans.get("labels")
    if isinstance(raw_labels, list):
        labels = [str(label).strip() for label in raw_labels if str(label or "").strip()]
    else:
        labels = []
    if not labels:
        single = str(ans.get("option_label") or "").strip()
        if single:
            labels = [single]
    free = str(ans.get("free_text") or "").strip()
    if free and labels:
        return f'{free} (also: {", ".join(labels)})'
    if free:
        return free
    return ", ".join(labels)


def _value_for(question_id: str, *, has_free_text: bool) -> int:
    if question_id in _FD_HIGH:
        base = 3
    elif question_id in _FD_LOW:
        base = 1
    else:  # FD mid + any non-FD archetype id
        base = 2
    # A typed answer is more specific and personal than a tapped chip.
    return base + (1 if has_free_text else 0)


def build_leads(
    archetype_answers: list[dict[str, Any]] | None,
) -> list[Lead]:
    """Derive ranked, un-pursued leads from committed archetype answers.

    Skips answers the user skipped or left blank. Sorted by descending
    narrative value; ties keep the bank's (chronological) order so the
    earliest layers surface first within a tier.
    """
    leads: list[Lead] = []
    for ans in archetype_answers or []:
        if not isinstance(ans, dict) or ans.get("skipped"):
            continue
        answer = _answer_text(ans)
        if not answer:
            continue
        qid = str(ans.get("question_id") or "").strip()
        question = str(ans.get("question_text") or "").strip()
        has_free_text = bool(str(ans.get("free_text") or "").strip())
        leads.append(
            Lead(
                label=qid or question or answer,
                question=question,
                answer=answer,
                value=_value_for(qid, has_free_text=has_free_text),
            )
        )
    # Stable sort by value desc; Python's sort preserves input order on ties.
    leads.sort(key=lambda x: x.value, reverse=True)
    return leads[:_MAX_LEADS]


def leads_to_json(leads: list[Lead]) -> str:
    return json.dumps([asdict(x) for x in leads])


def leads_from_json(raw: str | None) -> list[Lead]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out: list[Lead] = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        out.append(
            Lead(
                label=str(d.get("label") or ""),
                question=str(d.get("question") or ""),
                answer=str(d.get("answer") or ""),
                value=int(d.get("value") or 0),
                pursued=bool(d.get("pursued")),
            )
        )
    return out


def pick_next_lead(raw: str | None) -> Lead | None:
    """Highest-value un-pursued lead, or None. List is already value-sorted."""
    for lead in leads_from_json(raw):
        if not lead.pursued:
            return lead
    return None


def mark_pursued(raw: str | None, label: str) -> str:
    """Return the leads JSON with ``label`` flipped to pursued."""
    leads = leads_from_json(raw)
    updated = [
        Lead(x.label, x.question, x.answer, x.value, pursued=True)
        if x.label == label
        else x
        for x in leads
    ]
    return leads_to_json(updated)


def lead_hint(lead: Lead) -> str:
    """The soft <tribute_gap_hint> text steering the next beat to this lead."""
    if lead.question:
        return (
            f'Earlier, asked "{lead.question}", the contributor answered '
            f'"{lead.answer}". There is a real story behind that they have not '
            f"told yet -- if the moment allows, gently draw it out."
        )
    return (
        f'The contributor hinted at "{lead.answer}" but has not told the story '
        f"yet -- if the moment allows, gently draw it out."
    )
