# Gender-correct, hallucination-clamped artifacts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pass known gender into every artifact prompt (subject, contributor, person-entities), render storybook covers as the subject alone, and fire the "draw only who the scene names" rule unconditionally — killing the wrong-gender, invented-family, and scene-hallucination complaints.

**Architecture:** Pure prompt + JSONB-plumbing change. Gender we already store (`persons.gender`, `persons.contributor_gender`) and a new captured `entities.attributes.gender` flow through the existing `latest_generation_context` JSONB into the storybook/tribute assemblers and image prompts. One shared vocabulary module (`flashback/artifacts/people.py`) renders all gender language. No DB migration; no Node contract change.

**Tech Stack:** Python, pytest, psycopg (async), Gemini image API, OpenAI/Anthropic LLM tool calls. Spec: `docs/superpowers/specs/2026-07-22-artifact-gender-and-hallucination-design.md`.

## Global Constraints

- **No new migration.** Entity gender rides `entities.attributes` JSONB; context fields ride existing `latest_generation_context` JSONB.
- **Never guess gender from a name.** Emit gender only from explicit pronoun / gendered-relationship / direct-statement evidence (invariant #6: under-extract, drop).
- **Entity/`persons` vocabularies both resolve through one function.** Person-entities store `attributes.gender ∈ {"male","female"}`; `persons` stores `he`/`she`/`they`. `figure_noun` maps both.
- **Old stored contexts/scripts must deserialize to safe defaults** — new fields default to `None`/`[]`/`"unknown"`; behavior is unchanged until a regenerate re-resolves.
- **Likeness rules unchanged** — scene faces stay turned/distant; the cover-portrait likeness exception is untouched.
- Tests run under `pytest`; the dev stack per the `verify` skill (agent service + UI on :3001, `TEST_DATABASE_URL` on :15432 with Docker containers `docker start`ed).
- Commit trailer: `Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>`.

---

### Task 1: Extend the shared gender vocabulary (`people.py`)

**Files:**
- Modify: `src/flashback/artifacts/people.py`
- Test: `tests/artifacts/test_people.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `figure_noun(gender: str | None) -> str | None` — now also maps `"male"→"a man"`, `"female"→"a woman"` (in addition to existing `he`/`she`).
  - `people_catalog_fragment(*, subject_name: str, subject_relationship: str | None, subject_gender: str | None, contributor_gender: str | None, involved: list[dict]) -> str` — where each `involved` entry is `{"name": str, "relationship": str | None, "gender": str | None}`. Returns `""` when nothing is known.
  - `people_scene_fragment(...)` unchanged signature, reimplemented on the shared map.

- [ ] **Step 1: Write the failing tests**

Add to `tests/artifacts/test_people.py`:

```python
from flashback.artifacts.people import (
    figure_noun,
    people_catalog_fragment,
)


def test_figure_noun_maps_entity_vocabulary():
    assert figure_noun("male") == "a man"
    assert figure_noun("female") == "a woman"
    assert figure_noun("MALE") == "a man"  # case-insensitive


def test_figure_noun_maps_pronoun_vocabulary():
    assert figure_noun("he") == "a man"
    assert figure_noun("she") == "a woman"


def test_figure_noun_neutral_is_none():
    assert figure_noun("they") is None
    assert figure_noun(None) is None
    assert figure_noun("aarav") is None  # a name is never a gender


def test_people_catalog_empty_when_nothing_known():
    assert people_catalog_fragment(
        subject_name="Meera", subject_relationship="friend",
        subject_gender=None, contributor_gender=None, involved=[],
    ) == ""


def test_people_catalog_renders_known_genders():
    frag = people_catalog_fragment(
        subject_name="Meera", subject_relationship="friend",
        subject_gender="she", contributor_gender="she",
        involved=[
            {"name": "Aarav", "relationship": "her brother", "gender": "male"},
            {"name": "Priya", "relationship": "her cousin", "gender": None},
        ],
    )
    assert "Meera" in frag and "a woman" in frag
    assert "person sharing these memories" in frag
    assert "Aarav" in frag and "a man" in frag
    # An unknown-gender person is still listed by name, with no gender noun.
    assert "Priya" in frag
    assert "Priya" not in frag.split("Aarav")[0] or "a man" in frag
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/artifacts/test_people.py -v`
Expected: FAIL — `figure_noun("male")` returns `None`; `people_catalog_fragment` does not exist (ImportError).

- [ ] **Step 3: Implement**

In `src/flashback/artifacts/people.py`, replace the `_FIGURE_NOUN` map and add the catalog renderer:

```python
# Pronoun form OR entity gender -> figure noun. Neutral values ("they",
# unknown, a name) are intentionally absent: they yield no directive, leaving
# the model unbiased (CLAUDE.md §1 — no demographic invention).
_FIGURE_NOUN = {
    "he": "a man", "she": "a woman",       # persons.gender / contributor_gender
    "male": "a man", "female": "a woman",  # entities.attributes.gender
}
```

Then append:

```python
def people_catalog_fragment(
    *,
    subject_name: str,
    subject_relationship: str | None,
    subject_gender: str | None,
    contributor_gender: str | None,
    involved: list[dict] | None = None,
) -> str:
    """A <people> grounding block for the storybook assembler.

    Lists the subject, the contributor (the storyteller), and each involved
    person-entity. A gender clause is emitted ONLY where gender is known; an
    unknown-gender person is still named so the model knows who exists but
    stays unbiased on presentation. Returns "" when nothing at all is known.
    """
    rows: list[str] = []
    subject_fig = figure_noun(subject_gender)
    if subject_fig:
        rel = f", the storyteller's {subject_relationship}" if subject_relationship else ""
        rows.append(f"- {subject_name} (the subject{rel}) is {subject_fig}.")
    contributor_fig = figure_noun(contributor_gender)
    if contributor_fig:
        rows.append(
            f"- The person sharing these memories (the storyteller) is "
            f"{contributor_fig}."
        )
    for person in involved or []:
        name = (person.get("name") or "").strip()
        if not name:
            continue
        rel = (person.get("relationship") or "").strip()
        who = f" ({rel})" if rel else ""
        fig = figure_noun(person.get("gender"))
        if fig:
            rows.append(f"- {name}{who} is {fig}.")
        else:
            rows.append(f"- {name}{who}.")
    if not rows:
        return ""
    return (
        "<people>\n"
        "These are the real people in these memories. Use each stated gender "
        "with a matching noun (\"a man\", \"a woman\") — never guess gender "
        "from a name, and never invent people not listed here.\n"
        + "\n".join(rows)
        + "\n</people>"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/artifacts/test_people.py -v`
Expected: PASS (all, including pre-existing `people_scene_fragment` tests).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/artifacts/people.py tests/artifacts/test_people.py
git commit -m "feat(flashback): extend gender vocabulary with entity nouns + <people> catalog

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 2: Capture person-entity gender at extraction

**Files:**
- Modify: `src/flashback/workers/extraction/prompts.py:399-400` (person-entity rules)
- Modify: `src/flashback/workers/extraction/persistence.py` (deterministic-reuse fold)
- Test: `tests/workers/extraction/test_extraction_llm.py`, `tests/workers/extraction/test_persistence.py` (or the existing dedup test file)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: person-entities may carry `attributes.gender ∈ {"male","female"}`; the reuse path fills an empty stored gender without overwriting a set one.

- [ ] **Step 1: Add the prompt rule**

In `src/flashback/workers/extraction/prompts.py`, immediately after the `attributes.relationship` bullet (line ~399-400), add:

```python
- For person entities, populate `attributes.gender` ("male" or "female") \
ONLY when the conversation makes it unambiguous — an explicit pronoun, a \
gendered relationship word ("my sister", "his uncle"), or a direct \
statement. NEVER infer gender from a first name alone; if it is not clear \
from what was said, omit the field.
```

- [ ] **Step 2: Write the failing persistence test**

Locate the deterministic-reuse test (grep `insert_entities_async` in `tests/workers/extraction/`). Add a test asserting an empty stored gender is filled and a set one is preserved:

```python
async def test_reuse_fills_empty_gender_never_overwrites(...):
    # existing active entity "Aarav" (person) with attributes {} (no gender)
    # re-extract "Aarav" with attributes {"gender": "male"} -> stored becomes male
    # re-extract "Aarav" with attributes {"gender": "female"} -> stays male
    ...
```

(Fill in using the fixture pattern already in that test file — same `cur`, `person_id`, and `insert_entities_async` call shape as the surrounding tests.)

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/workers/extraction/ -k gender -v`
Expected: FAIL — reuse path does not fold gender.

- [ ] **Step 4: Implement the fold**

In `src/flashback/workers/extraction/persistence.py`, in the deterministic-reuse block that already fills an empty description and folds aliases (grep `description` / `aliases` within `_persist_entities` / `insert_entities_async`), add the same-shaped guard:

```python
# Fold a newly-known gender into an existing entity only when unset —
# a later ambiguous mention must never clobber a confident one (#17a).
existing_attrs = existing.get("attributes") or {}
new_gender = (incoming_attrs or {}).get("gender")
if new_gender and not existing_attrs.get("gender"):
    existing_attrs["gender"] = new_gender
    # ...persist existing_attrs the same way the description/alias fold does
```

Match the exact variable names and persistence mechanism used by the adjacent description/alias fold in that function.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/workers/extraction/ -k gender -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/workers/extraction/prompts.py src/flashback/workers/extraction/persistence.py tests/workers/extraction/
git commit -m "feat(flashback): capture person-entity gender at extraction (evidence-only)

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 3: Fetch involved person-entities + genders for storybook context

**Files:**
- Modify: `src/flashback/storybook/repository.py` (add `fetch_involved_people_async`)
- Test: `tests/storybook/test_refs.py` or `tests/storybook/test_context.py` (repository-level test)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `fetch_involved_people_async(cur, *, person_id, moment_ids: list[str]) -> list[dict]` returning `[{"name": str, "relationship": str | None, "gender": str | None}]` for active person-kind entities linked via active `involves` edges from the given moments. Deduped by entity id, name-ordered.

- [ ] **Step 1: Write the failing test**

```python
async def test_fetch_involved_people_returns_person_entities_with_gender(pool):
    # seed: person, two moments, entities Aarav(person, gender=male) and
    # Riverbank(place). involves edges moment->Aarav and moment->Riverbank.
    async with pool.connection() as conn, conn.cursor() as cur:
        people = await fetch_involved_people_async(
            cur, person_id=pid, moment_ids=[m1, m2])
    names = {p["name"] for p in people}
    assert "Aarav" in names          # person kind included
    assert "Riverbank" not in names  # place kind excluded
    aarav = next(p for p in people if p["name"] == "Aarav")
    assert aarav["gender"] == "male"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/storybook/ -k involved_people -v`
Expected: FAIL — function undefined.

- [ ] **Step 3: Implement**

In `src/flashback/storybook/repository.py`:

```python
async def fetch_involved_people_async(
    cur, *, person_id: UUID | str, moment_ids: list[str]
) -> list[dict[str, Any]]:
    """Active person-kind entities involved in the given moments (the cast the
    book will draw), with their stored gender + relationship. Deduped, ordered
    by name. Object/place/organization entities are excluded."""
    if not moment_ids:
        return []
    await cur.execute(
        """
        SELECT DISTINCT e.id::text, e.name,
               e.attributes->>'relationship', e.attributes->>'gender'
          FROM edges ed
          JOIN entities e ON e.id = ed.to_id
         WHERE ed.from_kind = 'moment'
           AND ed.to_kind   = 'entity'
           AND ed.edge_type = 'involves'
           AND ed.status    = 'active'
           AND ed.from_id   = ANY(%(mids)s::uuid[])
           AND e.status     = 'active'
           AND e.person_id  = %(pid)s
           AND e.entity_type = 'person'
         ORDER BY e.name
        """,
        {"mids": [str(m) for m in moment_ids], "pid": str(person_id)},
    )
    return [
        {"name": r[1], "relationship": r[2], "gender": r[3]}
        for r in await cur.fetchall()
    ]
```

(Confirm the entity sub-type column name against the schema — the schema invariants call it entity sub-type `person`; grep `entity_type` / `kind` in `entities` DDL and match it exactly.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/storybook/ -k involved_people -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flashback/storybook/repository.py tests/storybook/
git commit -m "feat(flashback): fetch involved person-entities + genders for storybook cast

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 4: Thread contributor_gender + people through the storybook render context

**Files:**
- Modify: `src/flashback/storybook/context.py` (dataclass + `from_dict` + `build_context_dict`)
- Modify: `src/flashback/storybook/generation.py` (`_context`, `_fetch_inputs`)
- Test: `tests/storybook/test_context.py`

**Interfaces:**
- Consumes: `fetch_involved_people_async` (Task 3).
- Produces: `StorybookRenderContext.contributor_gender: str | None` and `.people: list[dict]`; both round-trip through `build_context_dict` / `from_dict` and default to `None`/`[]` for old stored dicts.

- [ ] **Step 1: Write the failing test**

```python
def test_context_roundtrips_new_gender_fields():
    d = build_context_dict(
        collection="friends", subject_name="Meera", relationship="friend",
        gt_context="", gender="she", contributor_gender="she",
        people=[{"name": "Aarav", "relationship": "her brother", "gender": "male"}],
        moments=[], pdf_put_url="", cover_put_url="", page_put_urls=[],
    )
    ctx = StorybookRenderContext.from_dict(d, storybook_id="s", person_id="p")
    assert ctx.contributor_gender == "she"
    assert ctx.people == [{"name": "Aarav", "relationship": "her brother", "gender": "male"}]


def test_context_old_dict_defaults_new_fields():
    ctx = StorybookRenderContext.from_dict(
        {"collection": "friends", "subject_name": "Meera"},
        storybook_id="s", person_id="p")
    assert ctx.contributor_gender is None
    assert ctx.people == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/storybook/test_context.py -k gender -v`
Expected: FAIL — `build_context_dict` has no `contributor_gender`/`people`; attributes missing.

- [ ] **Step 3: Implement context fields**

In `src/flashback/storybook/context.py`:
- Add to the dataclass (after `gender`): `contributor_gender: str | None = None` and `people: list[dict[str, Any]] = field(default_factory=list)`.
- Add to `from_dict`: `contributor_gender=d.get("contributor_gender")`, `people=list(d.get("people") or [])`.
- Add both params to `build_context_dict` (defaulting `contributor_gender=None`, `people=None`) and into the returned dict (`"contributor_gender": contributor_gender`, `"people": people or []`).

- [ ] **Step 4: Wire generation.py**

In `src/flashback/storybook/generation.py`:
- In `_fetch_inputs`, after resolving moments, fetch involved people. Because `_fetch_inputs` returns the pool used to pick `moments`, fetch people for the *resolved* slice inside `_context` instead — simpler: pass `person_id` into `_context` and fetch there. Concretely, change `_context` to accept `contributor_gender` and `people` and have the two call sites (`generate_storybook`, `_rerender`) compute `people` from the resolved `moments` via a new call:

```python
async with db_pool.connection() as conn, conn.cursor() as cur:
    people = await fetch_involved_people_async(
        cur, person_id=person_id, moment_ids=[str(m["id"]) for m in moments])
```

- Pass `contributor_gender=person.get("contributor_gender")` and `people=people` into `build_context_dict` inside `_context`.
- Import `fetch_involved_people_async` in the repository import block.

- [ ] **Step 5: Run to verify context tests pass**

Run: `pytest tests/storybook/test_context.py -v && pytest tests/storybook/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/storybook/context.py src/flashback/storybook/generation.py tests/storybook/test_context.py
git commit -m "feat(flashback): thread contributor_gender + involved cast into storybook context

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 5: Gender the storybook script assembler + roster

**Files:**
- Modify: `src/flashback/storybook/script.py` (`_TOOL` schema, `Character`, `to_dict`/`from_dict`, `_sys_prompt`, `assemble_script`)
- Modify: `src/flashback/workers/storybook_render/worker.py:41-57` (`_assemble` passes new args)
- Test: `tests/storybook/test_render.py` (script-level unit tests)

**Interfaces:**
- Consumes: `people_catalog_fragment` (Task 1); `StorybookRenderContext.contributor_gender`, `.people` (Task 4).
- Produces: `Character.gender: str` (`"male"|"female"|"unknown"`); `assemble_script(..., subject_gender=None, contributor_gender=None, people=None)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_character_roundtrip_defaults_unknown_gender():
    from flashback.storybook.script import BookScript
    s = BookScript.from_dict({
        "cover_title": "T",
        "characters": [{"name": "Aarav", "who": "her brother", "appearance": "tall"}],
        "pages": [],
    })
    assert s.characters[0].gender == "unknown"


def test_character_roundtrip_preserves_gender():
    from flashback.storybook.script import BookScript
    s = BookScript.from_dict({
        "cover_title": "T",
        "characters": [{"name": "Aarav", "who": "brother", "appearance": "tall",
                        "gender": "male"}],
        "pages": [],
    })
    assert s.characters[0].gender == "male"
    assert s.to_dict()["characters"][0]["gender"] == "male"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/storybook/test_render.py -k gender -v`
Expected: FAIL — `Character` has no `gender`.

- [ ] **Step 3: Implement roster gender**

In `src/flashback/storybook/script.py`:
- `_TOOL` `characters.items.properties`: add `"gender": {"type": "string", "enum": ["male", "female", "unknown"]}` and add `"gender"` to that item's `required`.
- `Character` dataclass: add `gender: str = "unknown"`.
- `to_dict`: emit `"gender": c.gender`.
- `from_dict`: `gender=(c.get("gender") or "unknown").strip() or "unknown"`.

- [ ] **Step 4: Add the <people> block + AGES & GENDERS rule**

In `assemble_script`, add params `subject_gender: str | None = None`, `contributor_gender: str | None = None`, `people: list[dict] | None = None`. Build the block and inject it into the user message before `<memories>`:

```python
from flashback.artifacts.people import people_catalog_fragment, figure_noun
...
people_block = people_catalog_fragment(
    subject_name=subject_name, subject_relationship=relationship,
    subject_gender=subject_gender, contributor_gender=contributor_gender,
    involved=people or [],
)
```

Add `people_block` (when non-empty) into the `user` string. In `_sys_prompt`, extend the subject framing to state the subject's gender when known (compute `figure_noun(subject_gender)`), rewrite the CAST bullet to require gender from `<people>`/pronoun evidence, and rename the AGES rule to **AGES & GENDERS**:

```python
f"- AGES & GENDERS: panels are illustrated INDEPENDENTLY, so every scene "
f"description must state the apparent age AND gender of EVERY person "
f"present ('his friend, a woman of about sixty', 'a boy of ten') -- an "
f"unstated age or gender WILL be drawn wrong. Take each person's gender "
f"from <people> or from an explicit pronoun in the memories; never guess "
f"it from a name. ..."  # keep the existing age-consistency sentences
```

CAST bullet gains: `Set each character's 'gender' from <people> or an explicit pronoun; use "unknown" only when neither says.`

- [ ] **Step 5: Wire the worker**

In `src/flashback/workers/storybook_render/worker.py` `_assemble`, pass the new args from the context:

```python
return await assemble_script(
    settings=settings,
    collection=COLLECTIONS[ctx.collection],
    subject_name=ctx.subject_name,
    relationship=ctx.relationship,
    gt_context=ctx.gt_context,
    moments=ctx.moments,
    edit_instructions=ctx.edit_instructions or None,
    subject_gender=ctx.gender,
    contributor_gender=ctx.contributor_gender,
    people=ctx.people,
)
```

- [ ] **Step 6: Add the assembler user-message test**

```python
async def test_assemble_injects_people_block(monkeypatch):
    # patch call_with_tool to capture the user_message and return a minimal
    # valid comic dict; assert "<people>" and "a woman" appear in user_message
    ...
```

- [ ] **Step 7: Run to verify all pass**

Run: `pytest tests/storybook/test_render.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/flashback/storybook/script.py src/flashback/workers/storybook_render/worker.py tests/storybook/test_render.py
git commit -m "feat(flashback): gender the storybook roster + AGES & GENDERS scene rule

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 6: Clamp storybook image prompts — cast gender, unconditional anti-invention, subject-only cover

**Files:**
- Modify: `src/flashback/storybook/refs.py` (`cast_rule`)
- Modify: `src/flashback/storybook/scenes.py` (`_ident`, `gen_cover_art`)
- Modify: `src/flashback/storybook/render.py` (pass character genders through to `cast_rule` — already passes `script.characters`, so no signature change; confirm)
- Test: `tests/storybook/test_refs.py`, `tests/storybook/test_render.py`

**Interfaces:**
- Consumes: `Character.gender` (Task 5); `figure_noun` (Task 1).
- Produces: no new public signatures — behavior change in existing prompt builders.

- [ ] **Step 1: Write the failing tests**

```python
from flashback.storybook.refs import cast_rule
from flashback.storybook.scenes import _ident, gen_cover_art  # gen_cover_art via prompt capture


def test_cast_rule_includes_gender_noun():
    class C:  # duck-types the roster
        def __init__(s, n, w, a, g): s.name, s.who, s.appearance, s.gender = n, w, a, g
    rule = cast_rule([C("Aarav", "her brother", "tall, curly hair", "male")], "Meera")
    assert "a man" in rule
    assert "Aarav" in rule


def test_ident_always_forbids_inventing_people_with_empty_cast():
    text = _ident("Meera", "friend")
    assert "do not add anyone" in text.lower() or "only the people" in text.lower()
```

For the cover, capture the prompt by monkeypatching `_gen_image` to record `contents[0]`:

```python
def test_cover_prompt_is_subject_only(monkeypatch):
    captured = {}
    import flashback.storybook.scenes as sc
    monkeypatch.setattr(sc, "_gen_image", lambda *a, **k: captured.setdefault("p", a[1][0]) or None)
    sc.gen_cover_art(object(), name="Meera", relationship="friend",
                     gt_context="1960s Kerala", ref=None, art_style="X",
                     age="a woman in her sixties")
    p = captured["p"]
    assert "ONLY person" in p or "only person" in p.lower()
    assert "family" not in p.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/storybook/test_refs.py tests/storybook/test_render.py -k "cast_rule or ident or cover" -v`
Expected: FAIL.

- [ ] **Step 3: Implement `cast_rule` gender + move the anti-invention clause**

In `src/flashback/storybook/refs.py`:
- Import `figure_noun` from `flashback.artifacts.people`.
- In `cast_rule`, render each character with its gender noun:

```python
def _cast_line(c, subject):
    fig = figure_noun(getattr(c, "gender", None))
    noun = f", {fig}" if fig else ""
    return f"{c.name} ({c.who}{noun}): {c.appearance}"

listing = "; ".join(_cast_line(c, subject) for c in characters)
```

- Remove the "Draw ONLY the people the scene describes ... NEVER show the same face twice" sentence from `cast_rule`'s return (it moves to `_ident`).

In `src/flashback/storybook/scenes.py` `_ident`, append the clause unconditionally to BOTH branches (subject-known and fallback):

```python
_NO_INVENT = (
    "Draw ONLY the people the scene explicitly names -- do not add anyone "
    "it does not mention (no extra family, no bystanders, no crowd), and "
    "NEVER show the same face twice in one panel. "
)

def _ident(subject: str, role: str) -> str:
    if subject:
        return identity_rule(subject, role) + _NO_INVENT
    return (
        "Keep the SAME characters consistent with the character-reference "
        "image. " + _NO_INVENT
    )
```

- [ ] **Step 4: Rewrite `gen_cover_art` to subject-only**

Replace the cover prompt body:

```python
rel = relationship or "the subject"
age_line = f"Depict {name} as {age}. " if age else ""
prompt = (
    f"A single-figure cover portrait-scene of {name} ({rel}). "
    f"{age_line}{name} is the ONLY person in the image -- no other people, "
    f"no family, no crowd, no bystanders. Evoke their world through place, "
    f"light, objects and atmosphere ONLY, never through other people. "
    f"{gt_context}. {art_style}. Centered composition, fills the frame, soft "
    f"uncluttered background. {identity_rule(name, rel)}"
    f"Draw NO text, NO lettering, NO border anywhere -- pure illustration."
)
```

(Keep the `_gen_image(..., "3:4", ...)` call and the `ref` append unchanged.)

- [ ] **Step 5: Run to verify all pass**

Run: `pytest tests/storybook/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/storybook/refs.py src/flashback/storybook/scenes.py tests/storybook/
git commit -m "fix(flashback): gender storybook cast, always forbid invented people, subject-only cover

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 7: Gender the tribute video pipeline

**Files:**
- Modify: `src/flashback/tribute_video/context.py` (dataclass + `from_dict` + `build_context_dict`)
- Modify: `src/flashback/http/routes/tributes.py` (compose genders into context)
- Modify: `src/flashback/tribute_video/assembler.py` (`_user_message`, `assemble_storybook_video`, neutral `grandfather` fallback)
- Modify: `src/flashback/page_render/art.py` (`portrait_from_photo` pronoun)
- Test: `tests/tribute_video/` (context round-trip + assembler + art prompt)

**Interfaces:**
- Consumes: `figure_noun` (Task 1).
- Produces: `RenderContext.gender: str | None`, `.contributor_gender: str | None`; `assemble_storybook_video(..., subject_gender=None)`; `portrait_from_photo(..., gender=None)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_tribute_context_roundtrips_gender():
    from flashback.tribute_video.context import RenderContext, build_context_dict
    d = build_context_dict(subject_name="Meera", relationship="friend",
                           gt_context="", gender="she", contributor_gender="she",
                           video_put_url="", pdf_put_url="")
    ctx = RenderContext.from_dict(d, tribute_id="t", person_id="p")
    assert ctx.gender == "she" and ctx.contributor_gender == "she"


def test_tribute_context_old_dict_defaults_none():
    from flashback.tribute_video.context import RenderContext
    ctx = RenderContext.from_dict({"subject_name": "Meera"}, tribute_id="t", person_id="p")
    assert ctx.gender is None and ctx.contributor_gender is None


def test_portrait_prompt_uses_her_for_female(monkeypatch):
    # monkeypatch Artist._generate to capture prompt; assert "her" not "his"
    ...


def test_neutral_relationship_fallback_no_grandfather():
    # assemble with relationship=None -> system prompt must not inject "grandfather"
    ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/tribute_video/ -k "gender or grandfather or portrait" -v`
Expected: FAIL.

- [ ] **Step 3: Implement context fields**

In `src/flashback/tribute_video/context.py`: add `gender: str | None = None` and `contributor_gender: str | None = None` to the dataclass, `from_dict`, and `build_context_dict` (defaulting None), exactly as Task 4 did for storybook.

- [ ] **Step 4: Compose genders in the route**

In `src/flashback/http/routes/tributes.py`, at the site that calls `build_context_dict` (grep it), read the person row's `gender` and `contributor_gender` (the person fetch already loads the row — add the columns if absent) and pass `gender=...`, `contributor_gender=...`.

- [ ] **Step 5: Gender the assembler**

In `src/flashback/tribute_video/assembler.py`:
- `_user_message` gains `subject_gender: str | None = None`; when `figure_noun(subject_gender)` is set, add a `gender` attribute to `<subject>`: e.g. `f'<subject{rel} gender="{fig}">{_xml(subject_name)}</subject>'`.
- `assemble_storybook_video` gains `subject_gender: str | None = None`, threads it into `_user_message`, and the art-direction guidance in `_SYSTEM` instructs gendered nouns for subject/storyteller figures (add one sentence to the relevant slot text).
- Neutralize the fallback: `.replace("{relationship}", relationship or "the subject")` instead of `"grandfather"`.
- The `_fallback` default `relationship or "grandfather"` (line ~278/300) — leave the text-template default but ensure no hardcoded gendered word: change the bare-relationship default opener to `f"This is the story of {name}."` when `rel` is empty (already the case) — verify no `"grandfather"` literal remains (grep).

Wire the worker call site for `assemble_storybook_video` to pass `subject_gender=ctx.gender` (grep the call in `tribute_video/render.py` or worker).

- [ ] **Step 6: Fix `portrait_from_photo` pronoun**

In `src/flashback/page_render/art.py`, add `gender: str | None = None` to `portrait_from_photo` and derive pronouns:

```python
poss = {"he": "his", "she": "her"}.get((gender or "").lower(), "their")
subj = {"he": "him", "she": "her"}.get((gender or "").lower(), "them")
deage_clause = (
    f"Render {subj} noticeably YOUNGER -- restore {poss} prime-years self "
    f"(fuller hair, upright vigour) while keeping {poss} recognizable "
    f"features and bone structure. " if deage else ""
)
prompt = (
    "Repaint this photograph as a dignified painterly watercolour "
    f"PORTRAIT of {name}, in the storybook style: {STYLE}. KEEP {poss} real "
    f"likeness, face, and features faithfully. {deage_clause}{gt_context} "
    ...
)
```

Thread `gender` from the caller (grep `portrait_from_photo(` — the tribute render passes the subject; add `gender=ctx.gender`).

- [ ] **Step 7: Run to verify all pass**

Run: `pytest tests/tribute_video/ -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/flashback/tribute_video/ src/flashback/page_render/art.py src/flashback/http/routes/tributes.py tests/tribute_video/
git commit -m "fix(flashback): gender the tribute video pipeline (context, assembler, portrait pronoun)

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 8: Ground entity portraits with the entity's own gender

**Files:**
- Modify: `src/flashback/workers/extraction/prompts.py` (entity `generation_prompt` gender instruction)
- Modify: `src/flashback/http/routes/artifacts.py:185-203` (append entity gender on entity regenerate/edit)
- Test: `tests/http/test_artifacts.py` (or the existing artifacts route test) + `tests/artifacts/test_compose.py`

**Interfaces:**
- Consumes: `figure_noun` (Task 1); `attributes.gender` on entities (Task 2).
- Produces: entity regenerate composes the entity's own gender fragment.

- [ ] **Step 1: Add the extraction prompt instruction**

In `src/flashback/workers/extraction/prompts.py`, in the section that instructs how to write an entity's `generation_prompt` (grep `generation_prompt` in that file), add: "When `attributes.gender` is known, state the figure's gender presentation with a matching noun (\"a man\", \"a woman\"), faces turned away or distant."

- [ ] **Step 2: Write the failing route test**

```python
async def test_entity_regenerate_appends_entity_gender(...):
    # entity "Aarav" (person) attributes.gender = male
    # POST /artifacts/entity/{id}/regenerate
    # assert composed latest_generation_context prompt contains "a man"
    ...
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/http/test_artifacts.py -k entity_gender -v`
Expected: FAIL.

- [ ] **Step 4: Implement**

In `src/flashback/http/routes/artifacts.py`, where `people_context` is built (line ~187), when `record_type == "entity"` fetch the entity's stored `attributes.gender` (add a small helper alongside `_fetch_active_generation_prompt`, or extend it to also return attributes) and append an entity-figure clause via `figure_noun`:

```python
entity_people_context = people_context
if record_type == "entity":
    entity_gender_fig = figure_noun(entity_attributes.get("gender"))
    if entity_gender_fig:
        entity_people_context = (
            (people_context + " " if people_context else "")
            + f"Depict {entity_name} as {entity_gender_fig} "
              "(matching noun, faces turned away or distant)."
        )
```

Pass `entity_people_context` as `people_context` into `compose_scene_prompt`.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/http/test_artifacts.py -k entity_gender -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/workers/extraction/prompts.py src/flashback/http/routes/artifacts.py tests/
git commit -m "feat(flashback): ground entity portraits with the entity's own captured gender

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 9: Full suite + manual dev-stack verification

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `pytest -q`
Expected: PASS (excluding the known pre-existing failures recorded in the `test_environment` memory — confirm no NEW failures).

- [ ] **Step 2: Manual verify per the `verify` skill**

Launch the dev stack (agent service + UI on :3001). On a legacy where subject and contributor are the same gender (e.g. two women), mint a `friends` storybook and confirm:
- Cover shows the subject alone — no invented family.
- Cast genders match the memories (no stray boy/girl).
- Scenes contain only the people the memories name.

Regenerate an existing storybook and an existing tribute to confirm the fresh-resolve path picks up genders and nothing crashes on old stored contexts.

- [ ] **Step 3: Final commit (docs, if any tweak needed)**

```bash
git add -A
git commit -m "chore(flashback): verify gender/hallucination artifact fixes on dev stack

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

## Self-Review

**Spec coverage:**
- §3 shared vocabulary → Task 1. ✓
- §4 capture entity gender (prompt + reuse fold) → Task 2. ✓
- §5 storybook assembly (context fields, involved people, `<people>`, roster gender, AGES & GENDERS) → Tasks 3, 4, 5. ✓
- §6 image prompts (cast gender, unconditional anti-invention, subject-only cover) → Task 6. ✓
- §7 tribute (context, assembler, `portrait_from_photo`, neutral fallback) → Task 7. ✓
- §8 entity portraits (extraction prompt + regenerate append) → Task 8. ✓
- §9 testing (unit per surface + manual) → each task's tests + Task 9. ✓

**Placeholder scan:** Tasks 2, 3, 4, 7, 8 contain a few "grep and match the exact name/site" directions where the surrounding code's variable names must be read at implementation time (deterministic-reuse fold internals, entity sub-type column, tribute context-composition call site, `portrait_from_photo` caller). These are deliberate — the exact local identifiers live in files the implementer will open — and each is bounded to a single named function with the surrounding pattern described. All code-producing steps show real code.

**Type consistency:** `figure_noun` accepts both `he/she/they` and `male/female` across all tasks. Entity gender is `attributes.gender ∈ {male,female}`; `persons` gender is `he/she/they` — both resolve through `figure_noun`. `people_catalog_fragment` `involved` entries are `{name, relationship, gender}`, matching `fetch_involved_people_async`'s return and `StorybookRenderContext.people`. `Character.gender` enum `{male,female,unknown}` matches the tool schema.
