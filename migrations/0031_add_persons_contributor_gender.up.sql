-- The contributor (the person talking) appears in some moment scenes
-- alongside the subject ("my father and I on a bike"). v1 is single-
-- contributor per legacy, so the contributor's gender lives on the
-- agent-owned persons row (CLAUDE.md §3). Pronoun form he/she/they, NULL
-- when unstated. persons.gender remains the SUBJECT's gender.
ALTER TABLE persons ADD COLUMN contributor_gender TEXT;
