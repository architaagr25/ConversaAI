"""
Pulls content out of PDFs, form exports and rules files.

PDFs need more care than pages. Every page carries the same running header and
footer, which look like content to a parser. Tables lose their meaning if
flattened, and a table long enough to run past the bottom of a page comes back
as two unrelated tables. And a document set always contains at least one file
that will not open, which must be recorded and skipped rather than stopping the
run.

    python -m knowledge_base.documents
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.config import PROJECT_ROOT
from core.timing import track
from knowledge_base.models import Section, now_iso

log = logging.getLogger(__name__)

RAW = PROJECT_ROOT / "data" / "raw"
PROCESSED = PROJECT_ROOT / "data" / "processed"

# A line repeating on at least this share of pages is furniture, not content.
# Two thirds keeps a genuinely repeated clause on a three page document safe
# while still catching a header that appears on every page.
REPEAT_THRESHOLD = 0.66

# Fraction of page height treated as the header and footer bands. Only lines
# inside these are eligible to be dropped as furniture, so a sentence that
# happens to repeat in the body is never removed.
MARGIN_BAND = 0.09

# Words within this many points of each other vertically belong to one line.
LINE_TOLERANCE = 3.0

# Numbered clauses and article headings, in both languages used here.
HEADING_PATTERNS = [
    re.compile(r"^(\d+)\.\s+([A-Z][^.]{3,70})$"),          # 1. Definitions
    re.compile(r"^(Pasal\s+\d+)\s*:\s*(.+)$", re.I),        # Pasal 1: Kewajiban
    re.compile(r"^(Section\s+\d+)\s*[:.]\s*(.+)$", re.I),
]

MIN_SECTION_CHARS = 40


@dataclass
class DocumentReport:
    """What happened to one file."""

    origin: str
    ok: bool
    pages: int = 0
    sections: int = 0
    tables: int = 0
    tables_rejoined: int = 0
    boilerplate_lines: int = 0
    error: str = ""
    flags: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return text.strip()


# --- Running headers and footers --------------------------------------------


@dataclass
class Line:
    """One line of a page, with where it sits."""

    text: str
    top: float
    bottom: float
    page: int


def group_words_into_lines(words: list[dict], page_no: int) -> list[Line]:
    """Rebuild lines from positioned words.

    Working from coordinates rather than from a text dump matters because a
    header drawn at the top of the page and the first sentence of the body can
    come out of a text extractor joined into one string, at which point neither
    can be removed without damaging the other.
    """
    lines: list[Line] = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(word["top"] - lines[-1].top) <= LINE_TOLERANCE:
            lines[-1].text += " " + word["text"]
            lines[-1].bottom = max(lines[-1].bottom, word["bottom"])
        else:
            lines.append(Line(word["text"], word["top"], word["bottom"], page_no))
    return lines


def find_furniture(pages: list[list[Line]], heights: list[float]) -> set[str]:
    """Text repeating in the header or footer band across most pages.

    Detected rather than hardcoded: every document set has different running
    headers, and a rule written for one PDF is worthless on the next.
    """
    banded: list[tuple[int, str]] = []
    for index, (lines, height) in enumerate(zip(pages, heights)):
        for line in lines:
            in_header = line.top < height * MARGIN_BAND
            in_footer = line.bottom > height * (1 - MARGIN_BAND)
            if in_header or in_footer:
                banded.append((index, _clean(line.text)))

    # Counted by shape rather than by exact text. A footer holding a page
    # number is different on every page, so counting the text finds nothing;
    # replacing the digits makes the repetition visible.
    pages_seen: dict[str, set[int]] = {}
    texts_of_shape: dict[str, set[str]] = {}
    for index, text in banded:
        shape = re.sub(r"\d+", "#", text)
        pages_seen.setdefault(shape, set()).add(index)
        texts_of_shape.setdefault(shape, set()).add(text)

    furniture: set[str] = set()
    if len(pages) >= 2:
        needed = max(2, int(len(pages) * REPEAT_THRESHOLD))
        for shape, seen in pages_seen.items():
            if len(seen) >= needed:
                furniture |= texts_of_shape[shape]

    # A single page document still has furniture, it just cannot be found by
    # repetition. These only apply inside the margin bands, so a sentence in
    # the body that happens to match is never touched.
    for _, text in banded:
        if re.fullmatch(r"(page\s+)?\d+(\s+of\s+\d+)?", text, re.I):
            furniture.add(text)
        elif re.search(r"\b(confidential|draft|internal use only)\b", text, re.I):
            furniture.add(text)
        elif re.match(r"^[A-Z]{2,6}-[A-Z]{2,4}-\d{4}-\d{2}\b", text):
            furniture.add(text)

    return furniture


# --- Tables ------------------------------------------------------------------


def _row_text(row: list) -> list[str]:
    return [(cell or "").strip() for cell in row]


def rejoin_split_tables(
    tables: list[tuple[int, float, list[list]]]
) -> tuple[list[tuple[int, float, list[list]]], int]:
    """Join tables that a page break cut in half.

    A table running past the bottom of a page is extracted as two tables, and
    the fragment on the second page carries a couple of rows and no context.
    Two signals identify a continuation: the header row repeats, or the column
    count matches and the fragment starts immediately on the next page.

    The position of the first fragment is kept, so the joined table still
    belongs to the heading it started under.
    """
    if not tables:
        return [], 0

    joined: list[tuple[int, float, list[list]]] = []
    last_page: list[int] = []
    rejoins = 0

    for page_no, top, table in tables:
        if not table:
            continue

        if joined:
            start_page, start_top, previous = joined[-1]
            same_width = len(table[0]) == len(previous[0])
            adjacent = page_no == last_page[-1] + 1
            header_repeated = _row_text(table[0]) == _row_text(previous[0])

            if same_width and adjacent and (header_repeated or not _looks_like_header(table[0])):
                rows = previous + (table[1:] if header_repeated else table)
                joined[-1] = (start_page, start_top, rows)
                last_page[-1] = page_no
                rejoins += 1
                continue

        joined.append((page_no, top, list(table)))
        last_page.append(page_no)

    return joined, rejoins


def heading_above(page_lines: list[list[Line]], page_no: int, top: float,
                  furniture: set[str]) -> str:
    """The heading a table sits under.

    A clause whose entire content is a table leaves no prose behind, so the
    heading is dropped and the table ends up labelled only by its own column
    names. Reattaching it here means a question about what happens at sixty
    days late still finds the clause that governs it.
    """
    if page_no - 1 >= len(page_lines):
        return ""

    found = ""
    for line in page_lines[page_no - 1]:
        if line.bottom > top:
            break
        text = _clean(line.text)
        if not text or text in furniture:
            continue

        for pattern in HEADING_PATTERNS:
            match = pattern.match(text)
            if match:
                found = f"{match.group(1)} {match.group(2)}".strip()
                break
        else:
            # Unnumbered headings are short, start with a capital, and do not
            # end a sentence. Single words count: "Loadings" is a heading.
            if (len(text) < 70 and text[:1].isupper()
                    and not text.endswith((".", ":", ";", ","))):
                found = text
    return found


def _looks_like_header(row: list) -> bool:
    """A header row is short text with no numbers in most cells."""
    cells = [c for c in _row_text(row) if c]
    if not cells:
        return False
    wordy = sum(1 for c in cells if not re.search(r"\d", c))
    return wordy >= max(1, len(cells) - 1)


def table_to_text(table: list[list]) -> str:
    rows = []
    for row in table:
        cells = [c for c in _row_text(row) if c]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _word_in_box(word: dict, box: tuple) -> bool:
    x0, top, x1, bottom = box
    centre_x = (word["x0"] + word["x1"]) / 2
    centre_y = (word["top"] + word["bottom"]) / 2
    return x0 <= centre_x <= x1 and top <= centre_y <= bottom


def read_pdf(path: Path) -> tuple[list[list[Line]], list[float], list[tuple[int, list]]]:
    """Read a document once, keeping prose and tables apart.

    Words sitting inside a table are excluded from the prose. Without that the
    same figures appear twice, once as a correct table and once as a run of
    loose numbers where a blank cell has silently shifted a column, so the
    wrong version competes with the right one at retrieval time.
    """
    import pdfplumber

    page_lines: list[list[Line]] = []
    heights: list[float] = []
    tables: list[tuple[int, list]] = []

    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            heights.append(page.height)

            found = page.find_tables()
            boxes = [t.bbox for t in found]
            for table in found:
                data = table.extract()
                if data and len(data) > 1:
                    tables.append((page_no, table.bbox[1], data))

            words = [
                w for w in page.extract_words()
                if not any(_word_in_box(w, box) for box in boxes)
            ]
            page_lines.append(group_words_into_lines(words, page_no))

    return page_lines, heights, tables


# --- PDFs --------------------------------------------------------------------


def split_document(text: str, fallback_title: str) -> list[tuple[str, str]]:
    """Split on numbered clauses and article headings."""
    lines = [ln.strip() for ln in text.splitlines()]
    sections: list[tuple[str, str]] = []
    title = fallback_title
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        body = _clean(" ".join(b for b in buffer if b))
        if len(body) >= MIN_SECTION_CHARS:
            sections.append((title, body))
        buffer = []

    for line in lines:
        if not line:
            continue
        matched = None
        for pattern in HEADING_PATTERNS:
            match = pattern.match(line)
            if match:
                matched = f"{match.group(1)} {match.group(2)}".strip()
                break

        if matched:
            flush()
            title = matched
            continue
        buffer.append(line)

    flush()
    return sections


def extract_pdf(path: Path) -> tuple[list[Section], DocumentReport]:
    origin = f"documents/{path.name}"
    report = DocumentReport(origin=origin, ok=False)

    try:
        page_lines, heights, raw_tables = read_pdf(path)
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {str(exc)[:60]}"
        report.flags.append("unreadable_source")
        log.warning("pdf could not be read", extra={"file": path.name,
                                                    "reason": report.error})
        return [], report

    report.pages = len(page_lines)
    if not any(line.text.strip() for lines in page_lines for line in lines):
        report.error = "no extractable text, possibly a scanned document"
        report.flags.append("empty_extraction")
        return [], report

    furniture = find_furniture(page_lines, heights)
    report.boilerplate_lines = len(furniture)
    body = "\n".join(
        _clean(line.text)
        for lines in page_lines
        for line in lines
        if _clean(line.text) not in furniture
    )

    doc_title = path.stem.replace("_", " ").title()
    sections = [
        Section(
            source_type="pdf_policy",
            source_ref=f"{origin}#{title}",
            source_origin=origin,
            title=title,
            content=content,
            heading_level=2,
            language="id" if "multifinance" in path.stem else "en",
            retrieved_at=now_iso(),
            extraction_method="pdfplumber_text",
        )
        for title, content in split_document(body, doc_title)
    ]

    tables, rejoins = rejoin_split_tables(raw_tables)
    report.tables = len(tables)
    report.tables_rejoined = rejoins

    for index, (page_no, top, table) in enumerate(tables, 1):
        text = table_to_text(table)
        if len(text) < MIN_SECTION_CHARS:
            continue

        # The heading above says what the table is for. Fall back to the column
        # names only when there is no heading, since they are in the content
        # already and repeating them in the title helps nobody.
        context = heading_above(page_lines, page_no, top, furniture)
        label = " ".join(c for c in _row_text(table[0]) if c) or f"Table {index}"
        title = context or label[:60]

        sections.append(
            Section(
                source_type="pdf_table",
                source_ref=f"{origin}#{context or f'table {index}'}",
                source_origin=origin,
                title=f"{doc_title}: {title}",
                content=text,
                heading_level=3,
                language="id" if "multifinance" in path.stem else "en",
                retrieved_at=now_iso(),
                extraction_method="pdfplumber",
            )
        )

    report.sections = len(sections)
    report.ok = bool(sections)
    if not sections:
        report.error = "parsed but produced no usable sections"
    return sections, report


# --- Form export -------------------------------------------------------------


def extract_form(path: Path) -> tuple[list[Section], DocumentReport]:
    """Describe what the form captures, without importing the people in it.

    The rows are customer records, not company knowledge. A voice agent has no
    business retrieving somebody else's lead, so the individual rows are not
    turned into records. What is useful is the shape of the form and the values
    it accepts, which is what qualification questions are built from.
    """
    origin = f"forms/{path.name}"
    report = DocumentReport(origin=origin, ok=False)

    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {str(exc)[:60]}"
        report.flags.append("unreadable_source")
        return [], report

    if not rows:
        report.error = "no rows"
        return [], report

    columns = list(rows[0].keys())
    populated = {c: sum(1 for r in rows if (r.get(c) or "").strip()) for c in columns}

    lines = [f"The lead capture form records {len(columns)} fields per enquiry."]
    for column in columns:
        filled = populated[column]
        note = "" if filled == len(rows) else f" ({filled} of {len(rows)} populated)"
        lines.append(f"{column}{note}")

    sections = [
        Section(
            source_type="form_export",
            source_ref=f"{origin}#fields",
            source_origin=origin,
            title="Lead capture form fields",
            content="\n".join(lines),
            heading_level=2,
            language="en",
            retrieved_at=now_iso(),
            extraction_method="csv",
            quality_flags=["contains_personal_data_in_source"],
        )
    ]

    # The values the form actually accepts are useful; the people are not.
    for column in ("business_unit", "Product Interest"):
        values = sorted({(r.get(column) or "").strip() for r in rows} - {""})
        if values:
            sections.append(
                Section(
                    source_type="form_export",
                    source_ref=f"{origin}#{column}",
                    source_origin=origin,
                    title=f"Values accepted for {column}",
                    content=f"{column} takes these values: " + "; ".join(values),
                    heading_level=3,
                    language="en",
                    retrieved_at=now_iso(),
                    extraction_method="csv",
                )
            )

    report.ok = True
    report.sections = len(sections)
    report.flags.append(f"{len(rows)}_customer_rows_excluded")
    return sections, report


# --- Rules -------------------------------------------------------------------


def extract_rules(path: Path) -> tuple[list[Section], DocumentReport]:
    """Turn the qualification rules into retrievable text.

    Kept readable rather than dumped as YAML, because the agent has to explain a
    decline to a caller, and "test: 18 <= age <= 60" is not an explanation.
    """
    origin = f"rules/{path.name}"
    report = DocumentReport(origin=origin, ok=False)

    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {str(exc)[:60]}"
        report.flags.append("unreadable_source")
        return [], report

    sections: list[Section] = []
    version = data.get("version", "unknown")

    for unit_code, unit in (data.get("units") or {}).items():
        name = unit.get("name", unit_code)

        for kind in ("hard_rules", "soft_rules"):
            rules = unit.get(kind) or []
            if not rules:
                continue
            lines = []
            for rule in rules:
                outcome = rule.get("on_fail") or rule.get("on_pass") or ""
                applies = rule.get("applies_to")
                scope = f" Applies to {', '.join(applies)}." if applies else ""
                lines.append(
                    f"{rule['id']}: {rule.get('reason', '')}{scope} "
                    f"Condition {rule.get('test', '')}, outcome {outcome}."
                )
            label = "must pass" if kind == "hard_rules" else "reviewed"
            sections.append(
                Section(
                    source_type="rules_file",
                    source_ref=f"{origin}#{unit_code}.{kind}",
                    source_origin=origin,
                    title=f"{name}: criteria that {label}",
                    content="\n".join(lines),
                    heading_level=2,
                    language="en",
                    retrieved_at=now_iso(),
                    extraction_method="yaml",
                    quality_flags=[f"rules_version_{version}"],
                )
            )

        for key, label in (("down_payment_minimum", "Minimum down payment"),
                           ("tenor_months", "Available terms")):
            values = unit.get(key)
            if values:
                lines = [f"{product}: {value}" for product, value in values.items()]
                sections.append(
                    Section(
                        source_type="rules_file",
                        source_ref=f"{origin}#{unit_code}.{key}",
                        source_origin=origin,
                        title=f"{name}: {label}",
                        content=f"{label} by product.\n" + "\n".join(lines),
                        heading_level=3,
                        language="en",
                        retrieved_at=now_iso(),
                        extraction_method="yaml",
                    )
                )

    for trigger in data.get("escalation_triggers") or []:
        sections.append(
            Section(
                source_type="rules_file",
                source_ref=f"{origin}#{trigger['id']}",
                source_origin=origin,
                title=f"Escalation: {trigger['id']}",
                content=f"When {trigger['when']} Action: {trigger['action']}",
                heading_level=3,
                language="en",
                retrieved_at=now_iso(),
                extraction_method="yaml",
            )
        )

    prohibited = data.get("prohibited") or []
    if prohibited:
        sections.append(
            Section(
                source_type="rules_file",
                source_ref=f"{origin}#prohibited",
                source_origin=origin,
                title="Actions the agent must never take",
                content="The agent must never do any of the following.\n"
                        + "\n".join(f"- {item}" for item in prohibited),
                heading_level=2,
                language="en",
                retrieved_at=now_iso(),
                extraction_method="yaml",
            )
        )

    report.ok = bool(sections)
    report.sections = len(sections)
    return sections, report


# --- Runner ------------------------------------------------------------------


def extract_all() -> tuple[list[Section], list[DocumentReport]]:
    sections: list[Section] = []
    reports: list[DocumentReport] = []

    for path in sorted((RAW / "documents").glob("*.pdf")):
        with track("extract_pdf", detail=path.name):
            found, report = extract_pdf(path)
        sections.extend(found)
        reports.append(report)

    for path in sorted((RAW / "forms").glob("*.csv")):
        found, report = extract_form(path)
        sections.extend(found)
        reports.append(report)

    for path in sorted((RAW / "rules").glob("*.yaml")):
        found, report = extract_rules(path)
        sections.extend(found)
        reports.append(report)

    return sections, reports


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    print("=" * 92)
    print("Document extraction")
    print("=" * 92)

    sections, reports = extract_all()

    print(f"\n{'file':<44}{'pages':>7}{'sections':>10}{'tables':>8}{'rejoined':>10}{'boilerplate':>12}")
    print("-" * 92)
    for r in reports:
        if not r.ok:
            print(f"{r.origin:<44}{'SKIPPED':>7}  {r.error[:36]}")
            continue
        print(f"{r.origin:<44}{r.pages or '':>7}{r.sections:>10}{r.tables or '':>8}"
              f"{r.tables_rejoined or '':>10}{r.boilerplate_lines or '':>12}")

    failed = [r for r in reports if not r.ok]
    path = PROCESSED / "document_sections.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for section in sections:
            handle.write(json.dumps(asdict(section), ensure_ascii=False) + "\n")

    print(f"\n{'=' * 92}")
    print(f"  files          {len(reports) - len(failed)} parsed, {len(failed)} skipped")
    print(f"  sections       {len(sections)}")
    print(f"  tables rejoined across page breaks  {sum(r.tables_rejoined for r in reports)}")
    print(f"  written to     data/processed/document_sections.jsonl")

    for r in reports:
        for flag in r.flags:
            print(f"  flag  {r.origin}: {flag}")
    for r in failed:
        print(f"  SKIPPED  {r.origin}: {r.error}")
    print("\n  A skipped file does not stop the run. It is recorded and the rest continues.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
