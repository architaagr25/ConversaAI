"""
Talk to the agent by typing, without the audio path.

Everything the voice loop does apart from listening and speaking: retrieval,
grounding, slot filling, qualification and escalation. Faster to iterate on
than a microphone, and it makes the grounding visible, since each turn prints
what was retrieved and whether the reply was allowed to use it.

    .venv\\Scripts\\python scripts/try_agent.py
    .venv\\Scripts\\python scripts/try_agent.py --script cooperative
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voice_agent.agent import Agent  # noqa: E402

SCRIPTS = {
    "cooperative": [
        "Yes, now is fine.",
        "I want cover for me and my wife.",
        "I'm thirty five.",
        "Yes, I live in Manila.",
        "No, I'm not in hospital.",
        "About sixty thousand a month.",
        "What does the Plus plan cover?",
    ],
    "objection": [
        "Yes go ahead.",
        "I'm forty two years old.",
        "Yes, Philippines.",
        "No I'm not.",
        "Honestly this sounds more expensive than I expected.",
        "I already have insurance through my employer anyway.",
    ],
    "unsupported": [
        "Yes that's fine.",
        "Do you cover dental treatment?",
        "What is the capital of France?",
        "Can you tell me tomorrow's weather?",
    ],
    "escalation": [
        "Yes okay.",
        "I'm thirty.",
        "Actually, can I speak to a real person please?",
    ],
    "conflicting": [
        "Sure.",
        "I'm twenty eight years old.",
        "Actually sorry, I'm sixty five.",
        "Yes I live here.",
        "Am I still eligible?",
    ],
}


def show(turn, verbose: bool) -> None:
    print(f"\n  Agent: {turn.agent}")
    if verbose:
        marks = turn.timings
        bits = [f"{k} {v:.0f}ms" for k, v in marks.items()]
        detail = f"retrieved {turn.retrieved}"
        detail += ", grounded" if turn.grounded else ", no confident records"
        if turn.escalated_to:
            detail += f", ESCALATED {turn.escalated_to}"
        if turn.slots_filled:
            detail += f", captured {turn.slots_filled}"
        print(f"         [{detail}]")
        if bits:
            print(f"         [{'  '.join(bits)}]")
        for citation in turn.citations[:3]:
            print(f"         cite: {citation}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", choices=sorted(SCRIPTS))
    parser.add_argument("--pack", default="health_shield_en")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    agent = Agent(args.pack)
    print("=" * 84)
    print(f"Conversation: {args.pack}")
    print("=" * 84)
    warm = agent.warmup()
    print(f"  warm-up: " + ", ".join(f"{k} {v:.0f}ms" for k, v in warm.items()))
    print(f"\n  Agent: {agent.greeting()}")

    lines = SCRIPTS.get(args.script or "", [])
    if lines:
        for line in lines:
            print(f"\n  Caller: {line}")
            show(agent.respond(line), not args.quiet)
    else:
        print("\n  (type your replies, or a blank line to finish)")
        while True:
            try:
                said = input("\n  Caller: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not said:
                break
            show(agent.respond(said), not args.quiet)

    print(f"\n  Agent: {agent.closing_line()}")

    conversation = agent.conversation
    print(f"\n{'=' * 84}")
    print("Call summary")
    print("-" * 84)
    print(f"  turns              {len(conversation.turns)}")
    print(f"  collected          {conversation.slots or 'nothing'}")
    if conversation.assessment:
        result = conversation.assessment
        state = ("eligible" if result.eligible else "declined") \
            if result.decided else f"undecided, needs {', '.join(result.missing)}"
        print(f"  eligibility        {state}")
        if result.decline_reason:
            print(f"  reason             {result.decline_reason}")
        if result.requirements:
            print(f"  requirements       {', '.join(result.requirements)}")
    if conversation.corrections:
        print(f"  corrections        {'; '.join(conversation.corrections)}")
    if conversation.escalated_to:
        print(f"  escalated          {conversation.escalated_to}")
    if conversation.unanswered:
        print(f"  could not answer   {len(conversation.unanswered)}")
        for question in conversation.unanswered:
            print(f"    - {question}")
    if conversation.citations:
        print(f"  sources used       {len(conversation.citations)}")
        for citation in conversation.citations[:6]:
            print(f"    - {citation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
