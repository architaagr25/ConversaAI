"""
Live analysis: what it detects, what it refuses to detect, and staying off
the caller's critical path.

The tests that matter most here are the negative ones. A detector that fires
on everything scores perfectly against a set of turns that should all fire,
and is useless on a real call.
"""

from __future__ import annotations

import time

import pytest

from insights.live import LiveAnalyst
from insights.nudges import RULES, NudgeEngine, NudgeSettings
from insights.signals import (
    Signal,
    TurnInput,
    lexical_signals,
    needs_deliberation,
)


def kinds(caller: str, **kw) -> set[str]:
    return {s.kind for s in lexical_signals(TurnInput(caller=caller, **kw))}


class TestLexicalSignals:
    @pytest.mark.parametrize("text,expected", [
        ("I lost my job and cannot afford it", "hardship"),
        ("Lagi susah bulan ini, belum gajian", "hardship"),
        ("Medyo mahal po yata para sa akin ngayon", "hardship"),
        ("Nanti aja ya, pikir-pikir dulu", "soft_refusal"),
        ("Titingnan ko po, sa susunod na lang po", "soft_refusal"),
        ("I already told you that, you are not listening", "frustration"),
        ("I will pay after payday", "payment_promise"),
        ("Setelah gajian saya bayar", "payment_promise"),
        ("How do i sign up", "buying_signal"),
        ("Sorry what, i don't understand", "confusion"),
    ])
    def test_it_finds_what_is_there(self, text, expected):
        assert expected in kinds(text)

    @pytest.mark.parametrize("text", [
        "Yes, now is a good time",
        "I am thirty five years old",
        "Sa Maynila po ako nakatira",
        "Berapa sisa tenor saya?",
        "Thank you, that answers it",
    ])
    def test_an_ordinary_turn_produces_nothing(self, text):
        assert kinds(text) == set()

    @pytest.mark.parametrize("text,must_not", [
        # Shares vocabulary with a detector and means the opposite. This is
        # where a phrase list goes wrong, and where it has to be checked.
        ("I can afford it, that is not the problem", "hardship"),
        ("No, money is not an issue for me", "hardship"),
        ("I understand, that is clear enough", "confusion"),
        ("I will think about the colour of the car", "soft_refusal"),
    ])
    def test_it_does_not_fire_on_a_near_miss(self, text, must_not):
        assert must_not not in kinds(text)

    def test_it_watches_the_agent_as_well_as_the_caller(self):
        found = lexical_signals(TurnInput(
            caller="What is the limit?",
            agent="You are definitely approved, that is guaranteed."))
        agent_signals = [s for s in found if s.speaker == "agent"]
        assert any(s.kind == "agent_guarantee" for s in agent_signals)

    def test_the_customer_raising_repossession_is_not_the_agent_doing_it(self):
        # The words are in the caller's line, not the agent's. Scanning the
        # whole turn as one string would blame the agent for the customer's
        # question.
        found = kinds("Will you take the vehicle if I miss one?",
                      agent="I cannot discuss that on this call.")
        assert "agent_threat" not in found

    def test_certainty_from_the_call_loop_carries_full_confidence(self):
        found = lexical_signals(TurnInput(caller="Does it cover dental?",
                                          agent_refused=True))
        gap = next(s for s in found if s.kind == "knowledge_gap")
        assert gap.confidence == 1.0

    def test_a_repeated_question_is_matched_on_meaning_not_spelling(self):
        found = kinds("Magkano po ba ang premium ko every month?",
                      asked_before=["Magkano po ang premium ko kada buwan?"])
        assert "repeated_question" in found

    def test_two_different_questions_are_not_a_repeat(self):
        found = kinds("Sino po ang beneficiary?",
                      asked_before=["Magkano po ang premium ko kada buwan?"])
        assert "repeated_question" not in found

    def test_a_very_short_turn_cannot_be_a_repeat(self):
        # "Opo" against anything would otherwise overlap on nothing and
        # divide by a tiny number.
        assert "repeated_question" not in kinds(
            "Opo", asked_before=["Magkano po ang premium ko kada buwan?"])


class TestWhenTheModelIsAsked:
    def test_an_ordinary_short_turn_does_not_reach_it(self):
        turn = TurnInput(caller="I am thirty five years old.")
        assert not needs_deliberation(turn, lexical_signals(turn))

    def test_an_unsettled_signal_reaches_it(self):
        turn = TurnInput(caller="Nanti aja ya, pikir-pikir dulu.")
        assert needs_deliberation(turn, lexical_signals(turn))

    def test_a_long_turn_matching_nothing_reaches_it(self):
        # The case the phrase lists are worst at: an objection in words
        # nobody wrote down.
        turn = TurnInput(caller="The thing is my sister had a policy with "
                                "another company and it went badly for her")
        assert needs_deliberation(turn, lexical_signals(turn))


class TestControls:
    def _signal(self, kind="hardship", confidence=0.9):
        return [Signal(kind=kind, confidence=confidence, evidence="x")]

    def test_the_same_nudge_does_not_repeat_every_turn(self):
        # Hardship fired on four consecutive turns of a real call, because the
        # customer kept explaining the same difficulty.
        engine = NudgeEngine()
        assert engine.consider(self._signal(), turn=1)
        assert not engine.consider(self._signal(), turn=2)
        assert not engine.consider(self._signal(), turn=3)

    def test_the_cooldown_expires(self):
        engine = NudgeEngine()
        engine.consider(self._signal(), turn=1)
        cooldown = RULES["hardship"].cooldown_turns
        assert engine.consider(self._signal(), turn=1 + cooldown + 1)

    def test_a_call_has_a_budget(self):
        engine = NudgeEngine(NudgeSettings(max_per_call=2))
        for turn in range(1, 40, 5):
            engine.consider(self._signal(), turn=turn)
        assert len(engine.fired) == 2

    def test_only_one_arrives_per_turn_and_it_is_the_serious_one(self):
        engine = NudgeEngine()
        out = engine.consider(
            [Signal("payment_promise", 0.9, "x"),
             Signal("agent_guarantee", 0.95, "guaranteed", speaker="agent")],
            turn=1)
        assert [n.kind for n in out] == ["agent_guarantee"]

    def test_a_muted_kind_never_arrives(self):
        engine = NudgeEngine(NudgeSettings(muted={"hardship"}))
        assert not engine.consider(self._signal(), turn=1)

    def test_a_signal_below_its_floor_is_withheld(self):
        engine = NudgeEngine()
        assert not engine.consider(self._signal(confidence=0.4), turn=1)

    def test_compliance_is_held_to_a_higher_bar_than_advice(self):
        # A false compliance warning costs the reader's trust in the panel.
        # A false "slow down" costs nothing.
        assert (RULES["agent_guarantee"].minimum_confidence
                > RULES["hardship"].minimum_confidence)

    def test_raising_the_floor_withholds_more(self):
        strict = NudgeEngine(NudgeSettings(confidence_offset=0.3))
        assert not strict.consider(self._signal(confidence=0.75), turn=1)

    def test_turning_it_off_produces_nothing(self):
        engine = NudgeEngine(NudgeSettings(enabled=False))
        assert not engine.consider(self._signal(), turn=1)

    def test_what_was_withheld_is_kept(self):
        # Without this there is no way to tune a threshold afterwards, and no
        # way to notice a detector firing constantly and being swallowed.
        engine = NudgeEngine(NudgeSettings(muted={"hardship"}))
        engine.consider(self._signal(), turn=1)
        assert engine.report()["suppressed"][0]["reason"] == "muted"

    def test_context_signals_never_become_nudges(self):
        engine = NudgeEngine()
        assert not engine.consider(
            [Signal("sentiment", 0.9, "negative"),
             Signal("intent", 0.9, "pricing")], turn=1)


class TestItStaysOffTheCriticalPath:
    def test_submitting_returns_immediately(self):
        analyst = LiveAnalyst(allow_model=False)
        started = time.perf_counter()
        analyst.submit(TurnInput(caller="I cannot afford it", turn_number=1))
        elapsed = (time.perf_counter() - started) * 1000
        analyst.stop()
        # The whole point. Anything approaching a caller-noticeable delay here
        # means the analysis has ended up inside the call.
        assert elapsed < 50

    def test_the_work_still_happens(self):
        analyst = LiveAnalyst(allow_model=False)
        analyst.submit(TurnInput(caller="I lost my job and cannot afford it",
                                 turn_number=1))
        analyst.stop()
        found = analyst.collect()
        assert found and any(n.kind == "hardship" for n in found[0].nudges)

    def test_a_turn_arriving_while_busy_is_dropped_not_queued(self):
        # Advice about turn two, delivered at turn nine, is not advice.
        analyst = LiveAnalyst(allow_model=False)
        analyst.start()
        analyst.busy.set()
        assert analyst.submit(TurnInput(caller="hello", turn_number=2)) is False
        assert analyst.skipped == 1
        analyst.busy.clear()
        analyst.stop()

    def test_a_failure_in_analysis_never_reaches_the_call(self):
        class Exploding:
            def generate(self, *a, **kw):
                raise RuntimeError("gone")

        analyst = LiveAnalyst(model=Exploding())
        analyst.submit(TurnInput(caller="Nanti aja ya, pikir-pikir dulu",
                                 turn_number=1))
        analyst.stop()
        # The lexical tier still produced its result despite the model failing.
        assert analyst.collect()

    def test_the_report_separates_the_tiers(self):
        analyst = LiveAnalyst(allow_model=False)
        analyst.submit(TurnInput(caller="I cannot afford it", turn_number=1))
        analyst.stop()
        analyst.collect()
        report = analyst.report()
        assert report["turns_analysed"] == 1
        assert report["deliberated"] == 0
        assert "latency_ms" in report
