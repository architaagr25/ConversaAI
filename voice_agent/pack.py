"""
Loads a conversation pack and turns it into a system prompt.

The split matters. The pack describes how the agent behaves: who it is, what
it collects, when it hands over, what it must never do. It does not describe
what the agent knows. Waiting periods, premiums, exclusions and the approved
answers to objections are retrieved per turn from the knowledge base.

Putting them in the prompt instead would look simpler and would mean the agent
still quoting a policy term six months after it changed, with nothing to say
where the figure came from. Everything in the prompt below is behaviour; a test
asserts no policy content leaks into it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import yaml

from core.config import PROJECT_ROOT

log = logging.getLogger(__name__)

PACK_DIR = PROJECT_ROOT / "data" / "agents"


@dataclass
class Slot:
    name: str
    ask: str
    required: bool = False
    type: str = "text"
    validate: str = ""
    why: str = ""
    sensitive: bool = False
    may_refuse: bool = False
    on_invalid: str = ""
    on_refusal: str = ""
    # The rules file sometimes calls a field something else. Declaring the
    # mapping means a mismatch is visible rather than a rule that never fires.
    maps_to: str = ""
    # Words that identify this question in the agent's own wording, since the
    # model rephrases every question it asks.
    expects: list[str] = field(default_factory=list)

    @property
    def fact_name(self) -> str:
        return self.maps_to or self.name


@dataclass
class Pack:
    pack_id: str
    version: str
    business_unit: str
    language: str
    persona: dict
    opening: str
    slots: list[Slot]
    flow: list[dict]
    grounding: dict
    objections: dict
    escalation: dict
    business_action: dict
    closing: dict
    must_never: list[str]

    @property
    def required_slots(self) -> list[Slot]:
        return [s for s in self.slots if s.required]

    def slot(self, name: str) -> Slot | None:
        return next((s for s in self.slots if s.name == name), None)

    def to_facts(self, collected: dict) -> dict:
        """Rename collected answers into the vocabulary the rules use."""
        facts = {}
        for name, value in collected.items():
            slot = self.slot(name)
            facts[slot.fact_name if slot else name] = value
        return facts

    def state(self, name: str) -> dict | None:
        return next((s for s in self.flow if s["state"] == name), None)


class PackError(ValueError):
    """A pack that would produce a broken agent."""


REQUIRED_TOP_LEVEL = [
    "pack_id", "version", "business_unit", "language", "persona", "opening",
    "slots", "flow", "grounding", "objections", "escalation",
    "business_action", "closing", "must_never",
]


def load_pack(pack_id: str) -> Pack:
    """Read a pack and refuse to return a broken one.

    Validated at load rather than discovered mid-call. A missing escalation
    section means a caller asking for a person gets an argument instead.
    """
    path = PACK_DIR / f"{pack_id}.yaml"
    if not path.exists():
        raise PackError(f"no pack named {pack_id!r} in {PACK_DIR}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    missing = [key for key in REQUIRED_TOP_LEVEL if key not in data]
    if missing:
        raise PackError(f"{pack_id} is missing: {', '.join(missing)}")

    if not data["grounding"].get("unsupported"):
        raise PackError(
            f"{pack_id} has no unsupported-question responses. Without them the "
            "agent has no way to say it does not know."
        )

    if not data["escalation"].get("immediate"):
        raise PackError(f"{pack_id} lists no escalation triggers")

    slots = [Slot(**s) for s in data["slots"]]
    known = {s.name for s in slots}
    for state in data["flow"]:
        for name in state.get("collect", []):
            if name not in known:
                raise PackError(
                    f"{pack_id} state {state['state']!r} collects {name!r}, "
                    "which is not a declared slot"
                )

    return Pack(
        pack_id=data["pack_id"],
        version=str(data["version"]),
        business_unit=data["business_unit"],
        language=data["language"],
        persona=data["persona"],
        opening=data["opening"].strip(),
        slots=slots,
        flow=data["flow"],
        grounding=data["grounding"],
        objections=data["objections"],
        escalation=data["escalation"],
        business_action=data["business_action"],
        closing=data["closing"],
        must_never=data["must_never"],
    )


def _bullets(items: list[str], indent: str = "- ") -> str:
    return "\n".join(f"{indent}{item.strip()}" for item in items if item)


def build_system_prompt(pack: Pack) -> str:
    """Assemble the instructions the model runs under for a whole call.

    Behaviour only. No waiting periods, no premiums, no approved objection
    wording. Those arrive with each turn, attached to the records they came
    from, so the agent can be told to use them and nothing else.
    """
    persona = pack.persona
    sections: list[str] = []

    sections.append(
        f"You are {persona['name']}, a {persona['role']} at "
        f"{persona['company']}. You are speaking on a phone call. Everything "
        f"you write will be read aloud."
    )

    sections.append("How you speak:\n" + _bullets(persona.get("style", [])))
    sections.append("Never:\n" + _bullets(persona.get("never", [])))

    sections.append(
        "ANSWERING QUESTIONS. This is the rule that matters most.\n"
        + _bullets(pack.grounding.get("rules", []))
        + "\n- When you have no supporting record, say so in your own words, "
          "along the lines of: "
        + "; ".join(f'"{p.strip()}"' for p in pack.grounding["unsupported"])
        + "\n- Saying you do not know is a correct answer. Guessing is not."
    )

    sections.append(
        "Handling resistance:\n" + _bullets(pack.objections.get("approach", []))
        + "\n" + _bullets(pack.objections.get("never", []))
    )

    sections.append(
        "Handing over to a person:\n"
        + _bullets(pack.escalation.get("manner", []))
        + "\n- Once a handover starts, stop qualifying. Do not ask anything else."
    )

    collected = "\n".join(
        f"- {s.name}: ask it as \"{s.ask.strip()}\""
        + (" (required)" if s.required else "")
        + (" (they may decline, and that is fine)" if s.may_refuse else "")
        for s in pack.slots
    )
    sections.append(
        "What you are trying to find out, in roughly this order. Ask one at a "
        "time and acknowledge each answer before the next:\n" + collected
    )

    flow = "\n".join(
        f"- {s['state']}: {s['goal']}"
        + (f" Then move to {s['next']}." if s.get("next") else "")
        for s in pack.flow
    )
    sections.append("The shape of the call:\n" + flow)

    sections.append(
        "A caller can ask a question at any point, including in the middle of "
        "being asked one. Answer it, then return to where you were with a short "
        "question. Do not restart."
    )

    sections.append("Under no circumstances:\n" + _bullets(pack.must_never))

    sections.append(
        "Keep replies to one or two sentences unless asked to explain "
        "something. Long answers do not work out loud."
    )

    return "\n\n".join(sections)


def build_turn_context(records: list, confident: bool, fallbacks: list[str]) -> str:
    """The records available for this turn, and what may be done with them.

    Rebuilt every turn rather than accumulated, so the agent cannot answer from
    something retrieved three questions ago that is no longer relevant.
    """
    if not confident or not records:
        return (
            "NO SUPPORTING RECORDS were found for this question.\n"
            "You must tell the caller you do not have that information. Do not "
            "answer from memory, from earlier in the call, or from what seems "
            "likely. Suggested wording: "
            + "; ".join(f'"{f.strip()}"' for f in fallbacks)
        )

    lines = ["SUPPORTING RECORDS. Answer only from these.", ""]
    for index, record in enumerate(records, 1):
        marker = ""
        if record.authority == "binding":
            marker = "  [binding: this wins over anything that disagrees]"
        elif record.authority == "promotional":
            marker = "  [marketing copy: do not quote as policy]"
        lines.append(f"[{index}] {record.title}{marker}")
        lines.append(record.content.strip())
        lines.append("")

    lines.append(
        "If none of these answers the question, say you do not have that "
        "information rather than assembling an answer from parts of them."
    )
    return "\n".join(lines)


def available_packs() -> list[str]:
    return sorted(p.stem for p in PACK_DIR.glob("*.yaml"))
