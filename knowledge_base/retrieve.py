"""
Hybrid retrieval over the knowledge base.

Two searches run against every question. Keyword search finds records that use
the caller's exact words, which matters because policy vocabulary is precise:
"grace period" and "denda" are terms, not topics. Vector search finds records
that mean the same thing in different words, which matters because a caller
asking about their hulog will not say premium.

They run at the same time rather than one after the other, though at this
corpus size that is preparation rather than optimisation. Measured on 102
records: the embedding round trip takes 475 ms, the keyword search 0.3 ms, and
overlapping them saves 0.3 ms. The structure earns its place when the corpus is
large enough for keyword search to cost something; today the round trip is
essentially the whole query and the honest way to make retrieval faster is to
cache query embeddings, not to reorder these two.

Ranking is not purely by similarity. A marketing page is often the better
keyword match because it is written in plainer language than the policy
document it contradicts, so authority is applied after fusion. And when nothing
scores well enough, the honest result is nothing at all: the agent says it does
not know rather than answering from the closest thing it could find.

    python -m knowledge_base.retrieve            build the index
    python -m knowledge_base.retrieve "query"    search
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from core.config import settings
from core.embeddings import get_embedder, require_matching
from core.timing import track
from knowledge_base.store import DB_PATH, KB_DIR

log = logging.getLogger(__name__)

VECTORS_PATH = KB_DIR / "vectors.npy"
VECTOR_IDS_PATH = KB_DIR / "vector_ids.json"

# Reciprocal rank fusion. The constant softens the difference between rank 1
# and rank 2, so a record both searches like beats one that either loves.
RRF_K = 60

# Applied after fusion, not before. A campaign page frequently out-matches the
# policy document it contradicts, because marketing copy is written in the
# words people actually search with, while the clause that governs the answer
# is written in formal legal English that matches nothing a caller types.
#
# The spread is wide on purpose. A narrow one loses to the rank difference it
# is meant to correct: asked whether a 62 year old can apply, a FAQ about
# adding family members outranked the eligibility clause stating the entry age
# limit, because the FAQ shares more words with the question.
AUTHORITY_WEIGHT = {
    "binding": 1.00,
    "operational": 0.92,
    "published": 0.78,
    "promotional": 0.35,
}

# A record already known to contradict something more authoritative is not
# excluded, but it has to be clearly better than the alternative to surface.
CONTRADICTION_PENALTY = 0.5

# Rank fusion throws away how much better a match was, only that it was better.
# That is usually fine and occasionally wrong: asked what the Plus plan covers,
# the comparison table was the single closest record in the corpus by a wide
# margin and still lost to five policy clauses that merely outrank it on
# authority. Adding the similarity back lets a large semantic gap outweigh a
# one-step authority difference, while a small one still loses to it.
SIMILARITY_WEIGHT = 0.05

# Similarity is rescaled from this baseline rather than from zero. Unrelated
# text scores around 0.5 with this model, so treating 0.5 as the floor is what
# makes the remaining range meaningful.
SIMILARITY_FLOOR = 0.50


@dataclass
class Result:
    record_id: str
    title: str
    content: str
    source_ref: str
    category: str
    business_unit: str
    authority: str
    score: float
    similarity: float
    keyword_rank: int | None = None
    vector_rank: int | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        return self.source_ref


@dataclass
class SearchOutcome:
    """What a search found, and whether it is worth answering from."""

    query: str
    results: list[Result]
    confident: bool
    best_similarity: float
    reason: str = ""

    def __bool__(self) -> bool:
        return self.confident and bool(self.results)


def tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def load_records(retrievable_only: bool = True) -> list[dict]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    query = "SELECT * FROM records"
    if retrievable_only:
        query += " WHERE retrievable = 1"
    rows = [dict(r) for r in connection.execute(query)]
    connection.close()

    for row in rows:
        for field_name in ("pii_types", "terminology_variants",
                           "conflicts_with", "quality_flags"):
            row[field_name] = json.loads(row[field_name])
    return rows


def describe_table(title: str, content: str) -> str:
    """A sentence saying what a table is about.

    Tables embed badly. A grid of figures is mostly numbers, so "Benefit |
    Essential | Plus | Max" followed by twenty rows of pesos sits nowhere near
    "what does the Plus plan cover?" in vector space, and the record that holds
    the answer never surfaces. Naming the columns in prose gives the embedding
    something to match against.
    """
    lines = [line for line in content.split("\n") if "|" in line]
    if len(lines) < 2:
        return ""

    columns = [c.strip() for c in lines[0].split("|") if c.strip()]
    rows = [line.split("|")[0].strip() for line in lines[1:6]]
    rows = [r for r in rows if r]
    if not columns:
        return ""

    described = f"{title}. A table of {', '.join(columns)}."
    if rows:
        described += f" Covering {', '.join(rows)}."
    return described


def indexable_text(record: dict) -> str:
    """What gets embedded and searched.

    The heading is included because a chunk lifted out of a long section reads
    as orphaned text without it. The vocabulary variants are included because a
    caller asking about their hulog has to reach a record written about
    premiums, and keyword search cannot make that leap on its own.
    """
    parts = [record["title"]]

    described = describe_table(record["title"], record["content"])
    if described:
        parts.append(described)

    parts.append(record["content"])
    if record["terminology_variants"]:
        parts.append(" ".join(record["terminology_variants"]))
    return "\n".join(parts)


# --- Building ----------------------------------------------------------------


def build_index() -> dict:
    """Embed every searchable record and store the vectors beside the database."""
    records = load_records()
    embedder = get_embedder()

    with track("index_embed", detail=f"{len(records)} records"):
        vectors = embedder.encode_documents([indexable_text(r) for r in records])

    KB_DIR.mkdir(parents=True, exist_ok=True)
    np.save(VECTORS_PATH, vectors)
    VECTOR_IDS_PATH.write_text(
        json.dumps([r["record_id"] for r in records]), encoding="utf-8")

    # The signature is stored so a later search cannot be run with a different
    # model. Vectors from two models are not comparable, and mixing them
    # returns plausible nonsense rather than raising.
    connection = sqlite3.connect(DB_PATH)
    connection.execute(
        "INSERT INTO build_meta (key, value) VALUES ('embedder', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (embedder.signature,),
    )
    connection.commit()
    connection.close()

    return {"records": len(records), "dimensions": int(vectors.shape[1]),
            "signature": embedder.signature}


def stored_signature() -> str:
    connection = sqlite3.connect(DB_PATH)
    row = connection.execute(
        "SELECT value FROM build_meta WHERE key = 'embedder'").fetchone()
    connection.close()
    return row[0] if row else ""


# --- Searching ---------------------------------------------------------------


def rank_candidates(
    query: str,
    keyword_ranks: dict[str, int],
    vector_ranks: dict[str, int],
    similarities: dict[str, float],
    by_id: dict[str, dict],
    business_unit: str | None = None,
    top_k: int | None = None,
) -> SearchOutcome:
    """Fuse the two rankings, weight by authority, and decide whether to answer.

    Separate from the searching so it can be tested without a network call,
    which is where all the judgement lives.
    """
    top_k = top_k or settings.retrieval_top_k

    fused: dict[str, float] = {}
    for ranks in (keyword_ranks, vector_ranks):
        for record_id, rank in ranks.items():
            fused[record_id] = fused.get(record_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    scored: list[Result] = []
    for record_id, base in fused.items():
        record = by_id.get(record_id)
        if record is None:
            continue
        # "group" content applies everywhere, so it is never filtered out.
        if business_unit and record["business_unit"] not in (business_unit, "group"):
            continue

        weight = AUTHORITY_WEIGHT.get(record["authority"], 0.78)
        if any(f.startswith("contradicts_") for f in record["quality_flags"]):
            weight *= CONTRADICTION_PENALTY

        similarity = similarities.get(record_id, 0.0)
        headroom = max(0.0, similarity - SIMILARITY_FLOOR) / (1 - SIMILARITY_FLOOR)
        base += SIMILARITY_WEIGHT * headroom

        scored.append(Result(
            record_id=record_id,
            title=record["title"],
            content=record["content"],
            source_ref=record["source_ref"],
            category=record["category"],
            business_unit=record["business_unit"],
            authority=record["authority"],
            score=base * weight,
            similarity=similarity,
            keyword_rank=keyword_ranks.get(record_id),
            vector_rank=vector_ranks.get(record_id),
            flags=record["quality_flags"],
        ))

    scored.sort(key=lambda r: r.score, reverse=True)
    top = scored[:top_k]
    best = max((r.similarity for r in top), default=0.0)

    confident = bool(top) and best >= settings.retrieval_min_score
    reason = ""
    if not top:
        reason = "nothing matched"
    elif not confident:
        reason = (f"best match scored {best:.2f}, below the floor of "
                  f"{settings.retrieval_min_score:.2f}")

    return SearchOutcome(query=query, results=top, confident=confident,
                         best_similarity=best, reason=reason)


class Retriever:
    """Loads the index once and answers questions against it."""

    def __init__(self) -> None:
        from rank_bm25 import BM25Okapi

        self.records = load_records()
        self.by_id = {r["record_id"]: r for r in self.records}

        if not VECTORS_PATH.exists():
            raise FileNotFoundError(
                "no vector index. Run: python -m knowledge_base.retrieve")

        self.embedder = get_embedder()
        require_matching(stored_signature(), self.embedder)

        self.vectors = np.load(VECTORS_PATH)
        order = json.loads(VECTOR_IDS_PATH.read_text(encoding="utf-8"))
        # Vectors are stored in build order; the database may return a
        # different one. Align by identifier rather than trusting position.
        position = {record_id: i for i, record_id in enumerate(order)}
        self.rows = [r for r in self.records if r["record_id"] in position]
        self.vectors = np.stack([self.vectors[position[r["record_id"]]]
                                 for r in self.rows])

        self.bm25 = BM25Okapi([tokenise(indexable_text(r)) for r in self.rows])

    def _keyword_ranks(self, query: str) -> dict[str, int]:
        scores = self.bm25.get_scores(tokenise(query))
        ordered = np.argsort(scores)[::-1]
        return {self.rows[i]["record_id"]: rank
                for rank, i in enumerate(ordered) if scores[i] > 0}

    def _vector_ranks(self, vector: np.ndarray) -> tuple[dict[str, int], dict[str, float]]:
        similarities = self.vectors @ vector
        ordered = np.argsort(similarities)[::-1]
        ranks = {self.rows[i]["record_id"]: rank for rank, i in enumerate(ordered)}
        scores = {self.rows[i]["record_id"]: float(similarities[i])
                  for i in range(len(self.rows))}
        return ranks, scores

    def search(self, query: str, business_unit: str | None = None,
               top_k: int | None = None, trace: str = "") -> SearchOutcome:
        top_k = top_k or settings.retrieval_top_k

        # The embedding is a network round trip and the keyword search is
        # local. Starting the round trip first means the keyword work happens
        # inside the wait rather than after it.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_vector = pool.submit(self.embedder.encode_query, query)
            with track("bm25", trace=trace):
                keyword_ranks = self._keyword_ranks(query)
            with track("embed_query", trace=trace):
                vector = pending_vector.result()

        vector_ranks, similarities = self._vector_ranks(vector)
        return rank_candidates(query, keyword_ranks, vector_ranks, similarities,
                               self.by_id, business_unit, top_k)


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    return Retriever()


# --- Runner ------------------------------------------------------------------


def main() -> int:
    from core.logging_setup import setup_logging

    setup_logging(quiet_console=True)

    if len(sys.argv) > 1:
        retriever = get_retriever()
        query = " ".join(sys.argv[1:])
        outcome = retriever.search(query)

        print(f"\nQuery: {query}")
        print("=" * 88)
        if not outcome:
            print(f"  No confident answer. {outcome.reason}")
            print("  The agent would say it does not have this information.")
            if outcome.results:
                print(f"\n  Closest match was {outcome.results[0].title!r} at "
                      f"{outcome.best_similarity:.2f}")
            return 0

        for position, result in enumerate(outcome.results, 1):
            print(f"\n  {position}. {result.title}")
            print(f"     {result.category} / {result.authority} / "
                  f"{result.business_unit}")
            print(f"     score {result.score:.4f}   similarity "
                  f"{result.similarity:.3f}   "
                  f"keyword rank {result.keyword_rank}   "
                  f"vector rank {result.vector_rank}")
            print(f"     cite: {result.citation}")
            print(f"     {result.content[:150].replace(chr(10), ' ')}")
        return 0

    print("=" * 88)
    print("Building the vector index")
    print("=" * 88)
    summary = build_index()
    print(f"\n  records embedded  {summary['records']}")
    print(f"  dimensions        {summary['dimensions']}")
    print(f"  model             {summary['signature']}")
    print(f"\n  written to  data/kb/vectors.npy")
    print("\n  Search with:  python -m knowledge_base.retrieve \"your question\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
