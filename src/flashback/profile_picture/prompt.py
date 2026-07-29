"""Profile-picture prompt composition."""

from __future__ import annotations

from flashback.artifacts.presets import apply_preset

_GENDER_MAP = {"he": "male", "she": "female", "they": "non_binary"}

NEGATIVE_PROMPT = (
    "photograph, hyperrealistic skin pores, deepfake likeness of a real "
    "specific living person, flat cartoon shading, cel-shaded anime, "
    "Pixar 3D look, exaggerated cartoon proportions, plastic skin, "
    # Deity / mythological iconography — blocks the failure mode where a
    # name with strong religious priors (Krishna, Jesus, Buddha, Ganesh,
    # Apollo, Athena, etc.) makes the model default to depicting the
    # deity instead of an ordinary person with that name. We forbid the
    # strong deity tells (halo, divine aura, multi-arm, mythological
    # symbols, deity skin tones) but not ordinary cultural attire — a
    # tilaka, sari, kurta, kippah, hijab, cross-necklace etc. are fine
    # on an ordinary person and remain allowed.
    "religious deity, god, goddess, divine being, mythological figure, "
    "holy avatar, sacred icon, halo, divine aura, glowing aureole, "
    "celestial light, multi-armed figure, multiple arms, blue-skinned "
    "deity, green-skinned deity, gold-skinned deity, peacock-feather "
    "crown, deity holding a flute, lotus throne, religious altar "
    "backdrop, temple sanctum backdrop, "
    "text, watermark, signature, blurry, low quality, distorted, uncanny"
)

_SUBJECT_HINT: dict[str, str] = {
    "male": "male subject",
    "female": "female subject",
    "non_binary": "non-binary subject",
}

# Anchor that runs on every portrait, before name-driven priors get a
# chance to bias the composition toward a mythological reading. Sits
# right after the name so it's spatially close in the prompt.
_REAL_PERSON_ANCHOR = (
    "an ordinary contemporary person living a normal everyday life, "
    "not a religious or mythological figure, modern real-world setting"
)


def map_gender(gender: str | None) -> str:
    """Map persons.gender pronoun to the contract gender string."""
    return _GENDER_MAP.get(gender or "", "unspecified")


# ---------------------------------------------------------------------------
# Representational (no-photo) onboarding image — design 2026-07-29.
# With no reference photo, a portrait prompt makes the model invent a
# face for the name; the contributor reads it as a wrong stranger. The
# onboarding fallback is instead a figure seen from behind in the
# tribute-banner design language. The subject's NAME is deliberately
# excluded: with no face to anchor, a name only imports risk (deity
# priors, ethnicity guessing).
# ---------------------------------------------------------------------------

REPRESENTATIONAL_NEGATIVE_PROMPT = (
    "visible face, frontal view, figure turning toward the viewer, "
    "portrait framing, close-up headshot, multiple people, crowd, "
    "photograph, photorealism, 3D render, plastic CGI look, harsh "
    "saturation, neon colors, religious deity iconography, halo, divine "
    "aura, multi-armed figure, text, watermark, signature"
)

_REPR_FIGURE: dict[str, str] = {
    "male": "male figure",
    "female": "female figure",
}

_REPR_ELDER_WORDS = ("grandmother", "grandfather", "grandparent", "grandma", "grandpa")
_REPR_PARENT_WORDS = ("mother", "father", "parent", "aunt", "uncle", "mom", "dad")
_REPR_PEER_WORDS = ("friend", "sibling", "brother", "sister", "cousin", "colleague")


def compose_representational_prompt(
    *,
    gender: str | None,
    relationship: str | None,
) -> str:
    """Banner-language illustration prompt for the no-photo onboarding path.

    Single figure seen from behind — face never visible — in the soft flat
    storybook style of the friendship-day tribute banners (warm cream /
    terracotta / muted amber). Prototype-validated 2026-07-29.
    """
    gender_contract = map_gender(gender)
    figure = _REPR_FIGURE.get(gender_contract, "figure")

    rel = (relationship or "").strip().lower()
    clause = "simple everyday clothing"
    matched = next((w for w in _REPR_ELDER_WORDS if w in rel), None)
    if matched:
        figure = f"elderly {figure}"
        clause = f"a {matched}'s gentle posture, simple everyday clothing"
    else:
        matched = next((w for w in _REPR_PARENT_WORDS if w in rel), None)
        if matched:
            clause = (
                f"a {matched}'s warm unhurried posture in their middle "
                "years, simple everyday clothing"
            )
        elif any(w in rel for w in _REPR_PEER_WORDS):
            clause = f"dressed casually like a close {rel}"
    if figure == "figure":
        clause = f"{clause}, androgynous silhouette"

    return (
        f"Soft flat storybook illustration of a single {figure} seen from "
        f"behind, {clause}, walking along a gentle path into a warm "
        "landscape, cream and terracotta palette with muted amber accents, "
        "soft autumn foliage and rolling hills, a few birds high in the "
        "sky, generous negative space, clean minimal shapes, subtle paper "
        "grain, warm hopeful mood, the figure's face never visible, "
        "no text, no watermark"
    )


def compose_image_prompt(
    *,
    name: str,
    gender: str | None,
    relationship: str | None = None,
    user_instructions: str | list[str] | None = None,
    preset: str | None = None,
    ground_truth_context: str | None = None,
) -> str:
    """Return a painterly semi-realistic portrait prompt for the image model.

    Visual target: Red Dead Redemption 2 character-art aesthetic — naturalistic
    features rendered with painterly brushwork, cinematic studio lighting, rich
    warm color grade. Sits between full photorealism and stylized cartoon. The
    negative prompt forbids deepfake likeness of real specific living people
    (CLAUDE.md §1 product constraint) while allowing lifelike depiction.

    ``gender`` is the raw persons.gender value (``"he"``/``"she"``/``"they"``/
    ``None``). ``relationship`` is optional context (e.g. ``"grandmother"``).
    ``user_instructions`` is appended verbatim when the caller is an edit flow.
    Pass a list to stack cumulative edit history (each entry joined in order).
    ``preset`` selects a stylistic modifier from the shared registry
    (:mod:`flashback.artifacts.presets`); ``None`` uses the default RDR2 look.
    Unknown preset slugs raise ``ValueError``.
    """
    gender_contract = map_gender(gender)
    subject_hint = _SUBJECT_HINT.get(gender_contract, "")

    parts: list[str] = [
        f"Painterly semi-realistic portrait of {name}",
        _REAL_PERSON_ANCHOR,
    ]
    if subject_hint:
        parts.append(subject_hint)
    if relationship:
        parts.append(
            f"depicted as an ordinary contemporary {relationship}, "
            f"modern everyday clothing"
        )
    if ground_truth_context and ground_truth_context.strip():
        # Derived subject grounding (region/era/attire/features) — design
        # 2026-06-11 §5. Read at compose time so a manual regenerate after
        # ground truth lands produces the corrected portrait.
        parts.append(ground_truth_context.strip())
    parts += [
        "in the visual style of Red Dead Redemption 2 character art",
        "naturalistic features with visible painterly brushwork",
        "soft cinematic studio lighting with warm key and gentle rim light",
        "rich earthen color grade, shallow depth of field",
        "dignified lifelike expression, weight and texture in the skin and fabric",
        "hand-painted oil-painting quality leaning a touch more illustrative "
        "than photographic, clearly short of full photorealism, not cartoon",
        "no text no watermarks",
    ]
    for fragment in _normalize_instructions(user_instructions):
        parts.append(fragment)

    return apply_preset(", ".join(parts), preset)


def _normalize_instructions(
    value: str | list[str] | None,
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            continue
        stripped = entry.strip()
        if stripped:
            out.append(stripped)
    return out
