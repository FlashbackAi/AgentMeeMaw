"""render_video lays the bundled backing track under the storybook MP4.

Real ffmpeg (the bundled imageio-ffmpeg binary) is exercised here: we render a
tiny 2-page book at a low fps and probe the output for an audio stream. The
fallback path (bad track -> still emit the silent video) is covered too, since
music is decorative and must never fail a tribute render.
"""
from __future__ import annotations

import os
import subprocess

import imageio_ffmpeg
from PIL import Image

from flashback.tribute_video import style, video


def _page(size: tuple[int, int] = (120, 200)) -> video.Page:
    paper = Image.new("RGB", size, (230, 220, 200))
    illo = Image.new("RGBA", size, (180, 120, 60, 255))
    text = Image.new("RGBA", size, (0, 0, 0, 0))
    return video.Page(paper, illo, text)


def _probe(path: str) -> str:
    """Run the bundled ffmpeg over a file and return its stderr (stream info)."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    out = subprocess.run([ffmpeg, "-i", path], capture_output=True)
    return out.stderr.decode("utf-8", "replace")


def test_render_video_includes_audio_track(tmp_path):
    out = str(tmp_path / "v.mp4")
    video.render_video([_page(), _page()], out, fps=6,
                       audio_path=style.AUDIO_PATH)
    assert os.path.exists(out)
    assert "Audio:" in _probe(out)
    assert not os.path.exists(out + ".silent.mp4")  # temp cleaned up


def test_render_video_silent_when_no_audio(tmp_path):
    out = str(tmp_path / "v.mp4")
    video.render_video([_page(), _page()], out, fps=6, audio_path=None)
    assert os.path.exists(out)
    assert "Audio:" not in _probe(out)


def test_render_video_falls_back_to_silent_on_bad_audio(tmp_path):
    out = str(tmp_path / "v.mp4")
    video.render_video([_page(), _page()], out, fps=6,
                       audio_path=str(tmp_path / "missing.mp3"))
    assert os.path.exists(out)               # render still completes
    assert "Audio:" not in _probe(out)       # but with no audio
    assert not os.path.exists(out + ".silent.mp4")  # temp moved into place
