"""Regression guards for the node_edits prompt shape.

These tests don't run the LLM — they pin the load-bearing instructions in
the system prompts so a future edit can't accidentally re-introduce the
collapse-on-short-edit bug we hit on v1 (where a 4-word refinement like
"It was RDR2-like." sometimes replaced the entire moment narrative).
"""

from flashback.node_edits.prompts import (
    ENTITY_EDIT_PROMPT_VERSION,
    ENTITY_EDIT_SYSTEM_PROMPT,
    MOMENT_EDIT_PROMPT_VERSION,
    MOMENT_EDIT_SYSTEM_PROMPT,
)


class TestMomentEditPromptIsMergeFirst:
    def test_calls_out_merge_semantics_heading(self):
        assert "MERGE SEMANTICS" in MOMENT_EDIT_SYSTEM_PROMPT

    def test_omission_is_not_deletion(self):
        # Both the inline rule and the CRITICAL RULES restatement must
        # stay so the LLM sees the constraint twice.
        prompt = MOMENT_EDIT_SYSTEM_PROMPT
        assert "Omission" in prompt
        assert prompt.count("NOT a deletion signal") + prompt.count("Omission ≠ deletion") >= 2

    def test_default_keep_prior_detail(self):
        assert "Default to KEEPING every prior detail" in MOMENT_EDIT_SYSTEM_PROMPT

    def test_worked_example_is_present(self):
        # The concrete RDR2 example is the strongest signal to the LLM
        # that a short stylistic note must be MERGED, not used to
        # overwrite the narrative.
        prompt = MOMENT_EDIT_SYSTEM_PROMPT
        assert "WORKED EXAMPLE" in prompt
        assert "It was RDR2-like." in prompt
        assert "Do NOT emit" in prompt

    def test_full_rewrite_is_explicit_exception(self):
        # Carve-out for the rare case where the contributor actually
        # pastes a complete replacement narrative.
        assert "full-rewrite" in MOMENT_EDIT_SYSTEM_PROMPT
        assert "exception" in MOMENT_EDIT_SYSTEM_PROMPT.lower()

    def test_old_replacement_phrasing_is_gone(self):
        # These were the load-bearing strings that biased v1 toward
        # wholesale replacement. They must not come back.
        assert "the new narrative they want stored" not in MOMENT_EDIT_SYSTEM_PROMPT
        assert "drop what the new text removes" not in MOMENT_EDIT_SYSTEM_PROMPT
        assert (
            "Do not silently re-introduce details from the prior version"
            not in MOMENT_EDIT_SYSTEM_PROMPT
        )

    def test_version_bumped(self):
        # Provenance: v1 → v2 so logs distinguish before/after fix.
        assert MOMENT_EDIT_PROMPT_VERSION == "node_edits.moment.v2"


class TestEntityEditPromptIsMergeFirst:
    def test_calls_out_merge_semantics_heading(self):
        assert "MERGE SEMANTICS" in ENTITY_EDIT_SYSTEM_PROMPT

    def test_omission_is_not_deletion(self):
        assert "Omission" in ENTITY_EDIT_SYSTEM_PROMPT
        assert "NOT a deletion signal" in ENTITY_EDIT_SYSTEM_PROMPT

    def test_default_keep_prior_detail(self):
        assert "Default to KEEPING every detail" in ENTITY_EDIT_SYSTEM_PROMPT

    def test_full_rewrite_is_explicit_exception(self):
        assert "full-rewrite" in ENTITY_EDIT_SYSTEM_PROMPT
        assert "exception" in ENTITY_EDIT_SYSTEM_PROMPT.lower()

    def test_old_replacement_phrasing_is_gone(self):
        # v1 said "the new description" + "usually the contributor's
        # edit" which biased the LLM toward wholesale replacement.
        assert (
            "The CONTRIBUTOR'S EDITED TEXT — the new description"
            not in ENTITY_EDIT_SYSTEM_PROMPT
        )
        assert (
            "usually the contributor's edit, lightly copy-edited"
            not in ENTITY_EDIT_SYSTEM_PROMPT
        )

    def test_immutable_name_and_kind_rule_preserved(self):
        # This rule predates the bug fix and must survive the rewrite.
        assert "DO NOT change the entity's name or kind" in ENTITY_EDIT_SYSTEM_PROMPT

    def test_version_bumped(self):
        assert ENTITY_EDIT_PROMPT_VERSION == "node_edits.entity.v2"
