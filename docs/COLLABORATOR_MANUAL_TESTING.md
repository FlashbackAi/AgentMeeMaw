# Collaborator Feature — End-to-End Manual Testing Flow

A practical, sequential script to exercise the **whole** collaborator feature
(SP1 → SP6b) by hand, in order. It uses the local dev UI (port 3001), the agent
HTTP API (port 8000), and direct DB inspection. Each step says **what to do**,
**what to expect**, and **how to verify**.

> Read alongside `docs/COLLABORATOR_FEATURE_OVERVIEW.md` — that explains *why*
> each behaviour exists. This file is the *how to see it*.

---

## 0. Prerequisites & setup

1. **Docker / Postgres up.** Postgres on host port **15432**, db `flashback_test`
   (the dev UI reads `DATABASE_URL` = `…/flashback_test`).
2. **Clean DB** (fresh slate) — drop + re-apply all migrations:
   ```bash
   .venv/Scripts/python.exe - <<'PY'
   import glob, psycopg
   URL="postgresql://flashback:flashback@localhost:15432/flashback_test"
   with psycopg.connect(URL, autocommit=True) as c, c.cursor() as cur:
       cur.execute("DROP SCHEMA IF EXISTS public CASCADE"); cur.execute("CREATE SCHEMA public")
   for p in sorted(glob.glob("migrations/*.up.sql")):
       with psycopg.connect(URL, autocommit=True) as c, c.cursor() as cur:
           cur.execute(open(p,encoding="utf-8").read())
   print("clean DB ready")
   PY
   ```
   > **Heads-up:** running the pytest suite **also** rebuilds `flashback_test`
   > (it wipes manual data). Do manual testing in a window where you're not
   > running tests, or point tests at a different DB.
3. **Start the stack** (HTTP service :8000 + the 6 workers — extraction is the
   one that turns wrapped sessions into moments):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
   ```
   (Don't pass `-Reset` — the DB is already clean.) **Restart this after any
   code change** so new routes/logic load.
4. **Start the dev UI** (separate terminal, venv active) → open http://localhost:3001
   ```bash
   .venv/Scripts/python.exe local/server.py
   ```
5. **Auth is disabled locally** (`SERVICE_TOKEN_AUTH_DISABLED=true`), so the curl
   commands below need no token header.
6. **The dev UI gives you contributors:** the creator (a stable `user_id`) plus a
   **"Switch contributor"** panel to act as a named collaborator (display name +
   "voice anchor" = relationship). The active contributor's `user_id` is sent on
   every `/turn` and `/session/start`.

Throughout, the example subject is **David**; contributors are **Varun** (son /
creator), **Keerthi**, and **Ravi**.

A reusable DB peek (run anytime):
```bash
psql "postgresql://flashback:flashback@localhost:15432/flashback_test" -c "
  SELECT title, told_by_display_name, status FROM moments ORDER BY created_at;"
```

---

## 1. SP1 + SP2 — Provenance + speaker-first attribution

**Goal:** every moment records *who told it*; one contributor's recall credits
another by name.

1. **Create the legacy as the creator.** In the UI: subject `David`,
   relationship `father`, your name `Varun`, role `Son` → **Create legacy**. The
   `person_id` fills in.
2. **Session as Varun** → **Start session** → tell a moment:
   > "David taught me carrom on Sunday evenings — he'd always let me win the last game."
   **Wrap session.** Wait for extraction (watch the extraction worker window).
3. **Verify provenance stamped:**
   ```bash
   psql "$URL" -c "SELECT title, told_by_user_id, told_by_display_name FROM moments;"
   ```
   Expect Varun's moment with `told_by_user_id` = Varun's id (or NULL if you
   created before sending user_id) and `told_by_display_name='Varun'`.
4. **Switch to Keerthi** (Switch contributor panel: display name `Keerthi`,
   voice anchor `his daughter`) → **Start session** → tell a *different* moment:
   > "Appa loved cricket — he hit a last-ball six to win the Karimnagar final."
   **Wrap.** Extract.
5. **Verify speaker-first + attribution.** As **Varun** again, start a session and
   ask a recall question that pulls Keerthi's content:
   > "Tell me about David and cricket."
   **Expect:** the agent may weave in Keerthi's account and **credit her** —
   e.g. *"Keerthi, his daughter, remembered the last-ball six…"* (cross-contributor
   attribution). Your own moments come back un-credited.
   - In the **memory panel** (bottom of UI), moments show a `told_by` pill with
     the contributor name.

**Pass criteria:** moments carry the right `told_by_*`; cross-contributor recall
is credited by name + relationship; own/creator-era content is not.

---

## 2. SP3 — Collaborator onboarding (modal mirror + nudge + phase)

**Goal:** a new collaborator's "connection" + first "memory" graduate them from
`onboarding` → `active`; an indirect nudge fires until then.

1. **Switch to a brand-new collaborator** `Ravi`, voice anchor blank (simulate
   *no* modal answer yet). Start session.
2. **Expand the State Snapshot panel** (in the Instrumentation column) — the
   collaborator-onboarding readout shows `onboarding | connection ✗ | memory ✗`.
3. **The nudge:** on a switch/clarify turn you should see an indirect
   "defining memory" tap card (chips + free-text + skip). Answer or skip it.
4. **Connection via modal:** set Ravi's voice anchor `his colleague` in the
   Switch panel and start a new session (this mirrors the Node modal into
   `session_metadata`). State Snapshot now shows `connection ✓`.
5. **Memory:** have Ravi tell a moment; wrap; extract. Once his first moment
   commits, State Snapshot flips `memory ✓` and `phase → active` (green).
6. **Verify directly:**
   ```bash
   curl "http://localhost:8000/...";  # or:
   psql "$URL" -c "SELECT user_id, phase, voice_anchor_text, first_moment_id IS NOT NULL AS has_mem
                   FROM collaborator_onboarding;"
   ```

**Pass criteria:** phase is `onboarding` until both connection + memory are
satisfied, then sticky `active`; the nudge repeats each session until graduated;
the agent never asks the relationship directly in chat.

---

## 3. SP4 — Question scoping (provenance + scope gated)

**Goal:** a `private`/`personal` question raised in one contributor's session
doesn't surface in another's.

1. As **Keerthi**, talk about something sensitive/intimate (e.g. David's health
   struggles) so the producers emit a `private`-scoped question tied to her
   `user_id`. Wrap + let producers run (P2/P3/P5 at session wrap).
2. Inspect produced questions + scope:
   ```bash
   psql "$URL" -c "SELECT text, attributes->>'scope' AS scope, told_by_user_id FROM questions
                   WHERE source <> 'coverage_tap' ORDER BY created_at DESC LIMIT 10;"
   ```
3. **Switch to Ravi** → start session. **Expect:** Ravi is **not** asked
   Keerthi's `private` question. A `public` question (work/hobbies) *can* surface
   for anyone; a `personal` one only for its teller or creator-era.

**Pass criteria:** `public` → everyone; `personal` → teller + creator-era;
`private` → teller only. The leak that motivated SP4 (one contributor asked to
continue another's moment) is gone.

---

## 4. SP4-lite — Cross-contributor name recognition

**Goal:** when a contributor mentions an entity another contributor introduced,
the agent recognises it and credits the source.

1. Ensure Keerthi introduced a person-entity (e.g. **"Rahul"**, David's friend)
   in one of her moments.
2. As **Ravi**, in a recall turn, mention Rahul:
   > "Did David and Rahul stay close?"
3. **Expect:** the agent recognises Rahul and may credit who introduced him —
   *"Rahul — the one Keerthi mentioned…"*. If you (Ravi) had introduced Rahul,
   no cross-contributor framing.

**Pass criteria:** recognition + name/relationship credit only when a *different*
contributor first introduced the entity; creator-era/unresolved → recognised but
un-credited.

---

## 5. SP5 — Same-event linking + contradiction review

**Goal:** two contributors describing one event get linked (agent surfaces both);
conflicting accounts go to a Node-only review queue (agent stays silent).

**Detection is live at extraction time.** Keep accounts unmistakably about the
*same occasion* with overlapping entities.

### 5a. Same-event link
1. **Keerthi** already has "Last-ball six … Karimnagar final" (§1).
2. **Switch to Ravi** → tell the *same match*, complementary angle:
   > "I was at the Karimnagar inter-district final — David's last-ball six won the cup; the whole ground erupted."
   **Wrap + extract.** Compatibility LLM → `same_event` → auto-link.
3. **Verify (State Snapshot → "Same-Event Links (SP5)" section, or curl):**
   ```bash
   curl "http://localhost:8000/event_links?person_id=<PID>" | python -m json.tool
   ```
   Expect one link with both moment titles + `told_by_*_display_name` (Keerthi /
   Ravi). The UI section shows `⇄ TitleA · Keerthi ↔ TitleB · Ravi` with **ack** /
   **unlink** buttons.
4. **Agent surfaces it:** as either contributor, recall "tell me about that
   cricket final" — the reply weaves in the other's account ("Ravi remembers that
   day too…").
5. **Unlink:** click **unlink** (or `POST /event_links/{id}/unlink`) → it
   disappears from the feed.

### 5b. Contradiction
1. **Keerthi:** "David was **60** at his birthday party." (wrap/extract)
2. **Ravi:** "That party was for his **65th**, not his 60th." (wrap/extract) →
   `contradiction` → review row.
3. **Verify — and confirm the agent did NOT raise it in chat:**
   ```bash
   curl "http://localhost:8000/contradictions?person_id=<PID>" | python -m json.tool
   ```
   (Shown in the State Snapshot "Contradictions (SP5)" section with a **keep both**
   button.)
4. **Dismiss:** `POST /contradictions/{id}/dismiss` (or the button) — both moments
   stay active.

### 5c. The cross-contributor refinement guard
1. **Keerthi** has a moment; **Ravi** retells the *same* memory with *more detail*
   (a would-be "refinement").
2. **Expect:** because they're different contributors, the agent **does not**
   supersede Keerthi's moment — it's demoted to a `same_event` link (both kept).
   ```bash
   psql "$URL" -c "SELECT count(*) FROM moment_same_event_links WHERE status='active';"
   psql "$URL" -c "SELECT count(*) FROM moments WHERE status='superseded';"
   ```
   A same-contributor retelling *does* still supersede.

**Pass criteria:** complementary cross-contributor accounts link (+ agent
surfaces); conflicts queue silently; a cross-contributor "refinement" never
erases the other's account.

---

## 6. SP6a — Collaborator removal (reversible)

**Goal:** removing a contributor hides their moments + the entities orphaned to
them; everything else stays; restore is the exact inverse.

1. **Pick a removable contributor** (e.g. Ravi) and note an entity *only Ravi*
   mentioned ("OnlyRavi") and one *shared* with Keerthi.
2. **Remove:**
   ```bash
   curl -X POST "http://localhost:8000/collaborators/remove" \
     -H "Content-Type: application/json" \
     -d '{"person_id":"<PID>","user_id":"<RAVI_USER_ID>"}'
   ```
   Returns `{moments_removed, entities_removed, moments_resurrected}`.
3. **Verify hides:**
   - Ravi's moments → gone from the UI memory panel + retrieval.
   - "OnlyRavi" entity → gone. **Shared** entity → **stays**.
   ```bash
   psql "$URL" -c "SELECT title, status, told_by_display_name FROM moments;"
   psql "$URL" -c "SELECT name, status FROM entities;"
   ```
4. **Supersession resurrection:** if a Ravi moment had superseded one of
   Keerthi's (pre-guard data), Keerthi's resurfaces — `moments_resurrected ≥ 1`.
5. **SP5 feeds drop Ravi's items** automatically (links/contradictions touching a
   removed moment vanish from `GET /event_links` + `/contradictions`).
6. **Restore (exact inverse):**
   ```bash
   curl -X POST "http://localhost:8000/collaborators/restore" \
     -H "Content-Type: application/json" \
     -d '{"person_id":"<PID>","user_id":"<RAVI_USER_ID>"}'
   ```
   Everything returns; resurrected ancestors get re-superseded. Round-trip = the
   original active set.
7. **Fresh-start re-invite:** alternatively, re-add Ravi under a **new** user_id
   (Node's choice) — old content stays hidden, he starts clean.

**Pass criteria:** only Ravi's moments + orphaned entities hide; shared entities +
traits/questions/facts stay; restore is exact; remove/restore are idempotent
(call twice → zero counts, no error).

---

## 7. SP6b — Cross-contributor identity merges

**Goal:** duplicate person-cards merge; the survivor keeps the *first introducer's*
provenance; cross-contributor merges are surfaced with both names.

1. **Create a same-name duplicate across contributors:** have **Keerthi**
   introduce "Amma" (her moment) and **Ravi** also introduce "Amma" (his moment),
   on different days. Two active "Amma" entities now exist.
2. **Run the reconcile** (Node/cron triggers this; do it by hand):
   ```bash
   curl -X POST "http://localhost:8000/identity_merges/scan" \
     -H "Content-Type: application/json" -d '{"person_id":"<PID>"}'
   ```
3. **Inspect the result** (auto-merge if high confidence, else a review item):
   ```bash
   curl "http://localhost:8000/identity_merges/auto_merged?person_id=<PID>" | python -m json.tool
   curl "http://localhost:8000/identity_merges/suggestions?person_id=<PID>" | python -m json.tool
   ```
   **Expect** `cross_contributor: true` + `source_told_by_display_name` /
   `target_told_by_display_name` (Keerthi / Ravi).
4. **Verify first-introducer provenance:** the surviving "Amma" entity's
   `told_by_user_id` = whoever introduced it **earliest** (older `created_at`):
   ```bash
   psql "$URL" -c "SELECT name, status, told_by_user_id FROM entities WHERE name='Amma';"
   ```
5. **Unmerge:** `POST /identity_merges/{id}/unmerge` → the merged-away "Amma"
   comes back as a fresh entity with **its own** original `told_by`; the survivor
   reverts to its pre-merge `told_by`.
6. **Same-contributor dup** (both "Amma" by Keerthi) → merges too, but
   `cross_contributor: false`.

**Pass criteria:** duplicates merge; survivor carries the earliest introducer;
`cross_contributor` + names correct; unmerge restores both provenances; different
*surface forms* ("Mom" vs "Ishita") are intentionally **not** auto-detected
(out of scope).

---

## 8. Reset between runs

Re-run the **§0 clean DB** snippet, restart the stack, refresh the UI. Because
manual data lives in the same DB the test suite rebuilds, treat any pytest run as
a reset.

---

## Quick verification cheat-sheet

| What | Where |
|---|---|
| Moment provenance | memory panel pills · `SELECT told_by_* FROM moments` |
| Onboarding phase | State Snapshot panel · `collaborator_onboarding` |
| Question scope | `SELECT attributes->>'scope', told_by_user_id FROM questions` |
| Same-event links | State Snapshot "Same-Event Links" · `GET /event_links` |
| Contradictions | State Snapshot "Contradictions" · `GET /contradictions` |
| Removal | `SELECT status FROM moments/entities` · counts in the response |
| Merges | `GET /identity_merges/auto_merged\|suggestions` · `entities.told_by_user_id` |
