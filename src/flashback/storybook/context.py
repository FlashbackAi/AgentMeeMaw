"""The storybook render context the worker reads from Postgres.

Written by ``POST /storybooks`` (and regenerate/edit) into
``storybooks.latest_generation_context['storybook']`` BEFORE the SQS push
(Postgres authoritative; the message is a trigger only). Carries the
assembly INPUTS (subject descriptors, the qualifying moment pool, the chosen
collection) plus the Node-minted presigned URLs. Curation + script assembly
happen in the WORKER at render time -- the heavy LLM work never blocks the
HTTP request (the tribute pattern).

The anchor photo rule (user decision 2026-07-02): the person's latest
profile-picture generation context is the source of truth -- when its mode is
``with_reference`` Node mints a presigned GET for that ``reference_s3_key``
and passes it as ``anchor_photo_get_url``; when ``no_reference`` the field is
empty and identity refs are built from ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTEXT_KEY = "storybook"


@dataclass(frozen=True)
class StorybookRenderContext:
    storybook_id: str
    person_id: str
    collection: str
    subject_name: str
    relationship: str | None
    gt_context: str
    pdf_put_url: str
    cover_put_url: str
    page_put_urls: list[str] = field(default_factory=list)
    gender: str | None = None
    moments: list[dict[str, Any]] = field(default_factory=list)
    anchor_photo_get_url: str = ""
    # Cumulative free-text adjustments from the family; reshape the script.
    edit_instructions: list[str] = field(default_factory=list)
    # True on regenerate: keep the stored script, redraw the art.
    reuse_script: bool = False
    composed_at: str = ""

    @classmethod
    def from_dict(
        cls, d: dict[str, Any], *, storybook_id: str, person_id: str
    ) -> "StorybookRenderContext":
        return cls(
            storybook_id=storybook_id,
            person_id=person_id,
            collection=(d.get("collection") or ""),
            subject_name=(d.get("subject_name") or ""),
            relationship=d.get("relationship"),
            gt_context=(d.get("gt_context") or ""),
            pdf_put_url=(d.get("pdf_put_url") or ""),
            cover_put_url=(d.get("cover_put_url") or ""),
            page_put_urls=list(d.get("page_put_urls") or []),
            gender=d.get("gender"),
            moments=list(d.get("moments") or []),
            anchor_photo_get_url=(d.get("anchor_photo_get_url") or ""),
            edit_instructions=list(d.get("edit_instructions") or []),
            reuse_script=bool(d.get("reuse_script") or False),
            composed_at=(d.get("composed_at") or ""),
        )


def build_context_dict(
    *,
    collection: str,
    subject_name: str,
    relationship: str | None,
    gt_context: str,
    moments: list[dict[str, Any]],
    pdf_put_url: str,
    cover_put_url: str,
    page_put_urls: list[str],
    gender: str | None = None,
    anchor_photo_get_url: str = "",
    edit_instructions: list[str] | None = None,
    reuse_script: bool = False,
    composed_at: str = "",
) -> dict[str, Any]:
    """The dict stored under latest_generation_context['storybook']."""
    return {
        "collection": collection,
        "subject_name": subject_name,
        "relationship": relationship,
        "gt_context": gt_context,
        "gender": gender,
        "moments": moments,
        "pdf_put_url": pdf_put_url,
        "cover_put_url": cover_put_url,
        "page_put_urls": page_put_urls,
        "anchor_photo_get_url": anchor_photo_get_url,
        "edit_instructions": edit_instructions or [],
        "reuse_script": reuse_script,
        "composed_at": composed_at,
    }
