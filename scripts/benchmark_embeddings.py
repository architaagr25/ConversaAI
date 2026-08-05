"""
Compares embedding options on the three languages this system has to serve.

The knowledge base is written mostly in English, but callers ask questions in
Taglish and in Bahasa Indonesia. Retrieval therefore depends on the embedding
model placing a Tagalog or Indonesian question near an English answer. Not every
multilingual model can do this, and the failure is silent: the model loads, the
search runs, and the wrong passage comes back.

Two options are measured here:

  local    runs on this machine, no network, no cost, no rate limit
  hosted   a network call per query, but far wider language coverage

The comparison is deliberately not decided on English alone, because both
options handle English well and the difference only appears elsewhere.

Usage:
    .venv\\Scripts\\python scripts/benchmark_embeddings.py
"""

from __future__ import annotations

import os
import statistics
import time
import warnings
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
MODEL_CACHE = PROJECT_ROOT / ".cache" / "models"

# Each case is a pair that should score highly, except the two controls.
# Without controls, a model that returns high similarity for everything looks
# perfect. What matters is the gap between real matches and unrelated text.
CASES = [
    ("English to English", "match",
     "What is the waiting period for pre-existing conditions?",
     "How long until pre-existing illnesses are covered?"),

    ("Indonesian to English", "match",
     "Berapa cicilan per bulan kalau tenor 24 bulan?",
     "What is the monthly installment amount over a 24 month term?"),

    ("Tagalog to English", "match",
     "Ano po ang hulog kada buwan?",
     "What is the monthly payment amount?"),

    ("Taglish to English", "match",
     "Magkano po ang premium ko kung monthly ang bayad?",
     "How much is my premium if I pay monthly?"),

    ("Tagalog to Tagalog", "match",
     "Ano po ang hulog kada buwan?",
     "Magkano ang bayad ko bawat buwan?"),

    ("Indonesian control", "control",
     "Berapa cicilan per bulan kalau tenor 24 bulan?",
     "The office car park closes at eight in the evening."),

    ("Tagalog control", "control",
     "Ano po ang hulog kada buwan?",
     "The office car park closes at eight in the evening."),
]

LATENCY_QUERIES = [
    "What is the waiting period for pre-existing conditions?",
    "Magkano po ang premium para sa life insurance?",
    "Berapa cicilan per bulan kalau tenor 24 bulan?",
]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def local_encoder():
    """Return a function that embeds text on this machine, or None."""
    name = os.getenv(
        "EMBEDDING_LOCAL_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    try:
        from fastembed import TextEmbedding

        model = TextEmbedding(model_name=name, cache_dir=str(MODEL_CACHE))
        list(model.embed(["warm up"]))
    except Exception as exc:
        print(f"  local model unavailable: {str(exc)[:70]}")
        return None, name

    return (lambda texts: np.array(list(model.embed(texts)))), name


def hosted_encoder():
    """Return a function that embeds text through the hosted service, or None."""
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    name = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
    dims = int(os.getenv("EMBEDDING_DIMS", "768"))
    if not key:
        print("  hosted model unavailable: GEMINI_API_KEY is not set")
        return None, name

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)

    def encode(texts: list[str]) -> np.ndarray:
        # Retried because the free tier throttles, and a throttle here is a
        # pause rather than a failure.
        for attempt in range(4):
            try:
                response = client.models.embed_content(
                    model=name,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        task_type="SEMANTIC_SIMILARITY",
                        output_dimensionality=dims,
                    ),
                )
                return np.array([e.values for e in response.embeddings])
            except Exception as exc:
                if "429" in str(exc) and attempt < 3:
                    time.sleep(12)
                    continue
                raise
        raise RuntimeError("throttled repeatedly")

    return encode, f"{name} at {dims} dimensions"


def evaluate(label: str, encode, name: str) -> dict | None:
    print(f"\n{'=' * 72}")
    print(f"{label}: {name}")
    print("=" * 72)

    scores: dict[str, tuple[str, float]] = {}
    print(f"\n  {'case':<24}{'kind':<10}score")
    print(f"  {'-' * 44}")
    for case_label, kind, left, right in CASES:
        try:
            vectors = encode([left, right])
        except Exception as exc:
            print(f"  {case_label:<24}failed: {str(exc)[:40]}")
            continue
        score = cosine(vectors[0], vectors[1])
        scores[case_label] = (kind, score)
        flag = ""
        if kind == "match" and score < 0.40:
            flag = "  <-- too low to retrieve reliably"
        print(f"  {case_label:<24}{kind:<10}{score:+.3f}{flag}")

    if not scores:
        return None

    timings = []
    for query in LATENCY_QUERIES * 3:
        try:
            start = time.perf_counter()
            encode([query])
            timings.append((time.perf_counter() - start) * 1000)
        except Exception:
            pass

    worst_match = min(s for _, (k, s) in scores.items() if k == "match")
    best_control = max(s for _, (k, s) in scores.items() if k == "control")
    separation = worst_match - best_control

    print(f"\n  weakest match     {worst_match:+.3f}")
    print(f"  strongest control {best_control:+.3f}")
    print(f"  separation        {separation:+.3f}"
          "   (a match must clear an unrelated sentence by a wide margin)")
    if timings:
        print(f"  query latency     median {statistics.median(timings):.0f} ms, "
              f"worst {max(timings):.0f} ms")

    return {
        "label": label,
        "name": name,
        "separation": separation,
        "median_ms": statistics.median(timings) if timings else float("nan"),
    }


def main() -> int:
    print("Embedding comparison")
    print("English, Tagalog and Indonesian retrieval quality, plus query latency")

    results = []
    for label, factory in (("local", local_encoder), ("hosted", hosted_encoder)):
        encode, name = factory()
        if encode is None:
            continue
        outcome = evaluate(label, encode, name)
        if outcome:
            results.append(outcome)

    if not results:
        print("\nNothing could be evaluated.")
        return 1

    print(f"\n{'=' * 72}")
    print("Summary")
    print("=" * 72)
    print(f"{'option':<10}{'separation':>12}{'median query':>15}")
    print("-" * 72)
    for r in results:
        print(f"{r['label']:<10}{r['separation']:>+12.3f}{r['median_ms']:>13.0f}ms")

    best = max(results, key=lambda r: r["separation"])
    print(f"\nWidest separation: {best['label']}  ({best['name']})")
    print("A negative separation means unrelated text scores as highly as a real")
    print("match, so that option cannot be used for retrieval at any speed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
