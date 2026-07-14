-- 0039: Tribute Campaign CRM — occasion/relationship config moves to Postgres.
-- Spec: docs/superpowers/specs/2026-07-14-tribute-campaign-crm-design.md
--
-- Three config tables, one lifecycle pattern: state (draft|published|archived)
-- is the CRM lifecycle (runtime reads published only); edits use the house
-- supersession pattern (status flip + new row, version increments). Snapshot
-- provenance pins row ids — supersession = new row = new id.
-- Node never writes these tables (no grants); all writes go through the
-- agent admin API.

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

-- ----------------------------------------------------------------------------
-- Seeds. All seed copy is third-person address (spec section 3.5): a future
-- address_mode must stay additive, never a content rewrite.
-- ----------------------------------------------------------------------------

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
-- Window is past (inert); bank pinned as the campaign override — exactly the
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
