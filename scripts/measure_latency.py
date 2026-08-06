"""End-to-end latency, measured leg by leg.

A single end-to-end number hides where the time goes, and on this system the
legs are three orders of magnitude apart. Recognition costs hundreds of
milliseconds, signal extraction costs a fraction of one, and the model tier
costs seconds. Averaging those together produces a figure that describes none
of them.

So each leg is timed separately, on the same audio, in the order it actually
happens:

    audio received -> transcription -> signal detection -> nudge -> delivery

The audio is the recorded calls in results/calls/, replayed at real-time speed
in the same 20 ms frames the browser sends. That matters for the first leg:
chunk latency measured by feeding a whole file at once is a measurement of a
disk read, not of a call.

Delivery is measured as what it is here — serialising the nudge and putting it
on the socket. Over a real network there is a wire time this cannot see, so the
number is a floor rather than an estimate, and it is reported that way.

    .venv\\Scripts\\python scripts/measure_latency.py
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import PROJECT_ROOT  # noqa: E402
from insights.nudges import NudgeEngine  # noqa: E402
from insights.signals import (  # noqa: E402
    TurnInput,
    deliberate,
    lexical_signals,
    needs_deliberation,
)
from voice_agent.audio import FRAME_BYTES, Endpointer, resample, to_mono  # noqa: E402
from voice_agent.asr import MarketTranscriber  # noqa: E402

REPORT = PROJECT_ROOT / "results" / "latency_report.md"
CALLS = PROJECT_ROOT / "results" / "calls"

# Which recorded call belongs to which market, so recognition runs with the
# settings that call would really have used.
MARKETS = {
    "cooperative": "health_ph_en",
    "objection": "health_ph_en",
    "conflicting": "health_ph_en",
    "out_of_scope": "health_ph_en",
    "escalation": "health_ph_en",
    "ph_taglish": "life_ph",
    "ph_escalation": "life_ph",
    "id_collections": "multifinance_id",
    "id_javanese": "multifinance_id",
}


@dataclass
class Leg:
    """One component's measurements across the whole run."""

    name: str
    runs_on: str
    samples: list[float] = field(default_factory=list)

    def add(self, ms: float) -> None:
        self.samples.append(ms)

    def percentile(self, p: float) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        # Nearest rank. With samples this few, interpolating between two
        # points invents precision that is not in the data.
        index = min(len(ordered) - 1, int(round(p / 100 * len(ordered))) - 1)
        return ordered[max(0, index)]

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)


def read_call(path: Path) -> bytes:
    """A recorded call as 16 kHz mono samples."""
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
        channels = handle.getnchannels()
    return resample(to_mono(raw, channels), rate)


def measure() -> dict:
    legs = {
        "chunk": Leg("audio chunk handling", "every 20 ms frame"),
        "asr": Leg("transcription", "every utterance"),
        "signals": Leg("signal extraction", "every turn"),
        "model": Leg("model deliberation", "turns the first tier cannot settle"),
        "nudge": Leg("nudge generation", "every turn"),
        "delivery": Leg("delivery to the browser", "every nudge"),
    }
    # Not a component. Kept because transcription time is mostly a function of
    # how long the audio is, and without this the recognition row cannot be
    # read at all.
    utterance_ms = Leg("utterance length", "every utterance")

    transcriber = MarketTranscriber()
    transcriber.warmup()

    end_to_end: list[float] = []
    turns = 0
    nudges = 0
    files = 0

    for path in sorted(CALLS.glob("*.wav")):
        unit = MARKETS.get(path.stem)
        if not unit:
            continue
        files += 1

        audio = read_call(path)
        endpointer = Endpointer()
        engine = NudgeEngine()

        # Frame by frame, the way audio actually arrives. The clock on each
        # frame is the handling cost only — this deliberately does not sleep
        # through the call, because the wall time of a five minute recording is
        # five minutes and none of it is ours.
        for offset in range(0, len(audio) - FRAME_BYTES, FRAME_BYTES):
            frame = audio[offset:offset + FRAME_BYTES]

            started = time.perf_counter()
            utterance = endpointer.feed(frame)
            legs["chunk"].add((time.perf_counter() - started) * 1000)

            if not utterance:
                continue

            # The caller has stopped talking. Everything from here is on their
            # clock until the reply starts.
            turn_started = time.perf_counter()
            turns += 1

            transcript = transcriber.transcribe(
                utterance.wav, business_unit=unit,
                audio_ms=utterance.duration_ms)
            legs["asr"].add(transcript.milliseconds)
            utterance_ms.add(utterance.duration_ms)
            if not transcript:
                continue

            turn = TurnInput(caller=transcript.text, agent="",
                             turn_number=turns, business_unit=unit)

            started = time.perf_counter()
            signals = lexical_signals(turn)
            legs["signals"].add((time.perf_counter() - started) * 1000)

            if needs_deliberation(turn, signals):
                started = time.perf_counter()
                signals += deliberate(turn)
                legs["model"].add((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            produced = engine.consider(signals, turns)
            legs["nudge"].add((time.perf_counter() - started) * 1000)

            for nudge in produced:
                nudges += 1
                # What delivery costs here: turning it into the frame the
                # browser reads. The socket write itself is a memcpy into a
                # send buffer at this size.
                started = time.perf_counter()
                json.dumps({"kind": "nudge", "text": nudge.advice,
                            "detail": {"kind": nudge.kind,
                                       "priority": nudge.priority,
                                       "confidence": round(nudge.confidence, 2),
                                       "evidence": nudge.evidence,
                                       "turn": nudge.turn}})
                legs["delivery"].add((time.perf_counter() - started) * 1000)

            end_to_end.append((time.perf_counter() - turn_started) * 1000)

    return {"legs": legs, "end_to_end": end_to_end, "turns": turns,
            "nudges": nudges, "files": files, "utterance_ms": utterance_ms}


def render(result: dict) -> str:
    legs = result["legs"]
    end = result["end_to_end"]

    def row(leg: Leg) -> str:
        if not leg.samples:
            return (f"| {leg.name} | — | — | {leg.runs_on} | 0 |")
        return (f"| {leg.name} | {leg.p50:.2f} ms | {leg.p95:.2f} ms | "
                f"{leg.runs_on} | {len(leg.samples)} |")

    lines = [
        "# End-to-end latency, per component",
        "",
        f"Measured over {result['files']} recorded calls, replayed frame by "
        f"frame in the same 20 millisecond chunks the browser sends. "
        f"{result['turns']} turns, {result['nudges']} nudges.",
        "",
        "Each leg is timed separately because they are three orders of "
        "magnitude apart, and a combined figure would describe none of them.",
        "",
        "| Component | p50 | p95 | Runs on | Samples |",
        "| --- | --- | --- | --- | --- |",
        row(legs["chunk"]),
        row(legs["asr"]),
        row(legs["signals"]),
        row(legs["model"]),
        row(legs["nudge"]),
        row(legs["delivery"]),
        "",
    ]

    if end:
        ordered = sorted(end)
        p50 = ordered[len(ordered) // 2]
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        lines += [
            "## Audio received to nudge displayed",
            "",
            f"| p50 | p95 | Mean | Turns |",
            "| --- | --- | --- | --- |",
            f"| {p50:.0f} ms | {p95:.0f} ms | "
            f"{statistics.mean(end):.0f} ms | {len(end)} |",
            "",
            "Measured from the frame that ends the caller's turn to the nudge "
            "being ready to send. Recognition dominates it, which is why the "
            "component table matters more than this number.",
            "",
        ]

    lines += [
        "## What these numbers do and do not include",
        "",
        "**Chunk handling** is voice activity detection on one 20 ms frame. It "
        "has to stay far below 20 ms or audio arrives faster than it can be "
        "consumed, and the margin here is the headroom for a busier machine.",
        "",
        "**Transcription** is the hosted call, wall clock, including the "
        "network. It is the largest component by a wide margin and it is the "
        "one this project has least control over.",
        "",
        "It is also higher here than a caller experiences, and the reason is "
        "worth stating rather than leaving for someone to find. These "
        "recordings contain both sides of the call mixed into one track, so "
        "the endpointer cuts them into segments that include the agent "
        f"speaking. Median utterance here is "
        f"{result['utterance_ms'].p50 / 1000:.1f} seconds; a caller answering "
        "a qualification question is a fraction of that. For recognition time "
        "on caller-length audio, `asr_evaluation.md` measures 18 single "
        "utterances and reports a median of 346 ms. Both numbers are real; "
        "they are measuring different inputs.",
        "",
        "**Delivery** is serialising the nudge into the frame the browser "
        "reads. The wire time to a remote browser is not visible from here, so "
        "treat this as a floor rather than an estimate. On a local socket the "
        "write is a copy into a send buffer.",
        "",
        "**None of the last four legs are on the caller's clock.** Analysis is "
        "handed to a background worker the moment a turn completes, while the "
        "reply is already being synthesised. They are reported because the "
        "brief asks for them and because they decide whether a nudge lands "
        "before the call moves on, not because the caller waits for them.",
        "",
        "Reproduce with `.venv\\Scripts\\python scripts/measure_latency.py`.",
    ]
    return "\n".join(lines)


def main() -> None:
    print("=" * 78)
    print("End-to-end latency")
    print("=" * 78)
    print()
    print("Replaying recorded calls frame by frame. This makes a real")
    print("recognition call per turn, so it takes a couple of minutes.")
    print()

    result = measure()
    legs = result["legs"]

    print(f"  {'component':<26} {'p50':>10} {'p95':>10} {'samples':>9}")
    for key in ("chunk", "asr", "signals", "model", "nudge", "delivery"):
        leg = legs[key]
        if not leg.samples:
            print(f"  {leg.name:<26} {'—':>10} {'—':>10} {0:>9}")
            continue
        print(f"  {leg.name:<26} {leg.p50:>9.2f}m {leg.p95:>9.2f}m "
              f"{len(leg.samples):>9}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(render(result), encoding="utf-8")
    print()
    print(f"  {result['turns']} turns, {result['nudges']} nudges")
    print(f"  written to {REPORT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
