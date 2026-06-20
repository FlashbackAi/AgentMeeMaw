# Node Prompt — Tribute progress meter as a standalone endpoint

**For:** the Node Backend team.
**Status:** agent side **built** (Python repo — `GET /tributes/{tribute_id}/progress`,
`API.md` §7b updated). Node work outstanding: add the frontend-facing route that
proxies it.

---

## TL;DR

The tribute **completion meter** (memories / message / appearance / signature →
`percent` / `ready`) is currently only delivered as the `tribute_progress` block
on each `/turn` response — so it updates **only when the user sends a chat turn**.
We want the frontend to refresh the meter on its own (after an upload, on modal
open, while polling) without a turn.

The agent now exposes a **standalone read**:

```
GET /tributes/{tribute_id}/progress?person_id=<uuid>&campaign=<slug?>
```

Frontend can't call the agent (you're the auth boundary), so **add a Node route
that proxies it**. That's the whole task — a thin pass-through. No DB work, no new
state.

---

## 1. Why a proxy and not a direct Node read

The data lives in the `tribute_status` view, which you already read directly. But
the meter the UI renders is **decorated** — per-slot `label`/`hint` copy, the
campaign skin `title` ("A Letter to Dad"), the `next` steer. That decoration is
Python-owned domain logic (`flashback/tribute/checklist.py` + campaign copy) and
is the *same* serializer the `/turn` meter uses, so the two surfaces can never
drift. Reading the raw view yourself would mean re-implementing that copy in Node
and keeping it in sync by hand. Proxy the one read instead.

(If you ever need only raw `percent`/`ready`/counts — no labels — reading
`tribute_status` directly is fine. The proxy is specifically for the decorated
meter the UI shows.)

---

## 2. What to add

A frontend-facing route, e.g. **`GET /api/tributes/:tributeId/progress`**:

1. **AuthZ** as usual — confirm the signed-in user owns/contributes to the legacy
   behind this tribute.
2. **Derive `person_id` server-side** from your own records for that tribute. **Do
   not** take `person_id` from the client — it's the ownership scope. (The agent
   404s on mismatch, but you should never forward a client-supplied scope.)
3. Call the agent:
   ```
   GET {AGENT_BASE}/tributes/{tributeId}/progress?person_id={personId}[&campaign={slug}]
   Header: X-Service-Token: {AGENT_SERVICE_TOKEN}
   ```
4. **Pass `campaign`** when the UI is skinned (e.g. `fathers_day_2026`) so `title`
   and the message-slot `hint` come back skinned. Omit for neutral copy. Use the
   same slug you pass to `/generate`.
5. **Return the agent body verbatim** to the frontend.

### Status mapping

| Agent response | Node → frontend |
|---|---|
| `200` + JSON | `200`, body passed through |
| `404` (not found / wrong owner) | `404` |
| `422` (missing `person_id`) | shouldn't happen — you always supply it; treat as `500` |

### Response shape (pass through)

```json
{
  "percent": 70,
  "ready": false,
  "title": "A Letter to Dad",
  "next": "appearance",
  "slots": [
    {"key": "memories",   "label": "...", "hint": "...", "filled": true,  "count": 3, "target": 3},
    {"key": "message",    "label": "...", "hint": "...", "filled": true,  "count": null, "target": null},
    {"key": "appearance", "label": "...", "hint": "...", "filled": false, "count": null, "target": null},
    {"key": "signature",  "label": "...", "hint": "...", "filled": false, "count": null, "target": null}
  ]
}
```

- `next` = key of the first unfilled slot (drives the "next — …" steer), `null`
  when complete.
- `count`/`target` are non-null for the `memories` slot only.
- **This is identical to the `tribute_progress` block on `/turn`** — render it
  with the same component. The `/turn` block stays; this endpoint is an additional
  way to fetch the same thing out-of-band.

---

## 3. When the frontend should hit it

- On the tribute/meter screen mount.
- After an action that can move the meter but isn't a chat turn — photo/appearance
  upload, message edit, archetype answers saved.
- Light polling while the screen is open (e.g. every 10–20s) is fine; it's a cheap
  read. There's no push for the meter — extraction is async, so the meter can lag a
  chat turn by a bit and this endpoint is how you catch up.

---

## 4. What this is NOT

This is the **completion meter** only. The **render** lifecycle (after `/generate`:
`status` `generating → complete`, plus `video_url` / `pdf_url` / `rendered_at`) is
unchanged and separate:

- You already learn render completion via the transactional
  `tribute_render_complete` NOTIFY and write the URL columns (see
  `TRIBUTE_VIDEO_NODE_PROMPT.md`).
- Those render fields live on the **`tribute_status` view**, which you read
  directly — don't ask this endpoint for them; it doesn't return them.

So: meter → this proxy; render done + URLs → your existing NOTIFY + view read.

---

## 5. Checklist

- [ ] Add `GET /api/tributes/:tributeId/progress` (or your route convention).
- [ ] AuthZ the caller against the legacy; derive `person_id` server-side.
- [ ] Proxy to agent `GET /tributes/{id}/progress` with `X-Service-Token`, forward
      `campaign` when skinned.
- [ ] Pass the body through; map `404 → 404`.
- [ ] Point the meter UI at the new route for non-turn refreshes (keep reading the
      `/turn` `tribute_progress` block as before during chat).
