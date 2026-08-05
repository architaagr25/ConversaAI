"""
Applies and measures the personal data protection in core/privacy.py.

The form export is the only source carrying personal data and its columns are
known, so recall and over-detection can both be counted rather than asserted.
A detector nobody has scored is a claim, not a control.

    python -m knowledge_base.pii
"""

from __future__ import annotations

import csv
import json
import logging

from core.config import PROJECT_ROOT
from core.privacy import redact, scan

log = logging.getLogger(__name__)

RAW = PROJECT_ROOT / "data" / "raw"
PROCESSED = PROJECT_ROOT / "data" / "processed"

# Which detector kinds answer for which column of the export.
KIND_MAP = {
    "NAME": {"NAME"},
    "EMAIL": {"EMAIL"},
    "PHONE": {"PHONE_PH", "PHONE_ID"},
    "GOV_ID": {"GOV_ID_PH", "GOV_ID_ID", "GOV_ID_ID_NPWP"},
    "ADDRESS": {"ADDRESS"},
    "DOB": {"DOB"},
    "ACCOUNT": {"ACCOUNT"},
}

COLUMNS = {
    "NAME": "Full Name",
    "EMAIL": "email_address",
    "PHONE": "Contact No.",
    "GOV_ID": "Gov ID",
    "ADDRESS": "Home Address",
    "DOB": "DOB",
    "ACCOUNT": "Policy/Account No",
}


def measure_against_export() -> dict:
    """Score the detector where the answers are known."""
    path = RAW / "forms" / "lead_form_export.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    expected = {kind: [r[column].strip() for r in rows if r[column].strip()]
                for kind, column in COLUMNS.items()}

    findings = scan(path.read_text(encoding="utf-8"))
    found = [f.text.strip() for f in findings]

    def was_caught(value: str) -> bool:
        # Partial counts as a detection. A multi part address matched only as
        # far as the street did fire; whether it removed enough is what the
        # coverage figure measures separately.
        return any(hit in value or value in hit for hit in found)

    def coverage(value: str) -> float:
        longest = max((len(hit) for hit in found if hit in value), default=0)
        return longest / len(value) if value else 1.0

    results = {}
    for kind, values in expected.items():
        caught = [v for v in values if was_caught(v)]
        covered = [coverage(v) for v in caught] or [0.0]
        detections = [f for f in findings if f.kind in KIND_MAP[kind]]
        # A detector that redacts everything scores perfect recall and is
        # useless, so what it fires on wrongly is counted too.
        spurious = [f for f in detections
                    if not any(f.text.strip() in v or v in f.text.strip()
                               for v in values)]
        results[kind] = {
            "expected": len(values),
            "recall": len(caught) / len(values) if values else 1.0,
            "coverage": sum(covered) / len(covered),
            "detections": len(detections),
            "spurious": len(spurious),
            "examples": sorted({f.text.strip()[:34] for f in spurious})[:3],
        }

    results["_total"] = len(findings)
    return results


def protect_records(records: list[dict]) -> tuple[list[dict], dict]:
    """Redact every record, not only the ones expected to be dirty.

    The point of a safety net is catching what was not expected.
    """
    counts: dict[str, int] = {}
    touched = 0

    for record in records:
        combined = f"{record['title']}\n{record['content']}"
        # Names off here: the knowledge base holds company policy, and a false
        # positive would rewrite a policy term into a token.
        cleaned, findings = redact(combined, detect_names=False)

        if findings:
            touched += 1
            title, _, content = cleaned.partition("\n")
            record["title"], record["content"] = title, content
            record["pii"] = True
            record["pii_types"] = sorted({f.kind for f in findings})
            for finding in findings:
                counts[finding.kind] = counts.get(finding.kind, 0) + 1
        else:
            record["pii"] = False
            record["pii_types"] = []

    return records, {"records_touched": touched, "by_kind": counts}


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    print("=" * 84)
    print("Personal data detection")
    print("=" * 84)

    print("\nMeasured against the form export, where the answers are known")
    print("-" * 84)
    scores = measure_against_export()
    print(f"{'kind':<10}{'in file':>8}{'recall':>8}{'coverage':>10}"
          f"{'fired':>7}{'spurious':>10}")
    print("-" * 84)
    for kind in COLUMNS:
        row = scores[kind]
        flag = ""
        if row["recall"] < 0.95:
            flag = "  <-- missed"
        elif row["coverage"] < 0.9:
            flag = "  <-- partial"
        elif row["spurious"]:
            flag = "  <-- over-redacts"
        print(f"{kind:<10}{row['expected']:>8}{row['recall']:>7.0%}"
              f"{row['coverage']:>9.0%}{row['detections']:>8}"
              f"{row['spurious']:>9}{flag}")

    print("\n  recall is whether it fired at all, coverage is how much of the")
    print("  value it removed, spurious is detections matching nothing real")

    print("\nRedaction sample")
    print("-" * 84)
    sample = ("Maria Clara Santos, maria.santos@example.ph, +63 917 555 0142, "
              "TIN 284-551-903, 142 Mabini Street, Quezon City, policy HS-2026-88412")
    redacted, _ = redact(sample)
    print(f"  before  {sample}")
    print(f"  after   {redacted}")
    print(f"  stable  repeat run gives the same tokens: "
          f"{redact(sample)[0] == redacted}")

    print("\nApplied to the knowledge base as a safety net")
    print("-" * 84)
    source = PROCESSED / "cleaned_records.jsonl"
    records = [json.loads(line) for line
               in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    records, summary = protect_records(records)

    print(f"  records scanned   {len(records)}")
    print(f"  records redacted  {summary['records_touched']}")
    if summary["by_kind"]:
        for kind, count in sorted(summary["by_kind"].items()):
            print(f"    {kind:<16}{count}")
    else:
        print("    none, which is the expected result: customer rows were")
        print("    excluded at extraction rather than redacted afterwards")

    out = PROCESSED / "records.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    export_text = (RAW / "forms" / "lead_form_export.csv").read_text(encoding="utf-8")
    redacted_export, findings = redact(export_text)
    (PROCESSED / "lead_form_export.redacted.csv").write_text(
        redacted_export, encoding="utf-8")

    print(f"\n  written to  data/processed/records.jsonl")
    print(f"              data/processed/lead_form_export.redacted.csv "
          f"({len(findings)} items removed)")

    print("\nKnown limits")
    print("-" * 84)
    print("  Name detection depends on a stop list built from this corpus, so")
    print("  it will not carry to a corpus it has not seen. It is off when")
    print("  rewriting knowledge base records and in the outbound guard, where")
    print("  a false positive would corrupt policy text or drop a live call.")
    print("  Addresses are matched to the locality, not the postcode, leaving")
    print("  about 2 per cent of a typical address string in place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
