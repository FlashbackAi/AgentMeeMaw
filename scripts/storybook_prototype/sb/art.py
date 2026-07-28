"""Gemini illustration generation.

One optional character-reference image anchors the recurring figure; each
beat is then painted from its art_direction. Two background modes match the
compositor: "cream" (vignette on paper) and "green" (subject on chroma-green).

The house style + likeness ban reuse the service's SCENE_NEGATIVE_PROMPT so
the prototype paints in the same register the shipped pipeline does.
"""
from __future__ import annotations

import io
import time

from google import genai
from google.genai import types
from PIL import Image

from flashback.artifacts.compose import (
    COVER_PORTRAIT_NEGATIVE_PROMPT,
    SCENE_NEGATIVE_PROMPT,
)

from . import config

STYLE = (
    "painterly realism with soft, visible watercolour brushwork, in the "
    "naturalistic register of Red Dead Redemption 2 -- sitting between "
    "photorealism and cartoon; muted earthy palette, gentle natural light, "
    "tender and restrained, not sentimental"
)

NEGATIVE = (
    SCENE_NEGATIVE_PROMPT
    + ", text, lettering, caption, words, border, frame, vignette outline, "
    "collage, multiple panels"
)


def _background_instruction(blend: str) -> str:
    if blend == "green":
        return (
            "Place ONLY the figure and the single key object/ground on a SOLID, "
            "uniform chroma-key green background (RGB 0,177,64) with nothing else "
            "behind them -- no scenery, no sky, no walls; a soft contact shadow "
            "is fine."
        )
    return (
        "Paint it as a loose vignette on plain warm off-white cream paper, the "
        "scene grouped low and centred, the edges softly fading to nothing; no "
        "frame, no border, generous empty paper around the scene."
    )


def build_prompt(art_direction: str, gt_context: str, blend: str) -> str:
    parts = [art_direction.strip(), STYLE]
    if gt_context:
        parts.append(gt_context.strip())
    parts.append(_background_instruction(blend))
    parts.append(
        "Render any figure from behind, at a distance, or implied (hands, "
        "silhouette, the thing they are doing). No visible faces, no one facing "
        "the viewer."
    )
    parts.append("Avoid: " + NEGATIVE)
    return " ".join(p for p in parts if p)


def _extract_image(resp) -> Image.Image | None:
    for cand in (resp.candidates or []):
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return Image.open(io.BytesIO(inline.data)).convert("RGB")
    return None


def _to_part(img: Image.Image) -> types.Part:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")


class Artist:
    def __init__(self, model: str | None = None):
        self.client = genai.Client(api_key=config.env("GEMINI_API_KEY"))
        self.model = model or config.GEMINI_MODEL

    def _generate(self, contents: list, aspect: str) -> Image.Image:
        configs = [
            types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=aspect),
            ),
            # Fallback if a model build rejects image_config / aspect.
            types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        ]
        last = "unknown"
        for cfg in configs:
            for attempt in range(3):
                try:
                    resp = self.client.models.generate_content(
                        model=self.model, contents=contents, config=cfg
                    )
                    img = _extract_image(resp)
                    if img is not None:
                        return img
                    last = "no image in response"
                except Exception as exc:  # transient API/network
                    last = f"{type(exc).__name__}: {str(exc)[:160]}"
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Gemini generation failed: {last}")

    def character_reference(self, subject, gt_context: str) -> Image.Image:
        who = subject.relationship or "an elder"
        prompt = (
            f"Character reference of {who} ({subject.name}), single full-length "
            f"figure seen from behind and in three-quarter back view. "
            f"{gt_context} {STYLE}. Plain soft neutral background, no face "
            f"visible. Avoid: {NEGATIVE}"
        )
        return self._generate([prompt], "3:4")

    def portrait_from_photo(
        self,
        photo: Image.Image,
        subject,
        gt_context: str,
        *,
        deage: bool = False,
        blend: str = "cream",
    ) -> Image.Image:
        """Repaint a user-uploaded prime-years PHOTO into the storybook
        watercolour register, KEEPING the subject's real likeness.

        Cover/opener likeness exception (CLAUDE.md s1/s3): the contributor
        uploaded a consented photo, so the no-face / no-likeness bans are
        dropped here -- relaxed COVER_PORTRAIT negative, this page only.
        """
        deage_clause = (
            "Render him noticeably YOUNGER -- restore his prime-years self "
            "(fuller dark hair, upright vigour) while keeping his recognizable "
            "features and bone structure. "
            if deage else ""
        )
        prompt = (
            "Repaint this photograph as a dignified painterly watercolour "
            f"PORTRAIT of {subject.name}, in the storybook style: "
            f"{STYLE}. KEEP his real likeness, face, and features faithfully. "
            f"{deage_clause}{gt_context} "
            + _background_instruction(blend)
            + " A calm, warm head-and-shoulders portrait. "
            "Avoid: " + COVER_PORTRAIT_NEGATIVE_PROMPT
            + ", text, lettering, border, frame"
        )
        return self._generate([prompt, _to_part(photo)], "3:4")

    def illustrate(
        self,
        art_direction: str,
        gt_context: str,
        blend: str,
        *,
        reference: Image.Image | None = None,
        aspect: str | None = None,
    ) -> Image.Image:
        prompt = build_prompt(art_direction, gt_context, blend)
        contents: list = [prompt]
        if reference is not None:
            contents.append(
                "Keep the SAME recurring figure as the reference image (same "
                "build, clothing, hair, age); do NOT copy its pose or background."
            )
            contents.append(_to_part(reference))
        return self._generate(contents, aspect or config.ART_ASPECT)
