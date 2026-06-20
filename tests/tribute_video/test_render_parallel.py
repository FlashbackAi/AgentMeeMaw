"""_generate_illustrations runs page art concurrently but preserves order.

Uses a fake Artist (no Gemini, no template/ffmpeg) so we assert scheduling +
ordering only -- the quality-bearing prompts/model are unchanged by the
parallelization, so there's nothing image-specific to test here.
"""
from __future__ import annotations

import threading
import time

from PIL import Image

from flashback.tribute_video.book import Beat, Book
from flashback.tribute_video.render import _generate_illustrations


class _FakeArtist:
    """Records calls; each illustrate returns a 1x1 image tagged via .info."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self.max_in_flight = 0
        self._in_flight = 0

    def _tag(self, label: str) -> Image.Image:
        with self._lock:
            self.calls.append(label)
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        time.sleep(0.02)  # hold the slot so concurrency is observable
        with self._lock:
            self._in_flight -= 1
        img = Image.new("RGB", (1, 1))
        img.info["label"] = label
        return img

    def character_reference(self, *, name, relationship, gt_context):
        return self._tag("reference")

    def portrait_from_photo(self, photo, *, name, gt_context, deage, blend):
        return self._tag("opener_portrait")

    def illustrate(self, art_direction, gt_context, blend, *, reference=None,
                   aspect=None):
        return self._tag(f"illustrate:{art_direction}")


def _book() -> Book:
    return Book(
        cover_title="C",
        opener=Beat(line="o", art_direction="OPEN"),
        beats=[Beat(line=f"b{i}", art_direction=f"B{i}", moment_id=str(i))
               for i in range(5)],
        closing=Beat(line="c", art_direction="CLOSE"),
        message="thanks",
    )


def test_preserves_order_and_illustrates_every_page():
    artist = _FakeArtist()
    opener, beats, closing = _generate_illustrations(
        artist=artist, book=_book(), subject_name="Dad", relationship="father",
        gt_context="", prime_photo=None, deage=False, blend="cream",
        concurrency=4)

    assert opener.info["label"] == "illustrate:OPEN"
    assert [b.info["label"] for b in beats] == [
        f"illustrate:B{i}" for i in range(5)]  # order preserved
    assert closing.info["label"] == "illustrate:CLOSE"


def test_uses_prime_photo_for_opener_when_present():
    artist = _FakeArtist()
    opener, _beats, _closing = _generate_illustrations(
        artist=artist, book=_book(), subject_name="Dad", relationship="father",
        gt_context="", prime_photo=Image.new("RGB", (2, 2)), deage=True,
        blend="cream", concurrency=4)
    assert opener.info["label"] == "opener_portrait"


def test_runs_pages_concurrently():
    artist = _FakeArtist()
    _generate_illustrations(
        artist=artist, book=_book(), subject_name="Dad", relationship="father",
        gt_context="", prime_photo=None, deage=False, blend="cream",
        concurrency=4)
    # reference is serial first; the 7 page calls (opener+5 beats+closing)
    # should overlap, so we must have seen more than one in flight at once.
    assert artist.max_in_flight >= 2
