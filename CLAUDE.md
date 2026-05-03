# CLAUDE.md — Flashback AI: Legacy Mode (Agent Service)

This file is the operating manual for any contributor — Claude Code or
human — working in **this repo**. Read it before touching code.

> **Repo scope:** This repo is the **Python agent service only**. The
> Node.js Backend lives in a **separate repo**. We do not edit Node code
> here. We define the contract with Node and stay on our side of it.

---

## 1. Product context

Legacy Mode preserves memories of **deceased loved ones**. A surviving
contributor (spouse, child, sibling, friend) talks with the agent about a
specific person, and the system progressively builds a structured,
evidence-linked memory graph of that person's life.

This is **grief technology**. A few rules that flow from that:

- We do **not** build a "talk to Dad" chatbot. The agent is an
  interviewer/archivist, not an impersonator.
- We do **not** clone voices or generate photoreal video of the deceased
  in v1.
- We **do** generate Pixar-style stylized artifacts (images for persons /
  threads / entities, short videos for moments) for visual texture.
- The agent must never feel like a survey. Cold openers, dropped
  references, and emotional pacing all matter.
- DOB / DOD are deliberately **not** stored on `persons`. Asking up
  front is cold. Lifespan is derived from moment time anchors.

---

## 2. Tech stack (this repo)

| Concern | Choice |
|---|---|
| Language / runtime | Python |
| LLMs | Anthropic — Haiku for small calls, Sonnet for big calls |
| Embeddings | Voyage AI (`voyage-3` or `voyage-3-large`, 1024-dim) |
| Canonical graph | Postgres + pgvector |
| Working memory | Valkey (Redis-compatible) |
| Queues | AWS SQS — `extraction`, `embedding`, `artifact_generation` |

External dependencies we **call** but do not own: the Node Backend
(REST), Anthropic API, Voyage API, Postgres, Valkey, SQS.

---

## 3. Service boundaries — what's ours, what isn't

```
┌──────────┐     ┌──────────────────┐     ┌─────────────────────┐
│ Frontend │ ──▶ │ Node.js Backend  │ ──▶ │  THIS REPO          │
└──────────┘     │  (separate repo) │     │  Python Agent Svc   │
                 └────┬─────────────┘     └────┬────────────────┘
                      │                        │
                ┌─────┼─────┐         ┌────────┼─────────────────┐
                ▼     ▼     ▼         ▼        ▼                 ▼
            DynamoDB  S3  Postgres  Postgres  Valkey      SQS (3 queues)
            (Node)  (Node) (reads,  (writes,  (working    extraction
                          Node)     ours)     memory,     embedding
                                              ours)       artifact_gen
```

### What this repo owns

- The **Turn loop**: Conversation Gateway, Turn Orchestrator, Phase
  Gate, Intent Classifier, Retrieval Service, Response Generator.
- The **Segment loop**: Segment Detector, Extraction Worker.
- The **Background loop**: Thread Detector, Trait Synthesizer, Profile
  Summary Generator, Question Producers P2/P3/P5. (P1 inline in
  Extraction Worker. P4 inline at end of Thread Detector. P0 is a
  one-time seeder migration.)
- **All writes** to Postgres canonical graph (persons, moments,
  entities, threads, traits, questions, edges, history tables).
- **Working Memory** in Valkey (per-session ephemeral state).
- **Pushing jobs** onto all three SQS queues (extraction, embedding,
  artifact_generation).
- The **embedding worker** that drains the embedding queue and writes
  vector columns back to Postgres.

### What Node.js (other repo) owns

- Auth, users, contributor `person_roles`.
- Sessions and per-turn transcript log → DynamoDB.
- **All user-facing reads** from Postgres for the UI (legacy review
  surfaces).
- **Consuming** the `artifact_generation` queue: calling the
  image/video generation model, uploading to S3, writing the URL
  columns (`image_url`, `video_url`, `thumbnail_url`) back to Postgres.

### Hard rules

- **We never touch DynamoDB.** Session/turn metadata we need is passed
  in by Node on the request, or fetched from a Node API.
- **We never touch S3 or the URL columns.** We only write the
  `generation_prompt` column and push onto `artifact_generation`.
- **We never write to Node-owned tables** (users, person_roles, etc.).
- **No auth in this service.** Trust comes from a service-to-service
  token plus private network. Node is the auth boundary.
- **Node never writes to the canonical graph.** Reads only. If Node
  needs a write surface, we expose an agent endpoint for it.

---

## 4. The 15 invariants

Every piece of code touching the graph or queues must respect these.

1. **Filter `status='active'`** in every query against canonical
   tables. Prefer the `active_moments` (and equivalent) views.
2. **Always filter `person_id`** in similarity / vector searches.
   Never let a query cross legacies.
3. **Never mix vectors from different embedding models.** Every
   embedded row stores `embedding_model` + `embedding_model_version`.
   Re-embed on model change.
4. **Never generate embeddings inline.** Writes that need an embedding
   push a job to the `embedding` queue. The embedding worker fills it
   in.
5. **Supersession repoints ALL edges in the same transaction.** When a
   moment is superseded, every edge pointing at it is repointed to the
   new canonical row atomically.
6. **The Extraction Worker under-extracts.** No staging store in v1.
   Low-confidence material is dropped, not flagged.
7. **Working Memory in Valkey is ephemeral.** Anything that must
   persist is logged by Node into DynamoDB.
8. **No auth in this service.** Trust the network + token. See §3.
9. **Producers must populate `attributes.themes`** on every question
   they emit. Diversity and adjacency ranking depend on it.
10. **Cap universal-dimension questions at 1 per top-5** when ranking
    the next-question slate. Otherwise it feels like a survey.
11. **The Segment Detector is an LLM call**, not rules. It runs every
    turn once the buffer threshold is crossed — not "every 5 turns".
12. **Session Wrap pushes the Segment Detector with `force=true`.**
    That is the single mechanism that flushes the tail of a session
    into the extraction queue.
13. **Post-session sequencing is fixed.** Session Wrap → Extraction
    Worker drains → Trait Synthesizer → Profile Summary → P2/P3/P5
    in parallel. The Thread Detector is **not** part of this chain —
    it runs on its own count-based cadence (see #14). P4 runs inline
    at the end of the Thread Detector when it does run.
14. **Thread Detector runs every 15 new active moments.** Gate: total
    active moments ≥ 15. Trigger:
    `count(active_moments) - moments_at_last_thread_run ≥ 15`. After
    it completes, update `moments_at_last_thread_run` on the person.
15. **The rolling summary is owned by the Segment Detector path.** On
    segment boundary, regenerate the rolling summary (small LLM) over
    `(prior_rolling_summary + closed_segment_turns)`, store it in
    Working Memory, and include it in the `extraction` queue payload.
    The Extraction Worker reads it as compressed prior context when
    generating moments. The rolling summary is always a fresh
    compressed rewrite, not an append — never let it grow unbounded.

---

## 5. Schema invariants

- **Hybrid model:** strongly-typed node tables (`persons`, `moments`,
  `entities`, `threads`, `traits`, `questions`) **+ one generic
  `edges` table** that replaces all link tables and `evidence_*_ids`
  arrays.
- **Edge types:** `involves`, `happened_at`, `exemplifies`,
  `evidences`, `related_to`, `motivated_by`, `targets`, `answered_by`.
- **`validate_edge()` in app code**, not DB constraints. Every write
  goes through it.
- **Supersession via `status`** (`active` | `superseded` | `merged`),
  not deletion.
- **Embeddings:** `vector(1024)` columns with `embedding_model` and
  `embedding_model_version` alongside, on every embedded row.
- **Subject of a legacy** lives in `persons`. Never duplicate the
  subject into `entities`. Other people mentioned in moments are
  entities of sub-type `person`.
- **Entities** have 4 sub-types: `person`, `place`, `object`,
  `organization`, with type-specific `attributes` JSONB and `aliases`.
- **Questions are first-class nodes.** Their relational data lives in
  `edges` via `motivated_by`, `targets`, `answered_by`. The questions
  `attributes` JSONB only holds non-relational fields:
  `dropped_phrase`, `life_period`, `dimension`, `themes`.

### Persons cold-start columns

- `phase TEXT NOT NULL DEFAULT 'starter'` — `'starter' | 'steady'`.
  Sticky.
- `coverage_state JSONB NOT NULL DEFAULT '{"sensory":0,"voice":0,"place":0,"relation":0,"era":0}'`
- `phase_locked_at TIMESTAMP NULL`
- `moments_at_last_thread_run INT NOT NULL DEFAULT 0` — used by the
  Thread Detector trigger (every 15 new active moments).

### Artifact columns (Node writes URLs, we write prompts)

- `image_url`, `video_url`, `thumbnail_url` (videos only on `moments`,
  images on `persons`/`threads`/`entities`).
- `generation_prompt TEXT` on each artifact-bearing table.

---

## 6. Cold-start machinery

A fresh legacy starts in `phase='starter'`. Goal: at least one moment
in each of the **5 anchor dimensions** — `sensory`, `voice`, `place`,
`relation`, `era`.

- **Phase Gate** (code) fires **only** at session start or on a switch
  intent — never per-turn. Reads `persons.phase`, routes question
  selection to the starter or steady source.
- **Producer 0** is a **one-time seeder migration** (~15 starter
  questions, 5 dimensions × 2–3 phrasings, `source='starter_anchor'`,
  `attributes.dimension` set). Not a runtime component.
- **Anchor selection in starter phase:** lowest-coverage dimension;
  tiebreaker `sensory > voice > place > relation > era`. **First turn
  of a new legacy is always `sensory`.**
- **First-turn opener** is LLM-generated under tight constraints —
  must (a) name the deceased, (b) name the Flashback role, (c) ask the
  chosen anchor. Not templated.
- **Coverage Tracker** (code, runs after Extraction Worker) increments
  `persons.coverage_state` per moment based on extracted content.
- **Handover Check** flips `persons.phase` to `'steady'` and stamps
  `phase_locked_at` once all 5 dimensions are ≥ 1. **Sticky** — no
  auto-revert. Admin can manually reset.

### Coverage Tracker rules (per moment)

| Dim | Increments when |
|---|---|
| `sensory` | `sensory_details` non-empty |
| `voice` | trait extracted, OR linked entity has `saying`/`mannerism` attr |
| `place` | any `involves` edge to a `place` entity |
| `relation` | any `involves` edge to a `person` entity ≠ subject |
| `era` | `time_anchor` has a year, OR `life_period_estimate` is set |

Counters can climb past 1 — that's diagnostic. Only `≥ 1` matters.

---

## 7. Build order (this repo)

We build in this order. Each step gets its own Claude Code prompt; we
write them together as we go.

1. **Schema migrations** — node tables, generic `edges` table, history
   tables, phase/coverage columns, artifact URL/prompt columns,
   embedding-model columns, `active_*` views.
2. **Starter question seed migration** (Producer 0 output).
3. **Embedding worker** — drains `embedding` queue, calls Voyage,
   writes vector + model + version. The whole pipeline (what gets
   stored, when triggers fire) is documented in `ARCHITECTURE.md` §6.
4. **Conversation Gateway + Working Memory** — Valkey schema,
   hydration, write-back.
5. **Intent Classifier** (small LLM) — outputs `intent`, `confidence`,
   `emotional_temperature`.
6. **Retrieval Service** — tool surface over the canonical graph.
7. **Response Generator + starter opener** — big LLM, prompt families
   per intent.
8. **Phase Gate + question selection** — code; routes starter vs
   steady.
9. **Turn Orchestrator** — the loop: append turn → intent → retrieval
   → response → append response → segment detector.
10. **Segment Detector** (small LLM) — runs every turn after buffer
    threshold; emits boundary or "not yet".
11. **Extraction Worker** (big LLM) — moments + entities + traits +
    edges + inline P1 dropped-references; refinement detection and
    supersession; pushes embedding + artifact jobs.
12. **Coverage Tracker + Handover Check** — code; runs after
    extraction.
13. **Artifact queue push** — agent-side enqueue. Node consumes
    elsewhere.
14. **Thread Detector** — count-based trigger (every 15 new active
    moments, gated by total ≥ 15); clustering + match-or-create +
    inline P4 `thread_deepen` questions.
15. **Trait Synthesizer** (small LLM) — strength ladder
    (`mentioned_once → moderate → strong → defining`).
16. **Profile Summary Generator** — display name, top 5–7 traits, key
    threads, life period, key entities.
17. **Question Producers P2 / P3 / P5** — per-session and weekly.
18. **Session Wrap** — force-close segment, session summary, invoke
    profile summary, fan out to background workers.

---

## 8. API contract with Node

We expose an HTTP service. Node calls us; we never call Node.

- `POST /session/start` — body: `{ session_id, person_id, role_id,
  session_metadata }`. Returns the opener message. We hydrate Working
  Memory, run Phase Gate + question selection + Response Generator.
- `POST /turn` — body: `{ session_id, person_id, role_id, message }`.
  Returns the assistant reply + metadata (intent,
  emotional_temperature, etc.). Runs the Turn loop end-to-end.
- `POST /session/wrap` — body: `{ session_id, person_id }`. Force-
  closes the open segment, generates session summary, kicks off
  post-session sequencing. Returns the session summary.
- `POST /admin/reset_phase` — admin-only escape hatch for Handover
  Check stickiness. Body: `{ person_id }`.

We do **not** auth these endpoints. Node is the auth boundary.

Detailed request/response shapes live in `API.md` (written at step 4).

---

## 9. Conventions

- **Source of truth:** Excalidraw diagram for component shape, this
  doc + `ARCHITECTURE.md` for contracts, code for behavior. Update all
  three together.
- **Code over LLM** for orchestration. Turn Orchestrator, Phase Gate,
  Coverage Tracker, Handover Check, queue plumbing, edge writes — all
  code, not prompts. LLMs only at: Intent Classifier, Response
  Generator, Segment Detector, Extraction Worker, Trait Synthesizer,
  Thread Detector, the Producers.
- **Docs we maintain:** `CLAUDE.md` (this), `ARCHITECTURE.md`,
  `SCHEMA.md`, `QUESTION_BANK.md`, `API.md`.
- **No staging store in v1.** Extraction writes direct to canonical.
- **Rolling summary lives in Working Memory** and is regenerated at
  every segment boundary by the Segment Detector path. It is the
  agent's compressed long-term memory within a session and is
  included in the extraction queue payload. The transcript window in
  Working Memory keeps the last ~30 turns; the rolling summary
  carries everything older.
- **No entity hints, no dedicated emotional-temperature LLM in v1.**
  Emotional temperature comes from the Intent Classifier output.

---

## 10. When in doubt

- Re-read §3 (boundaries) and §4 (invariants).
- Check the Excalidraw diagram for component shape.
- Ask before adding a fourth queue, a new top-level service, or any
  cross-boundary read/write.
