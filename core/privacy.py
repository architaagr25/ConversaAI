"""
Finds and removes personal data.

Lives in core because three different parts of the system need it: the
knowledge base when importing a form export, the voice agent when a caller
gives their name and income, and the live call analysis when a transcript is
sent away to be scored.

Two markets means two sets of identifiers and neither looks like the other. A
Philippine record carries a TIN and a +63 mobile, an Indonesian one a 16 digit
KTP and a +62 number, and the two write addresses differently again.

Redaction is deterministic, so the same person becomes the same token every
time and records stay countable without anyone being identifiable. The tokens
are one way.

Names are the weak point and are treated as such. Telling a person's name from
a company's in free prose needs a trained model, so the detector here is
cautious, its accuracy is measured rather than claimed, and it is off by
default in the outbound guard where a false positive would drop a live call.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    kind: str
    text: str
    start: int
    end: int
    replacement: str


def token(kind: str, value: str) -> str:
    """A stable, one way token.

    The same input gives the same token, so two mentions of one person stay
    linked for counting. Unsalted only because every input here is invented;
    production would salt this per tenant.
    """
    digest = hashlib.sha256(value.strip().lower().encode()).hexdigest()[:6]
    return f"[{kind}_{digest}]"


# Order matters. Government identifiers are matched before bare numbers and
# labelled forms before unlabelled ones, so the most specific wins.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),

    ("GOV_ID_PH", re.compile(r"\bTIN[\s:]*\d{3}-\d{3}-\d{3}\b", re.I)),
    ("GOV_ID_ID", re.compile(r"\bKTP[\s:]*\d{16}\b|\b\d{16}\b")),
    ("GOV_ID_ID_NPWP", re.compile(r"\bNPWP[\s:]*[\d.\-]{15,20}\b", re.I)),

    ("PHONE_PH", re.compile(r"(?:\+63[\s-]?|\b0)9\d{2}[\s-]?\d{3}[\s-]?\d{4}\b")),
    ("PHONE_ID", re.compile(r"\+62[\s-]?8\d{2}[\s-]?\d{4}[\s-]?\d{3,4}\b")),

    ("ACCOUNT", re.compile(r"\b(?:HS|LP|MF)-\d{4}-\d{4,6}\b")),

    # The year range separates a birth date from the submission date in the
    # next column, which is not personal data.
    ("DOB", re.compile(r"\b\d{1,2}/\d{1,2}/(?:19\d{2}|20[01]\d)\b")),
]

# An address runs past the street into the locality. Matching only the street
# leaves "Quezon City, Metro Manila" in the text, which combined with anything
# else still identifies someone.
LOCALITY_TAIL = r"(?:,\s*[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,3}){0,3}"

ADDRESS_LEAD = re.compile(
    r"\b(?:\d+[A-Za-z]?\s+)?(?:Jl\.|Jalan|Block|Blk|Unit|Purok|Barangay|Brgy\.?)\s+"
    r"[^,\n]{3,60}" + LOCALITY_TAIL, re.I)

ADDRESS_PH = re.compile(
    r"\b\d{1,4}\s+[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,3}\s+"
    r"(?:Street|St\.|Avenue|Ave\.?|Road|Rd\.?|Drive|Highway)\b" + LOCALITY_TAIL)

NAME_CANDIDATE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z']{1,}){1,3}\b")

# Anything here is not a person. Built from the vocabulary this corpus uses,
# which is exactly why name detection does not generalise: a new deployment
# needs its own list. Measured in knowledge_base/pii.py rather than assumed.
NOT_A_NAME = {
    "solara", "finance", "group", "health", "shield", "life", "philippines",
    "multifinance", "indonesia", "insurance", "corporation", "assurance",
    "essential", "plus", "max", "term", "whole", "educational", "plan", "bank",
    "partner", "meridian", "tower", "bonifacio", "global", "city", "taguig",
    "jakarta", "selatan", "pusat", "bandung", "surabaya", "semarang", "jawa",
    "barat", "timur", "tengah", "quezon", "makati", "manila", "davao", "cavite",
    "pasig", "iloilo", "baguio", "cebu", "pampanga", "leyte", "yogyakarta",
    "sleman", "surakarta", "waiting", "period", "premium", "policy", "cover",
    "benefit", "claim", "rider", "maternity", "critical", "illness", "hospital",
    "accredited", "member", "annual", "monthly", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "monday", "saturday", "sunday", "pasal", "otoritas",
    "jasa", "keuangan", "menara", "lantai", "kartu", "keluarga", "toyota",
    "honda", "avanza", "vario", "brio", "singapore", "thailand", "malaysia",
    "asia", "east", "south", "philippine", "republic", "distribution",
    "operations", "lead", "product", "interest", "agent", "notes", "full",
    "name", "home", "address", "contact", "gov", "income",
    "account", "street", "avenue", "road", "drive", "pembiayaan",
    "mobil", "motor", "multiguna", "baru", "bekas", "slip", "gaji", "rekening",
    "koran", "terakhir", "usia", "tenor", "denda", "angsuran", "cicilan",
    "surat", "peringatan", "pertama", "kedua", "kunjungan", "lapangan",
}


def _looks_like_a_person(candidate: str) -> bool:
    words = [w.strip(".,'") for w in candidate.split()]
    if not 2 <= len(words) <= 4:
        return False
    if any(w.lower() in NOT_A_NAME for w in words):
        return False
    return all(2 <= len(w) <= 15 for w in words)


def scan(text: str, detect_names: bool = True) -> list[Finding]:
    """Every piece of personal data found, with where it sits."""
    findings: list[Finding] = []
    claimed: list[tuple[int, int]] = []

    def add(kind: str, match: re.Match) -> None:
        start, end = match.span()
        if any(start < e and end > s for s, e in claimed):
            return
        claimed.append((start, end))
        value = match.group(0)
        findings.append(Finding(kind, value, start, end, token(kind, value)))

    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            add(kind, match)

    for pattern in (ADDRESS_LEAD, ADDRESS_PH):
        for match in pattern.finditer(text):
            add("ADDRESS", match)

    if detect_names:
        for match in NAME_CANDIDATE.finditer(text):
            if _looks_like_a_person(match.group(0)):
                add("NAME", match)

    return sorted(findings, key=lambda f: f.start)


def redact(text: str, detect_names: bool = True) -> tuple[str, list[Finding]]:
    """Replace personal data with stable tokens."""
    findings = scan(text, detect_names=detect_names)
    if not findings:
        return text, []

    out, cursor = [], 0
    for finding in findings:
        out.append(text[cursor:finding.start])
        out.append(finding.replacement)
        cursor = finding.end
    out.append(text[cursor:])
    return "".join(out), findings


def contains_personal_data(text: str, detect_names: bool = False) -> bool:
    """Cheap check for the outbound guard.

    Names are off by default. The detector is cautious but not precise enough
    to block a request on, and a false positive interrupts a live call.
    """
    return bool(scan(text, detect_names=detect_names))
