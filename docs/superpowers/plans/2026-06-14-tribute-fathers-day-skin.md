# Tribute Father's Day Skin + Live Meter + Steering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the tribute flow campaign-aware (Father's Day copy + video length, featured window), surface the live completion meter on every `/turn`, and softly steer the conversation toward the unfilled checklist slots — without changing the neutral year-round behavior.

**Architecture:** A campaign "skin" is pure config (`flashback/tribute/campaigns.py`): it overrides the tribute display name, the message-invitation copy, the archetype framing, and the video target length, and marks a featured window. The active campaign slug rides in `session_metadata["campaign"]` → Working Memory (`current_tribute_campaign`) → read by `select_message_invitation` for copy, and is passed on the `/tributes/{id}/generate` request for video length — **so no migration is needed** (Node knows which campaign launched the flow). The live meter is computed once per turn (when in a tribute flow) and echoed in `/turn` metadata; the same `TributeProgress` drives a soft `tribute_gap_hint` rendered into the response prompt as a gentle nudge (never a hard filter — the product's "never a survey" rule).

**Tech Stack:** Python, FastAPI/pydantic, the existing tribute repo/progress + orchestrator step pipeline + response-generator context stack.

**Builds on Plans 1–3:** `tributes` + `tribute_status` view + `fetch_tribute_progress_async`; WM `current_tribute_id` + the `select_message_invitation` step + `MESSAGE_INVITATION_COPY`; the `/tributes/{id}/generate` endpoint + `VIDEO_TARGET_SECONDS`.

**Scope:**
- **In:** campaign registry + Father's Day skin; campaign slug plumbed through WM; skin-driven message copy + video length; `GET /tribute-campaigns` (featured surface for Node); live `tribute_progress` in `/turn` metadata; soft `tribute_gap_hint` nudge in the response prompt.
- **Out:** no DB migration (campaign is ephemeral session/request context, not persisted graph state); the general non-tribute storybook (still deferred); anything Node-side (featuring placement is Node's call — we only expose the data).

**Testing convention (per user instruction):** Build first, tests after. Tasks 1–7 implement + commit with no test run. Task 8 authors all tests and runs the suite once.

Spec: [`docs/superpowers/specs/2026-06-14-tribute-output-design.md`](../specs/2026-06-14-tribute-output-design.md) §9 (skin), §7 (live meter), §5 (steering as soft bias).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/flashback/tribute/campaigns.py` (create) | `Campaign` skins + `resolve_campaign`, `list_campaigns`, `active_featured_campaign` |
| `src/flashback/working_memory/schema.py` (modify) | Add `current_tribute_campaign` field + serialise |
| `src/flashback/working_memory/client.py` (modify) | Add `current_tribute_campaign` init param |
| `src/flashback/orchestrator/steps/apply_theme_unlock.py` (modify) | Stamp `current_tribute_campaign` from `session_metadata["campaign"]` |
| `src/flashback/orchestrator/steps/starter_opener.py` (modify) | Pass `current_tribute_campaign` into WM init |
| `src/flashback/orchestrator/steps/select_message_invitation.py` (modify) | Use the skin's message copy |
| `src/flashback/orchestrator/state.py` (modify) | Add `tribute_progress` to `TurnState` |
| `src/flashback/orchestrator/steps/load_tribute_progress.py` (create) | Step: compute progress when in a tribute flow |
| `src/flashback/orchestrator/steps/__init__.py` (modify) | Export the new step |
| `src/flashback/orchestrator/orchestrator.py` (modify) | Register the step; map progress into `TurnResult` |
| `src/flashback/orchestrator/failure_policy.py` (modify) | `load_tribute_progress` → DEGRADE |
| `src/flashback/orchestrator/protocol.py` (modify) | Add `tribute_progress` to `TurnResult` |
| `src/flashback/http/models.py` (modify) | `TributeProgressOut` + `TurnMetadata.tribute_progress`; `campaign` on generate req; campaign list models |
| `src/flashback/http/routes/turn.py` + `stream.py` (modify) | Map `tribute_progress` into metadata |
| `src/flashback/http/routes/tributes.py` (modify) | `GET /tribute-campaigns`; use skin video length on generate |
| `src/flashback/response_generator/schema.py` (modify) | `tribute_gap_hint` on `TurnContext` |
| `src/flashback/response_generator/context.py` (modify) | Render `<tribute_gap_hint>` |
| `src/flashback/response_generator/prompts.py` (modify) | Soft-steer note |
| `src/flashback/orchestrator/steps/generate_response.py` (modify) | Set `tribute_gap_hint` in `build_turn_context` |
| `tests/tribute/test_campaigns.py` + endpoint/meter tests (create) | |

---

### Task 1: Campaign registry

**Files:** Create `src/flashback/tribute/campaigns.py`

- [ ] **Step 1: Write the registry**

Create `src/flashback/tribute/campaigns.py`:

```python
"""Campaign skins for the tribute flow.

A skin is pure config + copy layered on the neutral tribute theme: it
overrides the display name + message-invitation copy + archetype framing
+ video target length, and marks a window where it's featured first in the
UX. 'Father's Day' is the launch skin; the neutral default exists
year-round. Skins never change behavior, only copy + a couple of numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from flashback.tribute.theme import (
    MESSAGE_INVITATION_COPY,
    TRIBUTE_DISPLAY_NAME,
    VIDEO_TARGET_SECONDS,
)


@dataclass(frozen=True)
class Campaign:
    slug: str
    display_name: str
    message_card_copy: str
    archetype_extra_context: str
    video_target_seconds: int
    featured: bool
    active_start: date | None
    active_end: date | None


NEUTRAL_CAMPAIGN = Campaign(
    slug="default",
    display_name=TRIBUTE_DISPLAY_NAME,
    message_card_copy=MESSAGE_INVITATION_COPY,
    archetype_extra_context="",
    video_target_seconds=VIDEO_TARGET_SECONDS,
    featured=False,
    active_start=None,
    active_end=None,
)

_CAMPAIGNS: dict[str, Campaign] = {
    "fathers_day_2026": Campaign(
        slug="fathers_day_2026",
        display_name="A Letter to Dad",
        message_card_copy=(
            "Fathers and sons don't always say it out loud. If he could "
            "hear one thing from you right now — what is it?"
        ),
        archetype_extra_context=(
            "This is a Father's Day tribute. Frame the questions around the "
            "subject as a father figure — what he was like, what he gave, "
            "the moments that stayed — while staying subject-status-agnostic."
        ),
        video_target_seconds=45,
        featured=True,
        active_start=date(2026, 6, 1),
        active_end=date(2026, 6, 22),
    ),
}


def resolve_campaign(slug: str | None) -> Campaign:
    """Return the campaign for a slug, or the neutral default."""
    if not slug or slug == "default":
        return NEUTRAL_CAMPAIGN
    return _CAMPAIGNS.get(slug, NEUTRAL_CAMPAIGN)


def list_campaigns() -> list[Campaign]:
    """Neutral first, then registered campaigns."""
    return [NEUTRAL_CAMPAIGN, *_CAMPAIGNS.values()]


def active_featured_campaign(today: date) -> Campaign | None:
    """The featured campaign whose window contains ``today``, if any."""
    for c in _CAMPAIGNS.values():
        if (
            c.featured
            and c.active_start is not None
            and c.active_end is not None
            and c.active_start <= today <= c.active_end
        ):
            return c
    return None
```

- [ ] **Step 2: Commit**

```bash
git add src/flashback/tribute/campaigns.py
git commit -m "feat(tribute): campaign skin registry + Father's Day skin

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 2: Plumb the campaign slug into Working Memory

**Files:**
- Modify: `src/flashback/working_memory/schema.py`
- Modify: `src/flashback/working_memory/client.py`
- Modify: `src/flashback/orchestrator/steps/apply_theme_unlock.py`
- Modify: `src/flashback/orchestrator/steps/starter_opener.py`

- [ ] **Step 1: WM field + serialise**

In `src/flashback/working_memory/schema.py`, add next to `current_tribute_id` (Plan 2):

```python
    # Active campaign skin slug for this tribute session (e.g.
    # 'fathers_day_2026'). Empty = neutral default. Read by
    # select_message_invitation for copy.
    current_tribute_campaign: str = ""
```

and in `serialise_state_for_init` (next to `current_tribute_id`):

```python
        "current_tribute_campaign": state.current_tribute_campaign,
```

- [ ] **Step 2: WM init param**

In `src/flashback/working_memory/client.py`, in `initialize(...)`, add the param next to `current_tribute_id`:

```python
        current_tribute_campaign: str = "",
```

and pass it into the `WorkingMemoryState(...)` constructor:

```python
            current_tribute_campaign=current_tribute_campaign,
```

- [ ] **Step 3: Stamp it from session_metadata in apply_theme_unlock**

In `src/flashback/orchestrator/steps/apply_theme_unlock.py`, in the tribute branch after `current_tribute_id` is stamped, add:

```python
        if theme.kind == "tribute" and tribute_id is not None:
            state.session_metadata["current_tribute_id"] = tribute_id
            campaign = state.session_metadata.get("campaign")
            if campaign:
                state.session_metadata["current_tribute_campaign"] = str(campaign)
```

> Implementer note: the existing code already has the `current_tribute_id` line; extend that same block (don't duplicate it).

- [ ] **Step 4: Pass into WM init in starter_opener**

In `src/flashback/orchestrator/steps/starter_opener.py`, in the `initialize(...)` call (next to `current_tribute_id`), add:

```python
            current_tribute_campaign=str(
                state.session_metadata.get("current_tribute_campaign") or ""
            ),
```

- [ ] **Step 5: Commit**

```bash
git add src/flashback/working_memory/schema.py src/flashback/working_memory/client.py src/flashback/orchestrator/steps/apply_theme_unlock.py src/flashback/orchestrator/steps/starter_opener.py
git commit -m "feat(tribute): plumb campaign slug into working memory

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 3: Skin-driven message-invitation copy

**Files:** Modify `src/flashback/orchestrator/steps/select_message_invitation.py`

- [ ] **Step 1: Resolve the skin copy**

In `select_message_invitation`, replace the hard-coded `MESSAGE_INVITATION_COPY` use with the skin's copy. Add the import:

```python
from flashback.tribute.campaigns import resolve_campaign
```

Then, where the tap text is built, resolve from the WM campaign (falling back to the neutral copy):

```python
        campaign = resolve_campaign(wm_state.current_tribute_campaign or None)
        invitation_copy = campaign.message_card_copy or MESSAGE_INVITATION_COPY

        tap = Tap(
            question_id=None,
            text=invitation_copy,
            dimension="",
            options=[],
            kind="message",
            field=None,
        )
        state.taps = [tap]
        await deps.working_memory.record_message_invitation_emitted(
            session_id=str(state.session_id),
            payload_json=json.dumps({"kind": "message", "text": invitation_copy}),
        )
```

> Implementer note: keep the existing `from flashback.tribute.theme import MESSAGE_INVITATION_COPY` import as the fallback.

- [ ] **Step 2: Commit**

```bash
git add src/flashback/orchestrator/steps/select_message_invitation.py
git commit -m "feat(tribute): skin-driven message invitation copy

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 4: Skin-driven video length on generate

**Files:** Modify `src/flashback/http/models.py`, `src/flashback/http/routes/tributes.py`

- [ ] **Step 1: Add `campaign` to the generate request**

In `src/flashback/http/models.py`, on `TributeGenerateRequest` (Plan 3), add:

```python
    campaign: str | None = None
```

- [ ] **Step 2: Use the skin's video length**

In `src/flashback/http/routes/tributes.py`, replace the hard-coded `VIDEO_TARGET_SECONDS` in the video-context call. Add the import:

```python
from flashback.tribute.campaigns import resolve_campaign
```

and in the `tribute_video` branch:

```python
        campaign = resolve_campaign(body.campaign)
        context = build_tribute_video_context(
            script=script,
            moments_by_id=moments_by_id,
            preset=preset_slug,
            target_duration_seconds=campaign.video_target_seconds,
            ground_truth_context=gt_scene,
        )
```

> Implementer note: `VIDEO_TARGET_SECONDS` is still imported for the neutral default (it backs `NEUTRAL_CAMPAIGN.video_target_seconds`); you can drop the direct import from this route if it's now unused, or leave it.

- [ ] **Step 3: Commit**

```bash
git add src/flashback/http/models.py src/flashback/http/routes/tributes.py
git commit -m "feat(tribute): skin-configurable video length on generate

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 5: `GET /tribute-campaigns` (featured surface for Node)

**Files:** Modify `src/flashback/http/models.py`, `src/flashback/http/routes/tributes.py`

- [ ] **Step 1: Response models**

In `src/flashback/http/models.py`, add:

```python
class TributeCampaignOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    featured: bool
    is_active: bool
    active_start: str | None = None
    active_end: str | None = None


class TributeCampaignsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaigns: list[TributeCampaignOut]
    active_featured_slug: str | None = None
```

- [ ] **Step 2: The endpoint**

In `src/flashback/http/routes/tributes.py`, add (extend the campaigns import to include `active_featured_campaign`, `list_campaigns`):

```python
from datetime import date, timezone, datetime

from flashback.http.models import (
    TributeCampaignOut,
    TributeCampaignsResponse,
)
from flashback.tribute.campaigns import (
    active_featured_campaign,
    list_campaigns,
    resolve_campaign,
)


@router.get("/tribute-campaigns", response_model=TributeCampaignsResponse)
async def get_tribute_campaigns() -> TributeCampaignsResponse:
    """Public campaign list + which campaign is featured today (for Node)."""
    today = datetime.now(timezone.utc).date()
    active = active_featured_campaign(today)
    out = []
    for c in list_campaigns():
        is_active = bool(
            c.featured
            and c.active_start
            and c.active_end
            and c.active_start <= today <= c.active_end
        )
        out.append(
            TributeCampaignOut(
                slug=c.slug,
                display_name=c.display_name,
                featured=c.featured,
                is_active=is_active,
                active_start=c.active_start.isoformat() if c.active_start else None,
                active_end=c.active_end.isoformat() if c.active_end else None,
            )
        )
    return TributeCampaignsResponse(
        campaigns=out,
        active_featured_slug=active.slug if active else None,
    )
```

> Implementer note: merge the imports with the existing import block in the file rather than duplicating module imports.

- [ ] **Step 3: Commit**

```bash
git add src/flashback/http/models.py src/flashback/http/routes/tributes.py
git commit -m "feat(tribute): GET /tribute-campaigns featured surface

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 6: Live meter — compute progress + echo in /turn metadata

**Files:**
- Modify: `src/flashback/orchestrator/state.py`
- Create: `src/flashback/orchestrator/steps/load_tribute_progress.py`
- Modify: `src/flashback/orchestrator/steps/__init__.py`
- Modify: `src/flashback/orchestrator/failure_policy.py`
- Modify: `src/flashback/orchestrator/protocol.py`
- Modify: `src/flashback/orchestrator/orchestrator.py`
- Modify: `src/flashback/http/models.py`
- Modify: `src/flashback/http/routes/turn.py`, `src/flashback/http/routes/stream.py`

- [ ] **Step 1: TurnState field**

In `src/flashback/orchestrator/state.py`, add to `TurnState` (near the other optional turn fields):

```python
    tribute_progress: "TributeProgress | None" = None
```

Add the import at the top of `state.py` under TYPE_CHECKING (the dataclass uses `from __future__ import annotations`, so a string annotation is fine):

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from flashback.tribute.progress import TributeProgress
```

> Implementer note: if `state.py` already imports things at runtime, keep the `TributeProgress` import under TYPE_CHECKING to avoid a circular import (tribute.progress is light, but TYPE_CHECKING is safest).

- [ ] **Step 2: The step**

Create `src/flashback/orchestrator/steps/load_tribute_progress.py`:

```python
"""Load tribute completion progress when the session is in a tribute flow.

Cheap read of the tribute_status view, gated on a current_tribute_id in
Working Memory. Feeds the live meter (/turn metadata) and the soft
gap-steering hint. Best-effort: failures degrade to no progress.
"""

from __future__ import annotations

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.state import TurnState
from flashback.tribute.progress import fetch_tribute_progress_async

log = structlog.get_logger("flashback.orchestrator")


async def load_tribute_progress(state: TurnState, deps: OrchestratorDeps) -> None:
    with timed_step(log, "load_tribute_progress"):
        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state
        tribute_id = wm_state.current_tribute_id
        if not tribute_id:
            return
        async with deps.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                state.tribute_progress = await fetch_tribute_progress_async(
                    cur, tribute_id=tribute_id
                )
```

- [ ] **Step 3: Register the step (export + pipeline + policy)**

In `src/flashback/orchestrator/steps/__init__.py` add the import + `__all__` entry:

```python
from flashback.orchestrator.steps.load_tribute_progress import load_tribute_progress
```
```python
    "load_tribute_progress",
```

In `src/flashback/orchestrator/failure_policy.py`, add to `TURN_POLICIES`:

```python
    "load_tribute_progress": Policy.DEGRADE,
```

In `src/flashback/orchestrator/orchestrator.py`, import it (alongside the other step imports) and register it in BOTH the JSON and streaming turn pipelines, right after the message-invitation step (so it reflects any message captured this turn). Mirror the existing `await execute(...)` shape:

```python
                await execute(
                    policies=TURN_POLICIES,
                    step_name="load_tribute_progress",
                    fn=lambda: load_tribute_progress(state, self._deps),
                    state=state,
                )
```

> Implementer note: place this OUTSIDE the `story`/`deepen` gate so the meter updates on every turn in a tribute flow (it self-gates on `current_tribute_id`). Put it just before `generate_response`.

- [ ] **Step 4: TurnResult + serialization**

In `src/flashback/orchestrator/protocol.py`, add to `TurnResult`:

```python
    tribute_progress: dict | None = None
```

In `src/flashback/orchestrator/orchestrator.py`, in `_build_turn_result`, set it:

```python
        tribute_progress=(
            {
                "percent": state.tribute_progress.percent,
                "ready": state.tribute_progress.ready,
                "slots": [
                    {"key": s.key, "label": s.label, "filled": s.filled}
                    for s in state.tribute_progress.slots
                ],
            }
            if state.tribute_progress is not None
            else None
        ),
```

- [ ] **Step 5: HTTP metadata**

In `src/flashback/http/models.py`, add to `TurnMetadata`:

```python
    tribute_progress: dict | None = None
```

In `src/flashback/http/routes/turn.py` (and `stream.py`'s `done` event), where `TurnMetadata(...)` is built from the result, add:

```python
        tribute_progress=result.tribute_progress,
```

> Implementer note: find where the turn route maps `TurnResult` → `TurnResponse`/`TurnMetadata` and add the field; mirror in the stream route's terminal `done` payload.

- [ ] **Step 6: Commit**

```bash
git add src/flashback/orchestrator/ src/flashback/http/models.py src/flashback/http/routes/turn.py src/flashback/http/routes/stream.py
git commit -m "feat(tribute): live completion meter in /turn metadata

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 7: Soft gap-steering nudge

**Files:**
- Modify: `src/flashback/response_generator/schema.py`
- Modify: `src/flashback/response_generator/context.py`
- Modify: `src/flashback/response_generator/prompts.py`
- Modify: `src/flashback/orchestrator/steps/generate_response.py`

- [ ] **Step 1: TurnContext field**

In `src/flashback/response_generator/schema.py`, add to `TurnContext`:

```python
    # Soft tribute steering: the hint for the first unfilled checklist slot,
    # or None. The agent should gently lean toward it when natural — never
    # as a survey, never a hard filter.
    tribute_gap_hint: str | None = None
```

- [ ] **Step 2: Render it**

In `src/flashback/response_generator/context.py`, where context sections are assembled, add:

```python
    if ctx.tribute_gap_hint:
        sections.append(
            f"<tribute_gap_hint>{xml_text(ctx.tribute_gap_hint)}</tribute_gap_hint>"
        )
```

> Implementer note: match how other optional sections are appended (the file already uses `xml_text` and a `sections` list — mirror the `tap_pending` block).

- [ ] **Step 3: Prompt note**

In `src/flashback/response_generator/prompts.py`, add a short note (near the `_TAP_PENDING_NOTE`) that gets included for turn prompts:

```python
_TRIBUTE_GAP_NOTE = """

If a <tribute_gap_hint> block is present, the contributor is building a
tribute and this is the one thing still missing from it. If the
conversation allows, gently lean the next beat toward it — a natural,
curious question, never a checklist item, never "we still need X". If the
moment doesn't fit, ignore it and follow the contributor.
"""
```

> Implementer note: append `_TRIBUTE_GAP_NOTE` to the same turn system prompt(s) that include `_TAP_PENDING_NOTE`. Find where `_TAP_PENDING_NOTE` is concatenated into the prompt and add `_TRIBUTE_GAP_NOTE` alongside it.

- [ ] **Step 4: Populate it in build_turn_context**

In `src/flashback/orchestrator/steps/generate_response.py`, in `build_turn_context`, set the hint from `state.tribute_progress` (first unfilled slot):

```python
    tribute_gap_hint = None
    if state.tribute_progress is not None and not state.tribute_progress.ready:
        for slot in state.tribute_progress.slots:
            if not slot.filled:
                tribute_gap_hint = slot.hint
                break
```

and pass `tribute_gap_hint=tribute_gap_hint` into the `TurnContext(...)` construction.

> Implementer note: `TributeSlot` has no `hint`-less variant — `slot.hint` is the user-facing copy from `checklist.SLOTS`. Confirm `build_turn_context` has access to `state` (it does — it's the standard signature).

- [ ] **Step 5: Commit**

```bash
git add src/flashback/response_generator/ src/flashback/orchestrator/steps/generate_response.py
git commit -m "feat(tribute): soft gap-steering nudge in the response prompt

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 8: Tests + run the suite

**Files:**
- Create: `tests/tribute/test_campaigns.py`
- Create: `tests/http/test_tribute_campaigns.py`

- [ ] **Step 1: Campaign registry unit tests**

Create `tests/tribute/test_campaigns.py`:

```python
"""Campaign registry: resolution, neutral fallback, featured window."""

from __future__ import annotations

from datetime import date

from flashback.tribute.campaigns import (
    NEUTRAL_CAMPAIGN,
    active_featured_campaign,
    resolve_campaign,
)


def test_resolve_unknown_or_none_is_neutral() -> None:
    assert resolve_campaign(None) is NEUTRAL_CAMPAIGN
    assert resolve_campaign("default") is NEUTRAL_CAMPAIGN
    assert resolve_campaign("nope") is NEUTRAL_CAMPAIGN


def test_resolve_fathers_day_overrides_copy_and_length() -> None:
    c = resolve_campaign("fathers_day_2026")
    assert c.slug == "fathers_day_2026"
    assert c.display_name == "A Letter to Dad"
    assert "say it" in c.message_card_copy.lower()
    assert c.video_target_seconds == 45


def test_active_featured_only_inside_window() -> None:
    assert active_featured_campaign(date(2026, 6, 15)) is not None
    assert active_featured_campaign(date(2026, 1, 1)) is None
    assert active_featured_campaign(date(2026, 12, 25)) is None
```

- [ ] **Step 2: Campaigns endpoint test**

Create `tests/http/test_tribute_campaigns.py`:

```python
"""GET /tribute-campaigns returns the list (no DB, no auth state needed)."""

from __future__ import annotations


async def test_list_campaigns(client) -> None:
    resp = await client.get("/tribute-campaigns", headers={"X-Service-Token": "test-token"})
    assert resp.status_code == 200
    body = resp.json()
    slugs = {c["slug"] for c in body["campaigns"]}
    assert "default" in slugs
    assert "fathers_day_2026" in slugs
```

> Implementer note: `client` (no-DB) is the right fixture here — the endpoint touches no database. If `require_service_token` rejects the test token, copy the exact header/fixture an existing no-DB GET test uses (e.g. `tests/http/test_artifact_presets*` or the presets test).

- [ ] **Step 3: Run the suite**

Bring up Postgres, then PowerShell:

```
$env:TEST_DATABASE_URL = "postgresql://flashback:flashback@localhost:15432/flashback_test"
python -m pytest tests/tribute tests/http/test_tribute_generate.py tests/http/test_tribute_campaigns.py -v
python -m pytest -q
```
Expected: tribute + tribute-HTTP tests PASS; whole suite PASS modulo known pre-existing failures.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test(tribute): campaign registry + campaigns endpoint

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

## Plan Self-Review

**Spec coverage (Plan 4 slice):**
- Campaign skin = copy + featured + length, neutral default year-round (spec §9) → Task 1 ✓
- Skin overrides message copy (spec §9) → Task 3 ✓
- Skin-configurable video length (spec §9 + §8 addendum) → Task 4 ✓
- Featured/active_window exposed for Node to feature first (spec §9 — placement is Node's) → Task 5 `GET /tribute-campaigns` ✓
- Live meter echoed in `/turn` metadata, monotonic within a tribute (spec §7) → Task 6 ✓
- Steering is **soft bias, never hard filter** (spec §5 / invariant-style) → Task 7 prompt nudge ✓
- No migration (campaign is ephemeral session/request context) → design choice stated ✓

**Placeholder scan:** No TBD/TODO. Implementer notes flag integration points to confirm against live code (the `TurnResult→TurnMetadata` mapping site; where `_TAP_PENDING_NOTE` is concatenated; the `sections` list in `context.py`; the no-DB GET test fixture) — verification instructions, not unfilled code.

**Type consistency:**
- `Campaign` fields (`slug`, `display_name`, `message_card_copy`, `video_target_seconds`, `featured`, `active_start/end`) consistent across `campaigns.py` (Task 1), the message step (Task 3), generate route (Task 4), and the campaigns endpoint (Task 5).
- `resolve_campaign(slug) -> Campaign` used identically in Tasks 3, 4, 5.
- WM `current_tribute_campaign` (str) consistent across schema/serialise (Task 2 step 1), init param (Task 2 step 2), apply_theme_unlock stamp (Task 2 step 3), starter_opener init (Task 2 step 4), and the message step read (Task 3).
- `state.tribute_progress: TributeProgress | None` set by `load_tribute_progress` (Task 6 step 2), consumed in `_build_turn_result` (Task 6 step 4) and `build_turn_context` (Task 7 step 4) — `.percent`, `.ready`, `.slots[].key/.label/.filled/.hint` all match the Plan 1 `TributeProgress`/`TributeSlot` shape.
- `TurnResult.tribute_progress: dict | None` (Task 6 step 4) → `TurnMetadata.tribute_progress: dict | None` (Task 6 step 5) — consistent.
- `TributeGenerateRequest.campaign` (Task 4) consumed via `resolve_campaign(body.campaign)` — consistent.
