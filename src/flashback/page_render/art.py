"""Gemini illustration generation for tribute pages.

One optional character-reference image anchors the recurring figure; each beat is
painted from its art_direction. The opener can be a portrait painted FROM the
contributor's consented prime photo (relaxed likeness ban, that page only). The
house style + likeness ban reuse the service's SCENE_NEGATIVE_PROMPT.

API key + model id are injected from settings (Config) by the caller.
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

from flashback.tribute_video import style
from flashback.usage import recorder as usage_recorder

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


class GeminiError(RuntimeError):
    """Image generation failed after retries."""


class Artist:
    def __init__(self, *, api_key: str, model: str | None = None,
                 aspect: str | None = None, feature: str = "tribute_image"):
        self.client = genai.Client(api_key=api_key)
        self.model = model or "gemini-3.1-flash-image"
        self.aspect = aspect or style.ART_ASPECT
        self.feature = feature  # usage_events label for cost attribution

    def _generate(self, contents: list, aspect: str) -> Image.Image:
        configs = [
            types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=aspect),
            ),
            types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        ]
        last = "unknown"
        for ci, cfg in enumerate(configs):
            for attempt in range(3):
                try:
                    resp = self.client.models.generate_content(
                        model=self.model, contents=contents, config=cfg)
                    # Meter every request that returned (billed even when no
                    # image lands). Soft-fail; never breaks the render.
                    usage_recorder.record_image_usage_sync(
                        feature=self.feature, provider="gemini",
                        model=self.model)
                    img = _extract_image(resp)
                    if img is not None:
                        return img
                    last = "no image in response"
                except Exception as exc:  # transient API/network
                    last = f"{type(exc).__name__}: {str(exc)[:160]}"
                # Back off before EVERY retry: an immediate re-request after a
                # no-image response tends to hit the same transient state, and
                # one exhausted page fails the whole render.
                if ci < len(configs) - 1 or attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise GeminiError(f"Gemini generation failed: {last}")

    def character_reference(self, *, name: str, relationship: str | None,
                            gt_context: str) -> Image.Image:
        who = relationship or "an elder"
        prompt = (
            f"Character reference of {who} ({name}), single full-length figure "
            f"seen from behind and in three-quarter back view. {gt_context} "
            f"{STYLE}. Plain soft neutral background, no face visible. "
            f"Avoid: {NEGATIVE}"
        )
        return self._generate([prompt], "3:4")

    def portrait_from_photo(self, photo: Image.Image, *, name: str,
                            gt_context: str, deage: bool = False,
                            blend: str = "cream") -> Image.Image:
        """Repaint a consented prime-years PHOTO into the storybook register,
        keeping the subject's real likeness (cover/opener likeness exception)."""
        deage_clause = (
            "Render him noticeably YOUNGER -- restore his prime-years self "
            "(fuller dark hair, upright vigour) while keeping his recognizable "
            "features and bone structure. " if deage else ""
        )
        prompt = (
            "Repaint this photograph as a dignified painterly watercolour "
            f"PORTRAIT of {name}, in the storybook style: {STYLE}. KEEP his real "
            f"likeness, face, and features faithfully. {deage_clause}{gt_context} "
            + _background_instruction(blend)
            + " A calm, warm head-and-shoulders portrait. Avoid: "
            + COVER_PORTRAIT_NEGATIVE_PROMPT + ", text, lettering, border, frame"
        )
        return self._generate([prompt, _to_part(photo)], "3:4")

    def illustrate(self, art_direction: str, gt_context: str, blend: str, *,
                   reference: Image.Image | None = None,
                   aspect: str | None = None) -> Image.Image:
        prompt = build_prompt(art_direction, gt_context, blend)
        contents: list = [prompt]
        if reference is not None:
            contents.append(
                "Keep the SAME recurring figure as the reference image (same "
                "build, clothing, hair, age); do NOT copy its pose or background.")
            contents.append(_to_part(reference))
        return self._generate(contents, aspect or self.aspect)
