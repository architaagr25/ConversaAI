"""
Latency measurement for the live pipeline.

A span is one timed stage. Spans sharing a trace id belong to the same call,
which is how end-to-end figures get assembled without guessing which
transcription produced which nudge.

    with track("asr", trace="call-17"):
        text = transcribe(audio)
    print(RECORDER.summary())
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(slots=True)
class Span:
    """One timed stage.

    started_at defaults to now, not zero. Zero puts the span at the start of the
    process clock and end-to-end comes out as the whole uptime of the program.
    """

    stage: str
    milliseconds: float
    trace: str = ""
    started_at: float = field(default_factory=time.perf_counter)
    detail: str = ""


@dataclass
class Recorder:
    """Collects spans and reports the distribution of each stage."""

    spans: list[Span] = field(default_factory=list)

    def add(self, span: Span) -> None:
        self.spans.append(span)

    def clear(self) -> None:
        self.spans.clear()

    def by_stage(self) -> dict[str, list[float]]:
        grouped: dict[str, list[float]] = defaultdict(list)
        for span in self.spans:
            grouped[span.stage].append(span.milliseconds)
        return dict(grouped)

    def by_trace(self, trace: str) -> list[Span]:
        return [s for s in self.spans if s.trace == trace]

    def end_to_end(self) -> list[float]:
        """Wall clock elapsed per trace, first span's start to last span's end.

        Summing the stages would overcount anything that ran concurrently.
        """
        grouped: dict[str, list[Span]] = defaultdict(list)
        for span in self.spans:
            if span.trace:
                grouped[span.trace].append(span)

        totals = []
        for spans in grouped.values():
            start = min(s.started_at for s in spans)
            finish = max(s.started_at + s.milliseconds / 1000 for s in spans)
            totals.append((finish - start) * 1000)
        return totals

    @staticmethod
    def percentiles(values: list[float]) -> dict[str, float]:
        if not values:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
        ordered = sorted(values)
        return {
            "count": len(ordered),
            "p50": statistics.median(ordered),
            # Under 20 samples an interpolated p95 invents precision, so report
            # the slowest observation instead.
            "p95": (
                ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]
                if len(ordered) >= 20
                else ordered[-1]
            ),
            "min": ordered[0],
            "max": ordered[-1],
        }

    def stats(self) -> dict[str, dict[str, float]]:
        out = {stage: self.percentiles(v) for stage, v in self.by_stage().items()}
        totals = self.end_to_end()
        if totals:
            out["end to end"] = self.percentiles(totals)
        return out

    def summary(self) -> str:
        """A table that can go straight into a report."""
        rows = self.stats()
        if not rows:
            return "no measurements recorded"

        lines = [
            f"{'stage':<22}{'n':>5}{'p50':>10}{'p95':>10}{'min':>10}{'max':>10}",
            "-" * 67,
        ]
        ordered = sorted(rows.items(), key=lambda kv: kv[0] == "end to end")
        for stage, s in ordered:
            if stage == "end to end":
                lines.append("-" * 67)
            lines.append(
                f"{stage:<22}{s['count']:>5}{s['p50']:>8.0f}ms{s['p95']:>8.0f}ms"
                f"{s['min']:>8.0f}ms{s['max']:>8.0f}ms"
            )
        return "\n".join(lines)


# Shared, since latency is reported for the system not per object.
# Tests and separate runs call clear() between them.
RECORDER = Recorder()


@contextmanager
def track(
    stage: str, trace: str = "", detail: str = "", recorder: Recorder | None = None
) -> Iterator[Span]:
    """Time a block and record it."""
    target = recorder or RECORDER
    started = time.perf_counter()
    span = Span(stage=stage, milliseconds=0.0, trace=trace, started_at=started, detail=detail)
    try:
        yield span
    finally:
        # In finally so a failed stage still reports its cost - a slow failure is
        # exactly what you want to see in the numbers.
        span.milliseconds = (time.perf_counter() - started) * 1000
        target.add(span)


@asynccontextmanager
async def track_async(
    stage: str, trace: str = "", detail: str = "", recorder: Recorder | None = None
):
    """Time an awaited block and record it."""
    target = recorder or RECORDER
    started = time.perf_counter()
    span = Span(stage=stage, milliseconds=0.0, trace=trace, started_at=started, detail=detail)
    try:
        yield span
    finally:
        span.milliseconds = (time.perf_counter() - started) * 1000
        target.add(span)


class Stopwatch:
    """Manual timing for stages that don't fit inside a block.

    Streaming needs this: first token and full reply both matter and they end at
    different moments.
    """

    __slots__ = ("_start", "_marks")

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._marks: dict[str, float] = {}

    def mark(self, name: str) -> float:
        """Record milliseconds since the stopwatch started, and return it."""
        elapsed = (time.perf_counter() - self._start) * 1000
        self._marks[name] = elapsed
        return elapsed

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000

    @property
    def marks(self) -> dict[str, float]:
        return dict(self._marks)

    def commit(self, trace: str = "", recorder: Recorder | None = None) -> None:
        """Push every mark into the recorder as its own span."""
        target = recorder or RECORDER
        previous = 0.0
        for name, at in self._marks.items():
            target.add(
                Span(
                    stage=name,
                    milliseconds=at - previous,
                    trace=trace,
                    started_at=self._start + previous / 1000,
                )
            )
            previous = at
