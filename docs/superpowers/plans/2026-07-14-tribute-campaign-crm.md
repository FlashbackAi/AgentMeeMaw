# Tribute Campaign CRM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move tribute occasion/relationship config from code into Postgres (3 config tables + admin API + generate-first authoring + preview with a real sample page) so a content person can launch Friendship Day — and every future occasion — from a CRM without a deploy.

**Architecture:** Two-axis config: `tribute_campaigns` (occasion wrapper) × `relationship_profiles` (tone owner) × `tribute_visual_themes` (page template/fonts/inks/audio). Deterministic composer turns structured content JSONB into prompt slots; the assembler/renderer skeletons stay code. Runtime resolves campaign→profile→neutral per touchpoint and snapshots everything into `latest_generation_context` at `/generate`; the render worker reads only the snapshot. Spec: `docs/superpowers/specs/2026-07-14-tribute-campaign-crm-design.md`.

**Tech Stack:** Python 3.11+, FastAPI, psycopg3 (async pool), Postgres, Pillow (compose), existing `call_with_tool` LLM interface, existing `page_render.art.Artist` (Gemini) for images.

## Global Constraints

- Migration number is **0039** (latest is 0038). Every `.up.sql` gets a matching `.down.sql`.
- Config lifecycle: `state IN ('draft','published','archived')`; edit-supersession via `status IN ('active','superseded')` + `version INT`; partial unique index on slug `WHERE status='active'`. Runtime reads `state='published' AND status='active'` only.
- Snapshot pins config **row ids** (supersession = new row = new id). No version field in snapshots.
- Every new LLM/image call site passes a distinct `feature` tag (existing `usage_events` plumbing does the recording): `relationship_classify`, `tribute_config_generate`, `tribute_template_generate`, `tribute_preview` (assembly), sample-page art rides the Artist's existing recording.
- All seed content (opener examples, fallback lines) is **third-person address**; `{name}` placeholder required in fallback/opener example templates ( `{relationship}` also allowed in fallbacks).
- The `other` profile reproduces today's neutral behavior and is delete/archive-protected.
- No auth added in this service: new admin routes join the existing `/admin` pattern (`require_service_token` + `require_admin_service_token`). `X-Admin-User` header → `updated_by` (default `"unknown"`).
- Node never writes these tables; no DB grants for them.
- Tests use the shared fixtures in `tests/http/conftest.py` (`client_with_db`, `async_db_pool`) with header `{"X-Service-Token": "test-token"}`; admin routes additionally send `{"X-Admin-Service-Token": "admin-test-token"}` (check conftest for the configured value; if absent, configure it there once).
- Test DB: docker containers must be running (`docker start` the postgres/valkey test containers); `TEST_DATABASE_URL` on `:15432` (see memory `test_environment`). Run tests with `python -m pytest <path> -v` from the repo root.
- Do not break in-flight render contexts: every new `RenderContext` field defaults to a value that reproduces today's behavior when absent from a stored snapshot.

---

### Task 1: Migration 0039 — config tables, columns, seeds

**Files:**
- Create: `migrations/0039_tribute_campaign_crm.up.sql`
- Create: `migrations/0039_tribute_campaign_crm.down.sql`
- Test: `tests/db/test_migration_0039.py`

**Interfaces:**
- Produces: tables `relationship_profiles`, `tribute_campaigns`, `tribute_visual_themes`; columns `persons.relationship_group TEXT NULL`, `tributes.campaign_id UUID NULL`; seeded rows: 8 published profiles (`group_slug` in `parent, grandparent, sibling, cousin, friend, spouse_partner, mentor, other`), 1 published campaign `fathers_day_2026`, 1 published visual theme `classic_keepsake` (NULL image = built-in kit).

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_migration_0039.py
"""Migration 0039: config tables exist, seeds present, lifecycle constraints."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def test_seeded_profiles_all_eight(async_db_pool) -> None:
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT group_slug FROM relationship_profiles "
                "WHERE state='published' AND status='active' ORDER BY group_slug"
            )
            slugs = [r[0] for r in await cur.fetchall()]
    assert slugs == sorted(
        ["parent", "grandparent", "sibling", "cousin", "friend",
         "spouse_partner", "mentor", "other"]
    )


async def test_friend_profile_is_playful_with_bank(async_db_pool) -> None:
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT voice, opener, archetype_bank, deage_cover "
                "FROM relationship_profiles "
                "WHERE group_slug='friend' AND status='active'"
            )
            voice, opener, bank, deage = await cur.fetchone()
    assert "playful" in voice["energy_words"]
    # Explicit product rule: never a formal introduction opener.
    assert "never" in voice and any("meet my" in n.lower() for n in voice["never"])
    assert all("{name}" in ex for ex in opener["examples"])
    assert isinstance(bank, list) and len(bank) >= 8
    assert all(len(q["options"]) >= 2 for q in bank)
    assert deage is False


async def test_fd_campaign_retrofitted(async_db_pool) -> None:
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT display_name, archetype_bank_override, deage_cover_override, "
                "featured, active_start, active_end, state "
                "FROM tribute_campaigns WHERE slug='fathers_day_2026' AND status='active'"
            )
            name, bank, deage, featured, start, end, state = await cur.fetchone()
    assert name == "A Letter to Dad"
    assert len(bank) == 22
    assert deage is True and featured is True and state == "published"
    assert str(start) == "2026-06-01" and str(end) == "2026-06-22"


async def test_classic_visual_theme_null_image_is_builtin(async_db_pool) -> None:
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT template_image, fonts, ink, audio_slug FROM tribute_visual_themes "
                "WHERE slug='classic_keepsake' AND status='active'"
            )
            img, fonts, ink, audio = await cur.fetchone()
    assert img is None
    assert fonts == {"main_slug": "playfair_italic", "eyebrow_slug": "eb_garamond"}
    assert ink == {"main_fill": "#3a2c1c", "eyebrow_fill": "#967648"}
    assert audio == "sentimental_piano"


async def test_new_columns_exist(async_db_pool) -> None:
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT relationship_group FROM persons LIMIT 0")
            await cur.execute("SELECT campaign_id FROM tributes LIMIT 0")


async def test_slug_unique_only_for_active(async_db_pool) -> None:
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            # superseding the friend row then re-inserting must be legal
            await cur.execute(
                "UPDATE relationship_profiles SET status='superseded' "
                "WHERE group_slug='friend' AND status='active' RETURNING id"
            )
            assert await cur.fetchone() is not None
            await cur.execute(
                """
                INSERT INTO relationship_profiles
                    (group_slug, display_name, synonyms, voice, opener, art,
                     fallback_opener, fallback_closing, state, version)
                VALUES ('friend', 'Friend', ARRAY['friend'],
                        '{"energy_words":["playful"],"narrator_stance":"x","emotion_rule":"y","never":[]}',
                        '{"style":"z","examples":["Go, {name}."]}',
                        '{"mood_words":["bright"],"avoid":[]}',
                        'Some people get lucky. I got {name}.',
                        'Thanks for all of it, {name}.', 'published', 2)
                """
            )
            await conn.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/db/test_migration_0039.py -v`
Expected: FAIL / ERROR — `relation "relationship_profiles" does not exist`

- [ ] **Step 3: Write the migration**

`migrations/0039_tribute_campaign_crm.up.sql` — complete content:

```sql
-- 0039: Tribute Campaign CRM — occasion/relationship config moves to Postgres.
-- Spec: docs/superpowers/specs/2026-07-14-tribute-campaign-crm-design.md
-- Lifecycle on all three config tables: state (CRM lifecycle) + the house
-- supersession pattern (status flip + new row, version increments).

CREATE TABLE relationship_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_slug   TEXT NOT NULL,
    display_name TEXT NOT NULL,
    synonyms     TEXT[] NOT NULL DEFAULT '{}',
    voice        JSONB NOT NULL,   -- {energy_words[], narrator_stance, emotion_rule, never[]}
    opener       JSONB NOT NULL,   -- {style, examples[]}
    art          JSONB NOT NULL,   -- {mood_words[], avoid[]}
    fallback_opener  TEXT NOT NULL,  -- {name}/{relationship} template
    fallback_closing TEXT NOT NULL,
    archetype_bank   JSONB,          -- [{question, options[]}] | NULL -> LLM
    message_invitation_copy TEXT,
    deage_cover  BOOLEAN NOT NULL DEFAULT FALSE,
    video_target_seconds INT,
    visual_theme_id UUID,            -- FK added after tribute_visual_themes
    state   TEXT NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft', 'published', 'archived')),
    status  TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded')),
    version INT NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX relationship_profiles_active_slug
    ON relationship_profiles (group_slug) WHERE status = 'active';

CREATE TABLE tribute_visual_themes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         TEXT NOT NULL,
    display_name TEXT NOT NULL,
    template_image BYTEA,            -- NULL -> built-in page-template.jpg kit
    template_mime  TEXT,
    fonts JSONB NOT NULL,            -- {main_slug, eyebrow_slug}
    ink   JSONB NOT NULL,            -- {main_fill, eyebrow_fill} hex strings
    audio_slug TEXT NOT NULL,
    state   TEXT NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft', 'published', 'archived')),
    status  TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded')),
    version INT NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX tribute_visual_themes_active_slug
    ON tribute_visual_themes (slug) WHERE status = 'active';

CREATE TABLE tribute_campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         TEXT NOT NULL,
    display_name TEXT NOT NULL,
    message_card_copy TEXT,
    archetype_extra_context TEXT NOT NULL DEFAULT '',
    video_target_seconds INT,
    featured BOOLEAN NOT NULL DEFAULT FALSE,
    active_start DATE,
    active_end   DATE,
    archetype_bank_override JSONB,   -- [{question, options[]}]
    deage_cover_override BOOLEAN,
    visual_theme_id UUID REFERENCES tribute_visual_themes(id) ON DELETE SET NULL,
    closing_card_copy TEXT,          -- reserved (share/end card); NOT rendered in v1
    state   TEXT NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft', 'published', 'archived')),
    status  TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded')),
    version INT NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX tribute_campaigns_active_slug
    ON tribute_campaigns (slug) WHERE status = 'active';

ALTER TABLE relationship_profiles
    ADD CONSTRAINT relationship_profiles_visual_theme_fk
    FOREIGN KEY (visual_theme_id) REFERENCES tribute_visual_themes(id)
    ON DELETE SET NULL;

ALTER TABLE persons  ADD COLUMN relationship_group TEXT;
ALTER TABLE tributes ADD COLUMN campaign_id UUID
    REFERENCES tribute_campaigns(id) ON DELETE SET NULL;

CREATE TRIGGER trg_relationship_profiles_updated_at BEFORE UPDATE ON relationship_profiles
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
CREATE TRIGGER trg_tribute_visual_themes_updated_at BEFORE UPDATE ON tribute_visual_themes
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();
CREATE TRIGGER trg_tribute_campaigns_updated_at BEFORE UPDATE ON tribute_campaigns
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ---------------------------------------------------------------------------
-- Seeds. All seed copy is third-person address (spec §3.5): a future
-- address_mode must stay additive, never a content rewrite.
-- ---------------------------------------------------------------------------

INSERT INTO tribute_visual_themes
    (slug, display_name, template_image, template_mime, fonts, ink, audio_slug, state)
VALUES (
    'classic_keepsake', 'Classic Keepsake', NULL, NULL,
    '{"main_slug": "playfair_italic", "eyebrow_slug": "eb_garamond"}',
    '{"main_fill": "#3a2c1c", "eyebrow_fill": "#967648"}',
    'sentimental_piano', 'published'
);

INSERT INTO relationship_profiles
    (group_slug, display_name, synonyms, voice, opener, art,
     fallback_opener, fallback_closing, archetype_bank,
     message_invitation_copy, deage_cover, video_target_seconds,
     visual_theme_id, state)
VALUES
(
  'parent', 'Parent',
  ARRAY['father','dad','daddy','papa','appa','nanna','baba','mother','mom','mummy','amma','maa','mata','parent'],
  '{"energy_words": ["tender", "admiring", "grateful"],
    "narrator_stance": "a child telling the story of the person who built their world",
    "emotion_rule": "quiet reverence throughout; the sacrifice is shown in concrete things, never named",
    "never": ["encyclopedia tone", "listing achievements like a resume"]}',
  '{"style": "a dedication: name them and, in a breath, who they were and why they mattered",
    "examples": ["Meet my father, {name} - the man who gave everything and asked for nothing.",
                 "This is {name}. Everything good in the family started with them."]}',
  '{"mood_words": ["warm", "nostalgic", "golden-hour light", "worn hands", "quiet dignity"],
    "avoid": ["gloomy", "posed studio portraits"]}',
  'Meet my {relationship}, {name}.',
  'Thank you for everything, {name}.',
  NULL, NULL, TRUE, NULL,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'), 'published'
),
(
  'grandparent', 'Grandparent',
  ARRAY['grandfather','grandpa','thatha','dadaji','nana','ajoba','grandmother','grandma','grandmom','ajji','dadi','nani','paati','grandparent'],
  '{"energy_words": ["reverent", "storybook-warm", "wonder"],
    "narrator_stance": "a grandchild passing down the family''s founding stories",
    "emotion_rule": "warmth of generations; time moves slowly in every scene; the closing lands as inheritance",
    "never": ["rushing the pacing", "modern slang"]}',
  '{"style": "open like the first page of a family legend - place them in their world",
    "examples": ["Long before any of us, there was {name}.",
                 "Every family has a beginning. Ours is called {name}."]}',
  '{"mood_words": ["sepia warmth", "old courtyards", "oil lamps", "heirlooms", "slow evenings"],
    "avoid": ["neon", "urban rush"]}',
  'Long before any of us, there was {name}.',
  'Their story lives on in all of us, {name}.',
  NULL, NULL, TRUE, NULL,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'), 'published'
),
(
  'sibling', 'Sibling',
  ARRAY['brother','bro','anna','bhai','bhaiya','dada','sister','sis','akka','didi','tai','sibling','elder brother','younger brother','elder sister','younger sister'],
  '{"energy_words": ["teasing", "protective", "shorthand-close"],
    "narrator_stance": "the sibling who shared a childhood roof and knows every embarrassing story",
    "emotion_rule": "affection hides inside the teasing; the protectiveness only shows at the end",
    "never": ["formal introductions", "solemn eulogy tone"]}',
  '{"style": "open mid-memory, like continuing an argument that started in childhood",
    "examples": ["Growing up with {name} meant never having a boring day.",
                 "Nobody could push my buttons like {name}. Nobody had my back like them either."]}',
  '{"mood_words": ["shared bedrooms", "school uniforms", "bicycles", "kitchen raids", "monsoon afternoons"],
    "avoid": ["stiff family portraits"]}',
  'Growing up with {name} meant never a boring day.',
  'Through everything, {name} - always my person.',
  NULL, NULL, FALSE, NULL,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'), 'published'
),
(
  'cousin', 'Cousin',
  ARRAY['cousin','cousin brother','cousin sister','cuz'],
  '{"energy_words": ["festive", "adventurous", "conspiratorial"],
    "narrator_stance": "the cousin and partner-in-adventures from every summer holiday and family function",
    "emotion_rule": "the fun of extended family carries it; the closeness sneaks up in the closing",
    "never": ["treating them like a distant relative", "solemn opener"]}',
  '{"style": "open like a summer-holiday story every cousin in the family already knows",
    "examples": ["Every family function had a ringleader. Ours was {name}.",
                 "Summers meant one thing: {name} was coming."]}',
  '{"mood_words": ["festival lights", "terrace games", "wedding chaos", "train journeys", "mango summers"],
    "avoid": ["office settings", "posed formality"]}',
  'Every family gathering began with {name}.',
  'Here''s to every summer, {name}.',
  NULL, NULL, FALSE, NULL,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'), 'published'
),
(
  'friend', 'Friend',
  ARRAY['friend','best friend','bestie','buddy','pal','childhood friend','college friend','school friend','bff','yaar','dost','close friend'],
  '{"energy_words": ["playful", "teasing", "loyal", "quick"],
    "narrator_stance": "their partner-in-crime telling everyone about the person they shared the most laughs and trouble with",
    "emotion_rule": "the warmth lives inside the jokes, never stated; sincerity breaks through only at the very end",
    "never": ["meet my friend introductions", "eulogy tone", "formal dedication opener"]}',
  '{"style": "never introduce them formally - open like the first line of a story told at every party: a tease, a mock-complaint, a dare",
    "examples": ["Nobody warned me about {name}.",
                 "Some people get lucky. I got {name}.",
                 "There are friends, and then there is {name}."]}',
  '{"mood_words": ["bright", "candid", "mid-motion", "caught-in-the-act", "daylight", "street corners", "chai stalls"],
    "avoid": ["posed", "solemn", "candlelight"]}',
  'Some people get lucky. I got {name}.',
  'For every laugh and every save - thank you, {name}.',
  '[
    {"question": "How did they and the contributor first collide?",
     "options": ["School bench", "College hostel", "The neighbourhood", "Through work"]},
    {"question": "What role did they play in the group?",
     "options": ["The ringleader", "The fixer", "The comedian", "The quiet anchor"]},
    {"question": "What kind of trouble did they get everyone into?",
     "options": ["Sneaking out", "Pranks that went too far", "Last-minute plans", "Borrowed vehicles"]},
    {"question": "What did a normal evening with them look like?",
     "options": ["Chai and gossip", "Long rides", "Cricket till dark", "Doing nothing, together"]},
    {"question": "What could they talk about for hours?",
     "options": ["Movies", "Cricket", "Big dreams", "Everyone else"]},
    {"question": "When things went wrong, what did they do?",
     "options": ["Showed up first", "Made it funny", "Fixed it quietly", "Never asked questions"]},
    {"question": "What is the funniest thing they ever pulled off?",
     "options": ["A legendary prank", "Talking out of trouble", "An impossible plan", "A disaster turned story"]},
    {"question": "What food or place belongs to this friendship?",
     "options": ["A chai stall", "A mess or canteen", "Street food rounds", "One particular corner"]},
    {"question": "What did they teach without ever meaning to?",
     "options": ["How to laugh it off", "Loyalty", "Courage", "How to enjoy nothing"]},
    {"question": "What is the one thing never said to them out loud?",
     "options": ["Thank you", "You are family", "I would not be me without you", "Miss you, idiot"]}
  ]',
  'Friends say everything except the one thing that matters. What''s the one thing they should hear?',
  FALSE, NULL,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'), 'published'
),
(
  'spouse_partner', 'Spouse / Partner',
  ARRAY['wife','husband','spouse','partner','better half','fiance','fiancee'],
  '{"energy_words": ["intimate", "steady", "devoted"],
    "narrator_stance": "the person who built a life beside them, speaking of small daily things that meant everything",
    "emotion_rule": "understatement carries the love; one small domestic detail per beat does the work",
    "never": ["grand romantic cliches", "performative declarations"]}',
  '{"style": "open small and domestic - one everyday image that holds the whole life together",
    "examples": ["Every morning started the same way: {name}, up before everyone.",
                 "A whole life can fit inside a small kitchen, when it''s shared with {name}."]}',
  '{"mood_words": ["morning light", "two cups of tea", "quiet rooms", "shared routines", "hands"],
    "avoid": ["stock romance", "sunsets on beaches"]}',
  'A whole life, built beside {name}.',
  'For every ordinary day made rich - {name}.',
  NULL, NULL, FALSE, NULL,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'), 'published'
),
(
  'mentor', 'Mentor / Teacher',
  ARRAY['mentor','teacher','guru','sir','madam','coach','professor','guide','boss','master','ustad'],
  '{"energy_words": ["grateful", "respectful", "formative"],
    "narrator_stance": "a student who only understood years later what was being taught",
    "emotion_rule": "gratitude grows through the beats; the lesson that stuck lands last",
    "never": ["overfamiliar teasing", "resume recitation"]}',
  '{"style": "open with the moment they first took the narrator seriously",
    "examples": ["Everyone has one person who saw them first. That was {name}.",
                 "{name} never raised their voice. They never needed to."]}',
  '{"mood_words": ["chalk dust", "desks", "workshops", "late corrections", "a nod of approval"],
    "avoid": ["party scenes"]}',
  'Everyone has one person who saw them first: {name}.',
  'Every lesson stayed, {name}. Thank you.',
  NULL, NULL, FALSE, NULL,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'), 'published'
),
(
  'other', 'Someone Special',
  ARRAY[]::TEXT[],
  '{"energy_words": ["warm", "proud", "true"],
    "narrator_stance": "someone in the family who loved this person, telling their story to a reader who never met them",
    "emotion_rule": "tender, admiring, plain-spoken, true; never an encyclopedia",
    "never": ["cryptic fragments", "invented facts"]}',
  '{"style": "one warm sentence naming them and, in a breath, who they were and why they mattered",
    "examples": ["Meet my {relationship}, {name}.", "This is the story of {name}."]}',
  '{"mood_words": ["warm", "grounded", "true to their world"],
    "avoid": []}',
  'Meet my {relationship}, {name}.',
  'Thank you for everything, {name}.',
  NULL, NULL, FALSE, NULL,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'), 'published'
);

-- Father's Day 2026 retrofit: the launch skin becomes the reference campaign.
-- Window is past (inert); bank pinned as the campaign override, exactly the
-- 22 questions that shipped in flashback/tribute/theme.py.
INSERT INTO tribute_campaigns
    (slug, display_name, message_card_copy, archetype_extra_context,
     video_target_seconds, featured, active_start, active_end,
     archetype_bank_override, deage_cover_override, visual_theme_id, state)
VALUES (
  'fathers_day_2026', 'A Letter to Dad',
  'Fathers and sons don''t always say it out loud. If he could hear one thing from you right now — what is it?',
  'This is a Father''s Day tribute. Frame the questions around the subject as a father figure — what he was like, what he gave, the moments that stayed — while staying subject-status-agnostic.',
  45, TRUE, DATE '2026-06-01', DATE '2026-06-22',
  '[
    {"question": "Where did your father grow up?",
     "options": ["A village", "A small town", "A big city", "Abroad"]},
    {"question": "What was your father''s main work or trade?",
     "options": ["A trade / manual work", "A salaried job", "His own business", "Farming / land"]},
    {"question": "Was his income steady, or did some months stretch thin?",
     "options": ["Steady wage", "Up and down", "Often tight", "We never lacked"]},
    {"question": "Was he raised by both parents, or did he lose someone early?",
     "options": ["Both, all through", "Lost his father young", "Lost his mother young", "Raised by others"]},
    {"question": "What did his own parents do for a living?",
     "options": ["Farming / land", "A trade or labour", "A small job", "I never knew them"]},
    {"question": "What did your father go without when he was a child?",
     "options": ["Schooling", "Enough food", "New clothes", "A childhood at all"]},
    {"question": "Did he talk much about his own childhood, or stay quiet about it?",
     "options": ["Told the same stories", "Only when asked", "Rarely spoke of it", "Never once"]},
    {"question": "What kind of clothes did you wear growing up?",
     "options": ["Branded / new", "Hand-me-downs", "Simple but clean", "The best they could afford"]},
    {"question": "What did your school or education look like?",
     "options": ["Private / English-medium", "Government school", "Convent", "Far from home"]},
    {"question": "How did you get to school each morning?",
     "options": ["He dropped me", "Bus / auto", "Bicycle", "Walked"]},
    {"question": "What treats could you reach for freely as a kid?",
     "options": ["Sweets", "Eating out", "Cold drinks", "Whatever I wanted"]},
    {"question": "Think of one thing you had as a kid that mattered -- did he have it at your age?",
     "options": ["He never had it", "Only much later", "He had it too", "Not sure"]},
    {"question": "What''s something he made sure you had that he never did?",
     "options": ["An education", "A home", "Comfort", "Real choices"]},
    {"question": "Did he ever uproot his life or give up something he''d built, for your sake?",
     "options": ["Sold a home", "Left his land", "Changed careers", "Moved everything"]},
    {"question": "What did he go without, day to day, while providing?",
     "options": ["Skipped meals", "No small comforts", "No rest", "Spent nothing on himself"]},
    {"question": "Did he work unusual hours, dangerous work, or more than one job?",
     "options": ["Long / odd hours", "Dangerous work", "More than one job", "Whatever paid"]},
    {"question": "What did he put first, ahead of himself?",
     "options": ["Our schooling", "Our health", "The family name", "Our future"]},
    {"question": "Did he have money he could have spent on himself but didn''t?",
     "options": ["Yes -- always chose us", "Sometimes", "He was genuinely stretched", "Not sure"]},
    {"question": "If he''d chosen himself, what could his life have looked like?",
     "options": ["More wealth", "Kept his land", "A bigger career", "His own dreams"]},
    {"question": "When did you first realize how much he gave up for you?",
     "options": ["Only as an adult", "When I had my own kids", "After he was gone", "I still don''t fully"]},
    {"question": "Stripped of all the sacrifice -- who is he, in one line?",
     "options": ["A quiet, strong man", "A dreamer who stayed", "All heart", "Still figuring it out"]},
    {"question": "What''s the one thing you''ve never said to him out loud?",
     "options": ["I love you", "I''m proud of you", "Thank you", "You''re my hero"]}
  ]',
  TRUE,
  (SELECT id FROM tribute_visual_themes WHERE slug = 'classic_keepsake'),
  'published'
);
```

`migrations/0039_tribute_campaign_crm.down.sql`:

```sql
ALTER TABLE tributes DROP COLUMN IF EXISTS campaign_id;
ALTER TABLE persons  DROP COLUMN IF EXISTS relationship_group;
DROP TABLE IF EXISTS tribute_campaigns;
DROP TABLE IF EXISTS relationship_profiles;
DROP TABLE IF EXISTS tribute_visual_themes;
```

Note: `relationship_profiles` is dropped before `tribute_visual_themes` (FK). Check how migrations are applied in tests (`tests/` conftest applies `migrations/*.up.sql` in order) — no registration step should be needed; confirm by running the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/db/test_migration_0039.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add migrations/0039_tribute_campaign_crm.up.sql migrations/0039_tribute_campaign_crm.down.sql tests/db/test_migration_0039.py
git commit -m "feat(crm): migration 0039 - tribute config tables, columns, seeds"
```

---

### Task 2: Config schema — typed carriers + validation

**Files:**
- Create: `src/flashback/tribute/config_schema.py`
- Test: `tests/tribute/test_config_schema.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ProfileConfig(id: str, group_slug: str, display_name: str, synonyms: tuple[str, ...], voice: dict, opener: dict, art: dict, fallback_opener: str, fallback_closing: str, archetype_bank: list[dict] | None, message_invitation_copy: str | None, deage_cover: bool, video_target_seconds: int | None, visual_theme_id: str | None, state: str, version: int)`
  - `@dataclass(frozen=True) CampaignConfig(id: str, slug: str, display_name: str, message_card_copy: str | None, archetype_extra_context: str, video_target_seconds: int | None, featured: bool, active_start: date | None, active_end: date | None, archetype_bank_override: list[dict] | None, deage_cover_override: bool | None, visual_theme_id: str | None, closing_card_copy: str | None, state: str, version: int)`
  - `@dataclass(frozen=True) VisualThemeConfig(id: str, slug: str, display_name: str, has_image: bool, template_mime: str | None, fonts: dict, ink: dict, audio_slug: str, state: str, version: int)` (image bytes fetched separately — never carried on the config object)
  - `NEUTRAL_CAMPAIGN: CampaignConfig` (id="", slug="default", display_name="A Tribute", everything else None/empty/45s-defaults — replaces `flashback.tribute.campaigns.NEUTRAL_CAMPAIGN`)
  - `validate_profile_payload(d: dict) -> list[str]` and `validate_campaign_payload(d: dict) -> list[str]` — return human-readable error strings (empty = valid). Enforce: voice keys (`energy_words` non-empty list, `narrator_stance`/`emotion_rule` non-empty str, `never` list), opener (`style` non-empty, `examples` non-empty list each containing `{name}`), art (`mood_words` non-empty list, `avoid` list), fallbacks non-empty + contain `{name}`, bank None or list of `{question: str, options: list[str] len>=2}`, hex colors match `^#[0-9a-fA-F]{6}$` when present.
  - `bank_to_archetype_questions(bank: list[dict]) -> list[ArchetypeQuestion]` — generalizes `build_fathers_day_archetype_questions` (ids `q{n}` / `q{n}_o{m}`, skip questions with <2 non-blank options). Import `ArchetypeQuestion` from `flashback.themes.archetype_llm`.

- [ ] **Step 1: Write the failing test** — `tests/tribute/test_config_schema.py` with: a valid profile payload validates to `[]`; missing `{name}` in an opener example / fallback produces an error mentioning the field; bank with a 1-option question produces an error; `bank_to_archetype_questions` on a 2-question bank returns 2 `ArchetypeQuestion`s with ids `q1`,`q2` and options `q1_o1..`; `NEUTRAL_CAMPAIGN.slug == "default"`. Use the seed friend-profile shapes from Task 1 as the valid fixture (copy the dict inline — tests must not read the migration).
- [ ] **Step 2: Run to fail** — `python -m pytest tests/tribute/test_config_schema.py -v` → import error.
- [ ] **Step 3: Implement** `config_schema.py` exactly per the Interfaces block (pure functions, no DB imports).
- [ ] **Step 4: Run to pass.**
- [ ] **Step 5: Commit** — `feat(crm): typed config carriers + payload validation`

---

### Task 3: Relationship resolver

**Files:**
- Create: `src/flashback/tribute/relationships.py`
- Test: `tests/tribute/test_relationships.py`

**Interfaces:**
- Consumes: `ProfileConfig` (Task 2); `call_with_tool` from `flashback.llm.interface`; profiles fetched via raw SQL here (repository lands in Task 4 — keep the internal `_fetch_active_profiles(cur)` private and swap to the repository in Task 4's port step).
- Produces:
  - `RELATIONSHIP_GROUPS: tuple[str, ...] = ("parent","grandparent","sibling","cousin","friend","spouse_partner","mentor","other")`
  - `match_synonym(label: str, profiles: list[ProfileConfig]) -> str | None` — case-insensitive, trimmed, exact match against each profile's `synonyms` and `group_slug`; also strips a leading `"my "`.
  - `async classify_relationship_llm(settings, label: str) -> str` — small-LLM `call_with_tool` (tool `classify_relationship`, schema `{"group": {"enum": RELATIONSHIP_GROUPS}}`), `feature="relationship_classify"`, timeout 6s, max_tokens 200; returns `"other"` on any `LLMError`/bad output.
  - `async ensure_relationship_group(cur, settings, *, person_id: str) -> str` — reads `persons.relationship_group`; if set, return it. Else read `persons.relationship`; empty → `"other"` (no write). Else synonym-match → LLM fallback; on a **confident** result (synonym hit or LLM success), `UPDATE persons SET relationship_group=%s WHERE id=%s`; on LLM failure return `"other"` WITHOUT writing (retries next entry).

- [ ] **Step 1: Failing tests** — synonym hit for "Dad"/"amma"/"my best friend"; unknown label invokes a monkeypatched `classify_relationship_llm` and writes back; LLM failure returns `other` and leaves the column NULL; second call short-circuits on the cached column (patch the LLM to raise if called). DB tests via `async_db_pool` inserting a person with `relationship='chittappa'`.
- [ ] **Step 2: Run to fail.** `python -m pytest tests/tribute/test_relationships.py -v`
- [ ] **Step 3: Implement.** Keep the LLM call behind `classify_relationship_llm` so tests patch one name.
- [ ] **Step 4: Run to pass.**
- [ ] **Step 5: Commit** — `feat(crm): relationship resolver (synonyms -> small-LLM -> cached column)`

---

### Task 4: Config repository — CRUD, publish, rollback, resolution

**Files:**
- Create: `src/flashback/tribute/config_repository.py`
- Modify: `src/flashback/tribute/relationships.py` (swap `_fetch_active_profiles` to the repository)
- Test: `tests/tribute/test_config_repository.py`

**Interfaces:**
- Consumes: Task 2 dataclasses.
- Produces (all async, take `cur`):
  - `fetch_profile_by_group(cur, group_slug: str, *, published_only: bool = True) -> ProfileConfig | None`
  - `fetch_profile_by_id(cur, profile_id) -> ProfileConfig | None`; same pair for campaigns (`fetch_campaign_by_slug`, `fetch_campaign_by_id`) and visual themes (`fetch_visual_theme_by_slug/_by_id`, plus `fetch_visual_theme_image(cur, theme_id) -> tuple[bytes, str] | None`)
  - `list_rows(cur, table: Literal["relationship_profiles","tribute_campaigns","tribute_visual_themes"], *, include_archived: bool = False, include_superseded: bool = False) -> list[dict]`
  - `create_row(cur, table, payload: dict, *, updated_by: str) -> str` (state='draft', version=1)
  - `supersede_edit(cur, table, row_id, payload: dict, *, updated_by: str) -> str` — one transaction: old row → `status='superseded'`; INSERT new row copying unspecified fields, `version = old.version + 1`, same `state` unless payload overrides; returns new id. Raises `LookupError` on missing/inactive row.
  - `set_state(cur, table, row_id, state: Literal["published","archived"], *, updated_by) -> None` — guard: archiving the `other` profile raises `ValueError("other profile is protected")`.
  - `rollback_to(cur, table, superseded_row_id, *, updated_by) -> str` — copy the superseded row as a fresh active+published row (version = current max for slug + 1), supersede the current active.
  - `active_featured_campaign_db(cur, today: date) -> CampaignConfig | None` — published+active, `featured AND active_start <= today <= active_end`.
  - `resolve_campaign_db(cur, slug: str | None) -> CampaignConfig` — slug empty/`"default"`/unknown/unpublished → `NEUTRAL_CAMPAIGN`.
- Also: campaign/profile row → dataclass mapping lives here (`_row_to_profile`, `_row_to_campaign`), reused by every later task.

- [ ] **Step 1: Failing tests** — resolve seeded FD by slug; unknown slug → NEUTRAL; featured window (2026-06-15 hit, 2026-07-14 miss); supersede-edit bumps version + keeps exactly one active row per slug; rollback restores the old content as a new active row; archiving `other` raises; `fetch_visual_theme_image` returns None for seeded classic (NULL image).
- [ ] **Step 2: Run to fail.**
- [ ] **Step 3: Implement**, then port `relationships.py` to use `fetch_profile_by_group`/list.
- [ ] **Step 4: Run to pass** (`python -m pytest tests/tribute/test_config_repository.py tests/tribute/test_relationships.py -v`).
- [ ] **Step 5: Commit** — `feat(crm): config repository (CRUD, supersession, publish, rollback)`

---

### Task 5: Deterministic composer

**Files:**
- Create: `src/flashback/tribute/composer.py`
- Test: `tests/tribute/test_composer.py`

**Interfaces:**
- Consumes: `ProfileConfig`, `CampaignConfig` (Task 2).
- Produces:
  - `@dataclass(frozen=True) ComposedDirectives(voice_block: str, opener_style: str, art_mood: str, fallback_opener: str, fallback_closing: str, deage_cover: bool, message_invitation_copy: str | None, bank: list[dict] | None, visual_theme_id: str | None)`
  - `compose_directives(profile: ProfileConfig, campaign: CampaignConfig) -> ComposedDirectives` — pure string assembly, no LLM:
    - `voice_block`: `"You are {narrator_stance}. Energy: {', '.join(energy_words)}. {emotion_rule}." + (" Never: {'; '.join(never)}." if never)`
    - `opener_style`: `"{style} Examples of the register (adapt, never copy verbatim): " + " | ".join(examples)`
    - `art_mood`: `"Overall visual mood: {', '.join(mood_words)}." + (" Avoid: {', '.join(avoid)}." if avoid)`
    - Override chain: `bank = campaign.archetype_bank_override or profile.archetype_bank`; `deage_cover = campaign.deage_cover_override if campaign.deage_cover_override is not None else profile.deage_cover`; `message_invitation_copy = campaign.message_card_copy or profile.message_invitation_copy`; `visual_theme_id = campaign.visual_theme_id or profile.visual_theme_id`.

- [ ] **Step 1: Failing tests** — golden-string assertions for the friend profile + NEUTRAL campaign; FD campaign over parent profile picks the 22-question override and deage True; campaign with `deage_cover_override=False` beats profile True; determinism (two calls, equal output).
- [ ] **Step 2–4:** fail → implement → pass.
- [ ] **Step 5: Commit** — `feat(crm): deterministic directive composer`

---

### Task 6: Assembler voice slots + profile-aware fallback

**Files:**
- Modify: `src/flashback/tribute_video/assembler.py`
- Test: `tests/tribute_video/test_assembler_slots.py` (create; follow existing assembler test file location if one exists — `Glob tests/**/test_*assembler*`)

**Interfaces:**
- Consumes: nothing new at import time (plain strings in, keeps module decoupled from config).
- Produces: `assemble_storybook_video(..., voice_block: str | None = None, opener_style: str | None = None, art_mood: str | None = None, fallback_opener: str = "", fallback_closing: str = "")`. `_fallback(...)` gains the same two template params; templates support `{name}` and `{relationship}` via `.format(name=..., relationship=...)` guarded so a missing key never raises (use `str.format_map` with a `defaultdict(str)`-style mapping).

- [ ] **Step 1: Failing tests**
  - Slot injection: monkeypatch `call_with_tool` to capture `system_prompt`; call with `voice_block="VOICEX"`, `opener_style="OPENX"`, `art_mood="ARTX"`; assert all three appear and the guardrail lines ("8 to 10 words", "NEVER a face") still appear.
  - Default equivalence: call with all-None slots; captured system must contain the current stanzas ("a loved one speaking, warm and proud", `Meet my {relationship}`) — i.e. legacy snapshots produce today's prompt.
  - Fallback templates: `settings=None` + `fallback_opener="Nobody warned me about {name}."` → `book.opener.line == "Nobody warned me about Arjun."`; empty template → current `"Meet my {rel}, {name}."` behavior.
- [ ] **Step 3: Implement** — restructure `_SYSTEM` into `_SYSTEM_TEMPLATE` with `{voice_slot}`, `{opener_slot}`, `{art_mood_slot}` placeholders; module constants `_DEFAULT_VOICE_SLOT`, `_DEFAULT_OPENER_SLOT`, `_DEFAULT_ART_MOOD_SLOT` hold the exact current text so `None` → identical prompt. Keep `{n}`/`{relationship}` substitution as-is.
- [ ] **Step 4: Run to pass** (also run any existing assembler tests: `python -m pytest tests -k assembler -v`).
- [ ] **Step 5: Commit** — `feat(crm): assembler voice/opener/art slots + templated fallback lines`

---

### Task 7: StyleKit — configurable template/fonts/inks/audio through the renderer

**Files:**
- Modify: `src/flashback/tribute_video/style.py` (add `StyleKit`, `DEFAULT_KIT`, `FONT_REGISTRY`, `AUDIO_REGISTRY`, `kit_from_style_dict`)
- Modify: `src/flashback/tribute_video/compose.py` (functions take `kit: StyleKit = DEFAULT_KIT`; replace direct `style.TEMPLATE_PATH`/`MAIN_FONT`/fill reads with kit fields)
- Modify: `src/flashback/tribute_video/render.py` (`render_book(..., kit: StyleKit = DEFAULT_KIT)`; audio default resolves via kit)
- Modify: `src/flashback/tribute_video/context.py` (`RenderContext.style: dict | None = None`, `profile_id: str = ""`, `campaign_id: str = ""`, `voice_block: str = ""`, `opener_style: str = ""`, `art_mood: str = ""`, `fallback_opener: str = ""`, `fallback_closing: str = ""` + `build_context_dict` mirrors)
- Modify: `src/flashback/workers/tribute_render/worker.py` (build kit from `ctx.style` — fetch template bytea by `visual_theme_id` via a small sync query in `persistence.py`, write to tmp file; pass slots into `assemble_storybook_video`; pass kit into `render_book`)
- Modify: `src/flashback/workers/tribute_render/persistence.py` (add `load_visual_theme_image_sync(pool, theme_id) -> tuple[bytes, str] | None`)
- Test: `tests/tribute_video/test_style_kit.py`, extend `tests/workers/` render-worker test if present (`Glob tests/**/test_*tribute_render*`)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) StyleKit(template_path: str, main_font: str, eyebrow_font: str, main_font_weight: int, main_fill: tuple[int,int,int], eyebrow_fill: tuple[int,int,int], audio_path: str)`
  - `DEFAULT_KIT = StyleKit(TEMPLATE_PATH, MAIN_FONT, EYEBROW_FONT, 560, (58,44,28), (150,118,72), AUDIO_PATH)`
  - `FONT_REGISTRY: dict[str, str] = {"playfair_italic": MAIN_FONT, "eb_garamond": EYEBROW_FONT}`; `AUDIO_REGISTRY: dict[str, str] = {"sentimental_piano": AUDIO_PATH}` — **expanding these is a content task: drop the file in assets and add one entry.** Unknown slug → fall back to the default entry (never raise).
  - `kit_from_style_dict(style: dict | None, *, template_override_path: str | None = None) -> StyleKit` — maps `{fonts:{main_slug,eyebrow_slug}, ink:{main_fill,eyebrow_fill}, audio_slug}` through the registries; hex → RGB tuple; None/missing anything → DEFAULT_KIT fields. `template_override_path` (worker's tmp file from DB bytes) wins over the built-in template path.
- Consumes: snapshot `style` dict shape written in Task 8:
  `{"visual_theme_id": str|None, "fonts": {...}, "ink": {...}, "audio_slug": str}`.

- [ ] **Step 1: Failing tests** — `kit_from_style_dict(None) == DEFAULT_KIT`; hex parsing (`"#112233"` → `(17,34,51)`); unknown slugs fall back; `RenderContext.from_dict` round-trips new fields and defaults them for a legacy dict (assert a dict WITHOUT the new keys yields `style=None`, empty slots); compose smoke: `_template(kit)` with a custom template path opens that image (write a 10×16 px JPEG in tmp_path via Pillow).
- [ ] **Step 3: Implement.** compose.py note: `_template()` currently does `Image.open(style.TEMPLATE_PATH)` — change to `Image.open(kit.template_path)` and thread `kit` from `render.py`'s per-page loop (`layout_for` stays untouched). Worker: only when `ctx.style` has a `visual_theme_id` AND the DB row has bytes does it write the tmp template file; else built-in path.
- [ ] **Step 4: Run** `python -m pytest tests/tribute_video tests/workers -v` — all pass, including pre-existing render tests (DEFAULT_KIT keeps them identical).
- [ ] **Step 5: Commit** — `feat(crm): StyleKit - config-driven template/fonts/inks/audio with builtin fallback`

---

### Task 8: Runtime rewiring — every touchpoint reads DB config

**Files:**
- Modify: `src/flashback/http/routes/themes.py` (unlock_prepare bank chain; add optional `campaign: str | None = None` to `UnlockPrepareRequest` in `src/flashback/http/models.py`)
- Modify: `src/flashback/orchestrator/steps/apply_theme_unlock.py` (resolve relationship group + stamp `tributes.campaign_id`)
- Modify: `src/flashback/orchestrator/steps/select_message_invitation.py` (copy chain campaign → profile → neutral)
- Modify: `src/flashback/orchestrator/steps/load_tribute_progress.py` + `src/flashback/tribute/progress.py` (progress takes the resolved `CampaignConfig`; callers resolve via `resolve_campaign_db`)
- Modify: `src/flashback/http/routes/tributes.py` (`_generate_video` resolves+composes+snapshots; `/tribute-campaigns` DB-backed; progress route resolves slug via DB)
- Delete: `src/flashback/tribute/campaigns.py` (registry) — port all imports; `flashback/tribute/theme.py`: delete `FATHERS_DAY_ARCHETYPE_BANK` + `build_fathers_day_archetype_questions` (content now lives in the 0039 seed)
- Modify: `src/flashback/themes/archetype_llm.py` — `generate_archetype_questions(...)` already accepts `subject_relationship`; ensure a new optional `extra_context: str = ""` param is appended to its user message when non-empty (campaign `archetype_extra_context`)
- Tests: `tests/http/test_themes_unlock_prepare_fd.py` (rewrite), `tests/tribute/test_fd_archetype_bank.py` (rewrite against seed), `tests/tribute/test_campaigns.py` (rewrite against repository), `tests/http/test_tribute_generate.py` (extend), new `tests/http/test_tribute_campaigns_db.py`

**Interfaces:**
- Consumes: Tasks 2–5, 7. All resolution helpers take `cur`.
- Produces (behavior contract):
  1. **unlock_prepare** (tribute kind, no cached questions): campaign = `resolve_campaign_db(cur, body.campaign)` falling back to `active_featured_campaign_db(cur, today)`; group = `ensure_relationship_group(...)`; profile = `fetch_profile_by_group(cur, group)` or `other`; `directives = compose_directives(profile, campaign)`; bank → `bank_to_archetype_questions(directives.bank)` when set, else `generate_archetype_questions(..., subject_relationship=<persons.relationship>, extra_context=campaign.archetype_extra_context, min=TRIBUTE_ARCHETYPE_MIN, max=TRIBUTE_ARCHETYPE_MAX)`. Non-tribute themes: unchanged.
  2. **apply_theme_unlock** (tribute kind): after `ensure_open_tribute_async`, resolve `session_metadata.campaign` slug → campaign row id (published only) and `UPDATE tributes SET campaign_id=%s WHERE id=%s AND campaign_id IS NULL`; call `ensure_relationship_group`. WM fields unchanged.
  3. **select_message_invitation**: replace `resolve_campaign(...)` block with: read tribute row's `campaign_id` (add `fetch_tribute_campaign_id_async(cur, tribute_id)` to `tribute/repository.py`) → `fetch_campaign_by_id` → else WM slug via `resolve_campaign_db` → copy chain `campaign.message_card_copy or profile.message_invitation_copy or MESSAGE_INVITATION_COPY` (profile via person's cached `relationship_group`; person_id available on `state`).
  4. **`_generate_video`**: resolve campaign (tribute row campaign_id → body.campaign fallback) + profile + `compose_directives` + visual theme config; `deage = directives.deage_cover and not body.cover_photo_is_prime_years`; extend `build_context_dict(...)` with the Task-7 fields (`style={"visual_theme_id":..., "fonts": vt.fonts, "ink": vt.ink, "audio_slug": vt.audio_slug}` or None when using builtin, `profile_id`, `campaign_id`, slots, fallbacks).
  5. **`GET /tribute-campaigns`**: same response shape, rows from `list_rows(..., "tribute_campaigns")` filtered `state='published'`, plus NEUTRAL first; `featured_today` from `active_featured_campaign_db`.
  6. **progress route + step**: `fetch_tribute_progress_async(cur, tribute_id=..., campaign=<CampaignConfig>, ...)` — check its current signature: it takes `campaign: Campaign`; change the type to `CampaignConfig` (field names match: `display_name`, `message_card_copy`).

- [ ] **Step 1: Failing tests** (write all, run to fail):
  - `unlock_prepare` with `campaign="fathers_day_2026"` on a tribute theme for a person with `relationship='father'` → exactly 22 questions, first is "Where did your father grow up?" (bank override wins; no LLM call — patch `generate_archetype_questions` to raise if called).
  - `unlock_prepare` with no campaign, person `relationship='best friend'` → 10 friend-bank questions (profile bank).
  - `unlock_prepare` no campaign, person `relationship='colleague'` (→ LLM classify patched to return `other`) → patched `generate_archetype_questions` called with `subject_relationship='colleague'`.
  - `/session/start`-driven `apply_theme_unlock` stamps `campaign_id` (unit-test the step directly with a fake state, seeded DB).
  - generate: `latest_generation_context['tribute_video']` contains `profile_id`, `campaign_id`, `voice_block` non-empty, `style.audio_slug == 'sentimental_piano'`; FD campaign + `cover_photo_is_prime_years=False` → `deage is True`; friend person neutral campaign → `deage is False`.
  - `/tribute-campaigns` returns `default` + `fathers_day_2026`, `featured_today is None` (Jul 14 outside window).
- [ ] **Step 3: Implement + port imports.** Files importing the deleted registry (from the Task-0 grep): `routes/tributes.py`, `routes/themes.py`, `steps/select_message_invitation.py`, `steps/load_tribute_progress.py`, `tribute/progress.py` — port each to `config_repository`. Update the module docstring note in `select_message_invitation.py` ("Plan 4's campaign skin" → "campaign/profile config, DB-backed").
- [ ] **Step 4: Run the full suite** — `python -m pytest tests -x -q` (known pre-existing failures per memory `test_environment` are acceptable; nothing NEW may fail).
- [ ] **Step 5: Commit** — `feat(crm): runtime resolves campaign/profile config from Postgres (code registry retired)`

---

### Task 9: Admin CRUD API

**Files:**
- Create: `src/flashback/http/routes/admin_tribute_config.py`
- Modify: `src/flashback/http/models.py` (request/response models), `src/flashback/http/app.py` (include router)
- Test: `tests/http/test_admin_tribute_config.py`

**Interfaces:**
- Consumes: Tasks 2, 4. Router pattern from `routes/admin.py`: `APIRouter(prefix="/admin", dependencies=[Depends(require_service_token), Depends(require_admin_service_token)])`.
- Produces endpoints (all JSON; `updated_by` from header `X-Admin-User` defaulting `"unknown"`):
  - `GET /admin/tribute_config/{table}` where `table ∈ {relationship_profiles, tribute_campaigns, visual_themes}` (map `visual_themes`→`tribute_visual_themes`), query `include_archived: bool = False` → `{"rows": [...]}` (visual themes: `has_image` bool, never bytes)
  - `POST /admin/tribute_config/{table}` — body `{payload: dict}` → validate (Task 2 validators; visual themes: fonts/ink/audio_slug required, slugs must exist in the registries — expose registries via Task 7 import; `template_image` in a payload is REJECTED with 422 — image bytes enter only through the Task-10 generation endpoint, which is where the ≤2 MB cap lives) → `create_row` → `{"id": ...}`; 422 with `{"errors": [...]}` on validation failure
  - `PUT /admin/tribute_config/{table}/{row_id}` — supersede-edit → `{"id": <new>, "version": n}`
  - `POST /admin/tribute_config/{table}/{row_id}/publish` — re-validate full row, then `set_state('published')`; response includes `warnings: []` — populated with `"featured window overlaps campaign '<slug>'"` when publishing a featured campaign whose window intersects another published featured campaign
  - `POST /admin/tribute_config/{table}/{row_id}/archive` — 409 `{"detail": "other profile is protected"}` on the guard
  - `POST /admin/tribute_config/{table}/{row_id}/rollback` — body `{to_row_id}` (a superseded row of the same slug) → new active id
  - `GET /admin/visual_themes/{row_id}/image` — `Response(content=bytes, media_type=mime)`; 404 when NULL
  - `GET /admin/asset-library` → `{"fonts": ["playfair_italic","eb_garamond"], "audio": ["sentimental_piano"]}` from the Task-7 registries

- [ ] **Step 1: Failing tests** — full lifecycle: create draft profile (valid payload) → edit → publish → list shows one active published v2 → rollback to v1 content → archive; invalid payload 422 with the `{name}` error; `other` archive 409; asset-library lists registries; image 404 on classic. Auth: request without `X-Admin-Service-Token` → 401/403 (mirror the assertion style in the existing admin route test — `Grep tests/http -l reset_phase`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to pass.**
- [ ] **Step 5: Commit** — `feat(crm): admin CRUD/publish/rollback API for tribute config`

---

### Task 10: Generate-first authoring (config drafts + template images)

**Files:**
- Create: `src/flashback/tribute/config_llm.py`
- Create: `src/flashback/tribute_video/template_gen.py`
- Modify: `src/flashback/config.py` (`HttpConfig` gains `gemini_api_key: str = field(default="", repr=False)`, `gemini_image_model: str = "gemini-2.5-flash-image"` — copy the default model id from the render worker config in the same file; wire both in `from_env` reading `GEMINI_API_KEY` / `GEMINI_IMAGE_MODEL`)
- Modify: `src/flashback/http/routes/admin_tribute_config.py` (+2 endpoints)
- Test: `tests/tribute/test_config_llm.py`, `tests/http/test_admin_generate.py`

**Interfaces:**
- Consumes: `call_with_tool`, `page_render.art.Artist`, Task 9 router.
- Produces:
  - `async generate_config_draft(settings, *, kind: Literal["profile","campaign"], relationship_group: str | None, occasion: str | None, brief: str) -> dict` — big-LLM `call_with_tool`, `feature="tribute_config_generate"`, timeout 60s, max_tokens 4000. Tool schema mirrors the payload shape (voice/opener/art objects, bank 8–12 questions × 4 options, copy fields). System prompt constraints: third-person address only; every opener example and fallback contains `{name}`; bank options ≤ 5 words each; never "Meet my friend"-style formal introductions for peer groups. Returns the raw dict; route validates with Task-2 validators before storing.
  - `async generate_template_candidates(artist: Artist, *, brief: str, n: int) -> list[bytes]` — n ≤ 4 sequential `artist` calls; prompt embeds the LAYOUT CONTRACT verbatim: "899x1600 portrait page background, decorative border only; the horizontal band from 18% to 46% of the height and the band from 47% to 98% must stay calm, low-texture, near-uniform so text and a pasted illustration remain legible; no text, no figures, no faces anywhere; painterly, print-quality" + the brief. Returns JPEG bytes (Artist output re-encoded via Pillow to JPEG, quality 88 stepping down by 8 until ≤ 2 MB — the spec's size cap, enforced at the only door images enter through). Usage tag: read `page_render/art.py` first — if `Artist.generate` accepts a `feature`/label param pass `"tribute_template_generate"`; if not, add an optional `feature` param to it defaulting to its current behavior and pass it here.
  - Endpoints: `POST /admin/tribute_config/generate` `{kind, relationship_group?, occasion?, brief}` → `{"payload": {...}, "errors": [...]}` (validated, NOT stored); `POST /admin/visual_themes/generate` `{brief, display_name, slug, n_candidates=3, fonts?, ink?, audio_slug?}` → creates n draft `tribute_visual_themes` rows (fonts/ink/audio default to classic's) → `{"candidates": [{"id", "version"}...]}` (CRM fetches images via the Task-9 image GET). 503 when `gemini_api_key` unset.

- [ ] **Step 1: Failing tests** — `generate_config_draft` with patched `call_with_tool` returning a canned friend payload → returned unmodified; route test: generate endpoint returns validated payload and `errors=[]`; visual generate with a patched `Artist.generate` (returns 1×1 JPEG bytes) creates 2 draft rows with images retrievable via the image GET; unset gemini key → 503.
- [ ] **Step 3: Implement.** (Check `page_render/art.py` for the Artist method name + signature before writing `template_gen.py` — reuse, don't subclass.)
- [ ] **Step 4: Run to pass.**
- [ ] **Step 5: Commit** — `feat(crm): generate-first authoring (config drafts + template candidates)`

---

### Task 11: Preview — Book + one composited sample page

**Files:**
- Create: `src/flashback/tribute/preview.py`
- Modify: `src/flashback/http/routes/admin_tribute_config.py` (+1 endpoint), `src/flashback/http/models.py`
- Test: `tests/tribute/test_preview.py`, `tests/http/test_admin_preview.py`

**Interfaces:**
- Consumes: `assemble_storybook_video` (Task 6), `compose_directives`, resolver, `fetch_scene_moments_async`/`fetch_theme_scene_moments_async` (existing, see `routes/tributes.py`), `kit_from_style_dict` + `compose` page functions (Task 7), `Artist`.
- Produces:
  - `async build_preview(settings, cur, *, person_id, profile: ProfileConfig, campaign: CampaignConfig, visual_theme: VisualThemeConfig | None) -> dict` — resolves candidates (theme-tagged → qualifying pool, exactly like `_generate_video`), composes directives, runs the real assembler (feature: pass `feature="tribute_preview"` through — add an optional `feature: str = "tribute_video"` param to `assemble_storybook_video` and forward it to `call_with_tool`), returns `{"book": {cover_title, opener, beats[], closing, message}, "resolved": {profile_id, campaign_id, visual_theme_id, group_slug}}`.
  - `render_sample_page(artist, *, book, kit, role: str = "opener", beat_index: int = 0) -> bytes` — one `Artist` art image for the chosen page's art_direction, composite via the Task-7/compose path (same function the renderer uses for a single page), JPEG bytes.
  - Endpoint `POST /admin/tribute_preview` — body `{person_id, profile_id?, profile_draft?, campaign_id?, campaign_draft?, visual_theme_id?, render_sample_page: bool = False, sample_page_role: str = "opener"}`; drafts are payload dicts validated inline (422 on errors); profile omitted → resolve from person; campaign omitted → NEUTRAL. Response `{book, resolved, sample_page_b64: str | None}`.
  - Rate limit: module-level in-process limiter `allow(key: str, per_minute: int) -> bool` (deque of timestamps) — preview 6/min, generate endpoints 4/min, keyed by `X-Admin-User`; 429 on exceed. Lives in `admin_tribute_config.py`.

- [ ] **Step 1: Failing tests** — `build_preview` with patched assembler returns book + resolved ids (draft profile dict path too); endpoint with `render_sample_page=True` + patched Artist returns base64 that decodes to a JPEG; 429 on the 7th call in a minute (freeze the limiter clock via monkeypatched `time.monotonic`); invalid draft → 422.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to pass.**
- [ ] **Step 5: Commit** — `feat(crm): preview endpoint (real assembly + composited sample page)`

---

### Task 12: Docs, Node work-order, retirement sweep, full verify

**Files:**
- Modify: `CLAUDE.md` (§3 boundary note: tribute config tables agent-owned + CRM write path; §9 new admin endpoints one-liner), `API.md` (admin endpoints + changed `unlock_prepare`/`tribute-campaigns` shapes), `NODE_INTEGRATION.md` (CRM section)
- Create: `docs/TRIBUTE_CRM_NODE_PROMPT.md` (the work-order)
- Test: full suite + grep sweeps

**Interfaces:** none new — this is closure.

- [ ] **Step 1: Retirement sweep** — `Grep -l "tribute.campaigns" src tests` and `Grep -l "FATHERS_DAY" src tests` must return nothing (Task 8 should have cleared these; fix stragglers). `Grep "resolve_campaign\(" src` → only `resolve_campaign_db`.
- [ ] **Step 2: Write the Node work-order** `docs/TRIBUTE_CRM_NODE_PROMPT.md`, following the structure of `docs/TRIBUTE_VIDEO_NODE_PROMPT.md`. Content requirements (verified against the Node repo skim 2026-07-14):
  - New Node proxy routes under the existing dashboard-admin gate (`requireDashboardAdmin`, `server.js` unless-list addition) → agent admin API via `agentClient.call(..., {admin: true})` (the `X-Admin-Service-Token` path that already exists); pass the Node admin's identity as `X-Admin-User`.
  - Proxy surface: list/create/edit/publish/archive/rollback for the three tables, asset-library, visual-theme image (stream bytes), config generate, visual generate, preview.
  - Runtime changes: forward `campaign` on `POST /themes/{id}/unlock_prepare` (body field, already validated ≤64 chars in `ConversationController.extractThemeContext` style); everything else (session_metadata.campaign, generate, progress) already ships.
  - FE contract section (their `FRONTEND_*.md` pipeline): screens = campaign list/editor (generate-first), profile editor (chips + bank rows), visual theme flow (brief → ≤4 candidates → pick → sample page), preview panel (person picker → Book text + sample image + "render sample page" as a separate button), publish with diff + rollback + audit (list superseded versions).
  - Sandbox full-video check: run the normal generate flow on a designated test legacy.
- [ ] **Step 3: Update the three contract docs** (CLAUDE.md/API.md/NODE_INTEGRATION.md) — keep each addition tight; CLAUDE.md gets one new hard-rule bullet: "Tribute occasion/relationship config lives in `tribute_campaigns` / `relationship_profiles` / `tribute_visual_themes`, written ONLY via the agent admin API; render reads snapshots, never live config."
- [ ] **Step 4: Full verify** — `python -m pytest tests -q` (no NEW failures vs the known list in memory `test_environment`); `python -m compileall src` clean.
- [ ] **Step 5: Commit** — `docs(crm): contract docs + Node CRM work-order; retire code-registry remnants`

---

## Post-plan launch checklist (not code tasks — tracked outside this plan)

1. Deploy agent (migration 0039 runs; FD behavior regression-checked by Task 8 tests).
2. Hand `docs/TRIBUTE_CRM_NODE_PROMPT.md` to the Node repo (target: screens by Jul 20).
3. Content person (with the user) drafts Friendship Day in the CRM: generate → tune → preview → sample page → publish with window Jul 28 – Aug 3.
4. **User-supplied assets needed for a properly playful Friendship Day kit:** 1–2 licensed upbeat audio tracks (mp3) and optionally 1–2 OFL playful fonts (e.g. Caveat, Nunito) dropped into `src/flashback/tribute_video/assets/` + one registry entry each (Task 7 made this a two-line change). Until then the classic fonts/track apply.
5. Dry run on the sandbox legacy Jul 20–21; done Jul 21.
