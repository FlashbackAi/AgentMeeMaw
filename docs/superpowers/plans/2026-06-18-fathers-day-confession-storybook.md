# Father's Day Confession Storybook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the existing Father's Day tribute skin so its storybook is a first-person "confession" about the father (voice = "he", to the world), opens on a stylized prime-years cover portrait derived from his uploaded photo, carries a story-gated hero line, uses two-sentence captions, and surfaces the brief's question bank as the theme's archetype questions.

**Architecture:** All changes are scoped to the `fathers_day_2026` campaign skin layered on the shared tribute theme — neutral tributes and every other theme are untouched. The assembler gains a campaign-conditional confession voice + two new optional fields (`defining_phrase`, `hero_line`); the storybook cover context gains a reference-image + de-age path; the cover (and only the cover) relaxes the no-likeness negative for the consented subject's own photo. Questions remain ephemeral priors that seed conversation → moments → storybook (invariant #22); nothing is generated directly from answers.

**Tech Stack:** Python, FastAPI, psycopg, structlog; OpenAI `gpt-5.1` (small) / Anthropic `claude-sonnet-4-6` (big) via `flashback.llm.interface.call_with_tool`; pytest.

## Global Constraints

- **Voice change is scoped to the FD campaign only.** The neutral tribute and all other paths keep the shipped "letter to you" voice. Selection is via `Campaign.confession_voice`.
- **Likeness exception is scoped to the storybook COVER only.** Page/scene art keeps the full `SCENE_NEGATIVE_PROMPT` (including the deepfake-likeness ban, CLAUDE.md §1/§3). Only the cover, built from the contributor-uploaded photo of the consented subject, uses the relaxed `COVER_PORTRAIT_NEGATIVE_PROMPT`.
- **Invariant #22 holds:** archetype answers are ephemeral priors that seed the opener; the storybook is assembled from extracted moments, never from answers directly.
- **Captions:** one or two short sentences, max impact (brief §2.6). Hard cap stays ≤ 600 chars in the tool schema; the prompt enforces brevity.
- **The agent never touches S3 or URL columns** (CLAUDE.md §3). The cover reference is passed as an S3 *key* string in the request and written only into `latest_generation_context`; Node does the rendering.
- **Commits** end with: `Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>` (never the Opus trailer).
- Tests run against the test DB on `:15432` (containers must be `docker start`ed) — but every task here is unit-level and needs no DB.

---

### Task 1: Campaign confession-voice + de-age flags

**Files:**
- Modify: `src/flashback/tribute/campaigns.py`
- Test: `tests/tribute/test_campaigns.py`

**Interfaces:**
- Produces: `Campaign.confession_voice: bool`, `Campaign.deage_cover: bool` (both default `False`; `fathers_day_2026` sets both `True`). `NEUTRAL_CAMPAIGN` keeps both `False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tribute/test_campaigns.py  (add)
from flashback.tribute.campaigns import NEUTRAL_CAMPAIGN, resolve_campaign


def test_fathers_day_uses_confession_voice_and_deage():
    c = resolve_campaign("fathers_day_2026")
    assert c.confession_voice is True
    assert c.deage_cover is True


def test_neutral_campaign_keeps_letter_voice():
    assert NEUTRAL_CAMPAIGN.confession_voice is False
    assert NEUTRAL_CAMPAIGN.deage_cover is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tribute/test_campaigns.py -v`
Expected: FAIL — `AttributeError: 'Campaign' object has no attribute 'confession_voice'`.

- [ ] **Step 3: Implement**

In `Campaign` dataclass add two fields after `archetype_extra_context`:

```python
    archetype_extra_context: str
    confession_voice: bool = False
    deage_cover: bool = False
    video_target_seconds: int = VIDEO_TARGET_SECONDS
```

(Keep existing fields; give the new ones defaults so `NEUTRAL_CAMPAIGN` and any other campaign need no change.) Then on the `fathers_day_2026` entry add:

```python
        archetype_extra_context=(...unchanged...),
        confession_voice=True,
        deage_cover=True,
        video_target_seconds=45,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tribute/test_campaigns.py -v`
Expected: PASS (all campaign tests green).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/tribute/campaigns.py tests/tribute/test_campaigns.py
git commit -m "feat(tribute): FD campaign confession-voice + de-age flags"
```

---

### Task 2: Father's Day archetype question bank

**Files:**
- Modify: `src/flashback/tribute/theme.py` (add the fixed bank + a builder)
- Test: `tests/tribute/test_fd_archetype_bank.py` (create)

**Interfaces:**
- Produces: `FATHERS_DAY_ARCHETYPE_BANK: list[tuple[str, list[str]]]` and `build_fathers_day_archetype_questions() -> list[ArchetypeQuestion]` returning the brief's questions as `ArchetypeQuestion` objects (stable `question_id` `q1..qN`, `option_id` `qK_oM`), reusing `flashback.themes.archetype_llm.ArchetypeQuestion`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tribute/test_fd_archetype_bank.py
from flashback.tribute.theme import (
    FATHERS_DAY_ARCHETYPE_BANK,
    build_fathers_day_archetype_questions,
)


def test_bank_is_nonempty_and_each_has_options():
    assert len(FATHERS_DAY_ARCHETYPE_BANK) >= 6
    for text, options in FATHERS_DAY_ARCHETYPE_BANK:
        assert text.strip()
        assert len(options) >= 2


def test_builder_produces_stable_ids_and_valid_options():
    qs = build_fathers_day_archetype_questions()
    assert len(qs) == len(FATHERS_DAY_ARCHETYPE_BANK)
    assert qs[0].question_id == "q1"
    first_opt = qs[0].options[0]
    assert first_opt["option_id"] == "q1_o1"
    assert first_opt["label"]
    # every question keeps >= 2 options after cleaning
    assert all(len(q.options) >= 2 for q in qs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tribute/test_fd_archetype_bank.py -v`
Expected: FAIL — `ImportError: cannot import name 'FATHERS_DAY_ARCHETYPE_BANK'`.

- [ ] **Step 3: Implement**

Append to `src/flashback/tribute/theme.py`:

```python
from flashback.themes.archetype_llm import ArchetypeQuestion

# The Father's Day theme's questions (docs/Fathers_Day_Storybook_Brief_v2.md
# §3). These are ephemeral priors (invariant #22): they seed the opener; the
# storybook is assembled from extracted moments, never from these answers.
# (text, [option labels]) — the deeper free-text-first beats still ship as MC
# with starter chips so the card surface is uniform; free-text + Skip are
# always available on the card.
FATHERS_DAY_ARCHETYPE_BANK: list[tuple[str, list[str]]] = [
    ("What was your father's main work or trade?",
     ["A trade / manual work", "A salaried job", "His own business", "Farming / land"]),
    ("Was his income steady, or did some months stretch thin?",
     ["Steady wage", "Up and down", "Often tight", "We never lacked"]),
    ("Was he raised by both parents, or did he lose someone early?",
     ["Both, all through", "Lost his father young", "Lost his mother young", "Raised by others"]),
    ("What kind of clothes did you wear growing up?",
     ["Branded / new", "Hand-me-downs", "Simple but clean", "The best they could afford"]),
    ("What did your school or education look like?",
     ["Private / English-medium", "Government school", "Convent", "Far from home"]),
    ("Did he ever uproot his life or give up something he'd built, for your sake?",
     ["Sold a home", "Left his land", "Changed careers", "Moved everything"]),
    ("Did he have money he could have spent on himself but didn't?",
     ["Yes — always chose us", "Sometimes", "He was genuinely stretched", "Not sure"]),
    ("What's the one thing you've never said to him out loud?",
     ["I love you", "I'm proud of you", "Thank you", "You're my hero"]),
]


def build_fathers_day_archetype_questions() -> list[ArchetypeQuestion]:
    """The fixed FD bank as ArchetypeQuestion objects (no LLM call)."""
    out: list[ArchetypeQuestion] = []
    for q_idx, (text, labels) in enumerate(FATHERS_DAY_ARCHETYPE_BANK, start=1):
        options = [
            {"option_id": f"q{q_idx}_o{o_idx}", "label": label}
            for o_idx, label in enumerate(labels, start=1)
            if label.strip()
        ]
        if len(options) < 2:
            continue
        out.append(
            ArchetypeQuestion(
                question_id=f"q{q_idx}",
                text=text,
                options=options,
            )
        )
    return out
```

(If a circular import arises — `archetype_llm` importing from `theme` — move the import inside the function. `theme.py` currently imports nothing from `themes`, and `archetype_llm` does not import `theme`, so a top-level import is fine; verify with the test run.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tribute/test_fd_archetype_bank.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/tribute/theme.py tests/tribute/test_fd_archetype_bank.py
git commit -m "feat(tribute): fixed Father's Day archetype question bank"
```

---

### Task 3: Serve the FD bank from unlock_prepare during the campaign window

**Files:**
- Modify: `src/flashback/http/routes/themes.py:178-220` (the archetype-generation branch)
- Test: `tests/http/test_themes_unlock_prepare_fd.py` (create)

**Interfaces:**
- Consumes: `build_fathers_day_archetype_questions` (Task 2), `flashback.tribute.campaigns.active_featured_campaign`.
- Behavior: when `theme.kind == "tribute"` AND `active_featured_campaign(today)` has `confession_voice` true AND the theme has no cached `archetype_questions`, use the fixed FD bank (persist + return it) instead of calling the LLM. Outside the window, behavior is unchanged (LLM-generated 6-8).

- [ ] **Step 1: Write the failing test**

```python
# tests/http/test_themes_unlock_prepare_fd.py
from datetime import date

from flashback.tribute.campaigns import active_featured_campaign
from flashback.tribute.theme import build_fathers_day_archetype_questions


def test_fd_window_is_active_in_june_2026():
    # Sanity: the campaign window covers the launch date the route keys on.
    assert active_featured_campaign(date(2026, 6, 18)) is not None


def test_fd_bank_questions_have_subject_agnostic_text():
    qs = build_fathers_day_archetype_questions()
    # The bank must not be empty and must be usable as MC archetype questions.
    assert qs and all(q.text and len(q.options) >= 2 for q in qs)
```

(Full HTTP-route coverage needs the DB harness; this task's unit test pins the seam inputs. The route change is verified by the existing `tests/http/test_tribute_generate.py` DB suite + manual unlock_prepare against local dev.)

- [ ] **Step 2: Run test to verify it fails / passes**

Run: `pytest tests/http/test_themes_unlock_prepare_fd.py -v`
Expected: PASS immediately (these assert on Task 2 + existing campaign code). If `active_featured_campaign(date(2026,6,18))` returns None, the campaign window in `campaigns.py` is wrong — fix the window, not the test.

- [ ] **Step 3: Implement the route seam**

In `themes.py`, add the import near the other tribute imports:

```python
from flashback.tribute.campaigns import active_featured_campaign
from flashback.tribute.theme import (
    TRIBUTE_ARCHETYPE_MAX,
    TRIBUTE_ARCHETYPE_MIN,
    build_fathers_day_archetype_questions,
)
```

Replace the `else:` body that currently always calls `generate_archetype_questions` so the FD fixed bank short-circuits first:

```python
    else:
        # Father's Day skin: serve the fixed authored bank (no LLM) during the
        # campaign window. Ephemeral priors only (invariant #22).
        from datetime import datetime, timezone

        fd_campaign = active_featured_campaign(
            datetime.now(timezone.utc).date()
        )
        if theme.kind == "tribute" and fd_campaign and fd_campaign.confession_voice:
            questions = build_fathers_day_archetype_questions()
        else:
            description = theme.description
            if not description and theme.kind == "universal":
                universal = get_universal_theme(theme.slug)
                description = (
                    universal.description
                    if universal is not None
                    else theme.display_name
                )
            if not description:
                description = theme.display_name
            if theme.kind == "tribute":
                q_min, q_max = TRIBUTE_ARCHETYPE_MIN, TRIBUTE_ARCHETYPE_MAX
            else:
                q_min, q_max = 3, 4
            questions = await generate_archetype_questions(
                settings=cfg,
                theme_slug=theme.slug,
                theme_display_name=theme.display_name,
                theme_description=description,
                theme_kind=theme.kind,
                subject_name=subject_name,
                subject_relationship=None,
                context_moments=None,
                min_questions=q_min,
                max_questions=q_max,
            )

        if questions:
            payload = [q.to_payload() for q in questions]
            async with db_pool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        await update_archetype_questions_async(
                            cur,
                            theme_id=str(theme_id),
                            questions=payload,
                        )
            generated_this_call = True
```

- [ ] **Step 4: Run the themes + tribute suites**

Run: `pytest tests/http/test_themes_unlock_prepare_fd.py tests/tribute -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/http/routes/themes.py tests/http/test_themes_unlock_prepare_fd.py
git commit -m "feat(tribute): serve fixed FD archetype bank from unlock_prepare in-window"
```

---

### Task 4: Confession voice + defining_phrase + hero_line in the assembler

**Files:**
- Modify: `src/flashback/tribute/assembly.py`
- Test: `tests/tribute/test_assembly.py`

**Interfaces:**
- Produces: `assemble_tribute_script(..., confession: bool = False)`; `TributeScript` gains `defining_phrase: str = ""` and `hero_line: str = ""`. When `confession=True`, the system prompt is the "to-the-world / he" variant; the tool schema accepts `defining_phrase` (always) + `hero_line` (story-gated). When `confession=False`, behavior + output are byte-for-byte the shipped letter voice.

- [ ] **Step 1: Write the failing tests**

```python
# tests/tribute/test_assembly.py  (add)
import asyncio

from flashback.tribute.assembly import TributeScript, assemble_tribute_script


def test_tribute_script_has_defining_phrase_and_hero_line_fields():
    s = TributeScript(scenes=[], opening_caption="", closing_caption="",
                       message_text="")
    assert s.defining_phrase == ""
    assert s.hero_line == ""


def test_confession_voice_falls_back_without_settings():
    # settings=None → fallback script, never raises, regardless of confession.
    script = asyncio.run(
        assemble_tribute_script(
            settings=None,
            candidates=[{"id": "m1", "title": "A morning"}],
            message_text="",
            person_name="Dad",
            person_relationship="father",
            max_scenes=3,
            confession=True,
        )
    )
    assert script.scenes  # fell back to chronological
    assert script.defining_phrase == ""  # fallback emits no cover lines
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tribute/test_assembly.py -v`
Expected: FAIL — `TypeError: __init__() ... 'defining_phrase'` / `unexpected keyword argument 'confession'`.

- [ ] **Step 3: Implement**

In `assembly.py`:

1. Extend the dataclass:

```python
@dataclass(frozen=True)
class TributeScript:
    scenes: list[Scene]
    opening_caption: str
    closing_caption: str
    message_text: str
    cover_title: str = ""
    cover_prompt: str = ""
    defining_phrase: str = ""   # cover line: who he IS at core (brief §2.3)
    hero_line: str = ""         # story-gated "fork in the road" (brief §2.4)
```

2. Add the confession system prompt as a module constant `_CONFESSION_SYSTEM` — first person, about him, addressed to the world, father = "he"; the single direct-address spike is allowed only on the climax/message page; two-sentence max-impact captions; plus instructions for `defining_phrase` (always) and `hero_line` (ONLY when the candidate scenes reveal a concrete given-up path — sold home, left land, dropped degree, money-not-spent — else omit; written fresh, never a template). Keep the `{max_scenes}` placeholder.

```python
_CONFESSION_SYSTEM = """\
You compose a Father's Day "confession" storybook a contributor is giving to
the world ABOUT their father. You receive candidate scenes (id + memory), the
subject (with relationship), and -- when present -- the contributor's closing
message.

Voice -- first person, to the world, the father is "he":
The narrator is the contributor speaking to a friend ABOUT their father. Use
"I" for the contributor and "he"/"him" for the father on every page, the
opening, and the closing. NEVER write it as a letter to the father ("you did
...") and NEVER third-person-detached ("Vinay's father..."). The ONLY place
you may turn and address him directly as "you" is the single climax line (the
contributor message page, or the last scene when no message is present) -- one
spike, then the closing pulls back to "he".

Captions -- two sentences, maximum impact:
- One or two SHORT sentences per scene. Aphoristic weight over description.
- Set the thing he had against the thing he gave, and stop. Cut every word
  that merely explains or pads. Understated beats heightened.
- Concrete and specific; never invent facts beyond the scene's own memory.

defining_phrase (ALWAYS): one line for who he IS at his core, stripped of all
the sacrifice -- the man, not his cost. Goes on the cover. <= 14 words.

hero_line (STORY-GATED): a single "fork in the road" line -- what he could have
been versus what he chose. Emit it ONLY when the candidate scenes reveal a
concrete given-up alternative (a sold home, abandoned land, a dropped degree, a
trade walked away from, money he had but didn't spend on himself). Write it
fresh in the narrator's voice, grounded in THIS father's specifics (e.g. "He
could have owned half that valley. He traded it for a report card."). If the
scenes do NOT clearly show a given-up path, leave hero_line empty. Never force
it; never use a generic template.

[...keep the existing "Produce:" scene/accent/pull_quote/layout/opening/closing
/cover_title/cover_prompt instructions, with captions retuned to the two-
sentence rule above and cover_prompt still a non-portrait establishing scene...]

Call the `assemble` tool exactly once.
"""
```

3. Extend `_ASSEMBLY_TOOL.input_schema["properties"]` with two optional fields (do NOT add them to `required`):

```python
            "defining_phrase": {"type": "string", "maxLength": 120},
            "hero_line": {"type": "string", "maxLength": 160},
```

4. Change the signature + system-prompt selection:

```python
async def assemble_tribute_script(
    *,
    settings,
    candidates,
    message_text,
    person_name,
    person_relationship,
    max_scenes,
    confession: bool = False,
) -> TributeScript:
    ...
    system = _CONFESSION_SYSTEM if confession else _ASSEMBLY_SYSTEM
    ...
    args = await call_with_tool(
        ...,
        system_prompt=system.replace("{max_scenes}", str(max_scenes)),
        ...,
    )
```

5. In the success path, populate the new fields (empty string when absent):

```python
    return TributeScript(
        scenes=scenes,
        opening_caption=(args.get("opening_caption") or "").strip(),
        closing_caption=(args.get("closing_caption") or "").strip(),
        message_text=message_text,
        cover_title=(args.get("cover_title") or "").strip(),
        cover_prompt=(args.get("cover_prompt") or "").strip(),
        defining_phrase=(args.get("defining_phrase") or "").strip(),
        hero_line=(args.get("hero_line") or "").strip(),
    )
```

(`_fallback_script` is unchanged — it leaves both new fields at their `""` default.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tribute/test_assembly.py -v`
Expected: PASS (including the shipped letter-voice tests, unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/tribute/assembly.py tests/tribute/test_assembly.py
git commit -m "feat(tribute): confession voice + defining_phrase + story-gated hero_line"
```

---

### Task 5: Cover portrait from the uploaded photo (reference + de-age + relaxed negative)

**Files:**
- Modify: `src/flashback/artifacts/compose.py` (add `COVER_PORTRAIT_NEGATIVE_PROMPT`)
- Modify: `src/flashback/tribute/artifact_context.py` (`build_storybook_context`)
- Test: `tests/tribute/test_artifact_context.py`

**Interfaces:**
- Consumes: `TributeScript.defining_phrase`, `TributeScript.hero_line` (Task 4).
- Produces: `build_storybook_context(..., cover_reference_s3_key: str | None = None, deage_cover: bool = False, defining_phrase: str | None = None, hero_line: str | None = None)`. When `cover_reference_s3_key` is set, the cover dict carries `reference_s3_key`, a portrait `prompt` (subject in his prime years), `negative = COVER_PORTRAIT_NEGATIVE_PROMPT`, and — when `deage_cover` — a de-age instruction. `defining_phrase` becomes the cover `caption` (falling back to `cover_title`/opening); `hero_line` is added as `cover["hero_line"]` only when non-empty.

- [ ] **Step 1: Write the failing tests**

```python
# tests/tribute/test_artifact_context.py  (add)
from flashback.artifacts.compose import (
    COVER_PORTRAIT_NEGATIVE_PROMPT,
    SCENE_NEGATIVE_PROMPT,
)
from flashback.tribute.artifact_context import build_storybook_context
from flashback.tribute.assembly import Scene, TributeScript


def _script():
    return TributeScript(
        scenes=[Scene(moment_id="m1", caption="He sold the house.")],
        opening_caption="For him.",
        closing_caption="The best I could ask for.",
        message_text="I love you, Dad.",
        cover_title="A Quiet Builder",
        cover_prompt="a concrete house rising in a village at dawn",
        defining_phrase="A man who spent himself so we'd never have to.",
        hero_line="He could have owned the valley. He chose a report card.",
    )


def test_cover_uses_reference_photo_and_relaxed_negative_and_lines():
    ctx = build_storybook_context(
        script=_script(),
        moments_by_id={"m1": {"narrative": "He sold the house."}},
        preset="default",
        max_pages=9,
        cover_reference_s3_key="uploads/p/prime.jpg",
        deage_cover=True,
        defining_phrase="A man who spent himself so we'd never have to.",
        hero_line="He could have owned the valley. He chose a report card.",
    )
    cover = ctx["cover"]
    assert cover["reference_s3_key"] == "uploads/p/prime.jpg"
    assert cover["caption"] == "A man who spent himself so we'd never have to."
    assert cover["hero_line"] == "He could have owned the valley. He chose a report card."
    assert cover["negative"] == COVER_PORTRAIT_NEGATIVE_PROMPT
    assert "deepfake likeness" not in COVER_PORTRAIT_NEGATIVE_PROMPT
    assert "prime" in cover["prompt"].lower()
    assert "younger" in cover["prompt"].lower() or "de-age" in cover["prompt"].lower()
    # Page art is unaffected — still the full scene negative incl. the ban.
    assert ctx["pages"][0]["negative"] == SCENE_NEGATIVE_PROMPT
    assert "deepfake likeness" in SCENE_NEGATIVE_PROMPT


def test_cover_without_reference_keeps_establishing_scene_behavior():
    ctx = build_storybook_context(
        script=_script(),
        moments_by_id={"m1": {"narrative": "He sold the house."}},
        preset="default",
        max_pages=9,
    )
    cover = ctx["cover"]
    assert "reference_s3_key" not in cover
    # No defining_phrase arg, but the script carries one → it wins over cover_title.
    assert cover["caption"] == "A man who spent himself so we'd never have to."
    # cover_prompt present → still composed with the scene negative (no portrait).
    assert cover["negative"] == SCENE_NEGATIVE_PROMPT


def test_cover_caption_falls_back_to_title_when_no_defining_phrase():
    script = TributeScript(
        scenes=[Scene(moment_id="m1", caption="He sold the house.")],
        opening_caption="For him.",
        closing_caption="",
        message_text="",
        cover_title="A Quiet Builder",
    )
    ctx = build_storybook_context(
        script=script,
        moments_by_id={"m1": {"narrative": "He sold the house."}},
        preset="default",
        max_pages=9,
    )
    assert ctx["cover"]["caption"] == "A Quiet Builder"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tribute/test_artifact_context.py -v`
Expected: FAIL — `ImportError: cannot import name 'COVER_PORTRAIT_NEGATIVE_PROMPT'`.

- [ ] **Step 3: Implement**

In `compose.py` add below `SCENE_NEGATIVE_PROMPT`:

```python
# Cover-only negative for the tribute prime-years portrait. The contributor
# uploads a photo of the consented subject and asks us to stylize it, so the
# no-likeness / no-portrait bans are intentionally DROPPED here (scoped
# exception to CLAUDE.md §1/§3 — cover only; page art keeps SCENE_NEGATIVE_PROMPT).
COVER_PORTRAIT_NEGATIVE_PROMPT = (
    "flat cartoon shading, cel-shaded anime, Pixar 3D look, exaggerated "
    "cartoon proportions, plastic surfaces, hyperrealistic photograph, harsh "
    "digital sharpening, text, watermark, signature, blurry, low quality, "
    "distorted, uncanny"
)
```

In `artifact_context.py`, add two prompt fragments near the top:

```python
COVER_PRIME_PORTRAIT = (
    "a dignified painterly PORTRAIT of the subject in his prime years (around "
    "his early twenties), warm magazine-cover lighting, "
    + STORYBOOK_PORTRAIT_ORIENTATION
)
COVER_DEAGE_INSTRUCTION = (
    "render him noticeably YOUNGER than the reference photo — restore his "
    "prime-years appearance (smooth skin, dark hair, upright vigor); keep his "
    "recognizable features and bone structure"
)
```

Change `build_storybook_context`'s signature to add the four params (after `cover_subtitle`):

```python
def build_storybook_context(
    *,
    script,
    moments_by_id,
    preset,
    max_pages,
    ground_truth_context=None,
    cover_subtitle=None,
    cover_reference_s3_key: str | None = None,
    deage_cover: bool = False,
    defining_phrase: str | None = None,
    hero_line: str | None = None,
):
```

Replace the cover-building block. Caption precedence: explicit `defining_phrase` arg → `script.defining_phrase` → `cover_title` → opening line.

```python
    cover_caption = (
        (defining_phrase or "").strip()
        or (script.defining_phrase or "").strip()
        or (script.cover_title or "").strip()
        or (script.opening_caption or "").strip()
    )
    cover: dict[str, Any] = {
        "caption": cover_caption,
        "subtitle": (cover_subtitle or "").strip(),
        "style_preset": preset,
    }
    hl = (hero_line if hero_line is not None else script.hero_line or "").strip()
    if hl:
        cover["hero_line"] = hl

    ref = (cover_reference_s3_key or "").strip()
    if ref:
        # Prime-years PORTRAIT from the contributor's uploaded photo.
        instructions = [COVER_PRIME_PORTRAIT]
        if deage_cover:
            instructions.append(COVER_DEAGE_INSTRUCTION)
        cover["reference_s3_key"] = ref
        cover["prompt"] = compose_scene_prompt(
            base_prompt=COVER_PRIME_PORTRAIT,
            instructions=(COVER_DEAGE_INSTRUCTION if deage_cover else None),
            preset=preset,
            ground_truth_context=ground_truth_context,
        )
        cover["negative"] = COVER_PORTRAIT_NEGATIVE_PROMPT
    else:
        cover_prompt = (script.cover_prompt or "").strip()
        if cover_prompt:
            cover["prompt"] = compose_scene_prompt(
                base_prompt=cover_prompt,
                instructions=STORYBOOK_PORTRAIT_ORIENTATION,
                preset=preset,
                ground_truth_context=ground_truth_context,
            )
            cover["negative"] = SCENE_NEGATIVE_PROMPT
```

Add the `COVER_PORTRAIT_NEGATIVE_PROMPT` import to the existing compose import line:

```python
from flashback.artifacts.compose import (
    SCENE_NEGATIVE_PROMPT,
    COVER_PORTRAIT_NEGATIVE_PROMPT,
    compose_scene_prompt,
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tribute/test_artifact_context.py -v`
Expected: PASS (existing context tests still green — defaults preserve old behavior).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/artifacts/compose.py src/flashback/tribute/artifact_context.py tests/tribute/test_artifact_context.py
git commit -m "feat(tribute): prime-years cover portrait from uploaded photo (cover-only likeness)"
```

---

### Task 6: Wire campaign + prime photo through the generate route

**Files:**
- Modify: `src/flashback/http/models.py:76-83` (`TributeGenerateRequest`)
- Modify: `src/flashback/http/routes/tributes.py`
- Test: `tests/http/test_tribute_generate.py` (add a unit-level request-model test; the DB-backed flow tests already exist)

**Interfaces:**
- Consumes: `Campaign.confession_voice`, `Campaign.deage_cover` (Task 1); `assemble_tribute_script(..., confession=...)` (Task 4); `build_storybook_context(..., cover_reference_s3_key=, deage_cover=, defining_phrase=, hero_line=)` (Task 5).
- Produces: `TributeGenerateRequest.prime_photo_s3_key: str | None = None`. The route resolves the campaign for BOTH artifact kinds, passes `confession=campaign.confession_voice` to the assembler, and passes the prime photo + de-age + cover lines into the storybook context.

- [ ] **Step 1: Write the failing test**

```python
# tests/http/test_tribute_generate.py  (add)
from flashback.http.models import TributeGenerateRequest


def test_request_accepts_prime_photo_key():
    req = TributeGenerateRequest(
        person_id="00000000-0000-0000-0000-000000000001",
        artifact_kind="storybook",
        campaign="fathers_day_2026",
        prime_photo_s3_key="uploads/p/prime.jpg",
    )
    assert req.prime_photo_s3_key == "uploads/p/prime.jpg"


def test_request_prime_photo_defaults_none():
    req = TributeGenerateRequest(
        person_id="00000000-0000-0000-0000-000000000001",
    )
    assert req.prime_photo_s3_key is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/http/test_tribute_generate.py::test_request_accepts_prime_photo_key -v`
Expected: FAIL — `extra="forbid"` rejects `prime_photo_s3_key`.

- [ ] **Step 3: Implement**

In `models.py`, add the field to `TributeGenerateRequest`:

```python
    person_id: UUID
    artifact_kind: Literal["tribute_video", "storybook"] = "tribute_video"
    preset: str | None = None
    campaign: str | None = None
    prime_photo_s3_key: str | None = None
```

In `tributes.py`, resolve the campaign once before assembly and pass `confession`:

```python
    campaign = resolve_campaign(body.campaign)
    script = await assemble_tribute_script(
        settings=cfg,
        candidates=candidates,
        message_text=tribute["message_text"] or "",
        person_name=tribute["person_name"] or "",
        person_relationship=tribute["person_relationship"],
        max_scenes=max_scenes,
        confession=campaign.confession_voice,
    )
```

(Delete the now-redundant `campaign = resolve_campaign(body.campaign)` inside the `tribute_video` branch — it's resolved above.)

In the storybook branch, thread the cover args:

```python
    else:
        context = build_storybook_context(
            script=script,
            moments_by_id=moments_by_id,
            preset=preset_slug,
            max_pages=STORYBOOK_MAX_PAGES,
            ground_truth_context=gt_scene,
            cover_reference_s3_key=body.prime_photo_s3_key,
            deage_cover=campaign.deage_cover,
            defining_phrase=script.defining_phrase or None,
            hero_line=script.hero_line or None,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/http/test_tribute_generate.py -v`
Expected: the two model tests PASS. DB-backed flow tests: run with the test container up (`docker start` the pg container, `TEST_DATABASE_URL` on `:15432`); if the container is down they skip/error per the known test-env note — not a regression from this task.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/http/models.py src/flashback/http/routes/tributes.py tests/http/test_tribute_generate.py
git commit -m "feat(tribute): thread FD confession voice + prime photo through generate route"
```

---

### Task 7: Node integration prompt — cover reference image + de-age + likeness exception

**Files:**
- Create: `docs/STORYBOOK_FD_COVER_NODE_PROMPT.md`
- Modify: `NODE_INTEGRATION.md` (add a short pointer section)

**Interfaces:** none (docs only). This is the cross-boundary contract for the one piece the agent cannot execute: rendering the cover from a reference image.

- [ ] **Step 1: Write the Node prompt doc**

Create `docs/STORYBOOK_FD_COVER_NODE_PROMPT.md` documenting, for the Node team:
- The storybook `latest_generation_context.storybook.cover` may now carry `reference_s3_key` (an S3 key of the contributor-uploaded prime/profile photo), a portrait `prompt`, a `hero_line` string, and `negative = COVER_PORTRAIT_NEGATIVE_PROMPT`.
- When `reference_s3_key` is present, Node renders the cover **image-to-image** from that photo (painterly prime-years portrait), honoring the de-age instruction already baked into `prompt`. When absent, Node falls back to the existing establishing-scene cover behavior.
- **Likeness policy:** the cover is the one sanctioned place a real subject's likeness is rendered — it is the contributor's own consented photo of the subject. Page/scene art is unchanged and keeps the no-likeness negative.
- Where `prime_photo_s3_key` comes from: the Father's Day theme's Q0.4 photo upload (Node-owned S3 upload), passed to `POST /tributes/{id}/generate` as `prime_photo_s3_key`. If the contributor uploaded only a current/older photo, send that key — the agent's `deage_cover` instruction tells the model to render his younger self.
- `caption` is the defining phrase; `hero_line` (when present) is the story-gated fork line — render it as secondary cover text.

- [ ] **Step 2: Add the pointer in NODE_INTEGRATION.md**

Add a short subsection under the tribute/storybook area linking to the new doc and stating the one-line contract change (cover may carry `reference_s3_key` + `hero_line`; render image-to-image when present).

- [ ] **Step 3: Commit**

```bash
git add docs/STORYBOOK_FD_COVER_NODE_PROMPT.md NODE_INTEGRATION.md
git commit -m "docs(tribute): Node contract for FD cover reference image + de-age"
```

---

## Self-Review

**Spec coverage (brief v2 → tasks):**
- §2.3 stylized prime-years cover + fallback to profile photo → Task 5 (reference image) + Task 6 (`prime_photo_s3_key`) + Task 7 (Node renders; "send whatever photo you have"). De-age when older photo → `deage_cover` (Tasks 1/5/6).
- §2.4 story-gated, LLM-authored hero line → Task 4 (`hero_line`, gated in prompt) + Task 5 (surfaced on cover) + Task 7 (rendered).
- §2.5 voice "he"/to-the-world with slide-14 spike → Task 4 (`_CONFESSION_SYSTEM`), selected by Task 1 flag via Task 6.
- §2.6 two-sentence captions → Task 4 (prompt rule).
- §3 questions = FD theme archetype bank, MC, invariant-#22 priors → Tasks 2 + 3.
- Defining phrase on cover (§2.3) → Task 4 (`defining_phrase`) + Task 5.

**Placeholder scan:** none — every code step shows the code; the only prose-authored deliverable is the Node doc (Task 7), which is itself the deliverable.

**Type consistency:** `confession` (bool) consistent Tasks 4/6; `TributeScript.defining_phrase` / `.hero_line` defined Task 4, consumed Tasks 5/6; `build_storybook_context` new kwargs defined Task 5, called Task 6; `Campaign.confession_voice` / `.deage_cover` defined Task 1, read Tasks 3/6; `build_fathers_day_archetype_questions` defined Task 2, used Task 3; `COVER_PORTRAIT_NEGATIVE_PROMPT` defined Task 5 (compose.py), imported Task 5 (artifact_context.py) + asserted in tests.

**Scope:** single subsystem (the FD tribute skin). No DB migration (archetype questions persist via the existing `update_archetype_questions_async`; no new columns — the prime photo key is request-only, written into existing `latest_generation_context`). One cross-boundary contract change, documented in Task 7.
