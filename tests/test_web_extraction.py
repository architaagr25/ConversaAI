"""
Extraction tests.

Most of these guard against silent wrongness rather than crashes: content that
lands under the wrong heading, boilerplate that survives, a heading stored with
nothing under it. All of those produce output that looks fine.
"""

from __future__ import annotations

from knowledge_base.web import (
    MIN_SECTION_CHARS,
    extract_html,
    split_sections,
    strip_boilerplate,
)
from selectolax.parser import HTMLParser

PAGE = """
<!DOCTYPE html>
<html lang="en">
<head><title>Plans | Solara</title></head>
<body>
  <div id="cookie-banner"><p>We use cookies.</p><button>Accept all</button></div>
  <header class="site-header"><nav class="primary"><ul>
    <li><a href="/a">Health</a></li><li><a href="/b">Login</a></li></ul></nav></header>
  <div class="breadcrumb">Home &rsaquo; Plans</div>
  <main>
    <h1>Health Shield</h1>
    <p>Cover for hospital treatment and day surgery at accredited hospitals across
       the Philippines, for individuals and for families on a single plan.</p>
    <h2>Waiting periods</h2>
    <p>Illnesses are covered after 30 days. Pre-existing conditions after 24 months,
       provided the condition was declared when the application was made.</p>
    <h2>Special offers</h2>
    <h2>Premiums</h2>
    <table><tr><th>Age</th><th>Essential</th></tr><tr><td>18 to 25</td><td>1,180</td></tr></table>
    <p>Premiums depend on age at entry, the plan chosen, and any riders attached
       to the policy at application or at renewal.</p>
    <h2>Riders</h2>
    <ul><li>Maternity cover for growing families, after a ten month waiting period</li>
        <li>Critical illness lump sum on diagnosis of a listed condition</li>
        <li>Daily hospital cash for every night spent as an in-patient</li></ul>
  </main>
  <footer class="site-footer"><p>All rights reserved. Registered office Taguig.</p></footer>
</body>
</html>
"""


class TestBoilerplate:
    def test_furniture_is_removed(self):
        tree = HTMLParser(PAGE)
        strip_boilerplate(tree)
        remaining = tree.body.text()
        for phrase in ("We use cookies", "Accept all", "Login", "All rights reserved"):
            assert phrase not in remaining

    def test_real_content_survives(self):
        tree = HTMLParser(PAGE)
        strip_boilerplate(tree)
        assert "accredited hospitals" in tree.body.text()

    def test_an_unknown_selector_does_not_stop_extraction(self):
        sections, report = extract_html("<html><body><h1>T</h1><p>" + "x" * 60
                                        + "</p></body></html>", "test")
        assert report.ok


class TestSectionSplitting:
    def _sections(self):
        tree = HTMLParser(PAGE)
        strip_boilerplate(tree)
        return split_sections(tree.css_first("main"), "Plans")

    def test_content_lands_under_its_own_heading(self):
        # The failure this guards against: css() with a comma-separated selector
        # returns results grouped by selector, not in document order, so every
        # heading is seen before any paragraph and the whole page ends up under
        # whichever heading came last.
        sections, _ = self._sections()
        by_title = {title: text for _, title, text in sections}
        assert "30 days" in by_title["Waiting periods"]
        assert "30 days" not in by_title.get("Riders", "")

    def test_every_heading_with_content_becomes_a_section(self):
        sections, _ = self._sections()
        titles = {title for _, title, _ in sections}
        assert {"Health Shield", "Waiting periods", "Premiums", "Riders"} <= titles

    def test_a_heading_with_nothing_under_it_is_dropped(self):
        sections, dropped = self._sections()
        assert "Special offers" not in {title for _, title, _ in sections}
        assert dropped >= 1

    def test_heading_level_is_kept(self):
        sections, _ = self._sections()
        levels = {title: level for level, title, _ in sections}
        assert levels["Health Shield"] == 1
        assert levels["Waiting periods"] == 2

    def test_list_items_are_captured_once(self):
        sections, _ = self._sections()
        riders = next(text for _, title, text in sections if title == "Riders")
        assert riders.count("Maternity cover") == 1

    def test_paragraphs_are_not_broken_by_source_line_wrapping(self):
        # Source files wrap paragraphs across lines. Those breaks are formatting
        # and would otherwise end up inside chunk boundaries.
        html = "<html><body><main><h1>T</h1><p>One sentence\n    split across\n" \
               "    three source lines here.</p></main></body></html>"
        sections, _ = extract_html(html, "test")
        assert "\n" not in sections[0].content


class TestTables:
    def test_columns_keep_their_boundaries(self):
        # "18 to 25 1,180" reads as two unrelated numbers without the separator.
        sections, _ = extract_html(PAGE, "plans.html")
        premiums = next(s for s in sections if s.title == "Premiums")
        assert "Age | Essential" in premiums.content
        assert "18 to 25 | 1,180" in premiums.content

    def test_rows_stay_on_separate_lines(self):
        sections, _ = extract_html(PAGE, "plans.html")
        premiums = next(s for s in sections if s.title == "Premiums")
        assert len(premiums.content.splitlines()) >= 2


class TestPageExtraction:
    def test_reports_what_was_stripped(self):
        sections, report = extract_html(PAGE, "plans.html")
        assert report.ok
        assert report.kept_chars < report.raw_chars
        assert report.stripped_pct > 0.3

    def test_source_reference_points_at_the_heading(self):
        sections, _ = extract_html(PAGE, "plans.html")
        waiting = next(s for s in sections if s.title == "Waiting periods")
        assert waiting.source_ref == "plans.html#Waiting periods"

    def test_language_is_taken_from_the_page(self):
        sections, _ = extract_html(PAGE.replace('lang="en"', 'lang="id-ID"'), "x")
        assert sections[0].language == "id"

    def test_empty_page_fails_rather_than_returning_a_blank_record(self):
        sections, report = extract_html("<html><body></body></html>", "empty.html")
        assert sections == []
        assert not report.ok
        assert report.error

    def test_malformed_html_does_not_raise(self):
        sections, report = extract_html("<html><body><p>unclosed", "broken.html")
        assert isinstance(report.ok, bool)

    def test_short_sections_are_dropped(self):
        html = f"<html><body><main><h1>T</h1><p>{'x' * (MIN_SECTION_CHARS - 5)}</p>" \
               "</main></body></html>"
        sections, _ = extract_html(html, "short.html")
        assert sections == []
