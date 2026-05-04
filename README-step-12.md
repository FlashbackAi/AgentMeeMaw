# Step 12 — Thread Detector

Step 12 stands up the **Thread Detector**: a long-running worker that
clusters moments into emergent narrative threads. Per
ARCHITECTURE.md §3.13, it runs on a count-based cadence —
**every 15 new active moments per legacy** — driven by SQS messages
the Extraction Worker pushes after a successful commit.

This is the third long-running worker in the system, alongside the
Embedding Worker (step 3) and the Extraction Worker (step 11).

## What landed

### Migration

- `migrations/0004_thread_detector_support.{up,down}.sql` — empty
  placeholder for build-order consistency. The `threads` table, the
  generic `edges` table, and `persons.moments_at_last_thread_run` all
  exist from migration 0001, so no schema changes were required.

### Step-11 modification

- `src/flashback/workers/extraction/thread_trigger.py` — converted
  from log-only to actual SQS push. The new
  `check_and_push_thread_detector_trigger(...)` reads the same
  counts the old `check_thread_detector_trigger(...)` did and, when
  the gate is satisfied, pushes a JSON body
  `{ person_id, active_count_at_trigger, last_count_at_trigger }`
  to the new `thread_detector` SQS queue.
- `src/flashback/workers/extraction/worker.py` — picks up a new
  `thread_detector_sender` dependency and calls the push function
  in the post-commit fan-out.
- `src/flashback/workers/extraction/__main__.py` — wires
  `THREAD_DETECTOR_QUEUE_URL` into the new sender.
- `tests/workers/extraction/test_thread_trigger.py` — flipped from
  "asserts log fires" to "asserts SQS push fires".

### New package

```
src/flashback/workers/thread_detector/
├── __init__.py
├── __main__.py            # CLI: `python -m flashback.workers.thread_detector run`
├── worker.py              # Drain loop + per-message orchestration
├── trigger_check.py       # Re-validate the 15-moment gate on receive
├── clustering.py          # HDBSCAN wrapper (cosine via normalized euclidean)
├── matching.py            # Centroid → existing-thread vector search
├── naming_llm.py          # Sonnet wrapper for thread naming
├── p4_llm.py              # Sonnet wrapper for thread_deepen questions
├── prompts.py             # System prompts + tool defs
├── persistence.py         # Per-cluster transactions + post-commit pushes
├── schema.py              # Pydantic models + Cluster dataclass
└── sqs_client.py          # Producer + consumer for the thread_detector queue
```

### Tests

`tests/workers/thread_detector/` — 9 test files, ~30 cases:

- `test_clustering.py` — pure HDBSCAN behaviour (no DB).
- `test_trigger_check.py` — 15-moment gate validation + baseline update (DB).
- `test_matching.py` — closest-thread lookup, threshold, model + person_id scoping (DB).
- `test_naming_llm.py` — coherent / incoherent / timeout shapes.
- `test_p4_llm.py` — themes invariant, 1–2 question bounds.
- `test_persistence.py` — new-thread, existing-match, incoherent rollback,
  ON CONFLICT idempotence, model/active filtering on cluster fetch (DB).
- `test_persistence_supersession_safety.py` — superseded moments invisible to clustering (DB).
- `test_worker.py` — end-to-end with 18 moments forming 2 themes (DB),
  stale-trigger ack-only, single-cluster failure, run-level failure no-ack,
  too-few-moments fast-path.

### Configuration

`src/flashback/config.py` adds:

- `ExtractionConfig.thread_detector_queue_url` (so the step-11 worker can push).
- `ThreadDetectorConfig` — full env block for the new worker, including:
  `THREAD_DETECTOR_QUEUE_URL`, `THREAD_DETECTOR_MIN_CLUSTER_SIZE`,
  `THREAD_DETECTOR_EXISTING_MATCH_DISTANCE`, and the
  `LLM_THREAD_NAMING_*` / `LLM_P4_*` families (default to `LLM_BIG_*`).

`.env.example` and `pyproject.toml` updated. New runtime deps:
`numpy>=1.26,<3` and `hdbscan>=0.8.40,<0.9`.

## How it works

### Cadence (CLAUDE.md §4 invariant #14)

The trigger is a count-based gate, NOT a wall-clock cadence:

```
active_count >= 15 AND active_count - moments_at_last_thread_run >= 15
```

The Extraction Worker re-evaluates this after every successful commit
and pushes a single SQS message when it fires. The Thread Detector
re-runs the check on receive and ack-skips stale messages — so
duplicate pushes, late deliveries, and races between extractions are
all benign.

### Clustering (HDBSCAN, cosine via normalized euclidean)

`hdbscan` doesn't ship a cosine metric. The standard workaround is to
L2-normalize input vectors and use Euclidean distance: on unit vectors,
`||a-b||² = 2 - 2·cos(a, b)`, so Euclidean distance is monotonic with
cosine distance. `clustering.py` does that explicitly.

Parameters:
- `min_cluster_size = 3` — a thread needs at least 3 supporting moments
  to be worth detecting. Tunable via `THREAD_DETECTOR_MIN_CLUSTER_SIZE`.
- Outliers (HDBSCAN label `-1`) are **dropped**, never force-clustered.
- Cluster `confidence` is the mean per-point membership probability
  (HDBSCAN's `probabilities_`), persisted as `threads.confidence`.

### Match-or-create (cosine distance threshold 0.4)

For each cluster, `matching.match_existing_thread` looks up the closest
active thread for the same legacy and embedding model identity. If the
cosine distance is below `THREAD_DETECTOR_EXISTING_MATCH_DISTANCE`
(default 0.4), we link new evidences to that thread. Otherwise we name
a new one. The threshold is slightly looser than refinement detection
(0.35) because threads are higher-level aggregates than moments.

### Per-cluster transactions and partial-progress semantics

Each cluster is processed independently:

1. Read-only match-or-create lookup.
2. (New-thread path only) Naming LLM call **outside** any transaction.
3. **Transaction A** — insert thread row (or no-op for matches) and
   `evidences` edges (`ON CONFLICT DO NOTHING` so retries are safe).
4. P4 LLM call **outside** any transaction.
5. **Transaction B** — insert `thread_deepen` questions + their
   `motivated_by` edges back to the thread.
6. Post-commit: thread embedding job (new only) + thread artifact job
   (new only) + per-question embedding jobs.

Splitting into two transactions keeps Sonnet calls out of the DB, which
is important because both calls can take 10s+. The two-phase split is
safe because `persons.moments_at_last_thread_run` is the only thing
that changes the trigger baseline, and it is only updated at the END
of a successful run. If the worker crashes between A and B, the next
trigger fires again, the existing thread now matches the cluster, the
naming step is skipped, and the missing P4 questions are filled in.

If a single cluster's persistence raises, the run continues with the
remaining clusters. The baseline update happens iff at least one
cluster was processed. A run-level failure (e.g., DB outage during the
moment fetch) leaves the message in flight so SQS can redrive.

### When `moments_at_last_thread_run` is updated

`trigger_check.update_moments_at_last_thread_run` runs in its own
transaction at the end of a successful run, setting the column to the
current count of active moments. After this update, a fresh trigger
requires another 15 new active moments to fire.

If the run did not process any clusters successfully, the baseline is
NOT updated — the trigger fires again on retry.

### LLM cost shape

Per run with N new clusters and M existing-thread links:
- N naming LLM calls (Sonnet)
- (N + M) P4 LLM calls (Sonnet)
- Total: **2N + M** Sonnet calls.

For a typical 15-moment trigger that yields 2 new clusters and 1
existing-thread link: 5 Sonnet calls per run.

### Embedding model isolation (CLAUDE.md §4 invariant #3)

Both the moment fetch (`fetch_clusterable_moments`) and the existing-
thread lookup (`match_existing_thread`) filter on the worker's
configured `(embedding_model, embedding_model_version)` pair. Moments
or threads still on a stale model are invisible to this run; they are
picked up after the embedding worker re-embeds them on the new model.

NULL-embedding moments (just-extracted, embedding still queued) are
skipped — they'll be picked up on the next trigger.

## Verified

- ✅ `python -m pytest tests/workers/thread_detector` — all cases pass
  against a live test database.
- ✅ `python -m pytest tests/workers/extraction/test_thread_trigger.py`
  — converted tests pass; assert SQS push.
- ✅ Entire repo `python -m pytest` — green.
- ✅ Migration `0004` applies and rolls back cleanly via the test
  fixture's `schema_applied` setup.
- ✅ End-to-end scenario in `test_worker.py`:
  - Seeded 18 moments forming 2 themes (cabin / workshop).
  - Pushed a trigger message; ran the worker once.
  - Asserted: 2 thread rows, 18 evidences edges, 2 thread_deepen
    questions, `moments_at_last_thread_run = 18`, SQS message acked,
    4 embedding jobs (2 thread + 2 question) and 2 artifact jobs
    pushed post-commit.
- ✅ `python -m flashback.workers.thread_detector run` exits with
  argparse help when no subcommand is passed and otherwise opens the
  pool / SQS clients exactly like its siblings.

## Out of scope (deferred)

- Trait Synthesizer (step 13).
- Profile Summary Generator (step 14).
- Producers P2/P3/P5 (step 15).
- Session Wrap (step 16).
- User-driven thread merge / split / archive — v2.
- "Partial match" review queue from ARCHITECTURE.md §3.13 — v1 treats
  partial as no-match.
- Thread description refresh on existing-thread match — we link new
  evidences but don't regenerate the description. Future: when an
  existing thread accumulates significantly more evidence, regenerate
  via LLM.
- Worker observability beyond structured logs — same as steps 3 and 11.

## Deviations from the prompt and edge cases worth flagging

1. **Two transactions per cluster instead of one.** The prompt sample
   nested both LLM calls (naming + P4) inside a single per-cluster
   transaction. We split them out: thread + evidences in one txn,
   questions + motivated_by edges in a second txn, with both LLM calls
   in between. Reason: holding a Postgres connection open across a
   Sonnet round-trip ties up pool capacity for ~10–30s and risks
   network-level transaction aborts. The two-phase split preserves the
   "partial progress is OK" property described in the prompt because
   the trigger baseline (`moments_at_last_thread_run`) is only updated
   at the end of the run, and the second-phase write is idempotent on
   retry via the existing-thread match path.

2. **Cluster confidence comes from HDBSCAN's `probabilities_`.** The
   prompt referenced `cluster.confidence` without specifying its source;
   we use the mean per-point membership probability, clipped to [0, 1].
   This lands in `threads.confidence` (REAL).

3. **Two `check_*` entry points on the trigger module.** We kept the
   read-only `check_thread_detector_trigger` (what step 11 originally
   shipped) alongside the new pushing variant
   `check_and_push_thread_detector_trigger`. Reason: tests and any
   future analysis paths can observe the trigger state without
   producing an SQS side-effect. The Extraction Worker uses the
   pushing variant.

4. **Edge case: superseded moments.** Per invariant #1, the cluster
   fetch reads `active_moments`. A moment that was superseded between
   extraction and detection is invisible to this run, even if it once
   carried an embedding. `test_persistence_supersession_safety.py`
   exercises this end-to-end. Note that any `evidences` edges from a
   prior run that pointed at the now-superseded moment have already
   been repointed to its successor by the extraction worker's
   supersession step (invariant #5), so the canonical graph stays
   consistent across the boundary.

5. **Edge case: stale-model moments and threads.** The cluster fetch
   and the existing-thread lookup both filter on the worker's
   configured `(embedding_model, embedding_model_version)`. A model
   roll-forward therefore quiets the Thread Detector for a legacy
   until the embedding worker has caught up. We considered an explicit
   "skipped: stale model" log; today we rely on the run logging
   `not_enough_moments` (or simply finding zero clusters) when the
   stale-model fraction is too high.

6. **Edge case: cluster including newly-superseded moment ids.** If
   the worker had already cached a moment id and the row is superseded
   between fetch and `process_cluster`, the `evidences` edge insert
   would still succeed (no FK on `edges`). The next extraction's
   supersession step would repoint that edge to the successor moment
   in the same transaction it marks the old row superseded. We don't
   re-validate moment status inside `process_cluster` — the timing
   window is small and the canonical graph self-heals via the existing
   supersession edge-repoint logic. Worth revisiting if it shows up in
   logs.

7. **Edge case: "same cluster" can change shape on retry.** HDBSCAN is
   not stable under input changes; a second run after a partial failure
   may produce slightly different clusters. The match-or-create path
   handles this naturally: similar clusters land back at the same
   thread (by centroid distance), `evidences` edges are idempotent,
   and the only observable difference is one extra P4 question pair on
   the thread. Acceptable for v1.
