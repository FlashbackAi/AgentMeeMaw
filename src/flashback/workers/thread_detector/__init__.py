"""Thread Detector worker package (step 12).

Per ARCHITECTURE.md §3.13, this is a periodic background job that
clusters moments into emergent narrative threads. It runs on a
count-based cadence — every 15 new active moments per person — driven
by SQS messages produced by the Extraction Worker post-commit
(see ``flashback.workers.extraction.thread_trigger``).
"""
