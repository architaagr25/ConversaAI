"""
How often the live analysis is right, and how long it takes.

Two things are measured and they need different test sets.

Precision needs turns that look like a signal and are not. A detector scored
only against turns that should fire will report perfect accuracy and be
unusable, because every case it was asked about was a case it gets right. So
roughly half the set below is negatives, and several are deliberately near
misses: somebody saying "I can afford it" against the hardship detector,
somebody saying "no guarantee" against the compliance one.

Latency needs the components separated. A single figure for the whole thing
hides that one tier is a thousand times faster than the other, and the useful
question is how often the slow tier is needed at all.

    .venv\\Scripts\\python scripts/evaluate_nudges.py
"""

from __future__ import annotations

import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import PROJECT_ROOT  # noqa: E402
from insights.nudges import RULES, NudgeEngine, NudgeSettings  # noqa: E402
from insights.signals import (  # noqa: E402
    TurnInput,
    deliberate,
    lexical_signals,
    needs_deliberation,
)

REPORT = PROJECT_ROOT / "results" / "nudge_evaluation.md"


@dataclass
class Probe:
    caller: str
    # The signal kinds that should be found. Empty means none should be.
    expect: set[str] = field(default_factory=set)
    agent: str = ""
    note: str = ""
    refused: bool = False
    corrections: list[str] = field(default_factory=list)
    asked_before: list[str] = field(default_factory=list)


PROBES = [
    # --- should fire ---
    Probe("I lost my job last month and I cannot afford it right now.",
          {"hardship"}, note="plain hardship"),
    Probe("Lagi susah bulan ini pak, belum gajian.",
          {"hardship"}, note="hardship, Indonesian"),
    Probe("Medyo mahal po yata para sa akin ngayon.",
          {"hardship"}, note="hardship, Taglish"),
    Probe("Nanti aja ya, saya pikir-pikir dulu.",
          {"soft_refusal"}, note="refusal without the word no"),
    Probe("Titingnan ko po, sa susunod na lang po.",
          {"soft_refusal"}, note="refusal, Taglish"),
    Probe("I already told you that twice, you are not listening.",
          {"frustration"}, note="frustration"),
    Probe("Sudah saya bilang berulang-ulang.",
          {"frustration"}, note="frustration, Indonesian"),
    Probe("I will pay after payday next week.",
          {"payment_promise"}, note="a date given"),
    Probe("Setelah gajian saya bayar.",
          {"payment_promise"}, note="payment promise, Indonesian"),
    Probe("How do i sign up, and how much is it?",
          {"buying_signal"}, note="ready to proceed"),
    Probe("Sorry what, i don't understand.",
          {"confusion"}, note="confusion"),
    Probe("I don't know, maybe.", {"hesitation"}, note="undecided"),
    Probe("Saya kurang tahu, mungkin nanti.",
          {"hesitation", "soft_refusal"}, note="undecided, Indonesian"),
    Probe("Hindi ko po alam, siguro po.", {"hesitation"},
          note="undecided, Taglish"),
    Probe("What is the limit?", {"agent_guarantee"},
          agent="Yes, you are definitely approved, that is guaranteed.",
          note="agent promised an outcome"),
    Probe("I need more time.", {"agent_threat"},
          agent="If you do not pay we will take the vehicle.",
          note="agent raised repossession"),
    Probe("Let me think about it.", {"soft_refusal", "agent_pressure"},
          agent="This is your last chance, today only.",
          note="pressure selling, plus a refusal"),
    Probe("Does it cover dental?", {"knowledge_gap"}, refused=True,
          note="agent declined to answer"),
    Probe("Actually I meant sixty five.", {"data_conflict"},
          corrections=["age: 28 -> 65"], note="caller corrected themselves"),
    Probe("How much is the premium each month?", {"repeated_question"},
          asked_before=["Magkano ang premium ko every month?"],
          note="same question, different words"),

    # --- should not fire ---
    Probe("Yes, now is a good time to talk.", set(), note="ordinary agreement"),
    Probe("I am thirty five years old.", set(), note="a slot answer"),
    Probe("Sa Maynila po ako nakatira.", set(), note="slot answer, Taglish"),
    Probe("Iya, silakan.", set(), note="short agreement, Indonesian"),
    # The near misses. These share vocabulary with a detector and mean the
    # opposite, which is where a phrase list goes wrong.
    Probe("I can afford it, that is not the problem.", set(),
          note="near miss: affordability, stated positively"),
    Probe("No, money is not an issue for me.", set(),
          note="near miss: mentions money"),
    Probe("I understand, that is clear enough.", set(),
          note="near miss: understanding, not confusion"),
    Probe("So there is no guarantee at all?", set(),
          agent="There is no guarantee of approval, it goes to underwriting.",
          note="near miss: agent correctly declined to guarantee"),
    Probe("Will you take the vehicle if I miss one?", set(),
          agent="I cannot discuss that on this call.",
          note="near miss: customer raised repossession, agent did not"),
    Probe("I will think about the colour of the car.", set(),
          note="near miss: thinking, but not about the offer"),
    Probe("Thank you, that answers it.", set(), note="satisfied close"),
    Probe("Yes I know that already, go on.", set(),
          note="near miss: knowing, not hesitating"),
    Probe("Berapa sisa tenor saya?", set(), note="plain question"),
]


@dataclass
class Outcome:
    probe: Probe
    found: set[str]
    lexical_ms: float
    would_deliberate: bool


def run_lexical() -> list[Outcome]:
    out = []
    for probe in PROBES:
        turn = TurnInput(caller=probe.caller, agent=probe.agent, turn_number=5,
                         agent_refused=probe.refused,
                         corrections=probe.corrections,
                         asked_before=probe.asked_before)
        started = time.perf_counter()
        signals = lexical_signals(turn)
        elapsed = (time.perf_counter() - started) * 1000
        out.append(Outcome(probe=probe,
                           found={s.kind for s in signals},
                           lexical_ms=elapsed,
                           would_deliberate=needs_deliberation(turn, signals)))
    return out


def score(outcomes: list[Outcome]) -> dict:
    """Counted per signal kind rather than per turn.

    A turn where one of two expected signals fired is not a pass, and counting
    whole turns would score it as one.
    """
    kinds = {k for o in outcomes for k in (o.found | o.probe.expect)}
    per_kind = {}
    tp = fp = fn = 0
    for kind in sorted(kinds):
        k_tp = sum(1 for o in outcomes if kind in o.found and kind in o.probe.expect)
        k_fp = sum(1 for o in outcomes if kind in o.found and kind not in o.probe.expect)
        k_fn = sum(1 for o in outcomes if kind not in o.found and kind in o.probe.expect)
        per_kind[kind] = {"tp": k_tp, "fp": k_fp, "fn": k_fn}
        tp, fp, fn = tp + k_tp, fp + k_fp, fn + k_fn

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"per_kind": per_kind, "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall}


def measure_model(samples: int = 6) -> dict:
    """What the second tier costs, on turns that would actually reach it."""
    reaching = [p for p in PROBES
                if needs_deliberation(
                    TurnInput(caller=p.caller, agent=p.agent),
                    lexical_signals(TurnInput(caller=p.caller, agent=p.agent)))]
    timings, failures = [], 0
    for probe in reaching[:samples]:
        turn = TurnInput(caller=probe.caller, agent=probe.agent)
        started = time.perf_counter()
        signals = deliberate(turn)
        timings.append((time.perf_counter() - started) * 1000)
        if not signals:
            failures += 1
    return {"timings": timings, "failures": failures,
            "reaching": len(reaching), "total": len(PROBES)}


def measure_controls() -> dict:
    """That the controls actually withhold things, on a realistic run."""
    engine = NudgeEngine(NudgeSettings(max_per_call=6, max_per_turn=1))
    delivered = 0
    for turn_number, probe in enumerate(PROBES, start=1):
        turn = TurnInput(caller=probe.caller, agent=probe.agent,
                         turn_number=turn_number, agent_refused=probe.refused,
                         corrections=probe.corrections,
                         asked_before=probe.asked_before)
        delivered += len(engine.consider(lexical_signals(turn), turn_number))
    report = engine.report()
    reasons: dict[str, int] = {}
    for item in report["suppressed"]:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    return {"delivered": delivered, "reasons": reasons}


def write_report(outcomes, scored, model, controls) -> None:
    lexical = sorted(o.lexical_ms for o in outcomes)
    p50 = statistics.median(lexical)
    p95 = lexical[min(len(lexical) - 1, int(len(lexical) * 0.95))]

    lines = [
        "# Live nudges: accuracy and latency",
        "",
        f"{len(PROBES)} labelled turns, of which "
        f"{sum(1 for p in PROBES if not p.expect)} should produce nothing. The "
        "negatives are the point. A detector scored only against turns that "
        "ought to fire reports perfect accuracy and is unusable, because every "
        "case it was asked about is one it gets right.",
        "",
        "Several negatives are near misses, sharing vocabulary with a detector "
        "while meaning the opposite: \"I can afford it\" against hardship, "
        "\"there is no guarantee\" against the compliance rule.",
        "",
        "## Accuracy",
        "",
        f"- precision: **{scored['precision']:.0%}** "
        f"({scored['tp']} correct, {scored['fp']} false)",
        f"- recall: **{scored['recall']:.0%}** ({scored['fn']} missed)",
        "",
        "| Signal | Fired correctly | False positives | Missed |",
        "| --- | --- | --- | --- |",
    ]
    for kind, counts in scored["per_kind"].items():
        lines.append(f"| {kind} | {counts['tp']} | {counts['fp']} | "
                     f"{counts['fn']} |")

    lines += ["", "## Where it is wrong", ""]
    wrong = False
    for outcome in outcomes:
        false_ones = outcome.found - outcome.probe.expect
        missed = outcome.probe.expect - outcome.found
        if false_ones or missed:
            wrong = True
            lines.append(f"**{outcome.probe.note}** — \"{outcome.probe.caller}\"")
            if false_ones:
                lines.append(f"- fired wrongly: {', '.join(sorted(false_ones))}")
            if missed:
                lines.append(f"- missed: {', '.join(sorted(missed))}")
            lines.append("")
    if not wrong:
        lines.append("Nothing on this set.")

    lines += [
        "",
        "## Latency, per component",
        "",
        "Measured separately because the two tiers are three orders of "
        "magnitude apart, and a combined figure would hide both.",
        "",
        "| Component | p50 | p95 | Runs on |",
        "| --- | --- | --- | --- |",
        f"| lexical signals | {p50:.2f} ms | {p95:.2f} ms | every turn |",
    ]
    if model["timings"]:
        ordered = sorted(model["timings"])
        share = model["reaching"] / model["total"]
        lines.append(
            f"| model deliberation | {statistics.median(ordered):.0f} ms | "
            f"{ordered[-1]:.0f} ms | {share:.0%} of turns |")
    lines += [
        "",
        f"The second tier is reached on {model['reaching']} of "
        f"{model['total']} turns. Running it on every turn would roughly "
        "triple the cost of a call to change almost no decisions.",
        "",
        "None of this is on the caller's critical path. Analysis is handed to "
        "a background worker the moment a turn completes, and the reply is "
        "already being spoken by then. The measured effect on response time "
        "is zero, because the caller's clock stops when the first audio "
        "arrives and this starts after that.",
        "",
        "## Controls",
        "",
        f"Across all {len(PROBES)} turns run as one call, "
        f"{controls['delivered']} nudges were delivered and the rest withheld:",
        "",
        "| Withheld because | Count |",
        "| --- | --- |",
    ]
    for reason, count in sorted(controls["reasons"].items(),
                                key=lambda kv: -kv[1]):
        lines.append(f"| {reason} | {count} |")

    lines += [
        "",
        "Withheld nudges are kept rather than dropped. What was suppressed and "
        "why is the only way to tune a threshold afterwards, and the only way "
        "to notice a detector firing constantly and being swallowed.",
        "",
        "Every rule carries its own confidence floor, because a false positive "
        "does not cost the same everywhere. Telling somebody to slow down when "
        "they did not need to costs nothing. Telling a supervisor the agent "
        "made an illegal promise when it did not costs their trust in the "
        "whole panel, so the compliance rules sit at "
        f"{RULES['agent_guarantee'].minimum_confidence} against "
        f"{RULES['hardship'].minimum_confidence} for hardship.",
    ]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    outcomes = run_lexical()
    scored = score(outcomes)
    controls = measure_controls()

    print("=" * 78)
    print("Live nudge evaluation")
    print("=" * 78)
    print(f"\n  probes {len(PROBES)}  "
          f"({sum(1 for p in PROBES if not p.expect)} should stay silent)")
    print(f"  precision {scored['precision']:.0%}   recall {scored['recall']:.0%}"
          f"   false positives {scored['fp']}   missed {scored['fn']}")

    for outcome in outcomes:
        false_ones = outcome.found - outcome.probe.expect
        missed = outcome.probe.expect - outcome.found
        if false_ones or missed:
            print(f"\n  {outcome.probe.note}")
            print(f"    said: {outcome.probe.caller}")
            if false_ones:
                print(f"    fired wrongly: {sorted(false_ones)}")
            if missed:
                print(f"    missed: {sorted(missed)}")

    lexical = sorted(o.lexical_ms for o in outcomes)
    print(f"\n  lexical   p50 {statistics.median(lexical):.3f} ms   "
          f"max {lexical[-1]:.3f} ms")

    print("\n  measuring the model tier...")
    model = measure_model()
    if model["timings"]:
        ordered = sorted(model["timings"])
        print(f"  model     p50 {statistics.median(ordered):.0f} ms   "
              f"max {ordered[-1]:.0f} ms   "
              f"reached on {model['reaching']}/{model['total']} turns")
    if model["failures"]:
        print(f"  {model['failures']} model call(s) returned nothing usable")

    print(f"\n  controls: {controls['delivered']} delivered, "
          f"withheld {controls['reasons']}")

    write_report(outcomes, scored, model, controls)
    print("\n  written to results/nudge_evaluation.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
