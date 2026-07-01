-- SP6b: capture both merged entities' first-introducer provenance on the
-- suggestion/auto-merge record at creation time, so cross-contributor merges
-- can be surfaced (the survivor's told_by is rewritten at merge time and the
-- original pair is otherwise unrecoverable). Nullable; NULL = creator era.
ALTER TABLE identity_merge_suggestions
    ADD COLUMN source_told_by_user_id UUID,
    ADD COLUMN target_told_by_user_id UUID;
