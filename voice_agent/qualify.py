"""
Runs the qualification rules against what a caller has told us.

The rules live in data/raw/rules/qualification_rules.yaml, the same file the
knowledge base indexes, so the version a compliance reviewer reads is the
version that decides outcomes. Nothing here restates a threshold.

Decisions are made in code rather than by the model. An eligibility outcome has
to be reproducible and explainable: the same answers must always produce the
same result, and the reason has to name the rule that produced it. A model
asked to apply the rules would usually be right, and "usually" is the wrong
standard for telling somebody they cannot buy insurance.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from core.config import PROJECT_ROOT

log = logging.getLogger(__name__)

RULES_PATH = PROJECT_ROOT / "data" / "raw" / "rules" / "qualification_rules.yaml"

# Only these appear in rule expressions. Evaluating them by hand rather than
# with eval keeps a data file from becoming a way to run code.
COMPARISONS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
}

# 18 <= age <= 60
RANGE = re.compile(r"^\s*(-?[\d.]+)\s*(<=|<)\s*(\w+)\s*(<=|<)\s*(-?[\d.]+)\s*$")
# age >= 51, residency == 'PH', has_valid_ktp == true
SIMPLE = re.compile(r"^\s*(\w+)\s*(<=|>=|==|!=|<|>)\s*(.+?)\s*$")


@dataclass
class RuleOutcome:
    rule_id: str
    passed: bool
    reason: str
    action: str
    skipped: bool = False


@dataclass
class Assessment:
    unit: str
    eligible: bool
    outcomes: list[RuleOutcome] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[RuleOutcome]:
        return [o for o in self.outcomes
                if not o.passed and not o.skipped and o.action == "decline"]

    @property
    def decline_reason(self) -> str:
        return self.blocking[0].reason if self.blocking else ""

    @property
    def decided(self) -> bool:
        """Whether there is enough information to say anything at all.

        A failed hard rule settles it on its own. Nothing a sixty-five year old
        says next makes them eligible, and reporting the outcome as undecided
        while also naming the rule that declined them is a contradiction the
        agent would end up speaking aloud.
        """
        return not self.missing or not self.eligible


@lru_cache(maxsize=1)
def load_rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


def _coerce(token: str):
    token = token.strip().strip("'\"")
    lowered = token.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return float(token) if "." in token else int(token)
    except ValueError:
        return token


def _comparable(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool)
    return isinstance(left, (int, float)) and isinstance(right, (int, float))


def evaluate(expression: str, facts: dict) -> bool | None:
    """Test one rule expression. None means the answer is not known yet.

    None is deliberately distinct from False. A caller who has not said their
    age has not failed the age check, and treating unknown as failure would
    decline people for not having answered yet.
    """
    match = RANGE.match(expression)
    if match:
        low, low_op, name, high_op, high = match.groups()
        value = facts.get(name)
        if value is None:
            return None
        if not _comparable(value, _coerce(low)):
            return None
        return (COMPARISONS[low_op](_coerce(low), value)
                and COMPARISONS[high_op](value, _coerce(high)))

    match = SIMPLE.match(expression)
    if match:
        name, operator, raw = match.groups()
        value = facts.get(name)
        if value is None:
            return None
        expected = _coerce(raw)
        if operator in ("==", "!=") or _comparable(value, expected):
            return COMPARISONS[operator](value, expected)
        return None

    # Compound expressions exist in the pack but are not supported here. Better
    # to skip one loudly than to guess at it silently.
    log.warning("rule expression not understood, skipping",
                extra={"expression": expression})
    return None


def _applies(rule: dict, facts: dict) -> bool:
    products = rule.get("applies_to")
    if not products:
        return True
    chosen = facts.get("product_interest") or facts.get("plan_interest")
    return chosen in products if chosen else False


def assess(unit: str, facts: dict) -> Assessment:
    """Apply a unit's rules to what is known, and say what is still needed."""
    rules = load_rules()
    config = (rules.get("units") or {}).get(unit)
    if config is None:
        raise ValueError(f"no qualification rules for unit {unit!r}")

    assessment = Assessment(unit=unit, eligible=True)

    for kind in ("hard_rules", "soft_rules"):
        for rule in config.get(kind) or []:
            if not _applies(rule, facts):
                continue

            result = evaluate(rule.get("test", ""), facts)
            action = rule.get("on_fail") or rule.get("on_pass") or ""
            reason = rule.get("reason", "")

            if result is None:
                assessment.outcomes.append(
                    RuleOutcome(rule["id"], False, reason, action, skipped=True))
                field_name = rule.get("field")
                if kind == "hard_rules" and field_name:
                    assessment.missing.append(field_name)
                continue

            # A soft rule with on_pass fires when its condition is met; a rule
            # with on_fail fires when it is not.
            fired = result if rule.get("on_pass") else not result
            assessment.outcomes.append(
                RuleOutcome(rule["id"], not fired if rule.get("on_fail") else result,
                            reason, action))

            if not fired:
                continue
            if action == "decline":
                assessment.eligible = False
            elif action in ("require_medical_questionnaire", "require_medical_exam",
                            "simplified_underwriting"):
                assessment.requirements.append(action)
            elif action:
                assessment.notes.append(f"{rule['id']}: {reason}")

    assessment.missing = sorted(set(assessment.missing))
    assessment.requirements = sorted(set(assessment.requirements))
    return assessment


def outcome_wording(assessment: Assessment, rules: dict | None = None) -> str:
    """What the agent should say, taken from the rules file rather than invented."""
    rules = rules or load_rules()
    outcomes = rules.get("outcomes") or {}

    if not assessment.decided:
        return f"Still need: {', '.join(assessment.missing)}."

    if not assessment.eligible:
        action = outcomes.get("decline", {}).get("action", "")
        return f"{assessment.decline_reason} {action}".strip()

    parts = []
    for requirement in assessment.requirements:
        wording = outcomes.get(requirement, {}).get("action")
        if wording:
            parts.append(wording)
    return " ".join(parts) if parts else "Eligible on the information given."
