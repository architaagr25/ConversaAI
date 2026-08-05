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
                return Transcript(
                    text=(response.text or "").strip(),
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


# Words the recogniser would otherwise render phonetically. Kept per market
# because a Philippine prompt full of Indonesian terms makes English worse.
DOMAIN_HINTS = {
    "health_ph_en": (
        "Solara Health Shield, premium, waiting period, pre-existing condition, "
        "rider, deductible, accredited hospital, Essential, Plus, Max"
    ),
    # magkano and lapse are here because the recogniser got both wrong without
    # them, rendering "magkano" as "magkana" and "ma-lapse" as "malapsi". Both
    # are common enough in this flow to be worth naming.
    "life_ph": (
        "Solara Life, magkano, premium, hulog, bayad, benepisyaryo, beneficiary, "
        "rider, ma-lapse, lapse, reinstatement, bancassurance, sum assured, "
        "kada buwan, takdang araw"
    ),
    "multifinance_id": (
        "Solara Multifinance, cicilan, angsuran, tenor, denda, jatuh tempo, "
        "DP, uang muka, pembiayaan, BPKB, plafon, restrukturisasi"
    ),
}


def hint_for(business_unit: str) -> str:
    return DOMAIN_HINTS.get(business_unit, "")
