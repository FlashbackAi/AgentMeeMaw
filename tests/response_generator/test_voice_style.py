"""Tests for voice-mode style-tag parsing."""

from __future__ import annotations

import pytest

from flashback.response_generator.voice_style import (
    DEFAULT_VOICE_STYLE,
    VOICE_STYLES,
    VoiceStyleStreamParser,
    extract_voice_style,
)


# --- extract_voice_style (non-streaming) -----------------------------------


def test_extract_pulls_leading_tag():
    assert extract_voice_style("[[style: tender]] I remember that day.") == (
        "tender",
        "I remember that day.",
    )


def test_extract_missing_tag_defaults_neutral_and_keeps_text():
    assert extract_voice_style("I remember that day.") == (
        DEFAULT_VOICE_STYLE,
        "I remember that day.",
    )


def test_extract_unknown_label_falls_back_to_default():
    style, text = extract_voice_style("[[style: giddy]] hi there")
    assert style == DEFAULT_VOICE_STYLE
    assert text == "hi there"


def test_extract_is_case_and_space_insensitive():
    assert extract_voice_style("[[ STYLE :  Warm ]]\n\nHello") == ("warm", "Hello")


def test_extract_only_consumes_a_leading_tag_not_mid_text():
    # A bracketed token later in the reply must be left untouched.
    text = "Sure thing [[style: warm]] stays put."
    assert extract_voice_style(text) == (DEFAULT_VOICE_STYLE, text)


# --- VoiceStyleStreamParser (streaming) ------------------------------------


def _run_stream(chunks: list[str]) -> tuple[str, str]:
    parser = VoiceStyleStreamParser()
    out = [parser.feed(c) for c in chunks]
    out.append(parser.flush())
    return parser.style, "".join(out)


def test_stream_tag_split_across_chunks():
    style, text = _run_stream(
        ["[[sty", "le: war", "m]]\n\nThat ", "sounds ", "lovely."]
    )
    assert style == "warm"
    assert text == "That sounds lovely."


def test_stream_tag_in_first_chunk():
    style, text = _run_stream(["[[style: curious]] Tell me more?"])
    assert style == "curious"
    assert text == "Tell me more?"


def test_stream_no_tag_passes_text_through():
    style, text = _run_stream(["Tell ", "me ", "more."])
    assert style == DEFAULT_VOICE_STYLE
    assert text == "Tell me more."


def test_stream_never_leaks_tag_into_emitted_text():
    parser = VoiceStyleStreamParser()
    emitted = ""
    for chunk in ["[[style: ", "tender]] ", "Soft words here."]:
        emitted += parser.feed(chunk)
    emitted += parser.flush()
    assert "[[" not in emitted and "style:" not in emitted


def test_stream_unknown_label_defaults_neutral():
    style, text = _run_stream(["[[style: zany]] hey"])
    assert style == DEFAULT_VOICE_STYLE
    assert text == "hey"


@pytest.mark.parametrize("style", VOICE_STYLES)
def test_every_whitelisted_style_round_trips(style):
    parser = VoiceStyleStreamParser()
    text = parser.feed(f"[[style: {style}]] body") + parser.flush()
    assert parser.style == style
    assert text == "body"
