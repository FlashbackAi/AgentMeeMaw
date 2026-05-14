"""Question text rendering helpers shared by deterministic selectors."""

from __future__ import annotations


_PRONOUNS: dict[str, dict[str, str]] = {
    "male": {"they": "he", "them": "him", "their": "his"},
    "he": {"they": "he", "them": "him", "their": "his"},
    "female": {"they": "she", "them": "her", "their": "her"},
    "she": {"they": "she", "them": "her", "their": "her"},
}
_DEFAULT_PRONOUNS = {"they": "they", "them": "them", "their": "their"}


def pronouns_for(gender: str | None) -> dict[str, str]:
    if gender is None:
        return _DEFAULT_PRONOUNS
    return _PRONOUNS.get(gender.strip().lower(), _DEFAULT_PRONOUNS)


def render_question_text(text: str, name: str, gender: str | None) -> str:
    out = text.replace("{name}", name)
    for placeholder, value in pronouns_for(gender).items():
        out = out.replace("{" + placeholder + "}", value)
    return out
