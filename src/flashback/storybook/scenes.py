"""Panel / chapter / cover illustration generation (Gemini-baked lettering).

Gemini letters the Sonnet-authored narration directly INTO the artwork
(speech bubbles + cream caption banners) — the spike-chosen approach over a
Pillow overlay. Gemini's lettering is ~95% accurate, so a gpt-5.1 vision
verifier confirms the words and the panel re-rolls (up to ``tries``) on
garble. A verifier error never blocks a render.
"""

from __future__ import annotations

import base64
import io

import structlog
from PIL import Image

from flashback.storybook.refs import (
    DEFAULT_GEMINI_IMAGE_MODEL,
    _gen_image,
    identity_rule,
    image_part,
)

log = structlog.get_logger("flashback.storybook.scenes")

DEFAULT_VERIFIER_MODEL = "gpt-5.1"


def lettering_ok(openai_client, img: Image.Image, expected: str,
                 *, model: str = DEFAULT_VERIFIER_MODEL) -> bool:
    """Vision check: does the hand-lettering show EXACTLY the authored words?

    Best-effort — a verifier error returns True so the render never blocks.
    NOTE: a reasoning model needs a real completion budget
    (max_completion_tokens=2000, reasoning_effort='low') or the content comes
    back empty and everything reads as BAD.
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    try:
        r = openai_client.chat.completions.create(
            model=model,
            max_completion_tokens=2000,
            reasoning_effort="low",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "An illustration is supposed to contain this "
                                "exact lettering, rendered EXACTLY ONCE:\n"
                                f'"{expected}"\n'
                                "Look at the image. Reply with ONLY 'OK' if "
                                "the lettering shown matches those words "
                                "exactly -- correctly spelled, in order, with "
                                "NO missing words, NO extra words, and NO "
                                "garbled/nonsense letter strings -- and the "
                                "text appears exactly once. If anything is "
                                "misspelled, garbled, cut off, or different, "
                                "or the text / its banner or bubble is "
                                "rendered more than once anywhere in the "
                                "image, reply ONLY 'BAD'."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}"
                            },
                        },
                    ],
                }
            ],
        )
        content = (r.choices[0].message.content or "").strip().upper()
        return content.startswith("OK")
    except Exception as exc:
        log.warning("storybook.lettering_verify_skipped", error=str(exc)[:160])
        return True


def _ident(subject: str, role: str) -> str:
    if subject:
        return identity_rule(subject, role)
    return (
        "Keep the SAME characters consistent with the character-reference "
        "image. "
    )


def gen_scene(
    client,
    scene: str,
    ref: Image.Image | None,
    art_style: str,
    aspect: str,
    *,
    text: str = "",
    kind: str = "caption",
    tries: int = 3,
    subject: str = "",
    role: str = "the subject",
    cast: str = "",
    verifier=None,
    model: str = DEFAULT_GEMINI_IMAGE_MODEL,
) -> Image.Image | None:
    """Full-bleed comic-panel illustration with lettering baked in.

    When ``text`` and a ``verifier`` (OpenAI client) are supplied, the
    lettering is verified and the panel re-rolled up to ``tries`` times on
    garble; the last attempt is returned rather than nothing.
    """
    if text:
        clean = text.strip().strip('"').strip("“”")
        if kind == "speech":
            text_rule = (
                f"The character is speaking. Render this line of dialogue as "
                f"hand-lettered text inside a clean white storybook speech "
                f"bubble with a tail pointing to the speaker, placed over a "
                f'calm area away from faces: "{clean}". '
            )
        else:
            text_rule = (
                f"Letter this caption as a single short line of narration "
                f"inside a clean cream caption banner with softly rounded "
                f"corners. FLOAT the banner in the lower portion of the panel "
                f"over a calm area, but keep the ENTIRE banner well inside "
                f"the frame -- leave a clear margin of at least 18% of the "
                f"image height between the banner and the bottom edge, and a "
                f"clear margin from the left and right edges. The banner must "
                f'NOT touch or bleed off any edge of the image: "{clean}". '
            )
        text_rule += (
            "This text MUST be present and rendered completely and fully "
            "visible, never cut off or running off any edge -- and rendered "
            "EXACTLY ONCE, in ONE single banner or bubble: never draw a "
            "second banner and never repeat the sentence anywhere. Spell "
            "every word EXACTLY as written, no extra or missing words, no "
            "gibberish letters. Use a clear, even, legible serif typeface, "
            "dark ink, well-kerned, large enough to read comfortably. The "
            "lettering must be the ONLY text in the image."
        )
    else:
        text_rule = (
            "Draw NO text, no lettering anywhere -- pure illustration only. "
        )
    prompt = (
        f"A single storybook comic-panel illustration: {scene}. {art_style}. "
        f"Full-bleed, fills the entire frame edge to edge, no border, no "
        f"margin. Compose so there is ONE calm area of negative space (open "
        f"sky, plain wall, water, or empty ground) along an edge or corner, "
        f"away from faces and the main action, where the text can rest. "
        f"{_ident(subject, role)}{cast}{text_rule}"
    )
    parts: list = [prompt]
    if ref is not None:
        parts.append(image_part(ref))

    expected = text.strip().strip('"').strip("“”") if text else ""
    last: Image.Image | None = None
    for attempt in range(tries):
        img = _gen_image(client, parts, aspect, model=model)
        if img is None or not expected or verifier is None:
            return img
        last = img
        if lettering_ok(verifier, img, expected):
            return img
        log.info(
            "storybook.lettering_reroll",
            attempt=attempt + 1,
            tries=tries,
            expected=expected[:48],
        )
    return last


def gen_chapter_art(
    client,
    scene: str,
    ref: Image.Image | None,
    art_style: str,
    aspect: str,
    *,
    subject: str = "",
    role: str = "the subject",
    cast: str = "",
    model: str = DEFAULT_GEMINI_IMAGE_MODEL,
) -> Image.Image | None:
    """Single chapter illustration painted to fade into warm paper edges."""
    prompt = (
        f"A single storybook illustration: {scene}. {art_style}. Centered "
        f"composition; the scene sits on a soft, uncluttered painterly "
        f"background that fades gently to pale warm cream parchment at all "
        f"four edges (a soft vignette into paper, no hard border). "
        f"{_ident(subject, role)}{cast}Draw NO text, NO lettering, NO frame "
        f"or border anywhere -- pure illustration only."
    )
    parts: list = [prompt]
    if ref is not None:
        parts.append(image_part(ref))
    return _gen_image(client, parts, aspect, model=model)


def gen_cover_art(
    client,
    *,
    name: str,
    relationship: str | None,
    gt_context: str,
    ref: Image.Image | None,
    art_style: str,
    age: str = "",
    model: str = DEFAULT_GEMINI_IMAGE_MODEL,
) -> Image.Image | None:
    """A single warm hero illustration of the subject for the cover.

    ``age`` is the descriptor of the book's dominant life stage; without an
    explicit age the model follows the reference sheet / relationship word
    and paints the subject old on every cover."""
    rel = relationship or "the subject"
    age_line = f"Depict {name} as {age} on this cover. " if age else ""
    prompt = (
        f"A single cover illustration for a family storybook about {name} "
        f"({rel}) -- {gt_context}. A warm, dignified portrait-scene with "
        f"{name} as the central figure, evocative of their life and world. "
        f"{age_line}"
        f"{art_style}. Centered composition, fills the frame, soft "
        f"uncluttered background. {identity_rule(name, rel)}"
        f"Draw NO text, NO lettering, NO border anywhere -- pure "
        f"illustration."
    )
    parts: list = [prompt]
    if ref is not None:
        parts.append(image_part(ref))
    return _gen_image(client, parts, "3:4", model=model)
