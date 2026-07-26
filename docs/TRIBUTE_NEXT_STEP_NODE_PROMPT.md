# Node/Frontend Prompt — `next_step` on unlock_prepare + slug-scoped answers

**For:** the Node Backend + frontend (legacy repo) team.
**Status:** agent side **shipped to `main` (`9bfc180`)**, 10 tests green, **not
yet deployed** — the live prod agent still runs the old code, so the re-ask bug
persists in prod until the next agent deploy. Coordinate the deploy so FE + agent
flip together.
**Fixes:** "Keep going" re-opening already-answered archetype questions instead
of advancing to the message step.

---

## TL;DR

`POST /themes/{id}/unlock_prepare` now returns a server-decided **`next_step`**
that tells the frontend where "Keep going" should land *after* unlock_prepare.
Plus two data-correctness fixes so the answered-question signal is actually
reliable. No new endpoints; it rides the existing response.

---

## 1. Root cause (why questions re-showed)

Campaigns **supersede on every CRM edit** (new id, `version+1`). A tribute is
stamped with the campaign **version id** current at creation. The answered-lookups
(`fetch_open_tribute_id_async` / `fetch_latest_tribute_answers_async`) scoped by
that **exact id**, so the moment a campaign was edited, in-flight tributes were
orphaned → `tribute_answered` came back **empty** → `unlockContext`'s
`question_text` filter had nothing to match → every question re-showed. It was
"empty," not "mismatched wording."

## 2. Agent-side changes (already shipped)

1. **Slug-scoped lookups** — both functions now match **any version of the same
   campaign slug**, so a campaign edit no longer orphans answers. (Mirrors what
   `_resolve_render_config` already does: re-resolve by slug.)
2. **Theme-level fallback folded into `tribute_answered`** — precedence: the
   open tribute row's answers win; the pre-0042 **theme-level** answers fill in
   only when the row has none. Pre-0042 legacies now filter correctly with
   **zero frontend change**. (The `tribute_answered` docstring was updated —
   it's no longer "THIS campaign's open tribute" exclusively.)
3. **`next_step` + `archetype_complete`** added to the response.

## 3. The `next_step` contract

```jsonc
// POST /themes/{id}/unlock_prepare  → response (existing shape + these)
{
  // ...archetype_questions, archetype_answers_draft, tribute_answered...
  "archetype_complete": true,          // every served question already covered
  "next_step": "message"               // "archetype" | "message" | "conversation"
}
```

Server derivation: `archetype` if not `archetype_complete`; else `message` **iff**
it's a real campaign entry AND the message slot is empty; else `conversation`.

- **`archetype_complete`** — convenience/telemetry only. `next_step` is the
  contract; don't design around needing `archetype_complete`.
- **Standalone never returns `"message"`** — the message step is campaign-only
  (posting `…/message` for a standalone 400s `message_not_supported`, per 0050).
  A standalone/neutral entry is always `"conversation"`.

## 4. Frontend consumption (confirmed mapping)

`next_step` governs the decision **after** `unlock_prepare` returns
(`useBeginThemeUnlock`), NOT `tributeEntryDecision`. The tribute-status branches
run first and **outrank** it:

| source | branch |
|---|---|
| tribute-status (before unlock_prepare) | `none` (no theme) / `watch` (`complete`) / `rendering` (`generating`) — unchanged, outrank `next_step` |
| `next_step: "archetype"` | build unlock context (`archetype_questions` minus `tribute_answered`), push `/{personId}/sessions/theme-unlock-{themeId}` |
| `next_step: "message"` | push `/{personId}/tribute/finish` (message card, no chat) |
| `next_step: "conversation"` | `POST /sessions { themeId, campaign }`, push `/{personId}/sessions/{sessionId}` |

This is exactly the mapping the frontend proposed; the agent derivation was built
to match it 1:1. The frontend also keeps its local `shouldAskForMessage`
predicate for the persistent home-screen ask (not an entry decision) — that's
independent and shouldn't cost a round trip.

## 5. Deploy coordination

- Agent code is on `main`, **undeployed**. Until it deploys, prod still re-asks.
- No migration; `next_step` rides the existing response. Deploy is a plain agent
  code roll.
- Wire the FE `useBeginThemeUnlock` branch, then ping to flip together.

## 6. Verification repro (`prod-test-1`)

The FD campaign tribute `d04e50cc` is staged as an in-progress campaign: `draft`,
15 row-answers covering the current FD bank, 38 memories (**65%**), **no
message**. Simulated against prod with the new resolution:
`archetype_complete=true`, slug-scoping resolves the (v8-stamped) row under the
current published FD, `next_step="message"`. After deploy, "Keep going" on that
person must route to `/tribute/finish`, not the archetype modal.
