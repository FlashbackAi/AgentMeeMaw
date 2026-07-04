"""Scene generation — lettering verification + reroll, identity binding."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PIL import Image

from flashback.storybook.scenes import (
    gen_chapter_art,
    gen_scene,
    lettering_ok,
)


def test_gen_scene_rerolls_on_bad_lettering() -> None:
    img = Image.new("RGB", (4, 4))
    with patch(
        "flashback.storybook.scenes._gen_image", return_value=img
    ) as g, patch(
        "flashback.storybook.scenes.lettering_ok", side_effect=[False, True]
    ) as v:
        out = gen_scene(
            MagicMock(),
            "scene",
            None,
            "style",
            "16:9",
            text="hello world",
            verifier=MagicMock(),
        )
    assert out is img
    assert g.call_count == 2
    assert v.call_count == 2


def test_gen_scene_returns_last_after_exhausted_rerolls() -> None:
    img = Image.new("RGB", (4, 4))
    with patch(
        "flashback.storybook.scenes._gen_image", return_value=img
    ) as g, patch(
        "flashback.storybook.scenes.lettering_ok", return_value=False
    ):
        out = gen_scene(
            MagicMock(), "scene", None, "style", "16:9",
            text="hello", tries=3, verifier=MagicMock(),
        )
    assert out is img
    assert g.call_count == 3


def test_gen_scene_no_text_skips_verifier() -> None:
    img = Image.new("RGB", (4, 4))
    with patch(
        "flashback.storybook.scenes._gen_image", return_value=img
    ), patch("flashback.storybook.scenes.lettering_ok") as v:
        gen_scene(MagicMock(), "scene", None, "style", "16:9", text="")
    v.assert_not_called()


def test_gen_scene_no_verifier_returns_first_image() -> None:
    img = Image.new("RGB", (4, 4))
    with patch(
        "flashback.storybook.scenes._gen_image", return_value=img
    ) as g, patch("flashback.storybook.scenes.lettering_ok") as v:
        out = gen_scene(
            MagicMock(), "scene", None, "style", "16:9", text="hello"
        )
    assert out is img
    assert g.call_count == 1
    v.assert_not_called()


def test_gen_scene_binds_identity_to_subject() -> None:
    img = Image.new("RGB", (4, 4))
    with patch(
        "flashback.storybook.scenes._gen_image", return_value=img
    ) as g:
        gen_scene(
            MagicMock(), "a scene", None, "style", "16:9",
            subject="Chandraiah", role="Grand Father",
        )
    prompt = g.call_args.args[1][0]
    assert "APPEARANCE-ONLY" in prompt
    assert "Chandraiah" in prompt


def test_gen_scene_carries_cast_block() -> None:
    img = Image.new("RGB", (4, 4))
    cast = "OTHER RECURRING PEOPLE: Mokshith (his son): slim, short hair. "
    with patch(
        "flashback.storybook.scenes._gen_image", return_value=img
    ) as g:
        gen_scene(
            MagicMock(), "a scene", None, "style", "16:9",
            subject="Chandraiah", cast=cast,
        )
    assert cast in g.call_args.args[1][0]


def test_gen_chapter_art_carries_cast_block() -> None:
    img = Image.new("RGB", (4, 4))
    cast = "OTHER RECURRING PEOPLE: Mokshith (his son): slim. "
    with patch(
        "flashback.storybook.scenes._gen_image", return_value=img
    ) as g:
        gen_chapter_art(
            MagicMock(), "a scene", None, "style", "3:4",
            subject="Chandraiah", cast=cast,
        )
    assert cast in g.call_args.args[1][0]


def test_caption_lettering_demands_exactly_once() -> None:
    img = Image.new("RGB", (4, 4))
    with patch(
        "flashback.storybook.scenes._gen_image", return_value=img
    ) as g:
        gen_scene(
            MagicMock(), "a scene", None, "style", "16:9",
            text="hello world",
        )
    prompt = g.call_args.args[1][0]
    assert "EXACTLY ONCE" in prompt
    assert "never draw a second banner" in prompt


def test_lettering_verifier_rejects_duplicates_in_prompt() -> None:
    client = MagicMock()
    msg = MagicMock()
    client.chat.completions.create.return_value = msg
    msg.choices = [MagicMock()]
    msg.choices[0].message.content = "OK"
    lettering_ok(client, Image.new("RGB", (4, 4)), "w")
    sent = client.chat.completions.create.call_args.kwargs["messages"][0]
    text = sent["content"][0]["text"]
    assert "rendered EXACTLY ONCE" in text
    assert "more than once" in text


def test_lettering_ok_true_on_verifier_error() -> None:
    broken = MagicMock()
    broken.chat.completions.create.side_effect = RuntimeError("boom")
    assert lettering_ok(broken, Image.new("RGB", (4, 4)), "words") is True


def test_lettering_ok_parses_ok_and_bad() -> None:
    client = MagicMock()
    msg = MagicMock()
    client.chat.completions.create.return_value = msg
    msg.choices = [MagicMock()]
    msg.choices[0].message.content = "OK"
    assert lettering_ok(client, Image.new("RGB", (4, 4)), "w") is True
    msg.choices[0].message.content = "BAD"
    assert lettering_ok(client, Image.new("RGB", (4, 4)), "w") is False
