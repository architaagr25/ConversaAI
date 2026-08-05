"""
Pulls usable content out of HTML pages.

Two things make this harder than it sounds. Most of a page is furniture -
navigation, cookie banners, footers - and it repeats identically across every
page, so keeping it produces dozens of records that all match any query. And a
heading with nothing under it looks like content until someone asks about it.

Sections are split on headings so a record can cite the exact part of the page
it came from, rather than pointing at a URL and leaving the reader to search.

    python -m knowledge_base.web
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from core.config import PROJECT_ROOT
from core.timing import track
from knowledge_base.models import Section, now_iso

log = logging.getLogger(__name__)

RAW_WEB = PROJECT_ROOT / "data" / "raw" / "web"
PROCESSED = PROJECT_ROOT / "data" / "processed"

# Removed before anything else runs. Site furniture is identical on every page,
# so keeping it means every page matches every query.
BOILERPLATE_SELECTORS = [
    "script", "style", "noscript", "iframe", "svg",
    "nav", "header", "footer", "aside",
    "#cookie-banner", ".banner", ".breadcrumb", ".site-header", ".site-footer",
    ".utility", ".footer-columns", ".copyright",
    # Common on real sites
    ".navbar", ".menu", ".sidebar", ".advertisement", ".cookie-consent",
    "[role=navigation]", "[role=banner]", "[role=contentinfo]",
    # Wikipedia furniture, which is heavy and would otherwise dominate
    ".mw-editsection", ".reflist", ".navbox", ".vector-menu", ".mw-jump-link",
    "#toc", ".toc", ".catlinks", ".printfooter", ".hatnote", ".infobox",
    "#mw-navigation", "#footer", ".mw-indicators", ".shortdescription",
    "sup.reference",
]

# Where the real content usually lives, best guess first.
CONTENT_ROOTS = ["main", "article", "#mw-content-text", "#content", ".content", "body"]

HEADINGS = {"h1", "h2", "h3", "h4"}
BLOCKS = {"p", "ul", "ol", "table", "blockquote", "dl"}

# Below this a section is a stub, not an answer.
MIN_SECTION_CHARS = 40

USER_AGENT = (
    "ConversaAI-KB/1.0 (knowledge base extraction; contact archita.agrawal25@gmail.com)"
)


@dataclass
class PageReport:
    """What happened to one page, whether or not it worked."""

    origin: str
    ok: bool
    raw_chars: int = 0
    kept_chars: int = 0
    sections: int = 0
    dropped_empty: int = 0
    method: str = ""
    error: str = ""

    @property
    def stripped_pct(self) -> float:
        if not self.raw_chars:
            return 0.0
        return 1 - (self.kept_chars / self.raw_chars)


def _clean_text(text: str) -> str:
    text = re.sub(r"\[\d+\]", "", text)          # citation markers
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _table_to_text(node) -> str:
    """Flatten a table into one line per row.

    Cell boundaries are kept as pipes. Without them "Essential 1,180 2,340"
    reads as three unrelated numbers, and the rate table becomes useless.
    """
    rows = []
    for tr in node.css("tr"):
        cells = [c.text(strip=True) for c in tr.css("th, td")]
        cells = [c for c in cells if c]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _has_ancestor(node, tags: set[str]) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.tag in tags:
            return True
        parent = parent.parent
    return False


def _in_document_order(node):
    """Depth-first walk, yielding nodes in the order they appear in the page.

    css() with a comma-separated selector groups its results by selector rather
    than by position, so every heading comes back before any paragraph. Section
    splitting needs real document order or the content all lands under the last
    heading on the page.
    """
    for child in node.iter(include_text=False):
        yield child
        yield from _in_document_order(child)


def strip_boilerplate(tree: HTMLParser) -> None:
    for selector in BOILERPLATE_SELECTORS:
        try:
            for node in tree.css(selector):
                node.decompose()
        except Exception:
            # An unsupported selector should not stop the whole extraction.
            continue


def find_content_root(tree: HTMLParser):
    for selector in CONTENT_ROOTS:
        node = tree.css_first(selector)
        if node is not None and len(node.text(strip=True)) > 200:
            return node
    return tree.body


def split_sections(root, page_title: str) -> tuple[list[tuple[int, str, str]], int]:
    """Split content into (level, heading, text) tuples.

    Returns the sections and how many were dropped for having a heading but no
    content. That count matters: those are the records that would look like a
    working extraction and then answer nothing.
    """
    if root is None:
        return [], 0

    current_title = page_title
    current_level = 1
    buffer: list[str] = []
    sections: list[tuple[int, str, str]] = []
    dropped = 0

    def flush() -> None:
        nonlocal buffer, dropped
        body = _clean_text("\n".join(b for b in buffer if b.strip()))
        if len(body) >= MIN_SECTION_CHARS:
            sections.append((current_level, current_title, body))
        elif current_title:
            dropped += 1
        buffer = []

    wanted = HEADINGS | BLOCKS
    for node in _in_document_order(root):
        tag = node.tag
        if tag not in wanted:
            continue

        if tag in HEADINGS:
            flush()
            current_title = _clean_text(node.text(strip=True))
            current_level = int(tag[1])
            continue

        # A paragraph inside a list or table is already covered by its parent.
        if _has_ancestor(node, BLOCKS):
            continue

        if tag == "table":
            # Rows stay on separate lines; the pipes carry the column meaning.
            text = _table_to_text(node)
        else:
            # Source files wrap paragraphs across lines. Those breaks are
            # formatting, not structure, and they survive into the content and
            # then into chunk boundaries unless flattened here.
            text = re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip()

        if text:
            buffer.append(text)

    flush()
    return sections, dropped


def extract_html(
    html: str, origin: str, source_type: str = "web_page"
) -> tuple[list[Section], PageReport]:
    """Turn one page into sections, reporting what was thrown away."""
    report = PageReport(origin=origin, ok=False, raw_chars=len(html))

    try:
        tree = HTMLParser(html)
    except Exception as exc:
        report.error = f"could not parse: {type(exc).__name__}"
        return [], report

    lang = "en"
    html_node = tree.css_first("html")
    if html_node is not None:
        lang = (html_node.attributes.get("lang") or "en").split("-")[0]

    title_node = tree.css_first("title")
    page_title = _clean_text(title_node.text(strip=True)) if title_node else origin
    page_title = re.split(r"\s*[|]\s*", page_title)[0].strip()

    strip_boilerplate(tree)
    root = find_content_root(tree)

    method = "structural"
    raw_sections, dropped = split_sections(root, page_title)

    # Fall back only when the structural pass found essentially nothing, which
    # means the page is built in a way these selectors do not match. Readability
    # extraction returns one blob with no headings, so it costs the per-section
    # citations - not worth taking from a short page that parsed correctly.
    structural_chars = sum(len(text) for _, _, text in raw_sections)
    if not raw_sections or structural_chars < 200:
        try:
            import trafilatura

            fallback = trafilatura.extract(html, include_tables=True, favor_recall=True)
            if fallback and len(fallback) > 300:
                raw_sections = [(1, page_title, _clean_text(fallback))]
                method = "readability"
        except Exception as exc:
            log.warning("readability fallback failed", extra={"origin": origin,
                                                             "reason": str(exc)[:80]})

    sections = [
        Section(
            source_type=source_type,
            source_ref=f"{origin}#{title}" if title else origin,
            source_origin=origin,
            title=title,
            content=text,
            heading_level=level,
            language=lang,
            retrieved_at=now_iso(),
            extraction_method=method,
        )
        for level, title, text in raw_sections
    ]

    report.ok = bool(sections)
    report.sections = len(sections)
    report.dropped_empty = dropped
    report.kept_chars = sum(s.char_count for s in sections)
    report.method = method
    if not sections:
        report.error = "no usable content found"
    return sections, report


def fetch(url: str, timeout: float = 20.0) -> str | None:
    """Fetch a page, returning None rather than raising if it cannot be had."""
    import httpx

    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en,id"},
        ) as client:
            response = client.get(url)
            if response.status_code != 200:
                log.warning("fetch rejected", extra={"url": url,
                                                     "status": response.status_code})
                return None
            return response.text
    except Exception as exc:
        log.warning("fetch failed", extra={"url": url, "reason": str(exc)[:100]})
        return None


def extract_local(directory: Path = RAW_WEB) -> tuple[list[Section], list[PageReport]]:
    sections: list[Section] = []
    reports: list[PageReport] = []

    for path in sorted(directory.glob("*.html")):
        origin = f"web/{path.name}"
        try:
            html = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            reports.append(PageReport(origin=origin, ok=False,
                                      error=f"unreadable: {type(exc).__name__}"))
            continue

        with track("extract_page", detail=origin):
            found, report = extract_html(html, origin)
        sections.extend(found)
        reports.append(report)

    return sections, reports


def extract_remote(urls: list[str], delay: float = 1.0) -> tuple[list[Section], list[PageReport]]:
    """Extract from live pages.

    The local corpus was written for this pipeline, so it proves very little on
    its own. These are pages nobody shaped to be convenient.
    """
    sections: list[Section] = []
    reports: list[PageReport] = []

    for index, url in enumerate(urls):
        if index:
            time.sleep(delay)  # don't hammer someone else's server

        html = fetch(url)
        if html is None:
            reports.append(PageReport(origin=url, ok=False, error="fetch failed"))
            continue

        origin = f"{urlparse(url).netloc}{urlparse(url).path}"
        with track("extract_page", detail=origin):
            found, report = extract_html(html, origin)
        report.origin = url
        sections.extend(found)
        reports.append(report)

    return sections, reports


def write_jsonl(sections: list[Section], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for section in sections:
            handle.write(json.dumps(asdict(section), ensure_ascii=False) + "\n")


def print_report(title: str, reports: list[PageReport]) -> None:
    print(f"\n{title}")
    print("-" * 96)
    print(f"{'page':<44}{'raw':>8}{'kept':>8}{'stripped':>10}{'sections':>10}{'empty':>7}{'method':>12}")
    print("-" * 96)
    for r in reports:
        origin = r.origin if len(r.origin) <= 42 else "..." + r.origin[-39:]
        if not r.ok:
            print(f"{origin:<44}{'':>8}{'':>8}{'':>10}{'FAILED':>10}  {r.error[:28]}")
            continue
        print(f"{origin:<44}{r.raw_chars:>8}{r.kept_chars:>8}"
              f"{r.stripped_pct:>9.0%}{r.sections:>10}{r.dropped_empty:>7}{r.method:>12}")


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    print("=" * 96)
    print("Web extraction")
    print("=" * 96)

    local_sections, local_reports = extract_local()
    print_report("Local corpus", local_reports)

    # Public pages, to show the extractor works on markup nobody wrote for it.
    # One is not English, which is where heading detection usually breaks.
    real_urls = [
        "https://en.wikipedia.org/wiki/Health_insurance",
        "https://en.wikipedia.org/wiki/Life_insurance",
        "https://id.wikipedia.org/wiki/Asuransi",
    ]
    remote_sections, remote_reports = extract_remote(real_urls)
    print_report("Live public pages", remote_reports)

    all_sections = local_sections + remote_sections
    write_jsonl(local_sections, PROCESSED / "web_sections.jsonl")
    write_jsonl(remote_sections, PROCESSED / "web_sections_public.jsonl")

    ok = [r for r in local_reports + remote_reports if r.ok]
    failed = [r for r in local_reports + remote_reports if not r.ok]
    raw_total = sum(r.raw_chars for r in ok)
    kept_total = sum(r.kept_chars for r in ok)

    print(f"\n{'=' * 96}")
    print(f"  pages processed    {len(ok)} ok, {len(failed)} failed")
    print(f"  sections kept      {len(all_sections)}")
    print(f"  empty dropped      {sum(r.dropped_empty for r in ok)}")
    if raw_total:
        print(f"  boilerplate removed {1 - kept_total / raw_total:.0%} "
              f"({raw_total - kept_total:,} of {raw_total:,} chars)")
    print(f"\n  written to data/processed/web_sections.jsonl and _public.jsonl")

    for r in failed:
        print(f"  FAILED  {r.origin}: {r.error}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
