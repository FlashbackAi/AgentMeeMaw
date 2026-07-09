"""Artist._generate retry behavior — no-image responses back off and retry."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from flashback.page_render import art as art_mod
from flashback.page_render.art import Artist, GeminiError


def _png_response() -> MagicMock:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    part = MagicMock()
    part.inline_data.data = buf.getvalue()
    cand = MagicMock()
    cand.content = MagicMock(parts=[part])
    resp = MagicMock()
    resp.candidates = [cand]
    return resp


def _empty_response() -> MagicMock:
    resp = MagicMock()
    resp.candidates = []
    return resp


def _artist() -> Artist:
    a = Artist.__new__(Artist)
    a.client = MagicMock()
    a.model = "gemini-test"
    a.aspect = "1:1"
    a.feature = "tribute_image"
    return a


def test_generate_retries_no_image_then_succeeds(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(art_mod.time, "sleep", sleeps.append)
    a = _artist()
    a.client.models.generate_content.side_effect = [
        _empty_response(), _png_response()
    ]
    img = a._generate(["p"], "1:1")
    assert img is not None
    assert a.client.models.generate_content.call_count == 2
    assert sleeps  # backed off between the no-image attempt and the retry


def test_generate_raises_after_exhausting_all_attempts(monkeypatch) -> None:
    monkeypatch.setattr(art_mod.time, "sleep", lambda *_: None)
    a = _artist()
    a.client.models.generate_content.return_value = _empty_response()
    with pytest.raises(GeminiError):
        a._generate(["p"], "1:1")
    # 2 configs x 3 attempts
    assert a.client.models.generate_content.call_count == 6
