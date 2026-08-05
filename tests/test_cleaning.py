"""
Cleaning and classification tests.

The conflict cases carry the most weight. Missing a contradiction lets the agent
repeat a marketing claim as though it were a policy term; inventing one buries
the real contradiction in noise.
"""

from __future__ import annotations

import pytest

from knowledge_base.clean import (
    EQUIVALENCE_TOLERANCE,
    TOPIC_MARKERS,
    claimed_durations,
    classify_authority,
    classify_category,
    classify_unit,
    content_hash,
    detect_conflicts,
    find_duplicate_groups,
    find_terminology,
    jaccard,
    normalise_dates,
    resolve_duplicates,
    shingles,
)
from knowledge_base.models import Record


def make_record(record_id: str, content: str, authority: str = "published",
                unit: str = "health_ph_en", category: str = "policy_rule") -> Record:
    return Record(
        record_id=record_id, title=record_id, content=content,
        category=category, business_unit=unit, authority=authority,
        source_type="web_page", source_ref=f"{record_id}#x",
        source_origin="test", source_retrieved_at="2026-03-01T00:00:00+00:00",
    )


class TestDateNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("Reviewed 01/03/2026 by ops", "2026-03-01"),
        ("Reviewed March 1, 2026 by ops", "2026-03-01"),
        ("Published 1 Mar 26", "2026-03-01"),
    ])
    def test_every_source_format_becomes_iso(self, raw, expected):
        result, changed = normalise_dates(raw)
        assert expected in result
        assert changed == 1

    def test_an_iso_date_is_left_alone(self):
        result, changed = normalise_dates("Reviewed 2026-03-01")
        assert result == "Reviewed 2026-03-01"
        assert changed == 0

    def test_an_unknown_month_name_is_not_mangled(self):
        result, _ = normalise_dates("Reviewed Smarch 1, 2026")
        assert "Smarch 1, 2026" in result

    def test_amounts_are_not_mistaken_for_dates(self):
        result, _ = normalise_dates("Cover up to PHP 2,000,000 per year")
        assert "2,000,000" in result


class TestClassification:
    def test_a_campaign_page_is_promotional_whatever_it_contains(self):
        section = {"source_origin": "web/health-shield-campaign.html",
                   "source_type": "web_page"}
        assert classify_authority(section) == "promotional"

    def test_policy_wording_is_binding(self):
        section = {"source_origin": "documents/policy.pdf", "source_type": "pdf_policy"}
        assert classify_authority(section) == "binding"

    def test_the_business_unit_comes_from_the_source(self):
        assert classify_unit({"source_ref": "x", "source_origin":
                              "web/multifinance-indonesia.html"}) == "multifinance_id"

    def test_rules_carry_their_unit_in_the_reference(self):
        assert classify_unit({"source_ref": "rules/q.yaml#life_ph.hard_rules",
                              "source_origin": "rules/q.yaml"}) == "life_ph"

    def test_the_company_boilerplate_is_corporate_not_partnership(self):
        # It mentions accredited agents, which would otherwise file the company
        # description under partnerships.
        section = {"title": "About Solara Finance Group",
                   "content": "Solara operates through 240 branches and more than "
                              "3,000 accredited agents."}
        assert classify_category(section) == "corporate"

    def test_an_objection_is_not_filed_as_a_question(self):
        section = {"title": "Common concerns",
                   "content": "This is more expensive than I expected."}
        assert classify_category(section) == "objection"

    def test_a_waiting_period_is_a_policy_rule(self):
        section = {"title": "Waiting periods",
                   "content": "A waiting period of 30 days applies to all illnesses."}
        assert classify_category(section) == "policy_rule"


class TestTerminology:
    def test_alternatives_travel_with_the_record(self):
        # Someone asking about their hulog must reach a record about premiums.
        _, variants = find_terminology("Your monthly premium is due on the 15th.")
        assert "hulog" in variants
        assert "bayad" in variants

    def test_indonesian_terms_are_covered(self):
        _, variants = find_terminology("Angsuran dibayarkan setiap bulan.")
        assert "cicilan" in variants

    def test_the_concept_is_recorded(self):
        concepts, _ = find_terminology("The grace period is 31 days.")
        assert "grace_period" in concepts

    def test_unrelated_text_produces_nothing(self):
        concepts, variants = find_terminology("The car park closes at eight.")
        assert concepts == [] and variants == []


class TestDuplicates:
    def test_identical_text_hashes_the_same_despite_formatting(self):
        assert content_hash("The Grace  Period is 31 days.") == \
               content_hash("the grace period IS 31 days")

    def test_reworded_copy_is_detected(self):
        original = ("Solara Finance Group has served customers across South East "
                    "Asia since 2009 through a network of 240 branches.")
        copy = ("Solara Finance Group has served customers across South East "
                "Asia since 2009 through a network of 240 branches.")
        records = [make_record("a", original), make_record("b", copy)]
        assert find_duplicate_groups(records)

    def test_different_content_is_not_grouped(self):
        records = [make_record("a", "Waiting period of 30 days applies to illness."),
                   make_record("b", "Commission is paid monthly in arrears to partners.")]
        assert find_duplicate_groups(records) == []

    def test_the_most_authoritative_copy_survives(self):
        text = "Solara has served customers across South East Asia since 2009 " \
               "through a network of two hundred and forty branches nationwide."
        records = [make_record("promo", text, authority="promotional"),
                   make_record("binding", text, authority="binding")]
        groups = find_duplicate_groups(records)
        records, dropped = resolve_duplicates(records, groups)
        assert dropped == 1
        assert records[0].duplicate_of == "binding"
        assert records[1].duplicate_of == ""

    def test_duplicates_are_marked_not_deleted(self):
        text = "Solara has served customers across South East Asia since 2009 " \
               "through a network of two hundred and forty branches nationwide."
        records = [make_record("a", text), make_record("b", text)]
        groups = find_duplicate_groups(records)
        records, _ = resolve_duplicates(records, groups)
        assert len(records) == 2
        assert any(r.duplicate_of for r in records)

    def test_jaccard_on_empty_input(self):
        assert jaccard(set(), shingles("anything at all here")) == 0.0


class TestDurationClaims:
    MARKER = TOPIC_MARKERS["pre_existing_waiting"]

    def test_a_written_number_is_read(self):
        found = claimed_durations(
            "A Waiting Period of twenty-four (24) months applies to any "
            "Pre-existing Condition.", self.MARKER)
        assert found[0][0] == 720

    def test_the_informal_wording_is_matched_too(self):
        # The FAQ never says "pre-existing"; a marker written only for the
        # formal term misses the page a caller is most likely to read.
        found = claimed_durations(
            "If you have a condition that existed before you joined, it is "
            "covered after 2 years.", self.MARKER)
        assert found and found[0][0] == 730

    def test_durations_in_other_sentences_are_ignored(self):
        found = claimed_durations(
            "Illnesses are covered after 30 days. Pre-existing conditions "
            "are covered after 24 months.", self.MARKER)
        assert [days for days, _ in found] == [720]

    def test_a_figure_repeated_in_one_sentence_is_counted_once(self):
        found = claimed_durations(
            "Pre-existing conditions are covered after 30 days, just 30 days.",
            self.MARKER)
        assert len(found) == 1


class TestConflicts:
    def test_a_promotional_claim_losing_to_the_policy_document(self):
        promo = make_record(
            "promo", "Even pre-existing conditions are covered after only 30 days.",
            authority="promotional")
        binding = make_record(
            "binding", "A Waiting Period of twenty-four (24) months applies to any "
                       "Pre-existing Condition declared at application.",
            authority="binding")
        conflicts = detect_conflicts([promo, binding])
        assert len(conflicts) == 1
        assert conflicts[0]["authoritative"] == "binding"
        assert conflicts[0]["superseded"] == "promo"
        assert "contradicts_binding_source" in promo.quality_flags

    def test_the_same_rule_written_two_ways_is_not_a_conflict(self):
        # 24 months is 720 days and 2 years is 730. Reporting that as a
        # contradiction would bury the real one.
        months = make_record("m", "Pre-existing conditions are covered after 24 months.")
        years = make_record("y", "A condition that existed before you joined is "
                                 "covered after 2 years.")
        assert detect_conflicts([months, years]) == []

    def test_tolerance_is_not_wide_enough_to_hide_a_real_gap(self):
        # The spread between 30 days and 720 is 96 per cent of the larger, far
        # outside a tolerance meant only for 720 against 730.
        assert (720 - 30) / 720 > EQUIVALENCE_TOLERANCE
        assert (730 - 720) / 730 <= EQUIVALENCE_TOLERANCE

    def test_different_markets_do_not_conflict(self):
        ph = make_record("ph", "Pre-existing conditions are covered after 24 months.",
                         unit="health_ph_en")
        idn = make_record("id", "Pre-existing conditions are covered after 30 days.",
                          unit="multifinance_id")
        assert detect_conflicts([ph, idn]) == []

    def test_a_pair_is_reported_once(self):
        a = make_record("a", "Pre-existing conditions are covered after 30 days.")
        b = make_record("b", "Pre-existing conditions are covered after 24 months.")
        conflicts = detect_conflicts([a, b])
        assert len(conflicts) == 1

    def test_superseded_duplicates_are_not_compared(self):
        a = make_record("a", "Pre-existing conditions are covered after 30 days.")
        b = make_record("b", "Pre-existing conditions are covered after 24 months.")
        a.duplicate_of = "z"
        assert detect_conflicts([a, b]) == []

    def test_both_records_record_the_other(self):
        a = make_record("a", "Pre-existing conditions are covered after 30 days.",
                        authority="promotional")
        b = make_record("b", "Pre-existing conditions are covered after 24 months.",
                        authority="binding")
        detect_conflicts([a, b])
        assert "b" in a.conflicts_with
        assert "a" in b.conflicts_with
