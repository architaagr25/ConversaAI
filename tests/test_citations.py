"""Which sources go under a reply, and which do not.

A citation is a claim that the answer came from that record. Getting it wrong
in the generous direction is the expensive one: sources under every reply,
including the ones that answer nothing, teach whoever is reading them that the
sources mean nothing, and then the citation under a quoted premium is ignored
along with the rest.
"""

from dataclasses import dataclass

from voice_agent.agent import records_behind


@dataclass
class FakeRecord:
    source_ref: str
    title: str
    content: str


# What retrieval returns when somebody asks about the cost of covering family.
# All about the same thing, which is the point: premium, monthly, plan and
# dependant are in most of them.
PREMIUM_RECORDS = [
    FakeRecord("rates.pdf#Rider monthly premium",
               "Rider monthly premium",
               "Dependant riders are charged a monthly premium on top of the "
               "Essential plan. A spouse rider is 480 pesos monthly."),
    FakeRecord("lead_form.csv#Product Interest",
               "Product Interest",
               "Product interest is recorded as Essential, Plus or Max, with "
               "an indication of whether dependants are included."),
    FakeRecord("rates.pdf#Essential plan premium",
               "Essential plan premium",
               "The Essential plan premium is 1,200 pesos monthly for a single "
               "adult member."),
    FakeRecord("wording.pdf#Dependant eligibility",
               "Dependant eligibility",
               "A dependant is a spouse or a child under twenty-one. Monthly "
               "premium applies per dependant on every plan."),
]


class TestAReplyThatOnlyOffersInformation:
    """The case seen live, and the reason the rule changed.

    The reply promises to go over the premiums. It states none of them. Plain
    word overlap cited two sources for it, because the words it shares with the
    rate table are the words that describe the whole topic.
    """

    def test_offering_to_give_figures_cites_nothing(self):
        reply = ("I understand you are interested in the Essential plan. "
                 "Since you mentioned you would like to include your family, "
                 "I can help you look at the costs for that. Would you like me "
                 "to go over the monthly premiums for your dependants under "
                 "the Essential plan?")
        assert records_behind(reply, PREMIUM_RECORDS) == []

    def test_a_slot_question_cites_nothing(self):
        reply = "Would this be just for you, or for family as well?"
        assert records_behind(reply, PREMIUM_RECORDS) == []


class TestAReplyThatActuallyAnswers:
    def test_a_quoted_figure_cites_the_record_it_came_from(self):
        reply = ("A spouse rider is four hundred and eighty pesos monthly on "
                 "top of your plan.")
        kept = records_behind(reply, PREMIUM_RECORDS)
        assert [r.source_ref for r in kept] == ["rates.pdf#Rider monthly premium"]

    def test_a_definition_cites_the_records_that_define_it(self):
        reply = ("A dependant means your spouse, or a child under twenty-one.")
        kept = [r.source_ref for r in records_behind(reply, PREMIUM_RECORDS)]
        assert "wording.pdf#Dependant eligibility" in kept
        # The two that only share the topic are not among them. The rider
        # record is, and legitimately: it says what a dependant and a spouse
        # cost, which is more than sharing a subject with the question.
        assert "lead_form.csv#Product Interest" not in kept
        assert "rates.pdf#Essential plan premium" not in kept


class TestNarrowRetrieval:
    """Discounting common words needs several records to compare.

    With one record every word is in more than half of them, and discounting on
    that basis would drop the citation from exactly the turns where retrieval
    found one precise answer.
    """

    def test_a_single_record_is_still_cited(self):
        only = [FakeRecord("wording.pdf#Dental",
                           "Dental",
                           "Dental treatment is excluded on every plan except "
                           "reconstructive work following an accident.")]
        reply = ("Dental treatment is excluded, apart from reconstructive work "
                 "after an accident.")
        assert len(records_behind(reply, only)) == 1

    def test_nothing_retrieved_cites_nothing(self):
        assert records_behind("Dental is not covered.", []) == []
