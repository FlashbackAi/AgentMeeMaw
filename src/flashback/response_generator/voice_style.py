"""Voice-mode style-tag parsing.

In voice mode the response LLM prefixes its reply with exactly one style
tag — ``[[style: tender]]`` — chosen from a fixed whitelist. Gemini TTS
takes prosody as a per-utterance *style instruction* (unlike ElevenLabs
v3, which parsed inline bracketed tags mid-text), so we lift this single
label out of the reply, hand it to Node via the stream's ``voice_style``
event (and the JSON response metadata), and never let it reach TTS or the
on-screen transcript.

Node maps the label to a Gemini TTS style instruction for the utterance.
The label is chosen by the same LLM that writes the words — mirroring how
ElevenLabs mode let the model pick ``[softly]`` / ``[chuckles]`` itself.
"""

from __future__ import annotations

import re

# Warm, contemplative register — this is legacy work. Mirrors the old
# ElevenLabs v3 tag whitelist collapsed to per-utterance styles:
#   [chuckles]/[warm] -> warm   [softly] -> tender
#   [curious] -> curious         [thoughtful]/[gentle pause] -> thoughtful
#   [sighs] -> wistful
VOICE_STYLES: tuple[str, ...] = (
    "warm",
    "tender",
    "curious",
    "thoughtful",
    "wistful",
    "neutral",
)
DEFAULT_VOICE_STYLE = "neutral"

# Leading ``[[style: <label>]]`` tag, tolerant of casing and internal
# spacing, consuming any trailing whitespace/newlines after it.
_TAG_RE = re.compile(r"^\s*\[\[\s*style\s*:\s*([a-zA-Z_]+)\s*\]\]\s*", re.IGNORECASE)

# Once the buffered prefix can no longer be the start of a tag (or grows
# past this many chars without closing), stop withholding text.
_GIVE_UP_AFTER = 64


def _normalize(label: str) -> str:
    label = label.strip().lower()
    return label if label in VOICE_STYLES else DEFAULT_VOICE_STYLE


def extract_voice_style(text: str) -> tuple[str, str]:
    """Pull a leading ``[[style: x]]`` tag off a complete reply.

    Returns ``(style, cleaned_text)``. A missing or unknown tag yields
    :data:`DEFAULT_VOICE_STYLE` and leaves the text otherwise intact.
    """
    match = _TAG_RE.match(text)
    if not match:
        return DEFAULT_VOICE_STYLE, text
    return _normalize(match.group(1)), text[match.end() :]


def _could_be_tag_prefix(buffer: str) -> bool:
    """True while ``buffer`` might still grow into a leading style tag."""
    head = buffer.lstrip()
    if head == "":
        return True
    # Building up the opening "[[" one bracket at a time.
    return head.startswith("[")


class VoiceStyleStreamParser:
    """Strip a leading style tag from a stream of text chunks.

    Buffers only until the leading tag resolves (or we're confident there
    isn't one), so at most the first couple of tokens are withheld. Once
    resolved, chunks pass straight through. Usage::

        parser = VoiceStyleStreamParser()
        for chunk in stream:
            out = parser.feed(chunk)
            if out:
                emit(out)
        tail = parser.flush()
        if tail:
            emit(tail)
        style = parser.style
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._style: str | None = None
        self._resolved = False

    @property
    def resolved(self) -> bool:
        """True once the leading tag (or its absence) has been decided."""
        return self._resolved

    @property
    def style(self) -> str:
        """The resolved style; :data:`DEFAULT_VOICE_STYLE` until/if none."""
        return self._style if self._style is not None else DEFAULT_VOICE_STYLE

    def feed(self, chunk: str) -> str:
        """Accept a raw chunk; return text safe to emit (tag stripped)."""
        if self._resolved:
            return chunk
        self._buffer += chunk
        match = _TAG_RE.match(self._buffer)
        if match:
            self._style = _normalize(match.group(1))
            self._resolved = True
            rest = self._buffer[match.end() :]
            self._buffer = ""
            return rest
        # No complete tag yet. Keep withholding only while a tag is still
        # plausible and the buffer is short; otherwise flush as-is.
        if (
            not _could_be_tag_prefix(self._buffer)
            or len(self._buffer) >= _GIVE_UP_AFTER
            or "\n" in self._buffer
        ):
            self._resolved = True
            out, self._buffer = self._buffer, ""
            return out
        return ""

    def flush(self) -> str:
        """Emit any withheld remainder at end of stream."""
        if self._resolved:
            return ""
        self._resolved = True
        out, self._buffer = self._buffer, ""
        return out
