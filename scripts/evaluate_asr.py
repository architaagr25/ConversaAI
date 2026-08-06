"""
Compares the two recognisers across all three markets.

Speech is synthesised, streamed through the same endpointer a call uses, and
transcribed by both providers. The same audio goes to both, so the comparison
is of the recognisers rather than of the recordings.

Reports word accuracy, the words that did not survive, recognition latency,
and what each provider does with a sentence that switches language halfway
through. Regional Indonesian is included because that is the case a provider
is most likely to be weak on and least likely to warn about.

    .venv\\Scripts\\python scripts/evaluate_asr.py
"""

from __future__ import annotations

import asyncio
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import PROJECT_ROOT, settings  # noqa: E402
from voice_agent.asr import (  # noqa: E402
    MARKETS,
    DeepgramTranscriber,
    MarketTranscriber,
    config_for,
    detect_code_switching,
)
from voice_agent.audio import SAMPLE_RATE, to_wav  # noqa: E402

REPORT = PROJECT_ROOT / "results" / "asr_evaluation.md"


@dataclass
class Case:
    unit: str
    voice: str
    kind: str
    said: str


CASES = [
    # English, Philippines
    Case("health_ph_en", settings.tts_voice_en, "plain",
         "How long is the waiting period for pre-existing conditions?"),
    Case("health_ph_en", settings.tts_voice_en, "numbers",
         "I am thirty five years old and I earn sixty thousand a month."),
    Case("health_ph_en", settings.tts_voice_en, "domain terms",
         "Does the Plus plan include an accredited hospital and a rider?"),

    # Taglish
    Case("life_ph", settings.tts_voice_fil, "plain tagalog",
         "Magkano po ang hulog ko kada buwan?"),
    Case("life_ph", settings.tts_voice_fil, "code switch",
         "Magkano po ang premium ko kung monthly ang bayad?"),
    Case("life_ph", settings.tts_voice_fil, "heavy code switch",
         "Ano po ang mangyayari kung ma-lapse ang policy, may grace period po ba?"),
    Case("life_ph", settings.tts_voice_fil, "domain terms",
         "Sino po ang benepisyaryo at pwede po bang palitan ang beneficiary?"),

    # Bahasa Indonesia
    Case("multifinance_id", settings.tts_voice_id, "plain",
         "Berapa denda kalau telat bayar cicilan seminggu?"),
    Case("multifinance_id", settings.tts_voice_id, "loanwords",
         "Sisa tenor saya berapa dan DP kemarin sudah masuk belum?"),
    Case("multifinance_id", settings.tts_voice_id, "colloquial",
         "Belum sempat pak, nanti aja ya, lagi susah bulan ini."),
    Case("multifinance_id", settings.tts_voice_id, "javanese",
         "Nuwun sewu, kulo dereng saget mbayar cicilan niki."),
    Case("multifinance_id", settings.tts_voice_id, "sundanese",
         "Punten, abdi teh can tiasa mayar ayeuna."),
]


@dataclass
class Result:
    case: Case
    provider: str
    heard: str
    accuracy: float
    missed: list[str] = field(default_factory=list)
    milliseconds: float = 0.0
    error: str = ""


async def synthesise(text: str, voice: str) -> bytes:
    import edge_tts

    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


def to_pcm(mp3: bytes) -> bytes:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(SAMPLE_RATE),
         "-ac", "1", "pipe:1"],
        input=mp3, capture_output=True, check=False)
    return result.stdout


NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50",
    "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
    "hundred": "100", "thousand": "1000",
}
TENS = {"20", "30", "40", "50", "60", "70", "80", "90"}


def normalise(text: str) -> list[str]:
    words = [NUMBER_WORDS.get(w, w)
             for w in re.findall(r"[a-z0-9']+", text.lower())]
    merged, index = [], 0
    while index < len(words):
        current = words[index]
        following = words[index + 1] if index + 1 < len(words) else None
        if current in TENS and following and following.isdigit() \
                and len(following) == 1:
            merged.append(str(int(current) + int(following)))
            index += 2
            continue
        merged.append(current)
        index += 1
    return merged


def score(expected: str, actual: str) -> tuple[float, list[str]]:
    wanted = normalise(expected)
    got = set(normalise(actual))
    joined = "".join(normalise(actual))
    if not wanted:
        return 0.0, []
    missed = [w for w in wanted if w not in got and w not in joined]
    return 1 - len(missed) / len(wanted), missed


def run() -> list[Result]:
    providers = [MarketTranscriber(), DeepgramTranscriber()]
    for provider in providers:
        provider.warmup()

    results: list[Result] = []
    for case in CASES:
        pcm = to_pcm(asyncio.run(synthesise(case.said, case.voice)))
        wav = to_wav(pcm)

        for provider in providers:
            transcript = provider.transcribe(wav, business_unit=case.unit)
            accuracy, missed = score(case.said, transcript.text)
            results.append(Result(
                case=case, provider=provider.name, heard=transcript.text,
                accuracy=accuracy if transcript.text else 0.0, missed=missed,
                milliseconds=transcript.milliseconds, error=transcript.error))
    return results


def write_report(results: list[Result]) -> None:
    by_provider: dict[str, list[Result]] = {}
    for r in results:
        by_provider.setdefault(r.provider, []).append(r)

    lines = [
        "# Speech recognition evaluation",
        "",
        "Two providers, three markets, twelve utterances each. The same audio "
        "goes to both, so what is compared is the recognisers rather than the "
        "recordings.",
        "",
        "Speech is synthesised, which makes it cleaner than a caller on a "
        "laptop microphone in a room with a fan. These are upper bounds.",
        "",
        "## Configuration",
        "",
        "| Market | Language hint | Why |",
        "| --- | --- | --- |",
    ]
    for unit, config in MARKETS.items():
        hint = f"`{config.language_hint}`" if config.language_hint \
            else "**none, deliberately**"
        lines.append(f"| {config.label} | {hint} | {' '.join(config.why.split())} |")

    lines += ["", "## Word accuracy", "",
              "| Market | Case | " +
              " | ".join(sorted(by_provider)) + " |",
              "| --- | --- | " + " | ".join("---" for _ in by_provider) + " |"]

    for case in CASES:
        row = [config_for(case.unit).label, case.kind]
        for provider in sorted(by_provider):
            match = next((r for r in results
                          if r.case is case and r.provider == provider), None)
            row.append(f"{match.accuracy:.0%}" if match else "-")
        lines.append("| " + " | ".join(row) + " |")

    lines += ["", "## Summary", "",
              "| Provider | Model | Mean accuracy | Median latency |",
              "| --- | --- | --- | --- |"]
    for provider, group in sorted(by_provider.items()):
        accuracy = statistics.mean(r.accuracy for r in group)
        latencies = [r.milliseconds for r in group if r.milliseconds]
        median = statistics.median(latencies) if latencies else 0
        model = settings.groq_asr_model if provider == "groq" \
            else settings.deepgram_model
        lines.append(f"| {provider} | `{model}` | {accuracy:.0%} | {median:.0f} ms |")

    lines += ["", "## Where words were lost", ""]
    any_missed = False
    for r in results:
        if r.missed or r.error:
            any_missed = True
            lines += [
                f"**{config_for(r.case.unit).label} / {r.case.kind} / {r.provider}**",
                "",
                f"- said: {r.case.said}",
                f"- heard: {r.heard or '(nothing)'}",
            ]
            if r.missed:
                lines.append(f"- lost: {', '.join(r.missed)}")
            if r.error:
                lines.append(f"- error: {r.error}")
            lines.append("")
    if not any_missed:
        lines.append("Nothing was lost.")

    lines += ["", "## Code switching", "",
              "What each provider returned for the sentences that move "
              "between languages mid-sentence.", ""]

    # A sentence that really does switch is only transcribed correctly if both
    # languages survive. Losing one half and returning fluent text for the
    # other is the failure worth naming, because nothing about the output
    # looks wrong on its own.
    switching = [r for r in results
                 if config_for(r.case.unit).expects_code_switching
                 and detect_code_switching(r.case.said).switched]
    kept: dict[str, int] = {}
    for r in switching:
        if detect_code_switching(r.heard).switched:
            kept[r.provider] = kept.get(r.provider, 0) + 1

    total = len({r.case.kind for r in switching})
    for provider in sorted(by_provider):
        lines.append(f"- **{provider}** kept both languages in "
                     f"{kept.get(provider, 0)} of {total} switching sentences.")
    lines.append("")

    for r in results:
        if not config_for(r.case.unit).expects_code_switching:
            continue
        switch = detect_code_switching(r.heard)
        expected = detect_code_switching(r.case.said)
        note = ""
        if expected.switched and not switch.switched and r.heard:
            note = "  <- one language lost"
        lines += [f"**{r.case.kind} / {r.provider}**",
                  f"- said: {r.case.said}",
                  f"- heard: {r.heard or '(nothing)'}",
                  f"- languages: {switch.describe()}{note}", ""]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    print("=" * 96)
    print("Speech recognition evaluation")
    print("=" * 96)

    results = run()

    print(f"\n{'market':<22}{'case':<20}{'groq':>8}{'deepgram':>10}  heard (groq)")
    print("-" * 96)
    for case in CASES:
        pair = {r.provider: r for r in results if r.case is case}
        groq, deepgram = pair.get("groq"), pair.get("deepgram")
        print(f"{config_for(case.unit).label:<22}{case.kind:<20}"
              f"{groq.accuracy:>7.0%}{deepgram.accuracy:>10.0%}  "
              f"{(groq.heard or '(nothing)')[:34]}")

    print(f"\n{'=' * 96}")
    for provider in ("groq", "deepgram"):
        group = [r for r in results if r.provider == provider]
        accuracy = statistics.mean(r.accuracy for r in group)
        latencies = [r.milliseconds for r in group if r.milliseconds]
        print(f"  {provider:<10}mean accuracy {accuracy:>5.0%}   median latency "
              f"{statistics.median(latencies) if latencies else 0:>6.0f} ms")

    failures = [r for r in results if r.error]
    if failures:
        print(f"\n  {len(failures)} request(s) returned an error:")
        for r in failures[:6]:
            print(f"    {r.provider} / {r.case.kind}: {r.error[:60]}")

    write_report(results)
    print(f"\n  written to results/asr_evaluation.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
