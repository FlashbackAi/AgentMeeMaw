# CRM integration audit — 2026-07-15 (verified, three repos)

Method: one auditor per repo swept against the agent contract; **every
finding was then independently re-verified by re-reading the cited
file/lines**. 13 findings confirmed, 0 refuted. Changes to Node and the
dashboard go through this doc — the agent repo does not touch them.

## The failure chain that caused today's breakage (now explained end-to-end)

1. Node's proxy timeout for theme generation is **180s**; 4 image
   candidates legitimately take 3+ minutes → Node aborts and the dashboard
   shows "Something went wrong — try again" **while the agent finishes and
   mints the drafts anyway** (UI desynced from reality).
2. The user retries with the same slug → the agent (pre-fix) crashed on
   the active-slug unique index → the `UniqueViolation: test_c1` 500.
3. Font picks 422'd (shape mismatch), and those pydantic-shaped 422s
   rendered as nothing/generic in the dashboard.
4. Even a successful candidate couldn't be published (agent's
   `template_mime` re-validation leak).

Agent side of this chain is **fixed and committed** (redo-safe slugs,
slug-collision 422s, publish unblock, normalized 422 shape, lenient
fonts/ink) — needs one agent deploy. The Node timeout (item 1) is the
remaining root-cause fix below.

## Also verified CLEAN (no action)

- **Node:** CRM routes gated (ensureAuth + dashboard allowlist), agent
  status+body passthrough verbatim, both service tokens on every call,
  X-Admin-User = verified allowlisted email, binary image streamed
  byte-exact, `campaign` forwarded on unlock_prepare, **the
  /tributes/{id}/message proxy already exists**, no direct Postgres
  writes, no unhandled rejections in the CRM layer.
- **Dashboard:** `{rows}` unwrapping fixed, **campaign payload keys
  renamed to the agent's (P0 fix applied — no more silent data loss)**,
  preview renders beat objects + the resolved profile×campaign line +
  sample page, id-replacement after edits correct (tested), theme images
  authed via Node with classic 404 fallback, publish warnings displayed,
  **no direct agent references anywhere**, tests match the real wire
  shapes.

---

## Node repo — 6 confirmed findings (paste to backend-services)

| # | Where | Finding | Severity | Fix |
|---|---|---|---|---|
| N1 | `legacy/service/agentClient.js:487` | `crmGenerateVisualThemes` timeout is 180s; 4-candidate image generation legitimately runs 3+ min → Node aborts (504) while the agent completes, desyncing the dashboard from real drafts | **breaks-flow** | Raise to ≥300_000 ms (update `agentClientCrm.test.js:133`) |
| N2 | `legacy/service/agentClient.js:479` | `crmGenerateConfig` timeout 120s = exactly the worst-case runtime; upper-bound runs get killed at the wire | degrades-errors | Raise to 240_000 ms |
| N3 | `legacy/service/agentClient.js:495` | `crmPreviewTribute` timeout 120s; `render_sample_page` adds an image call on top of assembly | degrades-errors | Raise to 240_000 ms |
| N4 | `legacy/utils/errors.js:51` | `mapAgentErrorToHttp` turns agent **422 → 500 'internal'** and drops `agentError.body` on ALL non-CRM proxies (hits `unlockPrepare`, `archetypeProgress`) — client-input errors surface as bodyless internal errors | degrades-errors | `case 422:` → `{status: 422, code: BAD_REQUEST}` and pass `err.body` as respondError detail |
| N5 | `legacy/controller/CrmController.js:27` | Table whitelist rejects the agent's canonical `tribute_visual_themes` (alias `visual_themes` works) | cosmetic | Add `tribute_visual_themes` to `CRM_TABLES` |
| N6 | `legacy/controller/TributesController.js:363` | Message-route 422 replaces the agent's detail with a fixed string | cosmetic | Pass `err.body` as the detail argument |

Bonus observation (not filed): the generic 409→`concurrent_edit` code
string is semantically wrong for archetype_progress's "already unlocked"
409 — status is right, label misleads.

## Dashboard repo — 7 confirmed findings (paste to flashback_agent_admin)

| # | Where | Finding | Severity | Fix |
|---|---|---|---|---|
| F1 | `src/crm/ThemesScreen.tsx:239` | List-fetch failures render a silently EMPTY themes panel (only 'forbidden' is handled) — indistinguishable from "no themes yet" | degrades-errors | Render `crmErrorMessage(list.error)` banner in the panel |
| F2 | `src/crm/ThemesScreen.tsx:83` | Asset-library fetch failure → font/audio dropdowns silently empty | degrades-errors | Render `crmErrorMessage(assets.error)` near the selects |
| F3 | `src/api/crmClient.ts:57` | A 422 whose body isn't exactly `{detail:{errors:[…]}}` yields empty fieldErrors, and Campaigns/Profiles Save suppresses the banner for kind='validation' → **renders literally nothing**. (Agent now always normalizes, so this is defense-in-depth) | degrades-errors | Synthesize a form-level error when `detail.errors` is missing/empty on a 422 |
| F4 | `src/crm/ThemesScreen.tsx:180` | Theme-generate 422s show only the generic "Some fields need attention." — parsed per-field messages never displayed; ink input accepts non-hex freely | degrades-errors | Render `fieldErrors` under the banner + validate ink `/^#[0-9a-fA-F]{6}$/` client-side |
| F5 | `src/crm/ThemesScreen.tsx:109` | Full re-generate replaces the card array without archiving the previous batch's drafts (per-candidate Redo DOES archive) — orphan drafts accumulate in the themes list | cosmetic | Archive outgoing candidate ids in `runGenerate` onSuccess |
| F6 | `src/crm/ThemesScreen.tsx:35` | Per-candidate Redo re-mints `_c1`, so two cards can show identical slug captions | cosmetic | Caption by row id / redo counter — identity is the id |
| F7 | `src/crm/errorText.ts:8` | EVERY 409 renders "This profile is protected and can't be archived." — wrong copy for non-archive 409s | cosmetic | Generalize the copy or branch on request context |

---

## Deploy/sequence checklist

1. **Agent deploy** (this repo, already committed): redo-safe slugs,
   slug 422s, publish unblock, normalized 422s, lenient fonts/ink, band-free
   template prompt, new fonts, message endpoint.
2. **Node**: N1 (the breaks-flow timeout) at minimum before the next
   theme-generation session; N2–N6 with it.
3. **Dashboard**: F1–F4 before the content person's Friendship Day run;
   F5–F7 whenever.
