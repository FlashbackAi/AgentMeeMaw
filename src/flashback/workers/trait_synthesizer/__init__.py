"""Trait Synthesizer worker package (step 13).

Per ARCHITECTURE.md §3.14, this is a small-LLM background job that
walks the existing traits and the active threads for a single person
and decides:

* which existing traits to promote / demote / leave alone along the
  ladder ``mentioned_once → moderate → strong → defining``;
* which new traits the threads support that no existing trait
  captures.

The worker drains the ``trait_synthesizer`` SQS queue (one message
per person, produced by Session Wrap — step 16). A ``run-once``
CLI subcommand executes the same logic synchronously for a single
person, for testing and ops.
"""
