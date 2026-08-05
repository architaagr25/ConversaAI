"""
Round trip through the voice backend: synthesise, endpoint, transcribe.

Speech is generated, turned into the format a browser would send, fed through
the same endpointer a live call uses, and transcribed. That exercises every
part of the audio path against real audio rather than against a fixture, and
it produces the recognition numbers the multilingual work will need later.

Words that come back changed are printed. A recogniser that renders "cicilan"
as "chichilan" is not broken, but it is worth knowing before it happens on a
call.

    .venv\\Scripts\\python scripts/check_speech.py
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings  # noqa: E402
from voice_agent.audio import (  # noqa: E402
    SAMPLE_RATE,
    Endpointer,
    duration_ms,
    silence,
)
from voice_agent.transcribe import Transcriber, hint_for  # noqa: E402

CASES = [
    ("English", settings.tts_voice_en, "health_ph_en",
     "How long is the waiting period for pre-existing conditions?"),
    ("English", settings.tts_voice_en, "health_ph_en",
     "I am thirty five years old and I live in the Philippines."),
    ("Taglish", settings.tts_voice_fil, "life_ph",
     "Magkano po ang premium ko kung monthly ang bayad?"),
    ("Taglish", settings.tts_voice_fil, "life_ph",
     "Ano po ang mangyayari kung ma-lapse ang policy ko?"),
    ("Indonesian", settings.tts_voice_id, "multifinance_id",
     "Berapa denda kalau telat bayar cicilan?"),
    ("Indonesian", settings.tts_voice_id, "multifinance_id",
     "Saya mau tanya soal tenor dan jatuh tempo angsuran."),
]


async def speak(text: str, voice: str) -> bytes:
    import edge_tts

    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


def to_pcm(mp3: bytes) -> bytes:
    """Convert to what a browser would send: 16 kHz mono 16-bit."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-acodec", "pcm_s16le",
         "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1"],
        input=mp3, capture_output=True, check=True,
    )
    return result.stdout


# Recognisers write numbers as digits. "thirty five" coming back as "35" is a
# correct transcription, and scoring it as two missed words understates
# accuracy while hiding the errors that are real.
NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
}


TENS = {"20", "30", "40", "50", "60", "70", "80", "90"}


def normalise(text: str) -> list[str]:
    """Words, with numbers written the way a recogniser writes them.

    Compound numbers are combined, so "thirty five" becomes "35" rather than
    "30" and "5". Ages are the reason: an agent asking how old somebody is gets
    a compound number back most of the time, and scoring it as two words makes
    a perfect transcription look like a fifty per cent failure.
    """
    words = [NUMBER_WORDS.get(w, w) for w in re.findall(r"[a-z0-9']+", text.lower())]

    merged: list[str] = []
    index = 0
    while index < len(words):
        current = words[index]
        following = words[index + 1] if index + 1 < len(words) else None
        if current in TENS and following and following.isdigit() and len(following) == 1:
            merged.append(str(int(current) + int(following)))
            index += 2
            continue
        merged.append(current)
        index += 1
    return merged


def compare(expected: str, actual: str) -> tuple[float, list[str]]:
    """Word accuracy, and which words did not survive."""
    wanted = normalise(expected)
    got = set(normalise(actual))
    # "thirty five" against "35": the digits are present as one token, so a
    # word only counts as missed if neither it nor its digit form appears, and
    # neither does the pair it may have been merged into.
    joined = "".join(normalise(actual))
    if not wanted:
        return 0.0, []
    missed = [w for w in wanted if w not in got and w not in joined]
    return 1 - len(missed) / len(wanted), missed


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    print("=" * 96)
    print("Voice backend round trip")
    print("=" * 96)

    transcriber = Transcriber()
    print(f"\n  recogniser        {transcriber.model}")
    warm = transcriber.warmup()
    print(f"  connection warm-up {warm:.0f} ms  (paid once, before any call)")

    print(f"\n{'language':<12}{'audio':>8}{'asr':>8}{'rtf':>7}{'words':>8}  transcript")
    print("-" * 96)

    results = []
    for language, voice, unit, sentence in CASES:
        mp3 = asyncio.run(speak(sentence, voice))
        pcm = to_pcm(mp3)

        # Through the same endpointer a live call uses, with trailing silence
        # so it closes the turn exactly as a caller pausing would.
        endpointer = Endpointer()
        utterances = list(endpointer.feed_stream(pcm + silence(1200)))
        if not utterances:
            print(f"{language:<12}{'':>8}  endpointer found no speech")
            continue

        utterance = utterances[0]
        started = time.perf_counter()
        transcript = transcriber.transcribe(
            utterance.wav, prompt=hint_for(unit),
            audio_ms=utterance.duration_ms)
        elapsed = (time.perf_counter() - started) * 1000

        accuracy, missed = compare(sentence, transcript.text)
        results.append((language, accuracy, transcript, missed, sentence))

        print(f"{language:<12}{duration_ms(pcm):>7.0f}ms{elapsed:>7.0f}ms"
              f"{transcript.real_time_factor:>7.2f}{accuracy:>7.0%}  "
              f"{transcript.text[:44]}")

    print(f"\n{'=' * 96}")
    print("Words that did not survive recognition")
    print("-" * 96)
    any_missed = False
    for language, accuracy, transcript, missed, sentence in results:
        if missed:
            any_missed = True
            print(f"  {language:<12}{', '.join(missed)}")
            print(f"      said:  {sentence}")
            print(f"      heard: {transcript.text}")
    if not any_missed:
        print("  none")

    if results:
        by_language: dict[str, list[float]] = {}
        for language, accuracy, *_ in results:
            by_language.setdefault(language, []).append(accuracy)
        print(f"\n{'=' * 96}")
        for language, scores in by_language.items():
            print(f"  {language:<12}{sum(scores) / len(scores):.0%} word accuracy "
                  f"over {len(scores)} utterances")

        latencies = [r[2].milliseconds for r in results]
        print(f"\n  recognition latency: median "
              f"{sorted(latencies)[len(latencies) // 2]:.0f} ms")

    print("\n  Synthetic speech is cleaner than a real caller on a laptop")
    print("  microphone. These numbers are an upper bound, not a forecast.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
