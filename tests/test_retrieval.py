"""
Retrieval tests.

The ranking is where the grounding actually happens: which record the agent
reads decides what it says. These run without a network call, because the
judgement being tested is in the fusion and weighting rather than in the
embedding service.
"""

from __future__ import annotations

import pytest

from knowledge_base.retrieve import (
    AUTHORITY_WEIGHT,
    CONTRADICTION_PENALTY,
    indexable_text,
    rank_candidates,
    tokenise,
)


def a_record(record_id: str, authority: str = "published",
             unit: str = "health_ph_en", flags: list[str] | None = None,
             title: str = "Some heading") -> dict:
    return {
        "record_id": record_id, "title": title,
        "content": "Some content about waiting periods and cover.",
        "category": "policy_rule", "business_unit": unit,
        "authority": authority, "source_ref": f"src#{record_id}",
        "quality_flags": flags or [], "terminology_variants": [],
    }


def outcome_for(records: list[dict], keyword: dict[str, int],
                vector: dict[str, int], similarity: float = 0.8, **kwargs):
    by_id = {r["record_id"]: r for r in records}
    sims = {r["record_id"]: similarity for r in records}
    return rank_candidates("q", keyword, vector, sims, by_id, **kwargs)


class TestTokenise:
    def test_punctuation_is_dropped(self):
        assert tokenise("Pre-existing, 24 months!") == ["pre", "existing", "24", "months"]

    def test_case_is_folded(self):
        assert tokenise("DENDA") == ["denda"]


class TestIndexableText:
    def test_the_heading_is_included(self):
        # A chunk lifted from a long section reads as orphaned text without it.
        record = a_record("a", title="Waiting periods")
        assert "Waiting periods" in indexable_text(record)

    def test_vocabulary_variants_are_included(self):
        # Keyword search cannot leap from hulog to premium on its own.
        record = a_record("a")
        record["terminology_variants"] = ["hulog", "bayad"]
        assert "hulog" in indexable_text(record)


class TestFusion:
    def test_a_record_found_by_both_searches_beats_one_found_by_either(self):
        records = [a_record("both"), a_record("keyword_only"), a_record("vector_only")]
        result = outcome_for(records,
                             keyword={"both": 1, "keyword_only": 0},
                             vector={"both": 1, "vector_only": 0})
        assert result.results[0].record_id == "both"

    def test_a_better_rank_scores_higher(self):
        records = [a_record("first"), a_record("second")]
        result = outcome_for(records, keyword={"first": 0, "second": 5},
                             vector={"first": 0, "second": 5})
        assert result.results[0].record_id == "first"

    def test_records_missing_from_both_rankings_do_not_appear(self):
        records = [a_record("found"), a_record("absent")]
        result = outcome_for(records, keyword={"found": 0}, vector={"found": 0})
        assert [r.record_id for r in result.results] == ["found"]


class TestAuthority:
    def test_a_binding_source_beats_a_better_ranked_published_one(self):
        # The real case: asked whether a 62 year old can apply, a FAQ about
        # adding family outranked the eligibility clause, because the FAQ
        # shares more words with the question.
        records = [a_record("faq", authority="published"),
                   a_record("policy", authority="binding")]
        result = outcome_for(records,
                             keyword={"faq": 0, "policy": 20},
                             vector={"faq": 4, "policy": 3})
        assert result.results[0].record_id == "policy"

    def test_a_promotional_record_is_pushed_down(self):
        records = [a_record("promo", authority="promotional"),
                   a_record("policy", authority="binding")]
        result = outcome_for(records, keyword={"promo": 0, "policy": 3},
                             vector={"promo": 0, "policy": 3})
        assert result.results[0].record_id == "policy"

    def test_a_contradicting_record_is_penalised_further(self):
        records = [a_record("clean", authority="published"),
                   a_record("wrong", authority="published",
                            flags=["contradicts_binding_source"])]
        result = outcome_for(records, keyword={"clean": 1, "wrong": 0},
                             vector={"clean": 1, "wrong": 0})
        assert result.results[0].record_id == "clean"

    def test_the_penalty_is_applied_on_top_of_authority(self):
        assert CONTRADICTION_PENALTY < 1.0
        assert AUTHORITY_WEIGHT["promotional"] < AUTHORITY_WEIGHT["binding"]

    def test_a_contradicting_record_is_still_returned(self):
        # Demoted, not hidden. The agent may need to know the claim exists.
        records = [a_record("wrong", flags=["contradicts_binding_source"])]
        result = outcome_for(records, keyword={"wrong": 0}, vector={"wrong": 0})
        assert [r.record_id for r in result.results] == ["wrong"]


class TestMarketScoping:
    def test_another_market_is_excluded(self):
        # An Indonesian installment question must never reach a Philippine
        # premium answer.
        records = [a_record("ph", unit="health_ph_en"),
                   a_record("id", unit="multifinance_id")]
        result = outcome_for(records, keyword={"ph": 0, "id": 0},
                             vector={"ph": 0, "id": 0},
                             business_unit="multifinance_id")
        assert [r.record_id for r in result.results] == ["id"]

    def test_group_content_applies_everywhere(self):
        records = [a_record("shared", unit="group")]
        result = outcome_for(records, keyword={"shared": 0}, vector={"shared": 0},
                             business_unit="multifinance_id")
        assert result.results

    def test_no_filter_returns_everything(self):
        records = [a_record("ph", unit="health_ph_en"),
                   a_record("id", unit="multifinance_id")]
        result = outcome_for(records, keyword={"ph": 0, "id": 0},
                             vector={"ph": 0, "id": 0})
        assert len(result.results) == 2


class TestConfidence:
    def test_a_strong_match_is_answerable(self):
        records = [a_record("a")]
        result = outcome_for(records, {"a": 0}, {"a": 0}, similarity=0.80)
        assert result.confident and bool(result)

    def test_a_weak_match_is_not(self):
        # Everything below the floor is treated as not knowing, which is the
        # whole defence against answering from the closest thing available.
        records = [a_record("a")]
        result = outcome_for(records, {"a": 0}, {"a": 0}, similarity=0.45)
        assert not result.confident
        assert not result
        assert "below the floor" in result.reason

    def test_nothing_found_is_not_confident(self):
        result = outcome_for([a_record("a")], {}, {})
        assert not result
        assert result.reason == "nothing matched"

    def test_the_weak_results_are_still_returned_for_inspection(self):
        records = [a_record("a")]
        result = outcome_for(records, {"a": 0}, {"a": 0}, similarity=0.45)
        assert result.results
        assert result.best_similarity == pytest.approx(0.45)

    def test_top_k_is_respected(self):
        records = [a_record(f"r{i}") for i in range(10)]
        ranks = {f"r{i}": i for i in range(10)}
        result = outcome_for(records, ranks, ranks, top_k=3)
        assert len(result.results) == 3


class TestCitations:
    def test_every_result_carries_one(self):
        records = [a_record("a")]
        result = outcome_for(records, {"a": 0}, {"a": 0})
        assert result.results[0].citation == "src#a"
