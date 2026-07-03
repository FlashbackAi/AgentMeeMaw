"""storybook_render persistence — context load, script save, NOTIFY, failure.

Real-Postgres tests (skipped without TEST_DATABASE_URL). Mirrors the
extraction NOTIFY test pattern: transactional pg_notify observed on a
LISTEN connection.
"""

from __future__ import annotations

import json

from flashback.storybook.context import CONTEXT_KEY, build_context_dict
from flashback.workers.storybook_render import persistence


def _insert_storybook(db_pool, person_id: str, *, context: dict | None,
                      status: str = "generating") -> str:
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO storybooks "
                "(person_id, status, collection, latest_generation_context) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (
                    person_id,
                    status,
                    "childhood",
                    json.dumps({CONTEXT_KEY: context} if context else {}),
                ),
            )
            return str(cur.fetchone()[0])


def _ctx_dict(**over) -> dict:
    base = dict(
        collection="childhood",
        subject_name="Subject",
        relationship="Grand Father",
        gt_context="gt",
        moments=[{"title": "t", "narrative": "n"}],
        pdf_put_url="https://s3.example/pdf?sig=1",
        cover_put_url="https://s3.example/cover?sig=1",
        page_put_urls=[f"https://s3.example/p{i}?sig=1" for i in range(7)],
        composed_at="2026-07-03T00:00:00Z",
    )
    base.update(over)
    return build_context_dict(**base)


def test_load_render_context_round_trip(db_pool, make_person) -> None:
    pid = make_person()
    sid = _insert_storybook(db_pool, pid, context=_ctx_dict())
    ctx = persistence.load_render_context(db_pool, storybook_id=sid)
    assert ctx is not None
    assert ctx.person_id == pid
    assert ctx.collection == "childhood"
    assert len(ctx.page_put_urls) == 7


def test_load_render_context_stale_composed_at_returns_none(
    db_pool, make_person
) -> None:
    pid = make_person()
    sid = _insert_storybook(db_pool, pid, context=_ctx_dict())
    assert (
        persistence.load_render_context(
            db_pool, storybook_id=sid, composed_at="different"
        )
        is None
    )


def test_load_render_context_missing_context_returns_none(
    db_pool, make_person
) -> None:
    pid = make_person()
    sid = _insert_storybook(db_pool, pid, context=None)
    assert persistence.load_render_context(db_pool, storybook_id=sid) is None


def test_save_and_reload_script(db_pool, make_person) -> None:
    pid = make_person()
    sid = _insert_storybook(db_pool, pid, context=_ctx_dict())
    assert persistence.load_saved_script(db_pool, storybook_id=sid) is None
    persistence.save_script(
        db_pool, storybook_id=sid, title="A Book",
        script_dict={"cover_title": "A Book", "pages": []},
    )
    saved = persistence.load_saved_script(db_pool, storybook_id=sid)
    assert saved == {"cover_title": "A Book", "pages": []}


def test_mark_complete_flips_status_and_notifies(db_pool, make_person) -> None:
    pid = make_person()
    sid = _insert_storybook(db_pool, pid, context=_ctx_dict())
    with db_pool.connection() as listen_conn:
        listen_conn.autocommit = True
        listen_conn.execute(f"LISTEN {persistence.NOTIFY_CHANNEL}")
        try:
            persistence.mark_complete(
                db_pool, storybook_id=sid, person_id=pid,
                collection="childhood", pdf_present=True,
                pages_present=7, cover_present=True,
            )
            received = list(listen_conn.notifies(timeout=5, stop_after=1))
        finally:
            listen_conn.execute("UNLISTEN *")
    assert len(received) == 1
    payload = json.loads(received[0].payload)
    assert payload["event"] == "storybook_render_complete"
    assert payload["storybook_id"] == sid
    assert payload["collection"] == "childhood"
    assert payload["pdf_present"] is True
    assert payload["pages_present"] == 7
    with db_pool.connection() as conn:
        row = conn.execute(
            "SELECT status, rendered_at FROM storybooks WHERE id = %s", (sid,)
        ).fetchone()
    assert row[0] == "complete"
    assert row[1] is not None


def test_mark_failed_only_touches_generating_rows(db_pool, make_person) -> None:
    pid = make_person()
    sid = _insert_storybook(db_pool, pid, context=_ctx_dict())
    persistence.mark_failed(db_pool, storybook_id=sid, error="boom")
    with db_pool.connection() as conn:
        row = conn.execute(
            "SELECT status, render_error FROM storybooks WHERE id = %s", (sid,)
        ).fetchone()
    assert row == ("failed", "boom")

    done = _insert_storybook(db_pool, pid, context=_ctx_dict(),
                             status="complete")
    persistence.mark_failed(db_pool, storybook_id=done, error="late boom")
    with db_pool.connection() as conn:
        row = conn.execute(
            "SELECT status, render_error FROM storybooks WHERE id = %s",
            (done,),
        ).fetchone()
    assert row == ("complete", None)  # never clobbered
