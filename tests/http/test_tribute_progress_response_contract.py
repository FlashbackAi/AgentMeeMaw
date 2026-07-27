"""Guard: progress_to_payload output must construct TributeProgressResponse.

The two-meter work added `kind` to progress_to_payload but not to the
`extra="forbid"` HTTP model, 500ing GET /tributes/{id}/progress in prod
(2026-07-27). The existing progress tests call fetch_tribute_progress_sync
directly and never build the response model, so they missed it. This pins the
payload<->model contract without a DB or HTTP server.
"""

from __future__ import annotations

import pytest

from flashback.http.models import TributeProgressResponse
from flashback.tribute.progress import (
    TributeProgress,
    TributeSlot,
    progress_to_payload,
)


def _progress(kind: str) -> TributeProgress:
    return TributeProgress(
        tribute_id="t1",
        percent=65,
        ready=False,
        kind=kind,
        slots=[
            TributeSlot(key="memories", label="Shared memories",
                        hint="Tell three stories.", filled=True, count=3, target=3),
            TributeSlot(key="appearance", label="How they looked",
                        hint="A few details.", filled=False),
        ],
        title="A Tribute",
        next_key="appearance",
        answered_layers=0,
    )


@pytest.mark.parametrize("kind", ["standalone", "campaign"])
def test_payload_constructs_response_model(kind: str) -> None:
    # Would have raised pydantic ValidationError (extra_forbidden: kind) before
    # the model gained the field.
    resp = TributeProgressResponse(**progress_to_payload(_progress(kind)))
    assert resp.kind == kind
    assert resp.percent == 65
    assert resp.ready is False
    assert [s.key for s in resp.slots] == ["memories", "appearance"]


def test_payload_keys_are_a_subset_of_model_fields() -> None:
    # Any key progress_to_payload emits must be a declared field on the
    # forbid-extras model, else the route 500s.
    payload_keys = set(progress_to_payload(_progress("campaign")).keys())
    model_fields = set(TributeProgressResponse.model_fields.keys())
    assert payload_keys <= model_fields, payload_keys - model_fields
