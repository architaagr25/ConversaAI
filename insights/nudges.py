"""
Turning signals into something worth telling somebody mid-call.

A nudge is only useful if it arrives while it can still change the call and if
it is rare enough to be read. Those two pull against each other, and most of
this file is the second one: a detector that fires on every turn trains the
person watching to ignore it, which is worse than having no detector, because
the one time it matters it is ignored too.

Four controls, and each exists because of a way the first version was
unusable.

Cooldown. Hardship fired on four consecutive turns of the Indonesian call,
because the customer kept explaining the same difficulty. The same nudge is
suppressed for a number of turns after it fires.

Budget. A whole call has a cap. Past about six the reader stops reading, and
which six arrive matters more than how many.

Priority. When two fire on one turn, the more serious one is shown. A
compliance breach outranks a sentiment change every time.

Confidence floor. Per nudge, not global, because the cost of a false positive
is not the same for all of them. Telling somebody to slow down when they did
not need to costs nothing. Telling a supervisor the agent made an illegal
promise when it did not costs their trust in the whole panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from insights.signals import Signal


@dataclass(frozen=True)
class NudgeRule:
    kind: str
    # What the person should do, not what the system saw. "Offer restructuring"
    # is actionable; "hardship detected" is a label.
    advice: str
    priority: int
    minimum_confidence: float
    cooldown_turns: int = 3
    # Whether it is for the supervisor watching or usable by the bot itself.
    audience: str = "supervisor"


RULES: dict[str, NudgeRule] = {
    # Compliance first. These are the ones that cost money.
    "agent_guarantee": NudgeRule(
        "agent_guarantee",
        "The agent promised an outcome it cannot promise. Correct it on this "
        "call before the customer relies on it.",
        priority=100, minimum_confidence=0.85, cooldown_turns=0),
    "agent_threat": NudgeRule(
        "agent_threat",
        "Repossession was raised. Stop that line and return to the payment "
        "date.",
        priority=100, minimum_confidence=0.85, cooldown_turns=0),
    "agent_pressure": NudgeRule(
        "agent_pressure",
        "That is pressure selling. Drop the deadline and let them decide.",
        priority=95, minimum_confidence=0.85, cooldown_turns=2),

    "escalation": NudgeRule(
        "escalation",
        "Handing to a person now. Pick up with what has already been "
        "collected rather than starting again.",
        priority=90, minimum_confidence=0.9, cooldown_turns=0),

    "hardship": NudgeRule(
        "hardship",
        "Genuine difficulty, not an objection. Stop asking for payment and "
        "offer the restructuring options.",
        priority=80, minimum_confidence=0.7, cooldown_turns=4),

    "frustration": NudgeRule(
        "frustration",
        "The caller is losing patience. Acknowledge it directly and stop "
        "asking questions for a turn.",
        priority=75, minimum_confidence=0.75, cooldown_turns=3),
    "churn_risk": NudgeRule(
        "churn_risk",
        "This call is about to end badly. Offer a person or a callback now.",
        priority=74, minimum_confidence=0.75, cooldown_turns=4),

    "soft_refusal": NudgeRule(
        "soft_refusal",
        "That was a no, not a maybe. Stop selling and agree a callback.",
        priority=70, minimum_confidence=0.72, cooldown_turns=3,
        audience="agent"),

    "repeated_question": NudgeRule(
        "repeated_question",
        "Asked before and not answered. Say plainly that it cannot be "
        "answered here rather than answering around it again.",
        priority=65, minimum_confidence=0.75, cooldown_turns=2,
        audience="agent"),
    "knowledge_gap": NudgeRule(
        "knowledge_gap",
        "Nothing in the knowledge base covered this. Worth adding.",
        priority=40, minimum_confidence=0.9, cooldown_turns=1),

    "data_conflict": NudgeRule(
        "data_conflict",
        "The caller changed an earlier answer. Confirm which one is right "
        "before it reaches the lead.",
        priority=60, minimum_confidence=0.9, cooldown_turns=0),

    "buying_signal": NudgeRule(
        "buying_signal",
        "They are ready. Move to the next step rather than continuing to "
        "qualify.",
        priority=55, minimum_confidence=0.72, cooldown_turns=3),
    "payment_promise": NudgeRule(
        "payment_promise",
        "A date was given. Confirm it back and record it.",
        priority=50, minimum_confidence=0.72, cooldown_turns=3),

    "hesitation": NudgeRule(
        "hesitation",
        "The caller is undecided rather than refusing. Ask what would help "
        "them decide instead of moving to the next question.",
        priority=52, minimum_confidence=0.72, cooldown_turns=3,
        audience="agent"),

    "confusion": NudgeRule(
        "confusion",
        "The last answer did not land. Say it again in shorter sentences.",
        priority=45, minimum_confidence=0.72, cooldown_turns=2,
        audience="agent"),
    "dead_air": NudgeRule(
        "dead_air",
        "Long silence. Check the caller is still there.",
        priority=35, minimum_confidence=0.9, cooldown_turns=2),
}

# Shown once a call at most, since the second one says nothing the first did
# not. Sentiment and intent are context for the panel rather than nudges, and
# never surface on their own.
CONTEXT_ONLY = {"sentiment", "intent"}


@dataclass
class Nudge:
    kind: str
    advice: str
    priority: int
    confidence: float
    evidence: str
    turn: int
    audience: str = "supervisor"
    latency_ms: float = 0.0


@dataclass
class NudgeSettings:
    """The controls, in one place so they can be changed without code."""

    enabled: bool = True
    # Off by kind, for a team that does not want a category at all.
    muted: set[str] = field(default_factory=set)
    max_per_call: int = 6
    # More than one per turn is unreadable while a call is running.
    max_per_turn: int = 1
    minimum_priority: int = 0
    # Raises every floor at once, for a team that wants fewer and surer.
    confidence_offset: float = 0.0


class NudgeEngine:
    """Decides which signals are worth interrupting somebody for."""

    def __init__(self, settings: NudgeSettings | None = None) -> None:
        self.settings = settings or NudgeSettings()
        self.fired: list[Nudge] = []
        self._last_turn: dict[str, int] = {}
        self.suppressed: list[dict] = []

    def _note_suppression(self, kind: str, why: str, turn: int) -> None:
        # Kept rather than dropped. What was withheld and why is the only way
        # to tune the thresholds afterwards, and the only way to notice a
        # detector that is firing constantly and being swallowed.
        self.suppressed.append({"kind": kind, "reason": why, "turn": turn})

    def consider(self, signals: list[Signal], turn: int,
                 latency_ms: float = 0.0) -> list[Nudge]:
        if not self.settings.enabled:
            return []

        candidates: list[tuple[NudgeRule, Signal]] = []
        for signal in signals:
            if signal.kind in CONTEXT_ONLY:
                continue
            rule = RULES.get(signal.kind)
            if not rule:
                continue
            if signal.kind in self.settings.muted:
                self._note_suppression(signal.kind, "muted", turn)
                continue
            if rule.priority < self.settings.minimum_priority:
                self._note_suppression(signal.kind, "below priority floor", turn)
                continue

            floor = min(1.0, rule.minimum_confidence
                        + self.settings.confidence_offset)
            if signal.confidence < floor:
                self._note_suppression(signal.kind, "below confidence floor", turn)
                continue

            since = turn - self._last_turn.get(signal.kind, -999)
            if since <= rule.cooldown_turns:
                self._note_suppression(signal.kind, "cooldown", turn)
                continue

            candidates.append((rule, signal))

        if not candidates:
            return []

        # Highest priority first, then the surest.
        candidates.sort(key=lambda pair: (-pair[0].priority, -pair[1].confidence))

        out: list[Nudge] = []
        for rule, signal in candidates:
            if len(out) >= self.settings.max_per_turn:
                self._note_suppression(rule.kind, "another nudge this turn", turn)
                continue
            if len(self.fired) >= self.settings.max_per_call:
                self._note_suppression(rule.kind, "call budget spent", turn)
                continue

            nudge = Nudge(kind=rule.kind, advice=rule.advice,
                          priority=rule.priority, confidence=signal.confidence,
                          evidence=signal.evidence, turn=turn,
                          audience=rule.audience, latency_ms=latency_ms)
            out.append(nudge)
            self.fired.append(nudge)
            self._last_turn[rule.kind] = turn

        return out

    def report(self) -> dict:
        return {
            "fired": [n.__dict__ for n in self.fired],
            "suppressed": self.suppressed,
            "counts": {"fired": len(self.fired),
                       "suppressed": len(self.suppressed)},
        }
