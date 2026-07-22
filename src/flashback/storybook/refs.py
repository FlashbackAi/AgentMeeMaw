"""Master character identity refs — one consistent, age-controlled subject.

The spike's fix for "old on one page, young on the next": ONE identity,
rendered as four age-anchored reference sheets (child/young/mid/old) in a
single fixed painterly style. The primary stage is generated first (from the
user's uploaded anchor photo when Node minted a GET URL for it, else from
ground truth) and every other stage is conditioned on it so the FACE carries
across ages. Each panel then renders against the ref matching its
``age_stage``, so age changes are intentional, never drift.

``identity_rule`` is the anti-mixup instruction: the reference binds to the
SUBJECT only and is appearance-only — never a hint about who performs the
action in a scene (the spike's grandfather-on-the-branch bug).
"""

from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor

import structlog
from google.genai import types
from PIL import Image

from flashback.artifacts.people import figure_noun
from flashback.usage import recorder as usage_recorder

log = structlog.get_logger("flashback.storybook.refs")

DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"

# One fixed style for every identity ref (NOT per-book): identity stays stable;
# each book still paints its scenes in its own art_style, conditioned on these.
REF_STYLE = (
    "hand-painted character illustration in the visual register of Red Dead "
    "Redemption 2 -- naturalistic features and lighting with painterly "
    "brushwork, clearly a painting (not a photo, not a cartoon)"
)

# stage -> age descriptor by subject gender ("male" / "female" / fallback).
_AGE_DESCRIPTORS: dict[str, dict[str, str]] = {
    "child": {
        "male": "a boy about ten years old",
        "female": "a girl about ten years old",
        "": "a child about ten years old",
    },
    "young": {
        "male": "a young man in his late twenties to early thirties",
        "female": "a young woman in her late twenties to early thirties",
        "": "a young adult in their late twenties to early thirties",
    },
    "mid": {
        "male": "a grandfather in his early sixties, hair going silver",
        "female": "a grandmother in her early sixties, hair going silver",
        "": "a grandparent in their early sixties, hair going silver",
    },
    "old": {
        "male": "an elderly man about seventy-five, white-haired and weathered",
        "female": (
            "an elderly woman about seventy-five, white-haired and weathered"
        ),
        "": (
            "an elderly person about seventy-five, white-haired and weathered"
        ),
    },
}

AGE_STAGES = tuple(_AGE_DESCRIPTORS)
PRIMARY_STAGE = "mid"  # the most common life period; the identity anchor


def age_descriptor(stage: str, gender: str | None) -> str:
    d = _AGE_DESCRIPTORS[stage]
    return d.get((gender or "").lower(), d[""]) or d[""]


def identity_rule(subject: str, role: str = "the subject") -> str:
    """Bind the reference image to ONE named person so the model never paints
    the subject's face/age onto a different character, and never promotes the
    subject into an action the scene gives to someone else."""
    return (
        f"IMPORTANT -- the character-reference image is {subject} ({role}), "
        f"and ONLY {subject}. Match {subject}'s face, features, hair and "
        f"build to the reference every time {subject} appears. NEVER put "
        f"{subject}'s face, hair, or age onto anyone else: children, women, "
        f"men and friends in the scene are DIFFERENT individuals who must "
        f"look clearly distinct from {subject} and age-appropriate (a child "
        f"must read as a real young child, never a shrunken {subject}). "
        f"The reference is APPEARANCE-ONLY -- it is NOT a hint about who is "
        f"in this panel, who is the main figure, or who performs the action. "
        f"Compose the panel STRICTLY from the scene description: whoever it "
        f"says climbs the tree / swims / runs / lies on the branch is exactly "
        f"that person, even when that person is a child and NOT {subject}. "
        f"{subject} is often just a background watcher, or not present at "
        f"all -- do not promote them into the main action, and do not add "
        f"them if the scene does not mention them. "
        f"Place every character exactly where the scene puts them. "
    )


def _cast_line(c) -> str:
    fig = figure_noun(getattr(c, "gender", None))
    noun = f", {fig}" if fig else ""
    return f"{c.name} ({c.who}{noun}): {c.appearance}"


def cast_rule(characters, subject: str) -> str:
    """Pin the OTHER recurring people (name + stable appearance + gender) so
    they stay recognisable panel to panel and are never painted with the
    subject's face (the two-identical-men bug), nor with the wrong gender.
    ``characters`` duck-types the script roster: objects with ``name`` /
    ``who`` / ``appearance`` / ``gender``. The anti-invention clause ("draw
    only who the scene names") lives in ``scenes._ident`` instead, since it
    must fire even when there is no recurring cast (this function returns
    "" for an empty roster)."""
    if not characters:
        return ""
    listing = "; ".join(_cast_line(c) for c in characters)
    return (
        f"OTHER RECURRING PEOPLE -- never drawn from the reference image: "
        f"{listing}. Whenever the scene names one of them, draw that person "
        f"matching this description at the age the scene states, always "
        f"clearly different from {subject} in face, hair, and build. "
    )


def _img_from_resp(resp) -> Image.Image | None:
    for c in resp.candidates or []:
        for p in c.content.parts or []:
            if getattr(p, "inline_data", None) and p.inline_data.data:
                return Image.open(io.BytesIO(p.inline_data.data)).convert("RGB")
    return None


def _gen_image(
    client,
    contents: list,
    aspect: str,
    *,
    image_size: str = "2K",
    net_tries: int = 5,
    model: str = DEFAULT_GEMINI_IMAGE_MODEL,
) -> Image.Image | None:
    """One Gemini image generation, resilient to transient failures.

    Retries with linear backoff on transport errors AND on responses that come
    back without an image (refusals / empty candidates) — previously the
    no-image case returned immediately and shipped a blank panel. Returns None
    only when every attempt fails (callers surface that as a blank-panel
    warning, never a silent skip).
    """
    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(
            aspect_ratio=aspect, image_size=image_size
        ),
    )
    last = "unknown"
    for attempt in range(net_tries):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents, config=cfg
            )
            # Meter every request that returned (billed even when no image
            # lands). Soft-fail; never breaks the render.
            usage_recorder.record_image_usage_sync(
                feature="storybook_image", provider="gemini", model=model
            )
            img = _img_from_resp(resp)
            if img is not None:
                return img
            last = "no image in response"
        except Exception as exc:
            last = str(exc)[:200]
        if attempt < net_tries - 1:
            log.info("storybook.gemini_retry", attempt=attempt + 1,
                     reason=last[:80])
            time.sleep(2 * (attempt + 1))
    log.warning("storybook.gemini_exhausted", tries=net_tries, error=last)
    return None


def image_part(img: Image.Image) -> types.Part:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")


def _gen_stage_ref(
    client,
    *,
    name: str,
    gt_context: str,
    stage: str,
    gender: str | None,
    base_ref: Image.Image | None = None,
    photo: Image.Image | None = None,
    model: str = DEFAULT_GEMINI_IMAGE_MODEL,
) -> Image.Image | None:
    """One age-anchored identity sheet. ``base_ref``/``photo`` anchor the FACE
    so every stage is the same person; ``stage`` controls only the age."""
    age = age_descriptor(stage, gender)
    ident = ""
    if photo is not None:
        ident = (
            "Match the facial identity (bone structure, features) of the "
            "reference PHOTOGRAPH provided. "
        )
    elif base_ref is not None:
        ident = (
            "This is the SAME person as the reference image provided -- "
            "identical face and features -- just at a different age. "
        )
    prompt = (
        f"Character reference sheet of {name}, {gt_context}. Depict them as "
        f"{age}. {ident}"
        f"Show the same person twice on a plain pale neutral background: a "
        f"head-and-shoulders portrait and a full standing figure, consistent "
        f"facial structure and build across both. {REF_STYLE}. Neutral "
        f"expression, even lighting. NO text, NO panels, NO props beyond "
        f"simple period-appropriate clothing."
    )
    parts: list = [prompt]
    if photo is not None:
        buf = io.BytesIO()
        photo.convert("RGB").save(buf, format="JPEG", quality=92)
        parts.append(
            types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")
        )
    if base_ref is not None:
        parts.append(image_part(base_ref))
    return _gen_image(client, parts, "3:4", image_size="1K", model=model)


class MasterRefs:
    """The subject's age-anchored identity refs, built once per render job."""

    def __init__(self) -> None:
        self._refs: dict[str, Image.Image] = {}

    def build(
        self,
        client,
        *,
        name: str,
        gt_context: str,
        gender: str | None = None,
        anchor_photo: Image.Image | None = None,
        model: str = DEFAULT_GEMINI_IMAGE_MODEL,
    ) -> None:
        src = "anchor photo" if anchor_photo is not None else "ground truth"
        log.info("storybook.master_refs_building", source=src)
        primary = _gen_stage_ref(
            client,
            name=name,
            gt_context=gt_context,
            stage=PRIMARY_STAGE,
            gender=gender,
            photo=anchor_photo,
            model=model,
        )
        if primary is not None:
            self._refs[PRIMARY_STAGE] = primary
        # The non-primary stages are independent calls all conditioned on the
        # same primary, so they run concurrently — pure scheduling, identical
        # prompts and conditioning, so identity quality is unchanged.
        others = [s for s in AGE_STAGES if s != PRIMARY_STAGE]
        with ThreadPoolExecutor(max_workers=len(others)) as ex:
            futs = {
                stage: ex.submit(
                    _gen_stage_ref,
                    client,
                    name=name,
                    gt_context=gt_context,
                    stage=stage,
                    gender=gender,
                    base_ref=primary,
                    photo=anchor_photo,
                    model=model,
                )
                for stage in others
            }
            for stage, fut in futs.items():
                ref = fut.result()
                if ref is not None:
                    self._refs[stage] = ref
                elif primary is not None:
                    self._refs[stage] = primary
        log.info(
            "storybook.master_refs_built", stages=sorted(self._refs)
        )

    def for_stage(self, stage: str | None) -> Image.Image | None:
        """The ref matching a panel's life stage, defaulting to the primary."""
        if stage and stage in self._refs:
            return self._refs[stage]
        return self._refs.get(PRIMARY_STAGE)
