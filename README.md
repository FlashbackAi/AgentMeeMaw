# Flashback Agent Service

Flashback's Python agent service powers Legacy Mode conversations for
preserving memories of deceased loved ones. It owns the conversational
turn loop, ephemeral Working Memory in Valkey, canonical graph writes in
Postgres, LLM extraction and synthesis workers, and SQS producer paths
that move captured memories through embedding, artifact, trait, profile,
thread, and question-generation workflows.

## Step Map

| Step | Component | Primary locations |
|---|---|---|
| 1 | Schema migrations | `migrations/`, `SCHEMA.md` |
| 2 | Starter question seed | `migrations/0002*`, `QUESTION_BANK.md` |
| 3 | Embedding worker | `flashback.workers.embedding`, `flashback.db.embedding_targets` |
| 4 | Gateway and Working Memory | `flashback.http`, `flashback.working_memory` |
| 5 | Intent Classifier | `flashback.intent_classifier` |
| 6 | Retrieval Service | `flashback.retrieval` |
| 7 | Response Generator | `flashback.response_generator` |
| 8 | Phase Gate and question selection | `flashback.phase_gate` |
| 9 | Turn Orchestrator | `flashback.orchestrator` |
| 10 | Segment Detector | `flashback.segment_detector`, `orchestrator.steps.detect_segment` |
| 11 | Extraction Worker | `flashback.workers.extraction` |
| 12 | Thread Detector | `flashback.workers.thread_detector` |
| 13 | Trait Synthesizer | `flashback.workers.trait_synthesizer` |
| 14 | Profile Summary Generator | `flashback.workers.profile_summary` |
| 15 | Question Producers P2/P3/P5 | `flashback.workers.producers` |
| 16 | Session Wrap | `orchestrator.steps.wrap_session`, `flashback.session_summary`, `flashback.queues` |

## Local Run

Local development needs Postgres with pgvector, Valkey, AWS-compatible
SQS queues, the HTTP service, and the long-running workers. Copy
`.env.example` to `.env`, fill in API keys and queue URLs, then apply
the migrations to the database.

Run the HTTP service:

```bash
uvicorn flashback.http.app:create_app --factory --host 0.0.0.0 --port 8000
```

Run workers in separate processes:

```bash
python -m flashback.workers.extraction run
python -m flashback.workers.embedding run
python -m flashback.workers.thread_detector run
python -m flashback.workers.trait_synthesizer run
python -m flashback.workers.profile_summary run
python -m flashback.workers.producers run-per-session
python -m flashback.workers.producers run-weekly
```

The steady-state system is the HTTP process plus worker processes for
extraction, embedding, thread detection, trait synthesis, profile
summary, and question production. Node remains responsible for auth,
DynamoDB session logs, UI reads, and artifact generation queue
consumption.

## Session Lifecycle

Node calls `/session/start`, then `/turn` for each contributor message,
then `/session/wrap` when the contributor ends. Wrap force-closes the
tail segment with the Segment Detector's `force=True` path, returns a
short next-session summary fragment, pushes trait/profile/P2 jobs in
parallel, and clears Working Memory.

`/session/wrap` returns `metadata.segments_extracted_count`. This
replaces the step-4 stub field `moments_extracted_estimate`.

## Identity Merge Review

When extraction or the identity-merge scanner detects a likely entity
identity correction, it writes a pending `identity_merge_suggestions`
row instead of merging automatically. The scanner gates candidates with
deterministic labels and uses embedding distance as supporting context
before a small LLM verifier. Node/UI should surface suggestions out-of-band, such as a toast:
"Old label may be Person B. Merge?" Approval calls
`POST /identity_merges/suggestions/{id}/approve`; rejection calls
`POST /identity_merges/suggestions/{id}/reject`.

Approval repoints edges, marks the losing entity `merged`, folds aliases
into the survivor, clears the survivor embedding fields, and queues a
fresh entity embedding job.

## What's Next

Deferred production work includes streaming responses, prompt
versioning, an eval harness, observability dashboards, weekly scheduling
for P3/P5, artifact-generation ops with Node, deployment runbooks,
backfill tooling for model upgrades, production polish for merge-review
surfaces, and human review surfaces for contradiction handling.
