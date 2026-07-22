from types import SimpleNamespace

from flashback.workers.tribute_render import worker as W


def _ctx():
    return SimpleNamespace(
        tribute_id="t1", subject_name="Dad", relationship="father", gt_context="",
        prime_photo_get_url="", deage=False, blend="cream", transition="bleed",
        fps=30, art_mood=None, style=None, gender=None,
        video_put_url="v", pdf_put_url="p", poster_put_url="")


def _settings(engine):
    return SimpleNamespace(render_concurrency=2, render_engine=engine)


def _stub_common(monkeypatch):
    monkeypatch.setattr(W, "assemble_book", lambda ctx, settings: W.Book(
        cover_title="x", opener=None, beats=[], closing=None))
    monkeypatch.setattr(W.transfer, "download_image", lambda url: None)
    monkeypatch.setattr(W.transfer, "upload_file",
                        lambda url, path, content_type: 200)


def test_remotion_engine_selected(monkeypatch, tmp_path):
    _stub_common(monkeypatch)
    calls = {"remotion": 0, "legacy": 0}
    monkeypatch.setattr(W, "render_book_remotion",
                        lambda **k: calls.__setitem__("remotion", calls["remotion"] + 1))
    monkeypatch.setattr(W, "render_book",
                        lambda **k: calls.__setitem__("legacy", calls["legacy"] + 1))
    W.render_and_upload(_ctx(), artist=None, tmpdir=str(tmp_path),
                        settings=_settings("remotion"))
    assert calls == {"remotion": 1, "legacy": 0}


def test_remotion_failure_falls_back_to_legacy(monkeypatch, tmp_path):
    _stub_common(monkeypatch)
    calls = {"legacy": 0}

    def boom(**k):
        raise RuntimeError("remotion down")

    monkeypatch.setattr(W, "render_book_remotion", boom)
    monkeypatch.setattr(W, "render_book",
                        lambda **k: calls.__setitem__("legacy", calls["legacy"] + 1))
    W.render_and_upload(_ctx(), artist=None, tmpdir=str(tmp_path),
                        settings=_settings("remotion"))
    assert calls["legacy"] == 1


def test_legacy_engine_opt_out(monkeypatch, tmp_path):
    _stub_common(monkeypatch)
    calls = {"remotion": 0, "legacy": 0}
    monkeypatch.setattr(W, "render_book_remotion",
                        lambda **k: calls.__setitem__("remotion", calls["remotion"] + 1))
    monkeypatch.setattr(W, "render_book",
                        lambda **k: calls.__setitem__("legacy", calls["legacy"] + 1))
    W.render_and_upload(_ctx(), artist=None, tmpdir=str(tmp_path),
                        settings=_settings("legacy"))
    assert calls == {"remotion": 0, "legacy": 1}


def test_theme_engine_pin_overrides_worker_default(monkeypatch, tmp_path):
    # A visual theme pinned to legacy (0045) keeps its look even though the
    # worker default is remotion — the Father's Day case.
    _stub_common(monkeypatch)
    calls = {"remotion": 0, "legacy": 0}
    monkeypatch.setattr(W, "render_book_remotion",
                        lambda **k: calls.__setitem__("remotion", calls["remotion"] + 1))
    monkeypatch.setattr(W, "render_book",
                        lambda **k: calls.__setitem__("legacy", calls["legacy"] + 1))
    ctx = _ctx()
    ctx.style = {"recipe": {"render_engine": "legacy"}}
    W.render_and_upload(ctx, artist=None, tmpdir=str(tmp_path),
                        settings=_settings("remotion"))
    assert calls == {"remotion": 0, "legacy": 1}


def test_config_engine_defaults_to_remotion(monkeypatch):
    from flashback.config import TributeRenderConfig

    for key in ("DATABASE_URL", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "x")
    monkeypatch.delenv("RENDER_ENGINE", raising=False)
    cfg = TributeRenderConfig.from_env(queue_required=False)
    assert cfg.render_engine == "remotion"
