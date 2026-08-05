"""
Scores retrieval against a fixed set of questions.

The set lives in data/eval/retrieval_queries.yaml, with the record that ought
to answer each question written down before the results are seen. Deciding
afterwards which answer looks reasonable is not evaluation.

Three verdicts. Correct means the expected record came back first, which is the
only case where an agent reading the top result alone would answer properly.
Partially correct means it came back within the top few, so an agent reading
all of them would still get there. Incorrect means it did not come back at all.

Out of scope questions invert this: the correct behaviour is to return nothing
and admit to not knowing.

    python -m knowledge_base.evaluate
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yaml

from core.config import PROJECT_ROOT, settings
from knowledge_base.retrieve import get_retriever

log = logging.getLogger(__name__)

QUERY_SET = PROJECT_ROOT / "data" / "eval" / "retrieval_queries.yaml"
REPORT = PROJECT_ROOT / "results" / "retrieval_evaluation.md"


@dataclass
class Scored:
    query_id: str
    category: str
    question: str
    expected: str
    verdict: str
    rank: int | None
    top_title: str
    top_ref: str
    top_authority: str
    similarity: float
    confident: bool
    why: str
    note: str = ""


def matches(result_ref: str, expected: str) -> bool:
    return expected.lower() in result_ref.lower()


def score_one(case: dict, retriever) -> Scored:
    outcome = retriever.search(case["question"], business_unit=case.get("unit"))
    top = outcome.results[0] if outcome.results else None

    base = {
        "query_id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "expected": "declines to answer" if case.get("decline") else case["expect"],
        "top_title": top.title if top else "-",
        "top_ref": top.source_ref if top else "-",
        "top_authority": top.authority if top else "-",
        "similarity": outcome.best_similarity,
        "confident": outcome.confident,
        "why": " ".join(case.get("why", "").split()),
    }

    if case.get("decline"):
        verdict = "correct" if not outcome.confident else "incorrect"
        note = ("declined as it should" if not outcome.confident
                else f"answered from {top.title!r} when it should not have")
        return Scored(**base, verdict=verdict, rank=None, note=note)

    # Where in the returned list the expected record landed, if anywhere.
    position = next((i for i, r in enumerate(outcome.results)
                     if matches(r.source_ref, case["expect"])
                     or matches(r.title, case["expect"])), None)

    if not outcome.confident:
        verdict, note = "incorrect", f"declined, {outcome.reason}"
    elif position == 0:
        verdict, note = "correct", "expected record ranked first"
    elif position is not None:
        verdict = "partially correct"
        note = f"expected record at position {position + 1}, not first"
    else:
        verdict = "incorrect"
        note = f"expected record absent from the top {len(outcome.results)}"

    return Scored(**base, verdict=verdict,
                  rank=position + 1 if position is not None else None, note=note)


def write_report(scores: list[Scored]) -> None:
    counts = {v: sum(1 for s in scores if s.verdict == v)
              for v in ("correct", "partially correct", "incorrect")}
    total = len(scores)

    lines = [
        "# Retrieval evaluation",
        "",
        "Every question below was written down with its expected answer before "
        "the results were seen. The knowledge base holds 102 searchable records "
        "across three markets.",
        "",
        f"**{counts['correct']} correct, {counts['partially correct']} partially "
        f"correct, {counts['incorrect']} incorrect, out of {total}.**",
        "",
        f"Confidence floor: {settings.retrieval_min_score}. Below it the agent "
        "declines rather than answering from the closest record it found.",
        "",
        "| Verdict | Meaning |",
        "| --- | --- |",
        "| correct | The expected record ranked first |",
        "| partially correct | It was in the top five but not first, so an agent "
        "reading all five still answers properly |",
        "| incorrect | It was not returned, or the search declined when it "
        "should have answered |",
        "",
        "---",
        "",
    ]

    for group in ("product", "policy_rule", "qualification", "pricing", "faq",
                  "objection", "process", "partnership_benefits", "escalation",
                  "out_of_scope"):
        in_group = [s for s in scores if s.category == group]
        if not in_group:
            continue

        lines.append(f"## {group.replace('_', ' ').title()}")
        lines.append("")
        for s in in_group:
            mark = {"correct": "PASS", "partially correct": "PARTIAL",
                    "incorrect": "FAIL"}[s.verdict]
            lines += [
                f"### {s.query_id} — {mark}",
                "",
                f"**Question.** {s.question}",
                "",
                f"**Expected.** `{s.expected}`",
                "",
                f"**Returned.** {s.top_title}",
                "",
                f"**Source.** `{s.top_ref}`",
                "",
                f"**Authority.** {s.top_authority} · "
                f"**similarity** {s.similarity:.3f} · "
                f"**answered** {'yes' if s.confident else 'no'}",
                "",
                f"**Why this record.** {s.why}",
                "",
                f"**Verdict.** {s.verdict} — {s.note}",
                "",
            ]
        lines.append("---")
        lines.append("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    cases = yaml.safe_load(QUERY_SET.read_text(encoding="utf-8"))
    retriever = get_retriever()

    print("=" * 100)
    print("Retrieval evaluation")
    print("=" * 100)
    print(f"\n{'id':<9}{'category':<22}{'sim':>7}{'rank':>6}  verdict")
    print("-" * 100)

    scores: list[Scored] = []
    for case in cases:
        scored = score_one(case, retriever)
        scores.append(scored)
        rank = str(scored.rank) if scored.rank else ("-" if scored.confident else "n/a")
        mark = {"correct": "correct", "partially correct": "PARTIAL",
                "incorrect": "INCORRECT"}[scored.verdict]
        print(f"{scored.query_id:<9}{scored.category:<22}"
              f"{scored.similarity:>7.3f}{rank:>6}  {mark}  {scored.note[:44]}")

    counts = {v: sum(1 for s in scores if s.verdict == v)
              for v in ("correct", "partially correct", "incorrect")}
    total = len(scores)

    print(f"\n{'=' * 100}")
    print(f"  correct            {counts['correct']:>3} of {total}")
    print(f"  partially correct  {counts['partially correct']:>3}")
    print(f"  incorrect          {counts['incorrect']:>3}")
    usable = counts["correct"] + counts["partially correct"]
    print(f"\n  answerable by an agent reading the top five: {usable} of {total} "
          f"({usable / total:.0%})")

    in_scope = [s for s in scores if s.category != "out_of_scope"]
    declined = [s for s in scores if s.category == "out_of_scope"]
    print(f"  in scope questions answered: "
          f"{sum(1 for s in in_scope if s.confident)} of {len(in_scope)}")
    print(f"  out of scope questions declined: "
          f"{sum(1 for s in declined if not s.confident)} of {len(declined)}")

    write_report(scores)
    print(f"\n  written to  results/retrieval_evaluation.md")

    return 0 if counts["incorrect"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
