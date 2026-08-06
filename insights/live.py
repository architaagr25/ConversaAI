"""
Running the analysis beside the call rather than inside it.

The rule this whole file exists for: the caller must not wait for it. A nudge
that makes the agent a second slower to answer has cost more than it gave,
because the silence is what the caller notices and the nudge is not for them.

So the work is handed to a background thread the moment a turn completes, and
the reply is already on its way out by then. Results arrive when they arrive
and are pushed to whoever is watching. If the analysis is slower than the next
turn, the next turn happens anyway.

Two consequences worth being explicit about.

Nudges can land one turn late. On a fast exchange the model tier occasionally
does not finish before the caller speaks again. It is delivered against the
turn it came from rather than pretended to be current.

Analysis is dropped rather than queued when it falls behind. A backlog on a
live call is worthless: advice about turn two delivered at turn nine is not
advice. There is one worker, and a turn arriving while it is busy is skipped
and counted.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from insights.nudges import Nudge, NudgeEngine, NudgeSettings
from insights.signals import Signal, TurnInput, extract

log = logging.getLogger(__name__)


@dataclass
class Insight:
    """One turn's worth of analysis, and how long it took."""

    turn: int
    signals: list[Signal] = field(default_factory=list)
    nudges: list[Nudge] = field(default_factory=list)
    milliseconds: float = 0.0
    deliberated: bool = False


class LiveAnalyst:
    """Analyses turns in the background and hands back what it finds."""

    def __init__(self, settings: NudgeSettings | None = None,
                 model=None, allow_model: bool = True) -> None:
        self.engine = NudgeEngine(settings)
        self.model = model
        self.allow_model = allow_model

        self._work: queue.Queue = queue.Queue()
        self._done: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

        self.skipped = 0
        self.insights: list[Insight] = []
        self.busy = threading.Event()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._worker:
            return
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="live-analysis")
        self._worker.start()

    def stop(self, drain_for: float = 2.0) -> None:
        """Finish what is in flight, then stop.

        A short wait rather than none, because the last turn of a call is
        often the interesting one and it is usually still running when the
        caller hangs up.
        """
        deadline = time.monotonic() + drain_for
        while self.busy.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        self._stop.set()
        self._work.put(None)
        if self._worker:
            self._worker.join(timeout=1.0)
            self._worker = None

    # -- submitting ----------------------------------------------------------

    def submit(self, turn: TurnInput, trace: str = "") -> bool:
        """Hand a turn over. Returns False if it was dropped.

        Dropped rather than queued on purpose. Advice about an earlier turn,
        delivered several turns later, is not advice.
        """
        if not self._worker:
            self.start()
        if self.busy.is_set():
            self.skipped += 1
            log.info("live analysis still busy, turn skipped",
                     extra={"turn": turn.turn_number})
            return False
        self._work.put((turn, trace))
        return True

    def collect(self) -> list[Insight]:
        """Everything finished since the last time this was asked."""
        out = []
        while True:
            try:
                out.append(self._done.get_nowait())
            except queue.Empty:
                return out

    # -- the worker ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._work.get()
            if item is None:
                return
            turn, trace = item
            self.busy.set()
            try:
                self._analyse(turn, trace)
            except Exception:
                # A failure here must never reach the call. The caller is
                # mid-conversation and this is a side channel.
                log.exception("live analysis failed")
            finally:
                self.busy.clear()

    def _analyse(self, turn: TurnInput, trace: str) -> None:
        started = time.perf_counter()
        signals = extract(turn, model=self.model, trace=trace,
                          allow_model=self.allow_model)
        elapsed = (time.perf_counter() - started) * 1000

        nudges = self.engine.consider(signals, turn.turn_number,
                                      latency_ms=elapsed)
        insight = Insight(turn=turn.turn_number, signals=signals,
                          nudges=nudges, milliseconds=elapsed,
                          deliberated=any(s.deliberated for s in signals))
        self.insights.append(insight)
        self._done.put(insight)

    # -- reporting -----------------------------------------------------------

    def report(self) -> dict:
        report = self.engine.report()
        report["turns_analysed"] = len(self.insights)
        report["turns_skipped"] = self.skipped
        report["deliberated"] = sum(1 for i in self.insights if i.deliberated)
        latencies = sorted(i.milliseconds for i in self.insights)
        if latencies:
            report["latency_ms"] = {
                "p50": latencies[len(latencies) // 2],
                "p95": latencies[min(len(latencies) - 1,
                                     int(len(latencies) * 0.95))],
                "max": latencies[-1],
            }
        return report
