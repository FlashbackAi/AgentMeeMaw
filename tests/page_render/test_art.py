"""Artist._generate retry behavior — no-image responses back off and retry."""

from __future__ import annotations

import io
import re
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


def test_build_prompt_default_mood_unchanged() -> None:
    """Father's Day regression guard: no art_mood -> the original STYLE
    string (register + sepia mood) appears verbatim in the prompt."""
    p = art_mod.build_prompt("A quiet porch at dusk.", "", "cream")
    assert art_mod.STYLE in p
    assert art_mod.DEFAULT_MOOD in p


def test_build_prompt_art_mood_replaces_default_mood() -> None:
    mood = "bright celebratory palette, sun-washed, light-hearted energy"
    p = art_mod.build_prompt("Two bicycles by a chai stall.", "", "cream",
                             art_mood=mood)
    assert mood in p
    assert art_mod.DEFAULT_MOOD not in p
    assert art_mod.REGISTER in p  # painterly register is not themeable


def _capture_prompt(a: Artist, monkeypatch) -> list:
    prompts: list = []

    def fake_generate(contents, aspect):
        prompts.append(contents[0])
        return Image.new("RGB", (4, 4))

    monkeypatch.setattr(a, "_generate", fake_generate)
    return prompts


def _words(pattern: str, prompt: str) -> bool:
    return re.search(rf"\b{pattern}\b", prompt) is not None


def test_portrait_prompt_uses_her_for_female(monkeypatch) -> None:
    a = _artist()
    prompts = _capture_prompt(a, monkeypatch)
    photo = Image.new("RGB", (4, 4))
    a.portrait_from_photo(photo, name="Meera", gt_context="", deage=True,
                          gender="she")
    prompt = prompts[0]
    assert "KEEP her real" in prompt
    assert "restore her prime-years" in prompt
    assert not _words("his", prompt)
    assert not _words("their", prompt)


def test_portrait_prompt_uses_his_for_male(monkeypatch) -> None:
    a = _artist()
    prompts = _capture_prompt(a, monkeypatch)
    photo = Image.new("RGB", (4, 4))
    a.portrait_from_photo(photo, name="Raj", gt_context="", deage=True,
                          gender="he")
    prompt = prompts[0]
    assert "KEEP his real" in prompt
    assert "restore his prime-years" in prompt
    assert not _words("her", prompt)
    assert not _words("their", prompt)


def test_portrait_prompt_neutral_for_unknown_gender(monkeypatch) -> None:
    a = _artist()
    prompts = _capture_prompt(a, monkeypatch)
    photo = Image.new("RGB", (4, 4))
    a.portrait_from_photo(photo, name="Alex", gt_context="", deage=True,
                          gender=None)
    prompt = prompts[0]
    assert "KEEP their real" in prompt
    assert "restore their prime-years" in prompt
    assert not _words("his", prompt)
    assert not _words("her", prompt)
