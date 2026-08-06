"""
Speech to text for one turn at a time.

A whole utterance is sent once the caller has stopped, rather than streamed
while they talk. For a turn-taking agent this is the right trade: the reply
cannot begin before the question ends anyway, and a recogniser given the
complete sentence gets the ending right, which streaming often does not.

Streaming transcription exists in this project, in the live call analysis,
where the point is to watch a conversation as it happens rather than answer it.

Language is a hint rather than a setting. Callers code-switch mid-sentence, and
forcing a language makes the recogniser render the other one phonetically.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from core.config import settings
from core.timing import track

log = logging.getLogger(__name__)

# Below this there is not enough audio to be a word.
MIN_AUDIO_BYTES = 4_000

TRANSIENT = ("429", "500", "502", "503", "504", "timeout", "unavailable")

# What Whisper returns when handed near-silence. It does not return nothing; it
# returns one of these, confidently, because the training data is full of
# subtitle files whose quiet passages carry exactly these captions.
#
# This is how a call turns into the agent talking to itself: room tone becomes
# "Thank you", the agent answers it, its own reply reaches the microphone, and
# round it goes. Seen in a real call before this filter existed.
# Only English subtitle artefacts. "Salamat po" and "terima kasih" were on
# this list briefly and should not have been: they are ordinary things a
# caller says, and filtering them would make the localised agents ignore a
# genuine thank-you. Quiet audio is already rejected before it gets here, on
# loudness, which is the right place to catch the case these were guarding.
HALLUCINATIONS = {
    "thank you", "thanks", "thank you.", "thanks for watching",
    "thanks for watching!", "thank you for watching", "you", "bye", "bye.",
    ".", "...", ". . .", "[silence]", "[music]", "(music)", "[blank_audio]",
    "subtitles by the amara.org community", "amara.org", "please subscribe",
}


# A single one of these is never an answer to anything. They come from the
# recogniser catching the leading edge of a word, or a caller drawing breath,
# and the agent then apologises for missing the end of a sentence that was
# never started. Kept to words that cannot stand alone: "no", "oo", "opo",
# "iya" and "yes" are complete answers in these markets and are not here.
FRAGMENTS = {
    "and", "so", "the", "a", "i", "um", "uh", "er", "ah", "eh", "mm", "hmm",
    "but", "or", "of", "to", "is", "it", "well", "like", "at", "in", "on",
    "ng", "na", "ba", "yung", "eh...", "ang", "sa", "yang", "di", "ke",
}


def is_probably_silence(text: str) -> bool:
    """Whether a transcript is what a recogniser says when it heard nothing."""
    cleaned = text.strip().lower().strip("¡!¿?\"'")
    if not cleaned:
        return True
    if cleaned in HALLUCINATIONS:
        return True
    # A single fragment, with or without trailing dots. "and..." reached the
    # agent as a turn and got a reply asking the caller to repeat themselves,
    # which is a worse outcome than having heard nothing at all.
    if cleaned.rstrip(".,").strip() in FRAGMENTS:
        return True
    # A handful of characters that are all punctuation is not speech.
    return not any(c.isalnum() for c in cleaned)


@dataclass
class Transcript:
    text: str
    milliseconds: float
    provider: str = "groq"
    model: str = ""
    language: str = ""
    audio_ms: float = 0.0
    error: str = ""

    def __bool__(self) -> bool:
        return bool(self.text.strip())

    @property
    def real_time_factor(self) -> float:
        """Recognition time against audio length. Under 1 is faster than real time."""
        return self.milliseconds / self.audio_ms if self.audio_ms else 0.0


class Transcriber:
    """Turns an utterance into text, or says clearly that it could not."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.groq_asr_model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from groq import Groq

            if not settings.groq_api_key:
                raise RuntimeError("GROQ_API_KEY is not set")
            self._client = Groq(api_key=settings.groq_api_key)
        return self._client

    def warmup(self) -> float:
        """Open the connection before a caller is waiting on it.

        Same reasoning as the language model: the first request of a process
        pays for the client and the handshake, and on a call that cost lands
        on the caller's first sentence.
        """
        from voice_agent.audio import silence, to_wav

        started = time.perf_counter()
        try:
            self.client.audio.transcriptions.create(
                file=("warmup.wav", to_wav(silence(400)), "audio/wav"),
                model=self.model,
            )
        except Exception as exc:
            log.warning("transcription warm-up failed",
                        extra={"reason": str(exc)[:120]})
        return (time.perf_counter() - started) * 1000

    def transcribe(self, wav: bytes, language: str | None = None,
                   prompt: str | None = None, audio_ms: float = 0.0,
                   trace: str = "", attempts: int = 3) -> Transcript:
        """Transcribe one utterance.

        A failure returns an empty transcript with the reason attached rather
        than raising. On a call the right response to a failed recognition is
        to ask the caller to repeat, not to drop the line.
        """
        if len(wav) < MIN_AUDIO_BYTES:
            return Transcript(text="", milliseconds=0.0, model=self.model,
                              audio_ms=audio_ms, error="too short to contain speech")

        delay = 1.0
        last = ""

        for attempt in range(attempts):
            try:
                with track("asr", trace=trace, detail=self.model) as span:
                    response = self.client.audio.transcriptions.create(
                        file=("turn.wav", wav, "audio/wav"),
                        model=self.model,
                        # Omitted entirely rather than passed as None, so the
                        # recogniser detects the language itself.
                        **({"language": language} if language else {}),
                        # A few in-domain words measurably improve rendering of
                        # terms like cicilan and bancassurance.
                        **({"prompt": prompt} if prompt else {}),
                        temperature=0.0,
                    )
                heard = (response.text or "").strip()
                if is_probably_silence(heard):
                    # The text goes in the message, not only in the fields.
                    # The console shows the message, and "a silence artefact
                    # was ignored" is useless without knowing what it was:
                    # a genuine subtitle artefact and a real one-word answer
                    # wrongly filtered look identical in the log otherwise.
                    log.info(f"recogniser returned a silence artefact, "
                             f"ignoring: {heard[:40]!r} from "
                             f"{round(audio_ms)} ms")
                    return Transcript(text="", milliseconds=span.milliseconds,
                                      model=self.model, audio_ms=audio_ms,
                                      error="silence")

                return Transcript(
                    text=heard,
                    milliseconds=span.milliseconds,
                    model=self.model,
                    language=language or "",
                    audio_ms=audio_ms,
                )
            except Exception as exc:
                last = str(exc)
                if not any(m in last.lower() for m in TRANSIENT) or attempt == attempts - 1:
                    break
                log.warning("transcription throttled, retrying",
                            extra={"wait_s": delay, "attempt": attempt + 1})
                time.sleep(delay)
                delay *= 2

        log.error("transcription failed", extra={"reason": last[:160]})
        return Transcript(text="", milliseconds=0.0, model=self.model,
                          audio_ms=audio_ms, error=last[:160])


def hint_for(business_unit: str) -> str:
    """The domain terms to prime the recogniser with for a market.

    The table itself lives with the rest of the per-market recognition
    settings. It used to be duplicated here, and the copies had already
    drifted: this one never gained the regional Indonesian politeness words,
    so the call loop was running on the weaker of the two without any sign
    that a better one existed.
    """
    from voice_agent.asr import config_for

    return config_for(business_unit).prompt
