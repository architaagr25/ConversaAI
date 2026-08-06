"""
Per-market recognition settings, and detecting a language switch.

These are the checks that would have caught the two mistakes actually made
here: a language hint set on the one market where it does damage, and a
switch detector that could not see the English half of a Taglish sentence.
"""

from __future__ import annotations

import pytest

from voice_agent.asr import (
    ENGLISH_DOMAIN,
    ENGLISH_FUNCTION,
    INDONESIAN_ONLY,
    MARKETS,
    TAGALOG_ONLY,
    DeepgramTranscriber,
    config_for,
    detect_code_switching,
)


class TestMarketConfiguration:
    def test_every_agent_pack_has_recognition_settings(self):
        # Keyed on the business unit rather than the pack file name. The two
        # are not the same string for every pack, and the business unit is
        # what the call loop and retrieval actually carry around.
        from voice_agent.pack import available_packs, load_pack

        for name in available_packs():
            unit = load_pack(name).business_unit
            assert unit in MARKETS, f"{name} ({unit}) has no recognition settings"

    def test_taglish_market_sets_no_language_hint(self):
        # The point of the whole file. Forcing Tagalog makes the recogniser
        # spell the English half phonetically.
        assert MARKETS["life_ph"].language_hint is None
        assert MARKETS["life_ph"].expects_code_switching

    def test_single_language_markets_do_set_a_hint(self):
        assert MARKETS["health_ph_en"].language_hint == "en"
        assert MARKETS["multifinance_id"].language_hint == "id"

    def test_every_market_explains_its_choice(self):
        for name, config in MARKETS.items():
            assert len(config.why.split()) >= 12, f"{name} is not explained"

    def test_prompts_carry_the_terms_the_market_actually_uses(self):
        assert "benepisyaryo" in MARKETS["life_ph"].prompt
        assert "cicilan" in MARKETS["multifinance_id"].prompt
        # Regional politeness words are short and easily lost, so they are
        # primed rather than left to chance.
        assert "monggo" in MARKETS["multifinance_id"].prompt
        assert "punten" in MARKETS["multifinance_id"].prompt

    def test_unknown_market_falls_back_rather_than_failing(self):
        assert config_for("no such market").language_hint == "en"

    def test_second_provider_covers_the_same_markets(self):
        for name in MARKETS:
            assert name in DeepgramTranscriber.LANGUAGES


class TestWordLists:
    def test_the_three_lists_do_not_overlap(self):
        # A word claimed by two languages proves nothing and skews the count.
        assert not TAGALOG_ONLY & INDONESIAN_ONLY
        assert not TAGALOG_ONLY & (ENGLISH_FUNCTION | ENGLISH_DOMAIN)
        assert not INDONESIAN_ONLY & (ENGLISH_FUNCTION | ENGLISH_DOMAIN)

    def test_function_and_domain_words_are_kept_apart(self):
        # They are weighted differently, so they must not be the same list.
        assert not ENGLISH_FUNCTION & ENGLISH_DOMAIN


class TestCodeSwitching:
    def test_plain_tagalog_is_not_reported_as_a_switch(self):
        result = detect_code_switching("Magkano po ang hulog ko kada buwan?")
        assert result.languages == ["tagalog"]
        assert not result.switched

    def test_english_finance_words_inside_tagalog_are_a_switch(self):
        # This is the case the first version got wrong. It saw only function
        # words, so a sentence half made of English finance terms came back
        # as Tagalog throughout.
        result = detect_code_switching(
            "Magkano po ang premium ko kung monthly ang bayad?")
        assert result.switched
        assert set(result.languages) == {"tagalog", "english"}

    def test_a_single_finance_word_still_counts(self):
        # One English word in eleven falls under the share threshold, but
        # "beneficiary" in a Tagalog sentence is not an accident.
        result = detect_code_switching(
            "Sino po ang benepisyaryo at pwede po bang palitan ang beneficiary?")
        assert result.switched
        assert "english" in result.languages

    def test_plain_english_is_not_turned_into_a_switch_by_finance_words(self):
        # The guard for the rule above: domain words only count as a switch
        # when some other language is present.
        result = detect_code_switching(
            "How long is the waiting period for pre-existing conditions?")
        assert result.languages == ["english"]
        assert not result.switched

    def test_indonesian_is_recognised_and_not_confused_with_tagalog(self):
        result = detect_code_switching(
            "Berapa denda kalau telat bayar cicilan seminggu?")
        assert result.languages == ["indonesian"]

    def test_regional_indonesian_is_still_indonesian(self):
        result = detect_code_switching(
            "Nuwun sewu, kulo dereng saget mbayar cicilan niki.")
        assert not result.switched

    def test_the_dominant_language_is_named_first(self):
        result = detect_code_switching(
            "Ano po ang mangyayari kung ma-lapse ang policy, may grace "
            "period po ba?")
        assert result.languages[0] == "tagalog"

    def test_dropping_one_language_entirely_shows_up(self):
        # What Deepgram returned for a Taglish sentence: it kept the English
        # words and lost every Tagalog one. The detector has to make that
        # visible rather than call it a successful transcription.
        result = detect_code_switching("Premium monthly")
        assert result.languages == ["english"]
        assert not result.switched

    @pytest.mark.parametrize("text", ["", "   ", "...", "!!!", "123 456"])
    def test_nothing_recognisable_is_reported_as_such(self, text):
        result = detect_code_switching(text)
        assert result.languages == []
        assert not result.switched
        assert "nothing" in result.describe()
