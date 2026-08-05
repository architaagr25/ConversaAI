"""
Knowledge base build tests.

Identifier stability and version stability carry the weight here. Both fail
silently: renumbered records still answer questions, they just answer them
under a different name than the citation given out last week, and a version
that moves on every rebuild makes the audit trail worthless without ever
raising anything.
"""

from __future__ import annotations

import sqlite3

import pytest

from knowledge_base.store import (
    MAX_CHUNK_CHARS,
    BuildReport,
    chunk_text,
    content_hash,
    expand_to_chunks,
    stable_id,
)
import knowledge_base.store as store


def a_record(**overrides) -> dict:
    base = {
        "record_id": "seed", "title": "Waiting periods",
        "content": "Illnesses are covered after 30 days from the commencement date.",
        "category": "policy_rule", "business_unit": "health_ph_en",
        "authority": "published", "source_type": "web_page",
        "source_ref": "web/plans.html#Waiting periods",
        "source_origin": "web/plans.html",
        "source_retrieved_at": "2026-03-01T00:00:00+00:00",
        "language": "en", "pii": False, "pii_types": [],
        "terminology_variants": [], "conflicts_with": [], "quality_flags": [],
        "duplicate_of": "", "char_count": 62,
    }
    base.update(overrides)
    return base


class TestStableIdentifiers:
    def test_the_same_source_gives_the_same_id(self):
        assert stable_id("web/plans.html#Waiting", 0) == \
               stable_id("web/plans.html#Waiting", 0)

    def test_a_different_source_gives_a_different_id(self):
        assert stable_id("web/plans.html#Waiting", 0) != \
               stable_id("web/faq.html#Waiting", 0)

    def test_chunks_of_one_section_are_distinguishable(self):
        assert stable_id("web/plans.html#Waiting", 0) != \
               stable_id("web/plans.html#Waiting", 1)

    def test_position_in_the_corpus_does_not_affect_the_id(self):
        # Numbering by processing order means adding one page renumbers
        # everything after it and breaks every citation already given out.
        first_run = [stable_id(r, 0) for r in ("a#x", "b#y", "c#z")]
        after_insertion = [stable_id(r, 0) for r in ("a#x", "NEW#n", "b#y", "c#z")]
        assert first_run[1] in after_insertion
        assert first_run[2] in after_insertion


class TestContentHash:
    def test_formatting_does_not_change_the_hash(self):
        assert content_hash("Thirty  DAYS apply.") == content_hash("thirty days apply")

    def test_different_content_hashes_differently(self):
        assert content_hash("30 days") != content_hash("24 months")


class TestChunking:
    def test_short_text_is_left_whole(self):
        assert chunk_text("One short clause.") == ["One short clause."]

    def test_long_text_is_split(self):
        text = " ".join(f"Sentence {i} with enough words to take up room."
                        for i in range(200))
        assert len(chunk_text(text)) > 1

    def test_every_chunk_is_within_the_limit(self):
        text = " ".join(f"Sentence {i} with enough words to take up room."
                        for i in range(200))
        assert all(len(c) <= MAX_CHUNK_CHARS for c in chunk_text(text))

    def test_nothing_is_lost_in_the_split(self):
        text = " ".join(f"Sentence {i} with enough words to take up room."
                        for i in range(200))
        joined = " ".join(chunk_text(text))
        assert all(word in joined for word in text.split())

    def test_chunks_overlap_so_a_fact_on_a_boundary_survives(self):
        text = " ".join(f"Sentence number {i} carries filler to give it length."
                        for i in range(1, 60))
        chunks = chunk_text(text, max_chars=400, overlap=120)
        tail = chunks[0].split(".")[-2].strip()
        assert tail.split()[-1] in chunks[1][:200]

    def test_table_rows_are_never_cut_in_half(self):
        # A row split across chunks loses the column it belongs to, which turns
        # a premium figure into a loose number.
        rows = "\n".join(f"Age band {i} | {1000 + i} | {2000 + i} | {3000 + i}"
                         for i in range(80))
        chunks = chunk_text(rows, max_chars=300)
        for chunk in chunks:
            for line in chunk.split("\n"):
                if line.strip():
                    assert line.count("|") == 3

    def test_a_short_trailing_fragment_is_folded_back(self):
        text = " ".join(f"Sentence {i} here." for i in range(60)) + " End."
        chunks = chunk_text(text, max_chars=300, overlap=50)
        assert all(len(c) > 50 for c in chunks)

    def test_empty_input(self):
        assert chunk_text("") == [""]


class TestExpansion:
    def test_a_short_record_stays_one_record(self):
        report = BuildReport()
        out = expand_to_chunks([a_record()], report)
        assert len(out) == 1
        assert out[0]["chunk_count"] == 1
        assert report.chunked == 0

    def test_a_long_record_becomes_several(self):
        report = BuildReport()
        long_text = " ".join(f"Clause {i} with sufficient length to matter."
                             for i in range(200))
        out = expand_to_chunks([a_record(content=long_text)], report)
        assert len(out) > 1
        assert {r["chunk_count"] for r in out} == {len(out)}
        assert [r["chunk_index"] for r in out] == list(range(len(out)))

    def test_each_chunk_hashes_its_own_content(self):
        report = BuildReport()
        long_text = " ".join(f"Clause {i} with sufficient length to matter."
                             for i in range(200))
        out = expand_to_chunks([a_record(content=long_text)], report)
        assert len({r["content_hash"] for r in out}) == len(out)

    def test_a_superseded_duplicate_is_stored_but_not_searchable(self):
        report = BuildReport()
        out = expand_to_chunks([a_record(duplicate_of="kb_other")], report)
        assert out[0]["retrievable"] == 0
        assert report.superseded == 1

    def test_a_live_record_is_searchable(self):
        out = expand_to_chunks([a_record()], BuildReport())
        assert out[0]["retrievable"] == 1


class TestVersioning:
    @pytest.fixture
    def temp_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "KB_DIR", tmp_path)
        monkeypatch.setattr(store, "DB_PATH", tmp_path / "kb.sqlite")
        monkeypatch.setattr(store, "JSONL_PATH", tmp_path / "records.jsonl")
        return tmp_path

    def _build(self, records):
        report = BuildReport()
        expanded = expand_to_chunks(records, report)
        store.write_store(expanded, report)
        return report, expanded

    def test_a_first_build_marks_everything_new(self, temp_store):
        report, _ = self._build([a_record()])
        assert report.new == 1 and report.changed == 0

    def test_rebuilding_unchanged_sources_leaves_versions_alone(self, temp_store):
        self._build([a_record()])
        report, expanded = self._build([a_record()])
        # Without this every rebuild looks like an edit and the history is
        # worthless.
        assert report.unchanged == 1 and report.changed == 0
        assert expanded[0]["version"] == 1

    def test_changed_content_moves_the_version(self, temp_store):
        self._build([a_record()])
        _, expanded = self._build(
            [a_record(content="Illnesses are covered after 45 days now.")])
        assert expanded[0]["version"] == 2

    def test_the_original_date_survives_a_change(self, temp_store):
        _, first = self._build([a_record()])
        original = first[0]["first_seen"]
        _, second = self._build(
            [a_record(content="Illnesses are covered after 45 days now.")])
        assert second[0]["first_seen"] == original

    def test_an_unchanged_record_keeps_its_last_updated_date(self, temp_store):
        # Stamping every record on every rebuild makes the audit trail useless
        # without ever raising anything.
        _, first = self._build([a_record()])
        was = first[0]["last_updated"]
        _, second = self._build([a_record()])
        assert second[0]["last_updated"] == was


    def test_the_store_is_queryable(self, temp_store):
        self._build([a_record(), a_record(source_ref="web/faq.html#Grace")])
        connection = sqlite3.connect(store.DB_PATH)
        count = connection.execute(
            "SELECT COUNT(*) FROM records WHERE retrievable = 1").fetchone()[0]
        connection.close()
        assert count == 2

    def test_list_fields_survive_the_round_trip(self, temp_store):
        self._build([a_record(terminology_variants=["hulog", "bayad"],
                              conflicts_with=["kb_x"], pii=True,
                              pii_types=["EMAIL"])])
        connection = sqlite3.connect(store.DB_PATH)
        row = connection.execute(
            "SELECT terminology_variants, conflicts_with, pii, pii_types "
            "FROM records").fetchone()
        connection.close()
        assert "hulog" in row[0] and "kb_x" in row[1]
        assert row[2] == 1 and "EMAIL" in row[3]


class TestCrossReferences:
    def test_conflict_pointers_are_rewritten_to_the_new_identifiers(self):
        # Earlier stages number records by position. Those numbers do not
        # survive here, and a pointer to one that no longer exists looks like a
        # working link.
        records = [
            a_record(record_id="kb_product_003", source_ref="web/campaign.html#Cover",
                     conflicts_with=["kb_policy_rule_071"]),
            a_record(record_id="kb_policy_rule_071", source_ref="documents/p.pdf#2",
                     conflicts_with=["kb_product_003"]),
        ]
        campaign, policy = expand_to_chunks(records, BuildReport())
        assert campaign["conflicts_with"] == [policy["record_id"]]
        assert policy["conflicts_with"] == [campaign["record_id"]]
        assert not any(c.startswith("kb_product_") for c in campaign["conflicts_with"])

    def test_duplicate_pointers_are_rewritten_too(self):
        records = [
            a_record(record_id="kb_corporate_006", source_ref="web/a.html#About",
                     duplicate_of="kb_corporate_021"),
            a_record(record_id="kb_corporate_021", source_ref="web/b.html#About"),
        ]
        out = expand_to_chunks(records, BuildReport())
        assert out[0]["duplicate_of"] == out[1]["record_id"]

    def test_an_unknown_pointer_is_left_alone(self):
        records = [a_record(conflicts_with=["kb_from_somewhere_else"])]
        out = expand_to_chunks(records, BuildReport())
        assert out[0]["conflicts_with"] == ["kb_from_somewhere_else"]
