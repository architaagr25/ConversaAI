"""
Agent brain tests.

Everything here runs without a model or a network. What is being checked is
the machinery around the model: what it is told, what it is allowed to see,
and the decisions taken before it is asked anything.
"""

from __future__ import annotations

import pytest

from voice_agent.agent import (
    Agent,
    Conversation,
    Turn,
    needs_knowledge,
    read_age,
    read_money,
    read_yes_no,
)


class TestReadingAge:
    @pytest.mark.parametrize("said,expected", [
        ("I'm 35", 35),
        ("I am 42 years old", 42),
        ("35 years old", 35),
        ("my age is 28", 28),
        ("I'm thirty five", 35),
        ("I am sixty five", 65),
        ("forty", 40),
        ("62", 62),
    ])
    def test_ages_people_actually_say(self, said, expected):
        assert read_age(said) == expected

    @pytest.mark.parametrize("said", [
        "yes that is fine",
        "I have two children",
        "around sixty thousand a month",
    ])
    def test_things_that_are_not_ages(self, said):
        age = read_age(said)
        assert age is None or age > 17

    def test_an_implausible_age_is_rejected(self):
        assert read_age("I am 250") is None


class TestReadingMoney:
    @pytest.mark.parametrize("said,expected", [
        ("about 60000 a month", 60000),
        ("60,000 monthly", 60000),
        ("60k", 60000),
        ("about sixty thousand a month", 60000),
        ("two hundred fifty thousand", 250000),
        ("5 juta per bulan", 5000000),
    ])
    def test_amounts_people_actually_say(self, said, expected):
        # Spoken words are the common case. An amount arriving as no answer
        # means the agent asks again, which is what makes a call irritating.
        assert read_money(said) == expected

    def test_nothing_found(self):
        assert read_money("I would rather not say") is None


class TestReadingYesNo:
    @pytest.mark.parametrize("said,expected", [
        ("yes", True), ("yeah I do", True), ("opo", True), ("iya", True),
        ("no", False), ("nope", False), ("hindi", False), ("tidak", False),
    ])
    def test_both_languages(self, said, expected):
        assert read_yes_no(said) is expected

    def test_ambiguous_answers_are_not_guessed(self):
        assert read_yes_no("well yes and no") is None
        assert read_yes_no("maybe") is None


class TestWhenToRetrieve:
    @pytest.mark.parametrize("said", [
        "What does the Plus plan cover?",
        "how long is the waiting period",
        "Can I add my wife",
        "Magkano po ang premium?",
        "Berapa denda kalau telat bayar",
        "tell me about the riders",
        "I was wondering about the waiting period",
    ])
    def test_questions_are_retrieved_for(self, said):
        assert needs_knowledge(said)

    @pytest.mark.parametrize("said", [
        "Honestly this sounds more expensive than I expected",
        "I already have insurance through my employer anyway",
        "I would rather think about it for a while",
        "I never claim so it feels like wasted money",
        "medyo mabigat sa budget ito",
    ])
    def test_objections_are_retrieved_for(self, said):
        # These carry no question word and no question mark. Detecting only
        # questions meant the agent improvised where an approved response
        # existed, which is the one thing objection handling must not do.
        assert needs_knowledge(said)

    @pytest.mark.parametrize("said", [
        "yes", "no", "okay", "sure", "thanks", "opo", "iya",
        "No I'm not.", "yes I do", "yeah that's right", "no not really",
        "I'm 35", "42 years old", "about 60000",
    ])
    def test_plain_answers_are_not_retrieved_for(self, said):
        # Skipping retrieval on these is what keeps a slot answer fast, and it
        # keeps records the reply never used out of the citation list.
        assert not needs_knowledge(said)

    def test_a_question_is_retrieved_for_even_when_short(self):
        assert needs_knowledge("is dental covered?")


class TestRefusalDetection:
    def test_a_refusal_is_recorded(self):
        turn = Turn(caller="q", agent="I do not have that detail here.",
                    grounded=False, sought_knowledge=True)
        assert turn.said_it_did_not_know

    def test_a_grounded_answer_is_never_a_refusal(self):
        # "I do not have a plan available for you at sixty-five" is a grounded
        # answer that shares wording with a refusal, and counting it as one put
        # a correctly answered question on the list of failures.
        turn = Turn(caller="am I eligible?",
                    agent="I do not have a plan available for you at sixty-five.",
                    grounded=True)
        assert not turn.said_it_did_not_know

    def test_an_ordinary_reply_is_not_a_refusal(self):
        turn = Turn(caller="q", agent="Dental is not covered on any plan.",
                    grounded=True)
        assert not turn.said_it_did_not_know


class TestEscalationDetection:
    @pytest.fixture
    def agent(self):
        return Agent("health_shield_en")

    @pytest.mark.parametrize("said,trigger", [
        ("can I speak to a real person please", "ESC-REQUEST"),
        ("just put me through to someone", "ESC-REQUEST"),
        ("I want to make a complaint", "ESC-COMPLAINT"),
        ("my claim was declined and I want it looked at", "ESC-CLAIM-DISPUTE"),
        ("I have chest pain, is this serious", "ESC-MEDICAL-ADVICE"),
        ("I am going to take legal action", "ESC-LEGAL"),
        ("my husband died last month", "ESC-VULNERABLE"),
    ])
    def test_each_trigger_is_caught(self, agent, said, trigger):
        assert agent._check_escalation(said) == trigger

    def test_ordinary_conversation_does_not_escalate(self, agent):
        assert agent._check_escalation("I am thirty five and live in Manila") == ""

    def test_the_same_question_asked_twice_escalates(self, agent):
        fresh = Agent("health_shield_en")
        fresh.conversation.turns.append(Turn(
            caller="do you cover physiotherapy sessions",
            agent="I do not have that detail here.", grounded=False,
            sought_knowledge=True))
        assert fresh._check_escalation(
            "so is physiotherapy covered or not") == "ESC-UNKNOWN-REPEAT"

    def test_two_different_unanswered_questions_do_not(self, agent):
        # A caller asking two different things the agent cannot answer is
        # having a normal call, not a stuck one.
        fresh = Agent("health_shield_en")
        fresh.conversation.turns.append(Turn(
            caller="what is the capital of France",
            agent="I do not have that detail here.", grounded=False,
            sought_knowledge=True))
        assert fresh._check_escalation("what is tomorrow's weather") == ""


class TestSlotCorrection:
    def test_a_correction_overwrites_the_earlier_answer(self):
        agent = Agent("health_shield_en")
        agent.conversation.slots.update(agent._extract_slots("I'm twenty eight"))
        assert agent.conversation.slots["age"] == 28

        found = agent._extract_slots("Actually sorry, I'm sixty five")
        assert found["age"] == 65

    def test_a_later_number_without_a_correction_cue_is_ignored(self):
        # Otherwise "my wife is thirty" overwrites the caller's own age.
        agent = Agent("health_shield_en")
        agent.conversation.slots.update(agent._extract_slots("I'm forty two"))
        assert agent._extract_slots("my wife is thirty") == {}

    def test_repeating_the_same_value_is_not_a_correction(self):
        agent = Agent("health_shield_en")
        agent.conversation.slots.update(agent._extract_slots("I'm forty two"))
        assert agent._extract_slots("sorry, forty two") == {}


class TestConversationRecord:
    def test_citations_are_collected_without_duplicates(self):
        conversation = Conversation(pack_id="p", business_unit="u")
        conversation.turns.append(Turn("a", "b", citations=["x", "y"]))
        conversation.turns.append(Turn("c", "d", citations=["y", "z"]))
        assert conversation.citations == ["x", "y", "z"]

    def test_unanswered_questions_are_listed(self):
        conversation = Conversation(pack_id="p", business_unit="u")
        conversation.turns.append(Turn("what is the weather",
                                       "I do not have that detail.", grounded=False,
                                       sought_knowledge=True))
        conversation.turns.append(Turn("is dental covered",
                                       "No, dental is excluded.", grounded=True))
        assert conversation.unanswered == ["what is the weather"]
