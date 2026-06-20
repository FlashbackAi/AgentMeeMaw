# Storybook prototype (throwaway spike)

Perfect the watercolor-template look — an AI illustration + a 6–8 word
emotional line composited into a fixed page template — **before** porting the
render path to the Node legacy repo. Not part of the shipped service; its deps
are NOT in the service `pyproject.toml`.

```
sb/config.py   layout fractions, colours, fonts, model ids, env loading
sb/data.py     READ-ONLY prod reads (person + qualifying moments)
sb/story.py    Sonnet: pick/order 15 beats -> {eyebrow, 6-8 word line, art_direction}
sb/art.py      Gemini (gemini-3.1-flash-image): character ref + per-beat illustration
sb/compose.py  Pillow: template + text + illustration -> page (cream | green blend)
generate.py    CLI orchestrator (caches story + raw art; writes PNGs + PDF)
test_compose.py  compositor smoke test (no Gemini / no DB)
```

## Setup
```
python -m pip install -r scripts/storybook_prototype/requirements.txt
```
- Reads keys from repo-root `.env.production` (`DATABASE_URL`,
  `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`). Loaded with `override=True` because a
  stale `DATABASE_URL=localhost:15432` lives in the shell environment.
- Fonts (Playfair Display + EB Garamond) are bundled in `fonts/`.
- **The prod DB is touched read-only** — only `SELECT`s, never a write.

## Run
```
# full run (default person = Chandraiah, cream blend)
python scripts/storybook_prototype/generate.py

python scripts/storybook_prototype/generate.py --blend green   # chroma-key blend
python scripts/storybook_prototype/generate.py --reuse-story    # skip the Sonnet call
python scripts/storybook_prototype/generate.py --reuse-art      # skip Gemini, reuse raw/
python scripts/storybook_prototype/generate.py --no-art         # text+layout only (no Gemini)
```
Output: `out/<person8>/pages_<blend>/page_NN.png` + `storybook_<blend>.pdf`.
Cached: `out/<person8>/book.json` and `out/<person8>/raw/*.png`.

## Blend modes
- **cream** (default) — Gemini paints a vignette on warm paper; the compositor
  tone-matches its paper to the template and feathers the edges so it melts in.
- **green** — Gemini paints the subject on chroma-green; the compositor keys the
  green to alpha so the subject floats on the template paper. Mirrors the Node
  `templateRenderer.js` punch-through, the safer bet for the port.

## Port boundary (later)
Python keeps owning the **text** (story arc, 6–8 word lines, art_direction).
Node owns the **render** (templates + compositing), as today. This spike's
`compose.py` is the reference for the new template/blend Node must reproduce.

## Out of scope here (noted for the Node port)
- Unlock gating at **92%** (production tribute meter).
- The final deliverable is a **video** (animated layers + page transitions),
  not the storybook/PDF — this spike only nails the still-page look.
