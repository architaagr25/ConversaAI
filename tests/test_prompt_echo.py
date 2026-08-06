"""The recogniser handing back the words it was primed with.

The domain hint is what makes cicilan and bancassurance come out spelled
right. It is also a list of words the recogniser has been told to expect, and
given audio with nothing in it, that list is what it returns.

Seen live: the agent answered "and the plus, max" repeatedly, on a call where
the caller had said nothing for a minute. It reads as the microphone picking up
the speakers. It is not. The hint for the English market ends "Essential, Plus,
Max", and that is where it came from.
"""

from voice_agent.asr import config_for
from voice_agent.transcribe import is_prompt_echo

ENGLISH = config_for("health_ph_en").prompt
INDONESIAN = config_for("multifinance_id").prompt


# The hint the English market used when this was seen. Kept as a fixture
# rather than read from the market, because the market no longer has these
# words in it and that is the actual fix. This is here to prove the filter
# behind it works.
HINT_THAT_CAUSED_IT = (
    "Solara Health Shield, premium, waiting period, pre-existing condition, "
    "rider, deductible, accredited hospital, grace period, Essential, Plus, Max"
)


class TestWhatCameBackFromTheHint:
    def test_the_phrase_seen_on_a_live_call(self):
        assert is_prompt_echo("and the plus, max", HINT_THAT_CAUSED_IT)

    def test_plan_names_on_their_own(self):
        assert is_prompt_echo("Essential, Plus, Max", HINT_THAT_CAUSED_IT)

    def test_a_pair_of_primed_terms(self):
        assert is_prompt_echo("premium, deductible", HINT_THAT_CAUSED_IT)

    def test_it_works_for_the_indonesian_hint_too(self):
        assert is_prompt_echo("cicilan, angsuran", INDONESIAN)


class TestTheHintsThemselves:
    """The filter is the net. This is the hole it was falling through.

    A word in the hint is a word the recogniser will reach for when it cannot
    make out the audio, so the hint has to earn every entry: only terms a
    general recogniser genuinely gets wrong. Ordinary words in it are pure
    cost.
    """

    def test_plan_names_are_not_primed(self):
        # They are ordinary English, spelled correctly with no help at all,
        # and they were what came back on a live call.
        for word in ("essential", "plus", "max"):
            assert word not in ENGLISH.lower()

    def test_ordinary_insurance_words_are_not_primed(self):
        for word in ("premium", "rider", "deductible", "hospital"):
            assert word not in ENGLISH.lower()

    def test_the_terms_that_earn_their_place_are_kept(self):
        assert "Solara Health Shield" in ENGLISH
        assert "bancassurance" in ENGLISH
        assert "cicilan" in INDONESIAN
        # Short, unusual, and genuinely lost without help.
        assert "nuwun sewu" in INDONESIAN

    def test_every_hint_stays_short(self):
        # Length is the cost. A long hint is a long list of things the
        # recogniser can hand back instead of an answer.
        from voice_agent.asr import MARKETS

        for market in MARKETS.values():
            assert len(market.prompt.split(",")) <= 14, market.business_unit


class TestWhatIsACaller:
    def test_a_caller_naming_a_plan_is_kept(self):
        # "plan" and "please" are not in the hint, and that is the difference.
        assert not is_prompt_echo("the Plus plan, please", ENGLISH)

    def test_a_single_primed_word_is_kept(self):
        # "Max" is a complete answer to which plan they want.
        assert not is_prompt_echo("Max", ENGLISH)

    def test_a_real_question_using_primed_words_is_kept(self):
        assert not is_prompt_echo("how long is the waiting period on that",
                                  ENGLISH)

    def test_a_sentence_is_never_treated_as_the_hint(self):
        assert not is_prompt_echo(
            "premium deductible rider grace period essential plus max",
            ENGLISH)

    def test_ordinary_speech_is_kept(self):
        assert not is_prompt_echo("I am thirty five and I live in Manila",
                                  ENGLISH)

    def test_nothing_is_discarded_when_there_is_no_hint(self):
        assert not is_prompt_echo("and the plus, max", "")
