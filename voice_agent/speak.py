"""
Speech synthesis.

Sentence by sentence rather than reply by reply. A three sentence answer takes
around a second and a half to synthesise in full, and none of it plays until it
is done; split into sentences, the first plays while the rest is still being
made. The caller hears a voice roughly a second earlier for no extra cost.

Very short sentences are merged before synthesis. "Got it." on its own produces
an audible click and a pause before the next clause, which reads as hesitation.

No key is needed for any of this, and the Filipino and Indonesian voices are
native rather than an English voice reading foreign words.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from core.config import settings
from core.llm import clean_for_speech

log = logging.getLogger(__name__)

# Below this a sentence is joined to the next one. Roughly four words.
MIN_SENTENCE_CHARS = 24

# Above this a sentence is split at a comma, so the first audio starts sooner.
MAX_SENTENCE_CHARS = 220

VOICES = {
    "en": settings.tts_voice_en,
    "fil": settings.tts_voice_fil,
    "tl": settings.tts_voice_fil,
    "id": settings.tts_voice_id,
}

# The locale each voice must actually be from. A voice is only native if its
# name starts with the right locale, and checking that is worth doing because
# the failure is silent: an English voice reading Taglish is still audio, still
# fluent-sounding, and completely wrong. It mispronounces every Tagalog word
# while sounding confident, which is harder to notice than no audio at all.
NATIVE_LOCALE = {"en": "en-", "fil": "fil-PH-", "tl": "fil-PH-", "id": "id-ID-"}


@dataclass
class Speech:
    text: str
    audio: bytes
    milliseconds: float
    voice: str
    format: str = "mp3"


@dataclass
class SpokenReply:
    """A whole reply, in the order it should be played."""

    parts: list[Speech] = field(default_factory=list)
    first_audio_ms: float = 0.0
    total_ms: float = 0.0

    @property
    def audio(self) -> bytes:
        return b"".join(p.audio for p in self.parts)

    @property
    def text(self) -> str:
        return " ".join(p.text for p in self.parts)


def split_for_speech(text: str) -> list[str]:
    """Break a reply into pieces worth synthesising separately."""
    cleaned = clean_for_speech(text)
    if not cleaned:
        return []

    rough = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]

    pieces: list[str] = []
    for sentence in rough:
        # A long sentence delays the first sound, so it is cut at a comma.
        while len(sentence) > MAX_SENTENCE_CHARS:
            cut = sentence.rfind(",", 0, MAX_SENTENCE_CHARS)
            if cut < MIN_SENTENCE_CHARS:
                break
            pieces.append(sentence[:cut + 1].strip())
            sentence = sentence[cut + 1:].strip()
        pieces.append(sentence)

    # Merge the stubs. "Got it." alone produces a click and a pause that reads
    # as the agent hesitating.
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < MIN_SENTENCE_CHARS:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def voice_for(language: str) -> str:
    """The native voice for a language, or English if there is not one.

    Falling back is the right behaviour, since a call with an accented voice
    beats a call with no voice. Saying so in the log is the other half: a
    market quietly running on an English voice is a defect, not a setting.
    """
    voice = VOICES.get(language)
    if not voice:
        log.error("no native voice, falling back to English",
                  extra={"language": language})
        return settings.tts_voice_en
    return voice


def is_native(language: str, voice: str) -> bool:
    prefix = NATIVE_LOCALE.get(language)
    return bool(prefix) and voice.startswith(prefix)


class Speaker:
    """Turns text into audio, a sentence at a time."""

    def __init__(self, language: str = "en", voice: str | None = None) -> None:
        self.language = language
        self.voice = voice or voice_for(language)
        if not is_native(language, self.voice):
            log.warning("voice is not native to the market",
                        extra={"language": language, "voice": self.voice})

    async def _synthesise(self, text: str, voice: str | None = None) -> Speech:
        import edge_tts

        chosen = voice or self.voice
        started = time.perf_counter()
        audio = bytearray()
        try:
            async for chunk in edge_tts.Communicate(text, chosen).stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
        except Exception as exc:
            log.error("speech synthesis failed",
                      extra={"reason": str(exc)[:120], "voice": chosen})
            return Speech(text=text, audio=b"", milliseconds=0.0, voice=chosen)

        return Speech(text=text, audio=bytes(audio),
                      milliseconds=(time.perf_counter() - started) * 1000,
                      voice=chosen)

    async def stream(self, text: str, voice: str | None = None):
        """Yield audio piece by piece, so playback starts on the first sentence."""
        pieces = split_for_speech(text)
        if not pieces:
            return

        started = time.perf_counter()
        first = True
        for piece in pieces:
            speech = await self._synthesise(piece, voice)
            if first:
                log.debug("first audio ready",
                          extra={"ms": round((time.perf_counter() - started) * 1000)})
                first = False
            if speech.audio:
                yield speech

    async def say(self, text: str, voice: str | None = None) -> SpokenReply:
        """Synthesise a whole reply, timing when the first audio was ready."""
        reply = SpokenReply()
        started = time.perf_counter()

        async for speech in self.stream(text, voice):
            if not reply.parts:
                reply.first_audio_ms = (time.perf_counter() - started) * 1000
            reply.parts.append(speech)

        reply.total_ms = (time.perf_counter() - started) * 1000
        return reply

    def say_sync(self, text: str, voice: str | None = None) -> SpokenReply:
        return asyncio.run(self.say(text, voice))

    async def warmup(self) -> float:
        """Open the connection before a caller is waiting on it."""
        started = time.perf_counter()
        await self._synthesise("Ready.")
        return (time.perf_counter() - started) * 1000


def to_pcm(mp3: bytes, sample_rate: int = 16_000) -> bytes:
    """Decode synthesised audio for anything that wants raw samples."""
    import subprocess

    if not mp3:
        return b""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-acodec", "pcm_s16le",
         "-ar", str(sample_rate), "-ac", "1", "pipe:1"],
        input=mp3, capture_output=True, check=False,
    )
    return result.stdout
