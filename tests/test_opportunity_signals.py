"""Money left on the table, and decisions stated as final.

Both of these detectors are different in kind from the rest. Every other signal
fires on something that was said. These two fire on something that was said and
then handled wrongly, which means neither can be judged from the caller's words
alone — the agent's reply in the same turn is half the evidence.

Getting that wrong is worse than not having the detector. Telling an agent they
missed an opportunity while they are in the middle of taking it is how a panel
gets muted, and after that the compliance warnings go unread too.
"""

from insights.signals import TurnInput, lexical_signals


def kinds(caller: str, agent: str = "") -> set[str]:
    return {s.kind for s in lexical_signals(TurnInput(caller=caller, agent=agent))}


class TestMissedOpportunity:
    def test_a_second_vehicle_that_went_past(self):
        assert "missed_opportunity" in kinds(
            "We have another car as well.",
            "Right. And what is your date of birth?")

    def test_dependants_that_went_past(self):
        assert "missed_opportunity" in kinds(
            "My wife and my two kids live with me.",
            "Thank you. What is your occupation?")

    def test_cover_held_elsewhere(self):
        assert "missed_opportunity" in kinds(
            "I already have a policy through work.",
            "Understood. What is your annual income?")

    def test_it_works_in_indonesian(self):
        assert "missed_opportunity" in kinds(
            "Mobil satunya sering dipakai istri saya.",
            "Baik pak. Alamatnya di mana ya?")

    def test_it_works_in_taglish(self):
        assert "missed_opportunity" in kinds(
            "Anak ko po dalawa, saka asawa ko po.",
            "Salamat po. Ano pong trabaho ninyo?")


class TestTheAgentAlreadyActedOnIt:
    """The same caller sentence, and a reply that followed up.

    This is the whole difference between a miss and a mention, and it is the
    half a detector built on the caller's words alone cannot see.
    """

    def test_offering_the_second_vehicle_is_not_a_miss(self):
        assert "missed_opportunity" not in kinds(
            "We have another car as well.",
            "I can add the second vehicle to the same policy, shall I?")

    def test_offering_to_include_the_family_is_not_a_miss(self):
        assert "missed_opportunity" not in kinds(
            "My wife and my two kids live with me.",
            "Would you like to include them in the cover?")

    def test_it_works_in_indonesian(self):
        assert "missed_opportunity" not in kinds(
            "Mobil satunya sering dipakai istri saya.",
            "Bisa sekalian kami tambahkan kendaraan itu pak.")


class TestFollowUpMatchingIsOnWholeWords:
    """Substring matching broke this detector and it was not obvious.

    "Too" sits inside "understood", so "Understood. What is your annual
    income?" counted as a follow-up and suppressed a real miss. "Add" sits
    inside "address", which would have done the same to every turn that asks
    for one.
    """

    def test_understood_does_not_count_as_too(self):
        assert "missed_opportunity" in kinds(
            "We have another car as well.", "Understood. Next question.")

    def test_address_does_not_count_as_add(self):
        assert "missed_opportunity" in kinds(
            "We have another car as well.", "What is your address please?")


class TestMissingDisclosure:
    def test_a_decision_stated_as_final(self):
        assert "missing_disclosure" in kinds(
            "So am I in?", "Yes, you are eligible and approved.")

    def test_a_decision_stated_as_final_in_taglish(self):
        assert "missing_disclosure" in kinds(
            "Pasado po ba ako?", "Opo, kwalipikado po kayo.")

    def test_a_qualified_decision_is_fine(self):
        assert "missing_disclosure" not in kinds(
            "So am I in?",
            "You are eligible on a preliminary basis, subject to underwriting.")

    def test_a_qualified_decision_is_fine_in_taglish(self):
        assert "missing_disclosure" not in kinds(
            "Pasado po ba ako?",
            "Opo, kwalipikado po kayo, pero depende pa po sa final review.")

    def test_the_caller_using_the_word_is_not_the_agent_saying_it(self):
        assert "missing_disclosure" not in kinds(
            "Was I approved last time I applied?",
            "Let me check what we have on file.")
