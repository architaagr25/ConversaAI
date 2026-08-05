"""
Builds the knowledge base: chunking, stable identifiers, versioning, storage.

Three things here are less obvious than they look.

Identifiers are derived from the source, not from position in a list. Numbering
records as they are processed means adding one page renumbers everything after
it, which breaks every citation already given out and makes version history
meaningless.

Chunks carry their heading. A passage lifted from the middle of a long section
reads as orphaned text; retrieval needs to know it is about waiting periods.

Versions are decided by content, not by rebuild. Re-running the pipeline on
unchanged sources must leave versions alone, or every record looks freshly
edited and nothing can be audited.

    python -m knowledge_base.store
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from core.config import PROJECT_ROOT

log = logging.getLogger(__name__)

PROCESSED = PROJECT_ROOT / "data" / "processed"
KB_DIR = PROJECT_ROOT / "data" / "kb"
DB_PATH = KB_DIR / "knowledge_base.sqlite"
JSONL_PATH = KB_DIR / "records.jsonl"

# A chunk has to be small enough to be a precise answer and large enough to
# stand on its own. Around 1400 characters is roughly two paragraphs, which is
# what a policy clause tends to be.
MAX_CHUNK_CHARS = 1400
CHUNK_OVERLAP = 180
# Below this a trailing fragment is folded back into the previous chunk rather
# than stored as a record that answers nothing.
MIN_CHUNK_CHARS = 120


@dataclass
class BuildReport:
    total_in: int = 0
    superseded: int = 0
    chunked: int = 0
    chunks_created: int = 0
    records_out: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(source_ref: str, chunk_index: int) -> str:
    """An identifier that survives a rebuild.

    Derived from where the content came from, so adding a page does not
    renumber the ones already there and a citation given to a caller last week
    still resolves.
    """
    digest = hashlib.sha256(source_ref.encode("utf-8")).hexdigest()[:10]
    return f"kb_{digest}_{chunk_index:02d}"


def content_hash(text: str) -> str:
    normalised = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


# --- Chunking ----------------------------------------------------------------


def _split_points(text: str) -> tuple[list[str], str]:
    """The smallest pieces it is safe to split between, and how to rejoin them.

    Table rows are kept whole, and rejoined with the newline they were split
    on. Joining rows with a space instead runs them into one line and the row
    boundaries disappear, which costs the same meaning as cutting one in half.
    """
    if "|" in text and "\n" in text:
        return [line for line in text.split("\n") if line.strip()], "\n"
    return [p for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()], " "


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split long text on sentence or row boundaries, with a little overlap.

    The overlap exists so a fact stated across a boundary is not lost by both
    chunks. It is taken from the end of the previous chunk rather than the
    start of the next, so the leading sentence of every chunk is still its own.
    """
    if len(text) <= max_chars:
        return [text]

    pieces, separator = _split_points(text)
    chunks: list[str] = []
    current: list[str] = []
    length = 0

    for piece in pieces:
        piece_len = len(piece) + 1
        if current and length + piece_len > max_chars:
            chunks.append(separator.join(current).strip())
            # Carry the tail of what was just written into the next chunk.
            tail: list[str] = []
            tail_len = 0
            for previous in reversed(current):
                if tail_len + len(previous) > overlap:
                    break
                tail.insert(0, previous)
                tail_len += len(previous) + 1
            current, length = tail, tail_len
        current.append(piece)
        length += piece_len

    if current:
        last = separator.join(current).strip()
        # A short trailing fragment belongs with what came before it.
        if chunks and len(last) < MIN_CHUNK_CHARS:
            chunks[-1] = f"{chunks[-1]}{separator}{last}".strip()
        else:
            chunks.append(last)

    return [c for c in chunks if c.strip()]


# --- Building ----------------------------------------------------------------


SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id            TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    content              TEXT NOT NULL,
    category             TEXT NOT NULL,
    business_unit        TEXT NOT NULL,
    authority            TEXT NOT NULL,
    source_type          TEXT NOT NULL,
    source_ref           TEXT NOT NULL,
    source_origin        TEXT NOT NULL,
    source_retrieved_at  TEXT NOT NULL,
    version              INTEGER NOT NULL DEFAULT 1,
    content_hash         TEXT NOT NULL,
    language             TEXT NOT NULL DEFAULT 'en',
    pii                  INTEGER NOT NULL DEFAULT 0,
    pii_types            TEXT NOT NULL DEFAULT '[]',
    terminology_variants TEXT NOT NULL DEFAULT '[]',
    conflicts_with       TEXT NOT NULL DEFAULT '[]',
    duplicate_of         TEXT NOT NULL DEFAULT '',
    quality_flags        TEXT NOT NULL DEFAULT '[]',
    char_count           INTEGER NOT NULL DEFAULT 0,
    chunk_index          INTEGER NOT NULL DEFAULT 0,
    chunk_count          INTEGER NOT NULL DEFAULT 1,
    retrievable          INTEGER NOT NULL DEFAULT 1,
    first_seen           TEXT NOT NULL,
    last_updated         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_unit_category ON records (business_unit, category);
CREATE INDEX IF NOT EXISTS idx_authority ON records (authority);
CREATE INDEX IF NOT EXISTS idx_retrievable ON records (retrievable);
CREATE INDEX IF NOT EXISTS idx_source ON records (source_origin);

CREATE TABLE IF NOT EXISTS build_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

LIST_FIELDS = ("pii_types", "terminology_variants", "conflicts_with", "quality_flags")


def load_records() -> list[dict]:
    path = PROCESSED / "records.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            "data/processed/records.jsonl is missing. Run knowledge_base.pii first."
        )
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expand_to_chunks(records: list[dict], report: BuildReport) -> list[dict]:
    """One record per chunk, each keeping the heading it came from.

    Cross references are rewritten to the new identifiers on the way through.
    The earlier stages number records by position; those numbers do not survive
    here, and a conflicts_with pointing at an identifier that no longer exists
    is worse than no pointer at all, because it looks like a working link.
    """
    out: list[dict] = []
    renamed = {r["record_id"]: stable_id(r["source_ref"], 0) for r in records}

    def remap(ids: list[str]) -> list[str]:
        return sorted({renamed.get(old, old) for old in ids})

    for record in records:
        record = {
            **record,
            "conflicts_with": remap(record.get("conflicts_with", [])),
            "duplicate_of": renamed.get(record.get("duplicate_of", ""),
                                        record.get("duplicate_of", "")),
        }
        report.total_in += 1

        # Superseded duplicates stay in the store for traceability but are not
        # searched, so the same answer cannot arrive five times.
        retrievable = 0 if record.get("duplicate_of") else 1
        if not retrievable:
            report.superseded += 1

        chunks = chunk_text(record["content"])
        if len(chunks) > 1:
            report.chunked += 1
            report.chunks_created += len(chunks)

        for index, chunk in enumerate(chunks):
            out.append({
                **record,
                "record_id": stable_id(record["source_ref"], index),
                "content": chunk,
                "content_hash": content_hash(chunk),
                "char_count": len(chunk),
                "chunk_index": index,
                "chunk_count": len(chunks),
                "retrievable": retrievable,
            })

    return out


def existing_versions(connection: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = connection.execute(
            "SELECT record_id, version, content_hash, first_seen, last_updated "
            "FROM records"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: {"version": r[1], "hash": r[2], "first_seen": r[3],
                   "last_updated": r[4]} for r in rows}


def write_store(records: list[dict], report: BuildReport) -> None:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA)

    previous = existing_versions(connection)
    stamp = now_iso()

    rows = []
    for record in records:
        record_id = record["record_id"]
        was = previous.get(record_id)

        if was is None:
            version, first_seen, last_updated = 1, stamp, stamp
            report.new += 1
        elif was["hash"] != record["content_hash"]:
            # Content changed, so the version moves and the change is dated.
            version, first_seen, last_updated = was["version"] + 1, was["first_seen"], stamp
            report.changed += 1
        else:
            # Unchanged. Both dates and the version stay exactly as they were,
            # or every rebuild looks like an edit and the history says nothing.
            version = was["version"]
            first_seen, last_updated = was["first_seen"], was["last_updated"]
            report.unchanged += 1

        rows.append((
            record_id, record["title"], record["content"], record["category"],
            record["business_unit"], record["authority"], record["source_type"],
            record["source_ref"], record["source_origin"],
            record["source_retrieved_at"], version, record["content_hash"],
            record.get("language", "en"), int(bool(record.get("pii"))),
            *[json.dumps(record.get(f, []), ensure_ascii=False) for f in LIST_FIELDS],
            record.get("duplicate_of", ""), record["char_count"],
            record["chunk_index"], record["chunk_count"], record["retrievable"],
            first_seen, last_updated,
        ))
        record["version"] = version
        record["first_seen"] = first_seen
        record["last_updated"] = last_updated

    connection.execute("DELETE FROM records")
    connection.executemany(
        """INSERT INTO records (
            record_id, title, content, category, business_unit, authority,
            source_type, source_ref, source_origin, source_retrieved_at,
            version, content_hash, language, pii, pii_types,
            terminology_variants, conflicts_with, quality_flags, duplicate_of,
            char_count, chunk_index, chunk_count, retrievable,
            first_seen, last_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )

    for key, value in {
        "built_at": stamp,
        "record_count": str(len(records)),
        "retrievable_count": str(sum(r["retrievable"] for r in records)),
        "schema_version": "1",
    }.items():
        connection.execute(
            "INSERT INTO build_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    connection.commit()
    connection.close()

    with JSONL_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    print("=" * 84)
    print("Building the knowledge base")
    print("=" * 84)

    report = BuildReport()
    source_records = load_records()
    chunked = expand_to_chunks(source_records, report)
    report.records_out = len(chunked)

    write_store(chunked, report)

    print(f"\n  sections in            {report.total_in}")
    print(f"  superseded duplicates  {report.superseded}  (stored, not searched)")
    print(f"  sections split         {report.chunked} -> {report.chunks_created} chunks")
    print(f"  records out            {report.records_out}")

    print(f"\n  new                    {report.new}")
    print(f"  content changed        {report.changed}")
    print(f"  unchanged              {report.unchanged}")

    lengths = sorted(r["char_count"] for r in chunked)
    print(f"\n  record length          min {lengths[0]}, "
          f"median {lengths[len(lengths) // 2]}, max {lengths[-1]}")

    connection = sqlite3.connect(DB_PATH)
    print("\n  searchable records by unit and category")
    print(f"    {'unit':<18}{'category':<24}{'n':>4}")
    print("    " + "-" * 46)
    for unit, category, count in connection.execute(
        "SELECT business_unit, category, COUNT(*) FROM records "
        "WHERE retrievable = 1 GROUP BY 1, 2 ORDER BY 1, 3 DESC"
    ):
        print(f"    {unit:<18}{category:<24}{count:>4}")

    total, searchable = connection.execute(
        "SELECT COUNT(*), SUM(retrievable) FROM records").fetchone()
    connection.close()

    print(f"\n{'=' * 84}")
    print(f"  {total} records stored, {searchable} searchable")
    print(f"  written to  data/kb/knowledge_base.sqlite")
    print(f"              data/kb/records.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
