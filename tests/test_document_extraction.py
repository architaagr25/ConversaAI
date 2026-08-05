"""
Document parsing tests.

The cases that matter here are the ones that produce wrong content rather than
no content: a table cut in half by a page break, a running footer read as a
clause, and figures appearing twice with a column silently shifted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import PROJECT_ROOT
from knowledge_base.documents import (
    Line,
    extract_form,
    extract_pdf,
    extract_rules,
    find_furniture,
    group_words_into_lines,
    heading_above,
    rejoin_split_tables,
    split_document,
    table_to_text,
)

DOCS = PROJECT_ROOT / "data" / "raw" / "documents"


def word(text: str, top: float, x0: float = 0.0) -> dict:
    return {"text": text, "top": top, "bottom": top + 10, "x0": x0, "x1": x0 + 20}


class TestLineGrouping:
    def test_words_at_the_same_height_form_one_line(self):
        lines = group_words_into_lines(
            [word("Reference", 700, 20), word("Confidential", 700, 200),
             word("Page", 700, 400)], 1)
        assert len(lines) == 1
        assert lines[0].text == "Reference Confidential Page"

    def test_words_at_different_heights_stay_apart(self):
        lines = group_words_into_lines([word("Top", 10), word("Bottom", 700)], 1)
        assert len(lines) == 2

    def test_words_are_ordered_left_to_right(self):
        lines = group_words_into_lines([word("second", 10, 300), word("first", 10, 20)], 1)
        assert lines[0].text == "first second"


class TestFurniture:
    def _pages(self, footers: list[str]) -> tuple[list[list[Line]], list[float]]:
        pages = []
        for index, footer in enumerate(footers, 1):
            pages.append([
                Line("Solara Finance Group", 5, 15, index),
                Line("A binding clause that appears only on this page.", 300, 310, index),
                Line(footer, 780, 790, index),
            ])
        return pages, [800.0] * len(footers)

    def test_a_header_repeating_on_every_page_is_removed(self):
        pages, heights = self._pages(["ref A Page 1", "ref A Page 2"])
        assert "Solara Finance Group" in find_furniture(pages, heights)

    def test_a_footer_is_caught_despite_its_page_number(self):
        # The page number makes every footer unique, so counting exact text
        # finds nothing. Counting by shape finds it.
        pages, heights = self._pages(["SHS-PW-2026-03 Confidential Page 1",
                                      "SHS-PW-2026-03 Confidential Page 2"])
        furniture = find_furniture(pages, heights)
        assert "SHS-PW-2026-03 Confidential Page 1" in furniture
        assert "SHS-PW-2026-03 Confidential Page 2" in furniture

    def test_body_content_is_never_removed(self):
        pages, heights = self._pages(["ref A Page 1", "ref A Page 2"])
        furniture = find_furniture(pages, heights)
        assert "A binding clause that appears only on this page." not in furniture

    def test_a_single_page_document_still_loses_its_footer(self):
        pages, heights = self._pages(["SMI-KP-2026-03 Confidential Page 1"])
        assert "SMI-KP-2026-03 Confidential Page 1" in find_furniture(pages, heights)

    def test_a_repeated_sentence_in_the_body_survives(self):
        # Repetition alone is not enough; it has to be in the margin band.
        pages = [[Line("This clause is repeated verbatim.", 400, 410, i)]
                 for i in (1, 2, 3)]
        assert find_furniture(pages, [800.0] * 3) == set()


class TestTableRejoining:
    HEADER = ["Assessment", "Loading"]

    def test_a_table_split_by_a_page_break_is_rejoined(self):
        tables = [
            (1, 500.0, [self.HEADER, ["Standard", "0%"], ["Mild", "25%"]]),
            (2, 100.0, [self.HEADER, ["Severe", "100%"]]),
        ]
        joined, rejoins = rejoin_split_tables(tables)
        assert rejoins == 1
        assert len(joined) == 1
        rows = joined[0][2]
        assert rows[0] == self.HEADER
        assert ["Severe", "100%"] in rows
        assert sum(1 for r in rows if r == self.HEADER) == 1  # header not duplicated

    def test_the_joined_table_keeps_the_first_fragments_position(self):
        tables = [(1, 500.0, [self.HEADER, ["a", "1"]]),
                  (2, 100.0, [self.HEADER, ["b", "2"]])]
        joined, _ = rejoin_split_tables(tables)
        assert joined[0][0] == 1
        assert joined[0][1] == 500.0

    def test_tables_with_different_widths_are_left_alone(self):
        tables = [(1, 500.0, [["A", "B"], ["1", "2"]]),
                  (2, 100.0, [["A", "B", "C"], ["1", "2", "3"]])]
        _, rejoins = rejoin_split_tables(tables)
        assert rejoins == 0

    def test_tables_on_non_adjacent_pages_are_left_alone(self):
        tables = [(1, 500.0, [self.HEADER, ["a", "1"]]),
                  (4, 100.0, [self.HEADER, ["b", "2"]])]
        _, rejoins = rejoin_split_tables(tables)
        assert rejoins == 0

    def test_an_empty_list_is_handled(self):
        assert rejoin_split_tables([]) == ([], 0)


class TestTableText:
    def test_columns_keep_their_boundaries(self):
        text = table_to_text([["Age", "Essential"], ["18 to 25", "1,180"]])
        assert text == "Age | Essential\n18 to 25 | 1,180"

    def test_blank_cells_are_dropped_not_shifted(self):
        text = table_to_text([["A", "", "C"]])
        assert text == "A | C"


class TestHeadingAbove:
    def test_a_numbered_clause_is_found(self):
        pages = [[Line("Pasal 3: Tahapan Penagihan", 100, 110, 1)]]
        assert heading_above(pages, 1, 200, set()) == "Pasal 3 Tahapan Penagihan"

    def test_a_single_word_heading_counts(self):
        pages = [[Line("Loadings", 100, 110, 1)]]
        assert heading_above(pages, 1, 200, set()) == "Loadings"

    def test_a_sentence_is_not_treated_as_a_heading(self):
        pages = [[Line("This paragraph explains the table below.", 100, 110, 1)]]
        assert heading_above(pages, 1, 200, set()) == ""

    def test_lines_below_the_table_are_ignored(self):
        pages = [[Line("Loadings", 300, 310, 1)]]
        assert heading_above(pages, 1, 200, set()) == ""

    def test_furniture_is_never_used_as_a_heading(self):
        pages = [[Line("Confidential", 100, 110, 1)]]
        assert heading_above(pages, 1, 200, {"Confidential"}) == ""


class TestClauseSplitting:
    def test_numbered_clauses_become_sections(self):
        text = ("1. Definitions\nA definition long enough to be kept as content here.\n"
                "2. Waiting periods\nAnother clause with sufficient length to survive.")
        sections = split_document(text, "Doc")
        titles = [t for t, _ in sections]
        assert "1 Definitions" in titles
        assert "2 Waiting periods" in titles

    def test_indonesian_articles_become_sections(self):
        text = ("Pasal 1: Kewajiban Pembayaran\nNasabah wajib membayar angsuran "
                "setiap bulan pada tanggal jatuh tempo.")
        sections = split_document(text, "Doc")
        assert sections[0][0] == "Pasal 1 Kewajiban Pembayaran"


class TestRealDocuments:
    def test_a_damaged_file_is_reported_not_raised(self):
        sections, report = extract_pdf(DOCS / "health_shield_annex_damaged.pdf")
        assert sections == []
        assert not report.ok
        assert report.error
        assert "unreadable_source" in report.flags

    def test_a_missing_file_is_reported_not_raised(self):
        sections, report = extract_pdf(DOCS / "does_not_exist.pdf")
        assert sections == []
        assert not report.ok

    @pytest.mark.skipif(not (DOCS / "health_shield_rate_table.pdf").exists(),
                        reason="sample documents not built")
    def test_rate_table_figures_are_not_duplicated_as_prose(self):
        # The same numbers appearing twice, once correctly and once with a
        # column shifted, is worse than not having them at all.
        sections, _ = extract_pdf(DOCS / "health_shield_rate_table.pdf")
        prose = " ".join(s.content for s in sections if s.source_type == "pdf_policy")
        assert "1,180" not in prose
        tables = [s for s in sections if s.source_type == "pdf_table"]
        assert any("1,180" in s.content for s in tables)

    @pytest.mark.skipif(not (DOCS / "health_shield_policy_wording.pdf").exists(),
                        reason="sample documents not built")
    def test_running_footer_does_not_appear_in_any_clause(self):
        sections, _ = extract_pdf(DOCS / "health_shield_policy_wording.pdf")
        joined = " ".join(s.content for s in sections)
        assert "Confidential" not in joined
        assert "Page 2" not in joined


class TestFormExport:
    def test_no_customer_records_are_imported(self):
        path = PROJECT_ROOT / "data" / "raw" / "forms" / "lead_form_export.csv"
        sections, report = extract_form(path)
        joined = " ".join(s.content for s in sections)
        for personal in ("Maria Clara Santos", "budi.santoso@example.id",
                         "+63 917 555 0142", "3273051203880004"):
            assert personal not in joined
        assert any("rows_excluded" in f for f in report.flags)

    def test_the_form_shape_is_captured(self):
        path = PROJECT_ROOT / "data" / "raw" / "forms" / "lead_form_export.csv"
        sections, _ = extract_form(path)
        assert any("form fields" in s.title.lower() for s in sections)


class TestRules:
    def test_escalation_triggers_become_sections(self):
        path = PROJECT_ROOT / "data" / "raw" / "rules" / "qualification_rules.yaml"
        sections, report = extract_rules(path)
        assert report.ok
        assert any("Escalation" in s.title for s in sections)

    def test_prohibited_actions_are_captured(self):
        path = PROJECT_ROOT / "data" / "raw" / "rules" / "qualification_rules.yaml"
        sections, _ = extract_rules(path)
        assert any("never" in s.title for s in sections)

    def test_a_missing_rules_file_is_reported(self):
        sections, report = extract_rules(Path("nowhere.yaml"))
        assert sections == []
        assert not report.ok
