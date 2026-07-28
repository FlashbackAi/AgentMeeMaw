"""Storybook prototype: real legacy -> opening + 6-9 word beats + conclusion,
each a watercolor page composited into the template.

  python scripts/storybook_prototype/generate.py                 # full run
  python scripts/storybook_prototype/generate.py --blend green
  python scripts/storybook_prototype/generate.py --reuse-story    # cache the Sonnet pass
  python scripts/storybook_prototype/generate.py --no-art         # preview text+layout only

Caches the Sonnet book + every raw illustration so re-running to tune the
compositor or a blend costs no LLM/image calls. The character reference is
reused across runs (stable figure) unless --fresh-ref. Prod DB is READ-ONLY.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image  # noqa: E402

from sb import compose, config, data, story  # noqa: E402


def _wc(s: str) -> int:
    return len(s.split())


def _placeholder_art() -> Image.Image:
    ref = Image.open(config.REFERENCE_PATH).convert("RGB")
    w, h = ref.size
    return ref.crop((int(0.04 * w), int(0.49 * h), int(0.96 * w), int(0.985 * h)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--person-id", default=config.DEFAULT_PERSON_ID)
    ap.add_argument("--pages", type=int, default=15, help="memory pages (excl. framing)")
    ap.add_argument("--limit", type=int, default=80, help="candidate moments pulled")
    ap.add_argument("--blend", choices=["cream", "green"], default="cream")
    ap.add_argument("--model", default=config.GEMINI_MODEL)
    ap.add_argument("--reuse-story", action="store_true")
    ap.add_argument("--reuse-art", action="store_true")
    ap.add_argument("--no-art", action="store_true", help="placeholder art (no Gemini)")
    ap.add_argument("--no-ref", action="store_true", help="skip character reference")
    ap.add_argument("--fresh-ref", action="store_true", help="regen the character ref")
    ap.add_argument("--prime-photo", default=None,
                    help="user prime-years photo for the opener portrait "
                         "(defaults to reference/prime_photo.*)")
    ap.add_argument("--deage", action="store_true",
                    help="de-age the prime photo to his prime years")
    ap.add_argument("--video", action="store_true",
                    help="also render an animated MP4 (layer reveal + transitions)")
    ap.add_argument("--transition", choices=["bleed", "turn", "dip"],
                    default="bleed", help="inter-page transition style")
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    run_dir = os.path.join(config.OUT_DIR, args.person_id[:8])
    raw_dir = os.path.join(run_dir, "raw")
    pages_dir = os.path.join(run_dir, f"pages_{args.blend}")
    for d in (raw_dir, pages_dir):
        os.makedirs(d, exist_ok=True)
    book_path = os.path.join(run_dir, "book.json")

    # 1) read-only DB
    subject, moments = data.fetch_subject_and_moments(args.person_id, limit=args.limit)
    print(f"subject: {subject.name} ({subject.relationship}) | moments: {len(moments)}")

    # 2) story (Sonnet) -- cached
    if args.reuse_story and os.path.exists(book_path):
        book = story.load_book(book_path)
        print(f"loaded cached book ({len(book.beats)} beats)")
    else:
        print("composing book with Sonnet ...")
        book = story.compose_book(subject, moments, n_pages=args.pages)
        story.save_book(book, book_path)

    # opening -> beats -> closing
    slides = [book.opening] + list(book.beats) + [book.closing]
    print(f"\ncover_title: {book.cover_title!r}")
    print(f"  OPEN  | {book.opening.line}")
    for i, b in enumerate(book.beats, 1):
        flag = "" if 8 <= _wc(b.line) <= 10 else f"  <-- {_wc(b.line)}w"
        print(f"  {i:2d}.   {b.line}{flag}")
    print(f"  CLOSE | {book.closing.line}\n")

    # 3) art
    artist = None
    reference = None
    prime_photo = None
    if not args.no_art:
        from sb.art import Artist
        artist = Artist(model=args.model)
        if not args.no_ref:
            ref_path = os.path.join(raw_dir, "_character_ref.png")
            if os.path.exists(ref_path) and not args.fresh_ref:
                reference = Image.open(ref_path).convert("RGB")
            else:
                print("generating character reference ...")
                reference = artist.character_reference(
                    subject, subject.scene_subject_context)
                reference.save(ref_path)
        photo_path = args.prime_photo or config.prime_photo_path()
        if photo_path and os.path.exists(photo_path):
            prime_photo = Image.open(photo_path).convert("RGB")
            print(f"opener portrait from prime photo: {os.path.basename(photo_path)}")

    # 4) compose pages (headers removed everywhere -> eyebrow="")
    vid = None
    if args.video:
        from sb import video as vid
    template = compose.load_template()
    template_rgba = template.convert("RGBA")
    pages: list[Image.Image] = []
    video_pages: list = []
    n_slides = len(slides)
    for i, beat in enumerate(slides, 1):
        is_opener = i == 1
        role = "opener" if is_opener else ("closing" if i == n_slides else "beat")
        layout = config.layout_for(role, i - 2)  # beat index 0-based
        raw_path = os.path.join(raw_dir, f"page_{i:02d}_{args.blend}.png")
        if args.no_art:
            illo = _placeholder_art()
        elif is_opener and prime_photo is not None:
            # Opener = watercolour portrait painted FROM the uploaded photo.
            # Always regenerated (the photo is the new input); 1 call.
            print("  painting opener portrait from prime photo ...")
            illo = artist.portrait_from_photo(
                prime_photo, subject, subject.scene_subject_context,
                deage=args.deage, blend=args.blend)
            illo.save(raw_path)
        elif args.reuse_art and os.path.exists(raw_path):
            illo = Image.open(raw_path).convert("RGB")
        else:
            print(f"  illustrating page {i:02d} ...")
            illo = artist.illustrate(
                beat.art_direction, subject.scene_subject_context,
                args.blend, reference=reference,
            )
            illo.save(raw_path)
        illo_layer = compose.illustration_layer(
            template, illo, args.blend, layout.art_box, layout.art_valign)
        txt_layer = compose.text_layer(template, "", beat.line, layout.text_box)
        page = Image.alpha_composite(
            Image.alpha_composite(template_rgba, illo_layer), txt_layer
        ).convert("RGB")
        page.save(os.path.join(pages_dir, f"page_{i:02d}.png"))
        pages.append(page)
        if vid is not None:
            video_pages.append(vid.Page(template, illo_layer, txt_layer))

    # 5) PDF
    pdf_path = os.path.join(run_dir, f"storybook_{args.blend}.pdf")
    if pages:
        pages[0].save(pdf_path, save_all=True, append_images=pages[1:], resolution=150.0)
    print(f"\nwrote {len(pages)} pages -> {pages_dir}")
    print(f"PDF -> {pdf_path}")

    # 6) video
    if vid is not None and video_pages:
        mp4 = os.path.join(run_dir, f"storybook_{args.blend}.mp4")
        print(f"rendering video ({args.transition} transitions) ...")
        vid.render_video(video_pages, mp4, fps=args.fps, transition=args.transition)
        print(f"MP4 -> {mp4}")


if __name__ == "__main__":
    main()
