"""
Localisation checks.

Politeness in Filipino is grammar. Mixing "po" with "mo" in one sentence is
the equivalent of switching between "sir" and "mate" mid-sentence, and it is
the mistake a model makes most often when asked to write Taglish.
"""

from __future__ import annotations

import pytest

from voice_agent.localisation import check_register, taglish_balance
from voice_agent.pack import build_system_prompt, load_pack


class TestFilipinoRegister:
    def test_consistent_formal_speech_passes(self):
        result = check_register(
            "Salamat po sa oras ninyo. Mababayaran po ninyo ito bago matapos "
            "ang grace period.", "fil")
        assert result.ok

    def test_consistent_informal_speech_passes(self):
        # Informal on its own is fine. It is only wrong beside "po".
        assert check_register("Salamat sa oras mo, tawag ka lang.", "fil").ok

    def test_mixing_formal_and_informal_is_caught(self):
        # The real case, from choosing the model: "Gets ko po na medyo mabigat
        # sa budget ... pasok sa presyong kaya mo" is formal and informal four
        # words apart.
        result = check_register(
            "Gets ko po na mabigat sa budget, pero may options na kaya mo.", "fil")
        assert not result.ok
        assert result.mixed

    def test_the_explanation_names_both_sides(self):
        result = check_register("Salamat po sa oras mo.", "fil")
        assert "po" in result.explain()
        assert "mo" in result.explain()

    def test_the_check_is_per_sentence(self):
        # A whole reply may soften towards the end; one sentence may not.
        result = check_register(
            "Magandang araw po, Sir. Sige, tawag ka lang.", "fil")
        assert result.ok

    def test_english_inside_taglish_is_not_a_fault(self):
        # Taglish is code-switching. An earlier version of this check flagged
        # "from" in "si Ella po ito from Solara Life", which is exactly how
        # people speak.
        result = check_register(
            "Si Ella po ito from Solara Life Philippines, tungkol po sa "
            "premium payment ninyo.", "fil")
        assert result.ok

    def test_a_reply_that_is_all_english_is_caught(self):
        result = check_register(
            "Thank you for your time today, we will call you back about the "
            "outstanding premium payment on your policy.", "fil")
        assert not result.ok
        assert result.english_drift


class TestIndonesianRegister:
    def test_formal_address_passes(self):
        assert check_register("Mohon Bapak segera melakukan pembayaran.", "id").ok

    def test_casual_address_alone_passes(self):
        assert check_register("Cicilan kamu jatuh tempo tiga hari lagi.", "id").ok

    def test_mixing_formal_and_casual_is_caught(self):
        result = check_register("Mohon Bapak segera bayar, cicilan kamu telat.", "id")
        assert result.mixed


class TestTaglishBalance:
    def test_all_tagalog_scores_low(self):
        assert taglish_balance("Salamat po sa oras ninyo, ingat po kayo") < 0.3

    def test_all_english_scores_high(self):
        assert taglish_balance("Thank you for your time today") > 0.8

    def test_real_taglish_sits_in_between(self):
        score = taglish_balance(
            "Naiintindihan ko po, may grace period po tayo na 31 days mula sa "
            "due date ninyo.")
        assert 0.25 < score < 0.75

    def test_empty_text(self):
        assert taglish_balance("") == 0.0


class TestPhilippinesPack:
    @pytest.fixture
    def pack(self):
        return load_pack("life_ph")

    def test_it_loads(self, pack):
        assert pack.business_unit == "life_ph"
        assert pack.language == "fil"

    def test_the_register_rules_reach_the_prompt(self, pack):
        prompt = build_system_prompt(pack).lower()
        assert "po" in prompt and "kayo" in prompt
        assert "never" in prompt

    def test_speech_conventions_reach_the_prompt(self, pack):
        # How amounts and dates are said aloud is what gives a translated
        # script away, so it has to be in the prompt rather than hoped for.
        prompt = build_system_prompt(pack).lower()
        assert "kinse" in prompt
        assert "pesos" in prompt

    def test_technical_terms_are_kept_in_english(self, pack):
        prompt = build_system_prompt(pack).lower()
        assert "do not translate" in prompt

    def test_escalation_triggers_are_in_the_local_language(self, pack):
        phrases = " ".join(
            p for group in pack.escalation["detect"].values() for p in group)
        assert "makausap ang tao" in phrases
        assert "abogado" in phrases

    def test_no_policy_figures_leak_into_the_prompt(self, pack):
        prompt = build_system_prompt(pack)
        for leak in ("31 days", "24 months", "1,180", "3 years"):
            assert leak not in prompt

    def test_the_soft_refusal_rule_is_present(self, pack):
        # "Titingnan ko po" is a no. Treating it as a maybe and pushing again
        # is the most common way a foreign-written script offends.
        approach = " ".join(pack.objections["approach"]).lower()
        assert "titingnan ko po" in approach
