# Tribute meter change — appearance slot retired (Node/FE work order)

**Change owner:** Agent service (migration 0050 + orchestrator).
**Status:** merged on the agent side; deploy = apply migration 0050 + ship code.
**Why you're reading this:** the tribute completion meter changed shape. The FE
currently renders a stale checklist (a "How they looked" card, four slots) and
assumes the meter can plateau, which is why a tribute reads "70% and never asks
for the message." Nothing below is optional if you render the meter.

---

## What changed on the backend

The `appearance` slot was **retired as a scored slot**. It fills only when the
subject's physical ground truth (attire / features / build) is captured, which
rarely happens, so it was silently capping every campaign tribute and — via an
old code gate — deadlocking the message step entirely.

Meter reweight (the `tribute_status` view / `GET /tributes/{id}/progress` /
`/turn` meter metadata):

| meter kind | slots now | weights |
|---|---|---|
| **campaign** | memories, message, signature | 50 / 35 / 15 |
| **standalone** | memories, signature | 85 / 15 |

- **Appearance is no longer in `slots[]`** and no longer contributes to `percent`.
- `percent` reaches **100** on a campaign at memories + message + signature.
- `ready` is **unchanged** (still memories≥3 + message for campaigns; stories
  alone for standalone). Appearance was never part of `ready` for live campaigns.

**Contract-safe:** the `tribute_status` view still exposes the
`appearance_present` column (we kept it — it signals whether the art has
physical ground truth). Any direct DB read keeps working. Only the *meter*
stopped scoring appearance.

---

## What the FE must change

1. **Stop rendering an `appearance` / "How they looked" checklist card.**
   Render `slots[]` **generically** — do not hardcode a fixed 4-slot layout or
   per-slot weights. Campaign returns 3 slots, standalone returns 2. `slots[]`
   entries are `{ key, label, hint, filled, count, target }`.

2. **Read `percent` from the payload; never compute it from slot weights.**
   The reweight is invisible if you trust the returned `percent`.

3. **`next` will never be `"appearance"`.** If you drive a "next: …" steer off
   the `next` field, it now only returns `memories` | `message` | `signature` |
   `null`.

4. **Gate the "Generate / Create video" CTA on `ready`, not `percent == 100`.**
   A campaign tribute is generatable at `ready = true` (memories + message),
   which now equals 100% anyway. If you wait for `percent == 100`, a tribute
   that's ready-but-missing-signature (85%) is wrongly blocked.

5. **Own the persistent message ask on the tribute screen.** This is the fix for
   "70% and never asks for the message." The in-chat message card is a **one-time
   warm-climax** prompt — by design it does not keep nagging. The persistent ask
   is the FE's: for a **campaign** tribute where `ready = false` and the `message`
   slot is unfilled, show the message card and submit via:

   ```
   POST /tributes/{tribute_id}/message
   body: { person_id, text }
   → returns the fresh progress payload (percent/ready/slots)
   ```

   On success the tribute jumps to 100% + `ready` with no chat session needed
   (the "finish-without-chat" lane, already live). Use the returned `slots[]`
   message-slot `hint` as the prompt copy (it resolves campaign → relationship →
   neutral).

6. **Message is campaign-only** (already true since the two-meter model, not new):
   standalone tributes have **no** message slot. Don't render a message card for
   them; `POST /tributes/{id}/message` returns **400** for a standalone tribute.

---

## Quick QA checklist

- [ ] Campaign tribute with 3+ stories + signature, no message → meter shows
      **65%**, `ready = false`, message card visible on the tribute screen.
- [ ] Submit the message → meter **100%**, `ready = true`, Generate enabled.
- [ ] No "How they looked" card anywhere.
- [ ] Standalone tribute → 2 slots, no message card, Generate gated on `ready`.
- [ ] Existing legacies that were "stuck at 50/70%" re-scale automatically on
      next read (the meter is a view — no backfill, no re-render).
