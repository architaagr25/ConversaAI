"""
Conversation pack and qualification tests.

Two things are being protected. That policy content stays out of the system
prompt, because a prompt that carries a waiting period will still be quoting it
long after the policy changed. And that eligibility decisions are reproducible,
because telling somebody they cannot buy insurance is not a place for "usually
right".
"""

from __future__ import annotations

import pytest

from voice_agent.pack import (
    PackError,
    Pack,
    available_packs,
    build_system_prompt,
    build_turn_context,
    load_pack,
)
from voice_agent.qualify import assess, evaluate, load_rules, outcome_wording


@pytest.fixture(scope="module")
def pack() -> Pack:
    return load_pack("health_shield_en")


class TestPackLoading:
    def test_the_pack_loads(self, pack):
        assert pack.pack_id == "health_shield_en"
        assert pack.business_unit == "health_ph_en"

    def test_required_slots_are_identified(self, pack):
        names = {s.name for s in pack.required_slots}
        assert {"age", "residency", "currently_admitted"} <= names

    def test_a_missing_pack_is_reported_clearly(self):
        with pytest.raises(PackError, match="no pack named"):
            load_pack("does_not_exist")

    def test_packs_can_be_listed(self):
        assert "health_shield_en" in available_packs()

    def test_a_slot_can_be_looked_up(self, pack):
        assert pack.slot("age").required
        assert pack.slot("nonexistent") is None


class TestPromptStaysBehavioural:
    """The assessment is explicit that policies must not live in the prompt."""

    @pytest.mark.parametrize("leak", [
        "24 months", "twenty-four", "30 days", "thirty days", "1,180", "999",
        "31 days", "PHP 250,000", "age 70", "18 to 60",
    ])
    def test_no_policy_figure_reaches_the_prompt(self, pack, leak):
        assert leak.lower() not in build_system_prompt(pack).lower()

    def test_no_approved_objection_wording_reaches_the_prompt(self, pack):
        prompt = build_system_prompt(pack).lower()
        for wording in ("no-claim discount", "employer cover usually ends",
                        "paying annually"):
            assert wording not in prompt

    def test_the_prompt_still_carries_the_behaviour(self, pack):
        prompt = build_system_prompt(pack)
        assert "Maya" in prompt
        assert "Solara Health Shield" in prompt
        assert "do not have" in prompt.lower()

    def test_the_prompt_says_saying_you_do_not_know_is_correct(self, pack):
        assert "guessing is not" in build_system_prompt(pack).lower()

    def test_the_prompt_carries_every_prohibition(self, pack):
        prompt = build_system_prompt(pack).lower()
        assert "invent a premium" in prompt
        assert "medical, tax or legal advice" in prompt

    def test_the_slot_questions_are_present(self, pack):
        assert "how old are you" in build_system_prompt(pack).lower()


class TestTurnContext:
    class FakeRecord:
        def __init__(self, title, content, authority="published"):
            self.title, self.content, self.authority = title, content, authority

    def test_records_are_offered_with_an_instruction_to_use_only_them(self):
        records = [self.FakeRecord("Waiting periods", "Covered after 24 months.")]
        context = build_turn_context(records, True, ["I do not have that."])
        assert "Answer only from these" in context
        assert "24 months" in context

    def test_a_binding_record_is_marked_as_winning(self):
        records = [self.FakeRecord("Clause 2", "Twenty-four months.", "binding")]
        assert "binding" in build_turn_context(records, True, ["x"])

    def test_marketing_copy_is_marked_as_not_quotable(self):
        records = [self.FakeRecord("Offer", "From PHP 999.", "promotional")]
        assert "do not quote as policy" in build_turn_context(records, True, ["x"])

    def test_no_confident_match_produces_a_refusal_instruction(self):
        context = build_turn_context([], False, ["I do not have that detail."])
        assert "NO SUPPORTING RECORDS" in context
        assert "do not have that detail" in context.lower()

    def test_low_confidence_never_leaks_the_records(self):
        # Retrieval returning something weak must not become an answer.
        records = [self.FakeRecord("Something", "A tempting but weak match.")]
        context = build_turn_context(records, False, ["I do not know."])
        assert "tempting" not in context


class TestRuleEvaluation:
    def test_a_range_is_understood(self):
        assert evaluate("18 <= age <= 60", {"age": 30}) is True
        assert evaluate("18 <= age <= 60", {"age": 62}) is False

    def test_boundaries_are_inclusive(self):
        assert evaluate("18 <= age <= 60", {"age": 18}) is True
        assert evaluate("18 <= age <= 60", {"age": 60}) is True

    def test_a_comparison_is_understood(self):
        assert evaluate("monthly_income >= 4000000", {"monthly_income": 5000000})
        assert not evaluate("monthly_income >= 4000000", {"monthly_income": 3000000})

    def test_equality_on_text_and_booleans(self):
        assert evaluate("residency == 'PH'", {"residency": "PH"}) is True
        assert evaluate("has_valid_ktp == true", {"has_valid_ktp": True}) is True
        assert evaluate("currently_admitted == false",
                        {"currently_admitted": True}) is False

    def test_an_unknown_fact_is_not_a_failure(self):
        # A caller who has not given their age has not failed the age check.
        assert evaluate("18 <= age <= 60", {}) is None

    def test_an_unparseable_expression_is_skipped_not_guessed(self):
        assert evaluate("channel == 'bank' and age < 46", {"age": 30}) is None


class TestAssessment:
    def test_a_qualifying_caller_passes(self):
        result = assess("health_ph_en", {
            "age": 35, "residency": "PH", "currently_admitted": False,
            "monthly_income": 50000})
        assert result.eligible and result.decided

    def test_being_over_the_entry_age_declines(self):
        result = assess("health_ph_en", {
            "age": 62, "residency": "PH", "currently_admitted": False})
        assert not result.eligible
        assert "18 to 60" in result.decline_reason

    def test_the_decline_names_the_rule_that_caused_it(self):
        result = assess("health_ph_en", {
            "age": 62, "residency": "PH", "currently_admitted": False})
        assert result.blocking[0].rule_id == "HS-AGE"

    def test_an_incomplete_answer_set_is_undecided_not_declined(self):
        result = assess("health_ph_en", {"age": 35})
        assert not result.decided
        assert "residency" in result.missing
        assert result.eligible

    def test_an_older_applicant_needs_a_questionnaire(self):
        result = assess("health_ph_en", {
            "age": 55, "residency": "PH", "currently_admitted": False,
            "monthly_income": 50000})
        assert result.eligible
        assert "require_medical_questionnaire" in result.requirements

    def test_max_cover_needs_one_at_any_age(self, pack):
        # The pack calls this plan_interest and the rules file calls it plan.
        # Passing the raw slot name means the rule silently never fires and the
        # caller is told no questionnaire is needed when one is.
        collected = {"age": 25, "residency": "PH", "currently_admitted": False,
                     "monthly_income": 50000, "plan_interest": "Max"}
        result = assess("health_ph_en", pack.to_facts(collected))
        assert "require_medical_questionnaire" in result.requirements

    def test_the_indonesian_rules_apply_their_own_thresholds(self):
        result = assess("multifinance_id", {
            "age": 30, "age_at_tenor_end": 33, "has_valid_ktp": True,
            "monthly_income": 3000000, "installment_to_income": 0.2,
            "has_existing_arrears": False})
        assert not result.eligible
        assert "4.000.000" in result.decline_reason

    def test_the_same_answers_always_give_the_same_result(self):
        facts = {"age": 62, "residency": "PH", "currently_admitted": False}
        first, second = assess("health_ph_en", facts), assess("health_ph_en", facts)
        assert first.eligible == second.eligible
        assert first.decline_reason == second.decline_reason

    def test_an_unknown_unit_is_an_error(self):
        with pytest.raises(ValueError, match="no qualification rules"):
            assess("not_a_unit", {})


class TestPackAndRulesAgree:
    """The pack and the rules file are separate documents that have to line up.

    Nothing enforces this at runtime: a field the rules test but the pack never
    collects produces a rule that is skipped forever, and the caller is told
    they qualify without it ever having been checked.
    """

    def test_every_hard_rule_field_can_be_collected(self, pack):
        rules = load_rules()["units"][pack.business_unit]
        collectable = {s.fact_name for s in pack.slots}
        for rule in rules["hard_rules"]:
            field = rule.get("field")
            assert field in collectable, (
                f"rule {rule['id']} tests {field!r}, which no slot provides")

    def test_slot_mappings_are_applied(self, pack):
        facts = pack.to_facts({"plan_interest": "Max", "age": 30})
        assert facts["plan"] == "Max"
        assert facts["age"] == 30

    def test_an_unmapped_slot_keeps_its_name(self, pack):
        assert pack.to_facts({"age": 30}) == {"age": 30}


class TestOutcomeWording:
    def test_a_decline_explains_itself(self):
        result = assess("health_ph_en", {
            "age": 62, "residency": "PH", "currently_admitted": False})
        wording = outcome_wording(result)
        assert "18 to 60" in wording
        assert "alternative" in wording.lower()

    def test_an_incomplete_assessment_says_what_is_missing(self):
        wording = outcome_wording(assess("health_ph_en", {"age": 35}))
        assert "Still need" in wording

    def test_the_wording_comes_from_the_rules_file(self):
        # Not invented here, so compliance can change it without a code change.
        rules = load_rules()
        assert "decline" in rules["outcomes"]
