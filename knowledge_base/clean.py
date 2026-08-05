"""
Turns extracted sections into classified, deduplicated records.

Four jobs. Classify each section so the agent knows what it is allowed to do
with it. Normalise dates and terminology so the same fact written two ways is
found by either. Collapse near-duplicates, of which there are many because
marketing copy gets reworded rather than reused. And find places where two
sources disagree, which is the one that matters most: a campaign page claiming
a shorter waiting period than the policy document is not a duplicate, it is a
contradiction, and the agent must not repeat it.

    python -m knowledge_base.clean
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict

from core.config import PROJECT_ROOT
from knowledge_base.models import AUTHORITY_RANK, Record

log = logging.getLogger(__name__)

PROCESSED = PROJECT_ROOT / "data" / "processed"

# Public reference pages are kept out of the knowledge base. They proved the
# extractor works on markup nobody wrote for it, which was their purpose, but
# an agent that answers Solara questions from a general encyclopaedia is
# ungrounded even when the encyclopaedia is right.
SOURCE_FILES = ["web_sections.jsonl", "document_sections.jsonl"]

SIMILARITY_THRESHOLD = 0.72   # above this, two records say the same thing
CONFLICT_BAND = (0.45, 0.72)  # related enough to compare, different enough to clash


# --- Classification ----------------------------------------------------------

UNIT_BY_SOURCE = [
    ("health-shield", "health_ph_en"),
    ("health_shield", "health_ph_en"),
    ("life-philippines", "life_ph"),
    ("multifinance", "multifinance_id"),
    ("partners", "group"),
    ("lead_form", "group"),
]

# Rules carry their unit in the reference, e.g. "#health_ph_en.hard_rules".
UNIT_IN_REF = re.compile(r"#(health_ph_en|life_ph|multifinance_id)\b")

AUTHORITY_BY_TYPE = {
    "pdf_policy": "binding",
    "pdf_table": "binding",
    "rules_file": "operational",
    "form_export": "operational",
    "web_page": "published",
}

# Order matters: the first match wins, so the narrow categories come first.
CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    # Boilerplate company description, which mentions agents and so would
    # otherwise be filed under partnerships.
    ("corporate", re.compile(
        r"^about solara|^tentang solara|^contact|office hours", re.I)),
    ("objection", re.compile(
        r"common concerns|too expensive|already covered|rather think about it|"
        r"wasted money|medyo mabigat|keberatan", re.I)),
    ("partnership_benefits", re.compile(
        r"partner|commission|accredited agent|bancassurance partnership", re.I)),
    ("qualification", re.compile(
        r"who can apply|eligib|entry age|criteria that|syarat|dokumen yang|"
        r"required fields|form fields|values accepted", re.I)),
    ("policy_rule", re.compile(
        r"waiting period|not covered|exclusion|lapse|grace period|reinstat|"
        r"cancel|denda|keterlambatan|penagihan|prohibited|escalation|"
        r"must never|restrukturisasi|beneficiar", re.I)),
    ("pricing", re.compile(
        r"premium|rate table|monthly premium|loading|down payment|"
        r"commission structure|cicilan|angsuran|biaya|tarif|available terms", re.I)),
    ("process", re.compile(
        r"how to apply|how do i|claim|paying your|pembayaran|pelunasan|"
        r"kewajiban pembayaran|training and support|becoming an", re.I)),
    ("faq", re.compile(
        r"frequently asked|common questions|pertanyaan|\?$", re.I)),
    ("product", re.compile(
        r"plan|cover|rider|produk|pembiayaan|term life|whole life|"
        r"educational|shield|compare", re.I)),
    ("corporate", re.compile(r"about solara|tentang solara|contact|office", re.I)),
]


def classify_unit(section: dict) -> str:
    match = UNIT_IN_REF.search(section["source_ref"])
    if match:
        return match.group(1)
    origin = section["source_origin"].lower()
    for needle, unit in UNIT_BY_SOURCE:
        if needle in origin:
            return unit
    return "group"


def classify_authority(section: dict) -> str:
    # A campaign page is advertising whatever else it looks like, and must never
    # be quoted as if it were cover.
    if "campaign" in section["source_origin"].lower():
        return "promotional"
    return AUTHORITY_BY_TYPE.get(section["source_type"], "published")


def classify_category(section: dict) -> str:
    haystack = f"{section['title']}\n{section['content'][:400]}"
    for category, pattern in CATEGORY_RULES:
        if pattern.search(haystack):
            return category
    return "product"


# --- Normalisation -----------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
SHORT_MONTHS = {m[:3].lower(): i for m, i in MONTHS.items()}

DATE_FORMS = [
    # 01/03/2026, day first, which is the convention in both markets here
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
     lambda m: f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"),
    # March 1, 2026
    (re.compile(r"\b([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})\b"),
     lambda m: (f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
                if m.group(1).lower() in MONTHS else m.group(0))),
    # 1 Mar 26
    (re.compile(r"\b(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{2})\b"),
     lambda m: (f"20{m.group(3)}-{SHORT_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
                if m.group(2).lower() in SHORT_MONTHS else m.group(0))),
]


def normalise_dates(text: str) -> tuple[str, int]:
    """Rewrite dates to ISO so they sort and compare.

    Four spellings of 1 March 2026 appear across the sources. Left alone,
    nothing can answer whether a record is current.
    """
    changed = 0
    for pattern, replace in DATE_FORMS:
        def _sub(match):
            nonlocal changed
            result = replace(match)
            if result != match.group(0):
                changed += 1
            return result
        text = pattern.sub(_sub, text)
    return text, changed


# Canonical term, then what callers and the sources actually say. Variants are
# not rewritten into the text; they travel with the record so keyword search
# finds it whichever word was used.
TERMINOLOGY = {
    "premium": ["contribution", "monthly due", "bayad", "hulog", "premium payment"],
    "installment": ["cicilan", "angsuran", "setoran", "monthly payment"],
    "waiting_period": ["waiting period", "qualifying period", "elimination period"],
    "lapse": ["lapse", "nahinto", "terminated for non-payment"],
    "due_date": ["due date", "jatuh tempo", "takdang araw"],
    "late_fee": ["denda", "late fee", "penalty", "surcharge"],
    "down_payment": ["down payment", "uang muka", " dp "],
    "tenor": ["tenor", "jangka waktu", "loan period"],
    "beneficiary": ["beneficiary", "benepisyaryo", "nominee"],
    "rider": ["rider", "add-on", "supplementary benefit"],
    "pre_existing_condition": ["pre-existing condition", "pre-existing", "prior illness"],
    "grace_period": ["grace period"],
    "sum_assured": ["sum assured", "coverage amount"],
}


def find_terminology(text: str) -> tuple[list[str], list[str]]:
    """Which concepts this record covers, and every word used for them.

    The canonical term counts as one of its own spellings. Without that a
    record written in the company's own vocabulary matches nothing, which is
    exactly backwards.
    """
    lower = f" {text.lower()} "
    concepts, variants = [], []

    for canonical, alternatives in TERMINOLOGY.items():
        spellings = [canonical.replace("_", " ")] + list(alternatives)
        if not any(word.lower() in lower for word in spellings):
            continue
        concepts.append(canonical)
        # Every alternative, matched or not, so a caller asking about their
        # hulog reaches a record written about premiums.
        variants.extend(word.strip() for word in spellings)

    return concepts, sorted(set(variants))


# --- Duplicates --------------------------------------------------------------


def shingles(text: str, size: int = 5) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def content_hash(text: str) -> str:
    normalised = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def find_duplicate_groups(records: list[Record]) -> list[list[int]]:
    """Group records that say the same thing.

    Reworded marketing copy is the common case, so exact hashing is not enough.
    Overlapping word sequences catch a paragraph that has been rewritten but
    still carries the same content.
    """
    fingerprints = [shingles(r.content) for r in records]
    groups: list[list[int]] = []
    assigned: dict[int, int] = {}

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            if jaccard(fingerprints[i], fingerprints[j]) < SIMILARITY_THRESHOLD:
                continue
            if i in assigned:
                groups[assigned[i]].append(j)
                assigned[j] = assigned[i]
            elif j in assigned:
                groups[assigned[j]].append(i)
                assigned[i] = assigned[j]
            else:
                assigned[i] = assigned[j] = len(groups)
                groups.append([i, j])

    return [sorted(set(g)) for g in groups]


def resolve_duplicates(records: list[Record], groups: list[list[int]]) -> tuple[list[Record], int]:
    """Keep the most authoritative copy, mark the rest.

    Duplicates are not deleted. Marking which record superseded which is what
    lets a reviewer check the pipeline did the right thing.
    """
    dropped = 0
    for group in groups:
        ranked = sorted(
            group,
            key=lambda i: (AUTHORITY_RANK.get(records[i].authority, 9),
                           -records[i].char_count),
        )
        keeper = ranked[0]
        for index in ranked[1:]:
            records[index].duplicate_of = records[keeper].record_id
            records[index].quality_flags.append("near_duplicate")
            dropped += 1
    return records, dropped


# --- Conflicts ---------------------------------------------------------------

DURATION = re.compile(
    r"(\d+|thirty|sixty|ninety|twenty-four|twelve|ten|two|three)\s*"
    r"[\(\)0-9\s]*(day|month|year)s?", re.I)

WORD_NUMBERS = {"two": 2, "three": 3, "ten": 10, "twelve": 12, "twenty-four": 24,
                "thirty": 30, "sixty": 60, "ninety": 90}

TOPIC_MARKERS = {
    # The FAQ never says "pre-existing", it says "a condition that existed
    # before you joined". Same rule, different words, and a marker written only
    # for the formal term misses the one a caller is most likely to read.
    "pre_existing_waiting": re.compile(
        r"pre-existing|condition that existed before|existing sickness|prior illness",
        re.I),
    "grace_period": re.compile(r"grace period", re.I),
    "illness_waiting": re.compile(r"illness(es)? (are|is) covered|applies to all illness", re.I),
}

# Two durations within this much of each other are the same rule written
# differently. 24 months and 2 years are 720 and 730 days, and reporting that
# as a contradiction would bury the real one.
EQUIVALENCE_TOLERANCE = 0.10


def _to_days(value: int, unit: str) -> int:
    unit = unit.lower()
    return value * {"day": 1, "month": 30, "year": 365}[unit]


def claimed_durations(text: str, topic: re.Pattern) -> list[tuple[int, str]]:
    """Durations stated in the same sentence as a topic.

    Sentence scope matters. A document mentioning both a 30 day illness wait and
    a 24 month pre-existing wait is not contradicting itself, and comparing
    across the whole record would say it was.
    """
    found: dict[int, str] = {}
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not topic.search(sentence):
            continue
        for match in DURATION.finditer(sentence):
            raw = match.group(1).lower()
            value = WORD_NUMBERS.get(raw, int(raw) if raw.isdigit() else None)
            if value is None:
                continue
            # Keyed by duration, so a sentence stating the same figure twice
            # does not produce the same contradiction twice.
            found.setdefault(_to_days(value, match.group(2)), sentence.strip())
    return sorted(found.items())


def detect_conflicts(records: list[Record]) -> list[dict]:
    """Find records making incompatible claims about the same thing."""
    conflicts: list[dict] = []
    reported: set[tuple[str, str, str]] = set()

    for topic, pattern in TOPIC_MARKERS.items():
        claims: list[tuple[Record, int, str]] = []
        for record in records:
            if record.duplicate_of:
                continue
            for days, sentence in claimed_durations(record.content, pattern):
                claims.append((record, days, sentence))

        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                left, left_days, left_text = claims[i]
                right, right_days, right_text = claims[j]
                if left.record_id == right.record_id:
                    continue
                if left.business_unit != right.business_unit:
                    continue

                spread = abs(left_days - right_days) / max(left_days, right_days)
                if spread <= EQUIVALENCE_TOLERANCE:
                    continue

                key = (topic, *sorted((left.record_id, right.record_id)))
                if key in reported:
                    continue
                reported.add(key)

                winner, loser = (left, right)
                if AUTHORITY_RANK.get(right.authority, 9) < AUTHORITY_RANK.get(left.authority, 9):
                    winner, loser = right, left

                conflicts.append({
                    "topic": topic,
                    "records": [left.record_id, right.record_id],
                    "claims": [f"{left_days} days ({left.authority})",
                               f"{right_days} days ({right.authority})"],
                    "quotes": [left_text[:120], right_text[:120]],
                    "resolved_by": "authority" if winner.authority != loser.authority
                                   else "unresolved",
                    "authoritative": winner.record_id,
                    "superseded": loser.record_id,
                })

                left.conflicts_with.append(right.record_id)
                right.conflicts_with.append(left.record_id)
                flag = f"contradicts_{winner.authority}_source"
                if flag not in loser.quality_flags:
                    loser.quality_flags.append(flag)

    return conflicts


# --- Pipeline ----------------------------------------------------------------


def load_sections() -> list[dict]:
    sections = []
    for name in SOURCE_FILES:
        path = PROCESSED / name
        if not path.exists():
            log.warning("missing input", extra={"file": name})
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                sections.append(json.loads(line))
    return sections


def build_records(sections: list[dict]) -> tuple[list[Record], dict]:
    counters = {"dates_normalised": 0}
    counts_by_category: defaultdict[str, int] = defaultdict(int)
    records: list[Record] = []

    for index, section in enumerate(sections, 1):
        content, changed = normalise_dates(section["content"])
        counters["dates_normalised"] += changed
        title, _ = normalise_dates(section["title"])

        category = classify_category(section)
        unit = classify_unit(section)
        counts_by_category[category] += 1

        concepts, variants = find_terminology(f"{title} {content}")

        records.append(Record(
            record_id=f"kb_{category}_{index:03d}",
            title=title,
            content=content,
            category=category,
            business_unit=unit,
            authority=classify_authority(section),
            source_type=section["source_type"],
            source_ref=section["source_ref"],
            source_origin=section["source_origin"],
            source_retrieved_at=section["retrieved_at"],
            content_hash=content_hash(content),
            language=section.get("language", "en"),
            terminology_variants=variants,
            quality_flags=list(section.get("quality_flags", []))
                          + [f"covers_{c}" for c in concepts],
        ))

    counters["by_category"] = dict(counts_by_category)
    return records, counters


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    print("=" * 88)
    print("Cleaning and classification")
    print("=" * 88)

    sections = load_sections()
    print(f"\n  loaded {len(sections)} sections from {len(SOURCE_FILES)} files")
    print("  public reference pages excluded from the knowledge base")

    records, counters = build_records(sections)

    print(f"\n  dates rewritten to ISO   {counters['dates_normalised']}")
    print("\n  classified by category")
    for category, count in sorted(counters["by_category"].items(),
                                  key=lambda kv: -kv[1]):
        print(f"    {category:<24}{count:>4}")

    print("\n  classified by authority")
    by_authority: defaultdict[str, int] = defaultdict(int)
    for record in records:
        by_authority[record.authority] += 1
    for authority in sorted(by_authority, key=lambda a: AUTHORITY_RANK.get(a, 9)):
        print(f"    {authority:<24}{by_authority[authority]:>4}")

    print("\n  classified by business unit")
    by_unit: defaultdict[str, int] = defaultdict(int)
    for record in records:
        by_unit[record.business_unit] += 1
    for unit, count in sorted(by_unit.items(), key=lambda kv: -kv[1]):
        print(f"    {unit:<24}{count:>4}")

    groups = find_duplicate_groups(records)
    records, dropped = resolve_duplicates(records, groups)
    print(f"\n  duplicate groups found   {len(groups)}")
    print(f"  records superseded       {dropped}")
    for group in groups:
        keeper = next(r for i, r in enumerate(records)
                      if i in group and not r.duplicate_of)
        print(f"    kept   {keeper.record_id:<22}{keeper.authority:<12}"
              f"{keeper.title[:34]}")
        for index in group:
            if records[index].duplicate_of:
                print(f"      dropped {records[index].record_id:<20}"
                      f"{records[index].authority:<12}"
                      f"{records[index].source_origin[:34]}")

    conflicts = detect_conflicts(records)
    print(f"\n  contradictions found     {len(conflicts)}")
    for conflict in conflicts:
        print(f"\n    topic: {conflict['topic']}")
        for record_id, claim, quote in zip(conflict["records"], conflict["claims"],
                                           conflict["quotes"]):
            marker = "WINS " if record_id == conflict["authoritative"] else "loses"
            print(f"      {marker} {record_id:<22}{claim}")
            print(f"            \"{quote}\"")
        print(f"      resolved by {conflict['resolved_by']}")

    live = [r for r in records if not r.duplicate_of]
    out = PROCESSED / "cleaned_records.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    report = PROCESSED / "conflicts.json"
    report.write_text(json.dumps(conflicts, indent=2, ensure_ascii=False),
                      encoding="utf-8")

    print(f"\n{'=' * 88}")
    print(f"  records written   {len(records)} ({len(live)} live, {dropped} superseded)")
    print(f"  written to        data/processed/cleaned_records.jsonl")
    print(f"                    data/processed/conflicts.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
