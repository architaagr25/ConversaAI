"""
Per-market speech recognition, and a second provider to compare against.

Each market is configured separately because the right settings differ and the
differences are not guessable. Two decisions matter.

Whether to pass a language hint at all. Forcing a language sounds obviously
correct and is wrong for Taglish: a recogniser told the audio is Tagalog
renders the English half phonetically, and half of what a Filipino caller says
on an insurance call is English. Indonesian is the opposite case, where the
hint helps because the speech really is one language.

Which words to prime it with. A short list of in-domain terms measurably
changes what comes back, and the list has to come from what the recogniser
actually gets wrong rather than from what seems likely.

Two providers are supported so the choice can be defended with numbers rather
than asserted. They disagree, and where they disagree is itself a finding.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from core.config import settings
from core.timing import track
from voice_agent.transcribe import Transcript, is_probably_silence

log = logging.getLogger(__name__)


@dataclass
class MarketASR:
    """How to listen to one market."""

    business_unit: str
    label: str
    # None means let the recogniser decide. Set only where the speech really
    # is one language.
    language_hint: str | None
    prompt: str
    why: str
    expects_code_switching: bool = False
    regional_varieties: list[str] = field(default_factory=list)


MARKETS: dict[str, MarketASR] = {
    "health_ph_en": MarketASR(
        business_unit="health_ph_en",
        label="English, Philippines",
        language_hint="en",
        prompt=(
            "Solara Health Shield, premium, waiting period, pre-existing "
            "condition, rider, deductible, accredited hospital, grace period, "
            "Essential, Plus, Max"
        ),
        why=(
            "Callers use English throughout on this product, so the hint is "
            "safe and slightly improves the handling of Filipino place names."
        ),
    ),

    "life_ph": MarketASR(
        business_unit="life_ph",
        label="Taglish, Philippines",
        # Deliberately unset. See why.
        language_hint=None,
        prompt=(
            "Solara Life, magkano, premium, hulog, bayad, benepisyaryo, "
            "beneficiary, rider, ma-lapse, lapse, reinstatement, "
            "bancassurance, sum assured, kada buwan, takdang araw, grace period"
        ),
        why=(
            "Taglish switches language inside a sentence. Forcing Tagalog "
            "makes the recogniser render the English words phonetically, and "
            "forcing English does the reverse. Letting it decide per segment "
            "is the only setting that handles both halves."
        ),
        expects_code_switching=True,
    ),

    "multifinance_id": MarketASR(
        business_unit="multifinance_id",
        label="Bahasa Indonesia",
        language_hint="id",
        prompt=(
            "Solara Multifinance, cicilan, angsuran, tenor, denda, jatuh "
            "tempo, DP, uang muka, pembiayaan, BPKB, plafon, restrukturisasi, "
            "nuwun sewu, monggo, nggih, punten, hatur nuhun"
        ),
        why=(
            "The speech is one language even when it borrows English finance "
            "words, so the hint helps. Regional politeness markers are in the "
            "prompt because they are short, unusual and easily lost."
        ),
        regional_varieties=["javanese", "sundanese"],
    ),
}


def config_for(business_unit: str) -> MarketASR:
    return MARKETS.get(business_unit, MARKETS["health_ph_en"])


# --- Second provider ---------------------------------------------------------


class DeepgramTranscriber:
    """The alternative recogniser, for comparison and for streaming later.

    Kept behind the same shape as the primary one so the two can be swapped in
    a benchmark without the caller knowing which is which.
    """

    name = "deepgram"

    # Deepgram takes a language code rather than a free-text prompt, and its
    # language list is not the same as Whisper's. Where a market's language is
    # not supported, "multi" is the closest thing available.
    LANGUAGES = {
        "health_ph_en": "en",
        "life_ph": "multi",
        "multifinance_id": "id",
    }

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.deepgram_model

    def transcribe(self, wav: bytes, business_unit: str = "health_ph_en",
                   audio_ms: float = 0.0, trace: str = "", **_) -> Transcript:
        import httpx

        if not settings.deepgram_api_key:
            return Transcript(text="", milliseconds=0.0, provider=self.name,
                              model=self.model, error="DEEPGRAM_API_KEY is not set")

        language = self.LANGUAGES.get(business_unit, "en")
        params = {"model": self.model, "language": language,
                  "smart_format": "true", "punctuate": "true"}

        try:
            with track("asr_deepgram", trace=trace, detail=self.model) as span:
                with httpx.Client(timeout=45) as client:
                    response = client.post(
                        "https://api.deepgram.com/v1/listen",
                        params=params,
                        headers={"Authorization": f"Token {settings.deepgram_api_key}",
                                 "Content-Type": "audio/wav"},
                        content=wav,
                    )
            if response.status_code != 200:
                return Transcript(text="", milliseconds=span.milliseconds,
                                  provider=self.name, model=self.model,
                                  audio_ms=audio_ms,
                                  error=f"HTTP {response.status_code}: "
                                        f"{response.text[:120]}")

            body = response.json()
            alternatives = (body.get("results", {}).get("channels", [{}])[0]
                            .get("alternatives", [{}]))
            heard = (alternatives[0].get("transcript") or "").strip()

            if is_probably_silence(heard):
                return Transcript(text="", milliseconds=span.milliseconds,
                                  provider=self.name, model=self.model,
                                  audio_ms=audio_ms, error="silence")

            return Transcript(text=heard, milliseconds=span.milliseconds,
                              provider=self.name, model=self.model,
                              language=language, audio_ms=audio_ms)
        except Exception as exc:
            log.error("deepgram transcription failed",
                      extra={"reason": str(exc)[:160]})
            return Transcript(text="", milliseconds=0.0, provider=self.name,
                              model=self.model, audio_ms=audio_ms,
                              error=str(exc)[:160])

    def warmup(self) -> float:
        from voice_agent.audio import silence, to_wav

        started = time.perf_counter()
        try:
            self.transcribe(to_wav(silence(400)))
        except Exception:
            pass
        return (time.perf_counter() - started) * 1000


class MarketTranscriber:
    """The primary recogniser, configured per market."""

    name = "groq"

    def __init__(self, model: str | None = None) -> None:
        from voice_agent.transcribe import Transcriber

        self.inner = Transcriber(model)
        self.model = self.inner.model

    def transcribe(self, wav: bytes, business_unit: str = "health_ph_en",
                   audio_ms: float = 0.0, trace: str = "", **_) -> Transcript:
        config = config_for(business_unit)
        return self.inner.transcribe(
            wav, language=config.language_hint, prompt=config.prompt,
            audio_ms=audio_ms, trace=trace)

    def warmup(self) -> float:
        return self.inner.warmup()


# --- Code switching ----------------------------------------------------------

# Words that only exist in one of the two languages, used to work out whether a
# sentence moved between them. Deliberately small: the point is to detect a
# switch, not to classify every word. "Kami" is in neither list even though it
# is common in both, because it means "we" in both and so proves nothing.
TAGALOG_ONLY = {
    "ang", "ng", "mga", "po", "opo", "ako", "ikaw", "kayo", "niyo", "siya",
    "tayo", "namin", "natin", "nila", "iyan", "iyon", "ito", "dito",
    "doon", "kung", "kasi", "dahil", "pero", "para", "wala", "meron",
    "mayroon", "hindi", "oo", "magkano", "kailan", "paano", "bakit", "sino",
    "ano", "saan", "bayad", "hulog", "buwan", "araw", "salamat", "sige",
    "naman", "lang", "din", "rin", "nga", "muna", "pala", "yung", "pwede",
}

INDONESIAN_ONLY = {
    "yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada", "adalah",
    "saya", "aku", "kamu", "anda", "kita", "mereka", "ini", "itu",
    "sudah", "belum", "akan", "bisa", "tidak", "nggak", "iya", "ya", "kalau",
    "karena", "tapi", "juga", "lagi", "masih", "berapa", "kapan", "bagaimana",
    "gimana", "kenapa", "siapa", "dimana", "bulan", "hari", "bayar", "cicilan",
    "angsuran", "denda", "tenor", "jatuh", "tempo", "terima", "kasih",
}

ENGLISH_FUNCTION = {
    "the", "is", "are", "and", "but", "for", "with", "that", "this", "have",
    "will", "would", "can", "please", "thank", "you", "your", "what", "when",
    "how", "why", "who", "where", "which", "there", "here", "about", "from",
}

# The finance vocabulary speakers keep in English. An earlier version left
# these out, on the reasoning that a loanword says nothing about which language
# somebody is in. That is exactly backwards for Taglish: reaching for the
# English word mid-sentence IS the switch. Without them the detector called
# "Magkano po ang premium ko kung monthly ang bayad" Tagalog throughout.
ENGLISH_DOMAIN = {
    "premium", "policy", "monthly", "annual", "grace", "period", "coverage",
    "beneficiary", "rider", "lapse", "claim", "payment", "plan", "insurance",
    "due", "date", "branch", "bank", "online", "transfer", "balance", "agent",
    "approve", "approved", "reject", "process", "customer", "service",
}

ENGLISH_MARKERS = ENGLISH_FUNCTION | ENGLISH_DOMAIN


@dataclass
class CodeSwitch:
    languages: list[str]
    switched: bool
    shares: dict[str, float]

    def describe(self) -> str:
        if not self.languages:
            return "nothing recognisable"
        if not self.switched:
            return f"{self.languages[0]} throughout"
        parts = ", ".join(f"{lang} {self.shares[lang]:.0%}"
                          for lang in self.languages)
        return f"switched between {parts}"


def detect_code_switching(text: str) -> CodeSwitch:
    """Which languages a sentence used, and whether it moved between them.

    Counted on words that belong to one language, including the English
    finance terms a Taglish speaker reaches for mid-sentence. Everything else
    is ignored, so shared and ambiguous words do not tip the count either way.
    """
    import re

    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return CodeSwitch(languages=[], switched=False, shares={})

    counts = {
        "tagalog": sum(1 for w in words if w in TAGALOG_ONLY),
        "indonesian": sum(1 for w in words if w in INDONESIAN_ONLY),
        "english": sum(1 for w in words if w in ENGLISH_MARKERS),
    }
    total = sum(counts.values())
    if not total:
        return CodeSwitch(languages=[], switched=False, shares={})

    shares = {lang: count / total for lang, count in counts.items() if count}
    # A stray function word should not count as a switch, so a language has to
    # carry a real share of the sentence to be counted as present.
    present = {lang for lang, share in shares.items() if share >= 0.15}

    # One finance term is different, and the share rule is too blunt for it.
    # "Beneficiary" inside an otherwise Tagalog sentence is not an incidental
    # word, it is the switch, and on a long sentence a single one falls under
    # the threshold. It counts on its own, but only alongside another language,
    # so a sentence that is simply English is still reported as English.
    if any(w in ENGLISH_DOMAIN for w in words) and (present - {"english"}):
        present.add("english")

    ordered = sorted(present, key=lambda lang: -shares[lang])
    return CodeSwitch(languages=ordered, switched=len(ordered) > 1, shares=shares)
