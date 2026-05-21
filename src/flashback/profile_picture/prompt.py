"""Profile-picture prompt composition."""

from __future__ import annotations

_GENDER_MAP = {"he": "male", "she": "female", "they": "non_binary"}

NEGATIVE_PROMPT = (
    "photorealistic, photograph, hyperrealistic, real person, deepfake, "
    "text, watermark, signature, blurry, low quality, distorted, uncanny"
)

_SUBJECT_HINT: dict[str, str] = {
    "male": "male character",
    "female": "female character",
    "non_binary": "non-binary character",
}


def map_gender(gender: str | None) -> str:
    """Map persons.gender pronoun to the contract gender string."""
    return _GENDER_MAP.get(gender or "", "unspecified")


def compose_image_prompt(
    *,
    name: str,
    gender: str | None,
    relationship: str | None = None,
    user_instructions: str | None = None,
) -> str:
    """Return a Pixar-style portrait prompt ready to send to the image model.

    ``gender`` is the raw persons.gender value (``"he"``/``"she"``/``"they"``/
    ``None``). ``relationship`` is optional context (e.g. ``"grandmother"``).
    ``user_instructions`` is appended verbatim when the caller is an edit flow.
    """
    gender_contract = map_gender(gender)
    subject_hint = _SUBJECT_HINT.get(gender_contract, "")

    parts: list[str] = [f"Pixar-style stylized portrait of {name}"]
    if subject_hint:
        parts.append(subject_hint)
    if relationship:
        parts.append(f"depicted as a {relationship}")
    parts += [
        "warm expressive character design",
        "soft cinematic lighting",
        "rich color palette with gentle depth of field",
        "dignified and lifelike expression",
        "studio quality 3D render",
        "no text no watermarks",
    ]
    if user_instructions:
        stripped = user_instructions.strip()
        if stripped:
            parts.append(stripped)

    return ", ".join(parts)
