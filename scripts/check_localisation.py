"""
Runs a localised pack against realistic caller lines and checks the output.

Two things are measured. Whether the politeness register holds inside every
sentence, which is the mistake models make most often and which a native
speaker notices immediately. And whether the reply stays in the language it is
supposed to be in rather than drifting into English.

The replies are printed in full, because a score of "consistent" only means
nothing mechanical is wrong. Whether it sounds like a person is a judgement
somebody has to make by reading it.

    .venv\\Scripts\\python scripts/check_localisation.py --pack life_ph
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voice_agent.agent import Agent  # noqa: E402
from voice_agent.localisation import check_register, taglish_balance  # noqa: E402

PROBES = {
    "life_ph": [
        ("cooperative", "Opo, ako po ang policyholder."),
        ("unaware", "Ha? Wala po akong natanggap na notice."),
        ("hardship", "Medyo tight po ngayon ang budget namin."),
        ("soft refusal", "Titingnan ko po muna, tatawag na lang po ako."),
        ("question", "Magkano po ba ang due ko at kailan po ang deadline?"),
        ("objection", "Ang mahal po kasi, baka i-cancel ko na lang."),
        ("unsupported", "May pension plan po ba kayo para sa mga OFW?"),
        ("escalation", "Pwede po bang makausap ang tao?"),
    ],
    "multifinance_id": [
        ("formal", "Iya betul, saya nasabahnya."),
        ("colloquial", "Iya nih, gimana ya, cicilannya belum kebayar."),
        ("hardship", "Lagi susah pak, belum gajian bulan ini."),
        ("indirect refusal", "Belum sempat pak, nanti aja ya."),
        ("question", "Berapa dendanya kalau telat seminggu?"),
        ("loanwords", "Sisa tenornya berapa dan DP saya kemarin sudah masuk?"),
        ("objection", "Kok mahal banget sih dendanya."),
        ("javanese accent", "Nuwun sewu, kulo dereng saget mbayar cicilan niki."),
        ("javanese follow up", "Nggih, monggo dijelaske malih."),
        ("sundanese accent", "Punten, abdi teh can tiasa mayar ayeuna."),
        ("unsupported", "Bisa nggak saya tukar tambah kendaraannya?"),
        ("escalation", "Bisa bicara sama orangnya langsung?"),
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", default="life_ph")
    args = parser.parse_args()

    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    probes = PROBES.get(args.pack)
    if not probes:
        print(f"no probes defined for {args.pack}")
        return 1

    agent = Agent(args.pack)
    language = agent.pack.language

    print("=" * 92)
    print(f"Localisation check: {args.pack}  ({language})")
    print("=" * 92)
    agent.warmup()

    print(f"\nOpening line")
    print("-" * 92)
    opening = agent.greeting()
    print(f"  {opening}")
    check = check_register(opening, language)
    print(f"  register: {check.explain()}")

    results = []
    for label, said in probes:
        turn = agent.respond(said)
        check = check_register(turn.agent, language)
        results.append((label, check))

        print(f"\n{label}")
        print("-" * 92)
        print(f"  Caller: {said}")
        print(f"  Agent:  {turn.agent}")
        marks = []
        marks.append("register " + ("ok" if not check.mixed else "MIXED"))
        if check.english_drift:
            marks.append(f"english drift {sorted(set(check.english_drift))[:4]}")
        if language in ("fil", "tl"):
            marks.append(f"english share {taglish_balance(turn.agent):.0%}")
        if agent.conversation.region != "standard":
            marks.append(f"region {agent.conversation.region}")
        if turn.escalated_to:
            marks.append(f"escalated {turn.escalated_to}")
        elif turn.grounded:
            marks.append(f"grounded, {len(turn.citations)} sources")
        elif turn.sought_knowledge:
            marks.append("no supporting record, said so")
        print(f"  [{'  '.join(marks)}]")
        if not check.ok:
            print(f"  PROBLEM: {check.explain()}")

    clean = sum(1 for _, c in results if c.ok)
    print(f"\n{'=' * 92}")
    print(f"  {clean} of {len(results)} replies clean on the mechanical checks")
    for label, check in results:
        if not check.ok:
            print(f"    {label}: {check.explain()}")
    print("\n  Mechanical checks only. Whether this sounds like a person is a")
    print("  judgement a native speaker has to make by reading the replies above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
