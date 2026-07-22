"""Gender-correct depiction of the people who recur in artifacts.

Moment scenes (and, on regenerate, entity/thread art) can depict the
SUBJECT and — when a memory involves them, "my father and I on a bike" —
the CONTRIBUTOR. Without a gender cue the image model tends to default to
one presentation. These helpers turn the stored pronoun form
(``he``/``she``/``they``/``None``) into an explicit figure noun and a short
compose-time fragment. ``they`` / unknown stays neutral so we never push a
wrong guess (CLAUDE.md §1 — no demographic invention).

Faces still stay turned/distant per the scene no-faces rule; this only
fixes presentation, not likeness.
"""

from __future__ import annotations

# Pronoun form OR entity gender -> figure noun. Neutral values ("they",
# unknown, a name) are intentionally absent: they yield no directive, leaving
# the model unbiased (CLAUDE.md §1 — no demographic invention).
_FIGURE_NOUN = {
    "he": "a man", "she": "a woman",       # persons.gender / contributor_gender
    "male": "a man", "female": "a woman",  # entities.attributes.gender
}


def figure_noun(gender: str | None) -> str | None:
    """Map a stored pronoun form to a scene figure noun, or None if neutral."""
    return _FIGURE_NOUN.get((gender or "").strip().lower())


def people_scene_fragment(
    *,
    subject_gender: str | None,
    contributor_gender: str | None,
) -> str:
    """A short comma-joinable grounding fragment for scene/portrait compose.

    Returns ``""`` when neither gender is known. Mirrors the role language of
    the extraction ``<people_in_scenes>`` block so auto and regenerate paths
    agree on who is who.
    """
    clauses: list[str] = []
    subject_fig = figure_noun(subject_gender)
    if subject_fig:
        clauses.append(f"the subject as {subject_fig}")
    contributor_fig = figure_noun(contributor_gender)
    if contributor_fig:
        clauses.append(f"the contributor as {contributor_fig}")
    if not clauses:
        return ""
    return (
        "Depict any human figures with correct gender presentation: "
        + ", ".join(clauses)
        + " (matching noun, not a neutral figure; faces turned away or distant)."
    )


def people_catalog_fragment(
    *,
    subject_name: str,
    subject_relationship: str | None,
    subject_gender: str | None,
    contributor_gender: str | None,
    involved: list[dict] | None = None,
) -> str:
    """A <people> grounding block for the storybook assembler.

    The subject is always listed (name-only when gender is unknown — they are
    the story's throughline and always exist); the contributor is listed only
    when their gender is known (there is no contributor name/identity here, so
    a name-only row would be pure boilerplate); involved people are always
    listed (name-only when gender is unknown). A gender clause is emitted
    ONLY where gender is known — an unknown-gender person is still named so
    the model knows who exists but stays unbiased on presentation. Returns
    "" only when there is no subject name AND nothing else is known.
    """
    rows: list[str] = []
    name = (subject_name or "").strip()
    if name:
        rel = f", the storyteller's {subject_relationship}" if subject_relationship else ""
        subject_fig = figure_noun(subject_gender)
        if subject_fig:
            rows.append(f"- {name} (the subject{rel}) is {subject_fig}.")
        else:
            rows.append(f"- {name} (the subject{rel}).")
    contributor_fig = figure_noun(contributor_gender)
    if contributor_fig:
        rows.append(
            f"- The person sharing these memories (the storyteller) is "
            f"{contributor_fig}."
        )
    for person in involved or []:
        name = (person.get("name") or "").strip()
        if not name:
            continue
        rel = (person.get("relationship") or "").strip()
        who = f" ({rel})" if rel else ""
        fig = figure_noun(person.get("gender"))
        if fig:
            rows.append(f"- {name}{who} is {fig}.")
        else:
            rows.append(f"- {name}{who}.")
    if not rows:
        return ""
    return (
        "<people>\n"
        "These are the real people in these memories. Use each stated gender "
        "with a matching noun (\"a man\", \"a woman\") — never guess gender "
        "from a name, and never invent people not listed here.\n"
        + "\n".join(rows)
        + "\n</people>"
    )
