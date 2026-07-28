"""Visual test of the compositor with NO Gemini / NO DB.

cream  -> crop the watercolor art out of reference/example-page.jpg and
          re-blend it into the blank template (should look ~like the original).
green  -> synthesize a subject silhouette on chroma-green and key it out.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw  # noqa: E402

from sb import compose, config  # noqa: E402

OUT = os.path.join(config.OUT_DIR, "_compositor_test")
os.makedirs(OUT, exist_ok=True)


def cream_placeholder() -> Image.Image:
    """The watercolor art region cropped from the reference page."""
    ref = Image.open(config.REFERENCE_PATH).convert("RGB")
    w, h = ref.size
    # The reference illustration lives in the lower ~half.
    return ref.crop((int(0.04 * w), int(0.49 * h), int(0.96 * w), int(0.985 * h)))


def green_placeholder() -> Image.Image:
    img = Image.new("RGB", (900, 700), config.CHROMA_GREEN)
    d = ImageDraw.Draw(img)
    # crude standing figure (back view) in earthy tones
    d.ellipse((410, 120, 500, 215), fill=(70, 55, 42))          # head
    d.polygon([(395, 215), (515, 215), (540, 470), (370, 470)], fill=(120, 95, 70))  # torso
    d.rectangle((400, 470, 455, 650), fill=(60, 50, 40))        # legs
    d.rectangle((455, 470, 510, 650), fill=(60, 50, 40))
    return img


def main():
    tmpl = compose.load_template()
    print("template:", tmpl.size, "| paper:", compose.paper_color(tmpl))

    cream = compose.compose_page(
        eyebrow="His Little World",
        line="His room held his whole small world.",
        illo=cream_placeholder(),
        blend="cream",
        template=tmpl,
    )
    p1 = os.path.join(OUT, "test_cream.png")
    cream.save(p1)
    print("wrote", p1)

    green = compose.compose_page(
        eyebrow="The Long Walk",
        line="He walked the fields before first light.",
        illo=green_placeholder(),
        blend="green",
        template=tmpl,
    )
    p2 = os.path.join(OUT, "test_green.png")
    green.save(p2)
    print("wrote", p2)


if __name__ == "__main__":
    main()
