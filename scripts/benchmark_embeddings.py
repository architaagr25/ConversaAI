"""
Compares embedding models on the three languages this system has to serve.

The knowledge base is written mostly in English, but callers ask questions in
Taglish and in Bahasa Indonesia. Retrieval therefore depends on the embedding
model placing a Tagalog or Indonesian question near an English answer. Not every
multilingual model can do this, and the failure is silent: the model loads, the
search runs, and the wrong passage comes back.

This script measures the two things that decide the choice:

  quality  cosine similarity between question pairs that should match, checked
           against a pair that should not, so a model that scores everything
           highly is not mistaken for a good one
  speed    how long a single query takes to embed, since this sits inside the
           call loop and adds directly to how long the caller waits

Usage:
    .venv\\Scripts\\python scripts/benchmark_embeddings.py
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

warnings.filterwarnings("ignore")

MODEL_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "models"

CANDIDATES = [
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "intfloat/multilingual-e5-large",
]

# Each case is a pair that should score highly, except the two marked as controls.
# Without controls a model that returns high similarity for everything looks perfect.
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


def evaluate(model_name: str) -> dict | None:
    print(f"\n{'=' * 72}")
    print(model_name)
    print("=" * 72)
    print("loading (downloads on first use)...")

    try:
        model = TextEmbedding(model_name=model_name, cache_dir=str(MODEL_CACHE))
        list(model.embed(["warm up"]))
    except Exception as exc:
        print(f"  could not load: {exc}")
        return None

    scores: dict[str, float] = {}
    print(f"\n  {'case':<24}{'kind':<10}score")
    print(f"  {'-' * 44}")
    for label, kind, left, right in CASES:
        va, vb = np.array(list(model.embed([left, right])))
        score = cosine(va, vb)
        scores[label] = score
        flag = ""
        if kind == "match" and score < 0.40:
            flag = "  <-- too low to retrieve reliably"
        print(f"  {label:<24}{kind:<10}{score:+.3f}{flag}")

    timings = []
    for query in LATENCY_QUERIES * 5:
        start = time.perf_counter()
        list(model.embed([query]))
        timings.append((time.perf_counter() - start) * 1000)

    median = float(np.median(timings))
    p95 = float(np.percentile(timings, 95))
    dims = len(list(model.embed(["x"]))[0])

    print(f"\n  dimensions        {dims}")
    print(f"  query latency     median {median:.0f} ms, p95 {p95:.0f} ms")

    worst_match = min(s for label, s in scores.items() if "control" not in label)
    best_control = max(s for label, s in scores.items() if "control" in label)
    print(f"  weakest match     {worst_match:+.3f}")
    print(f"  strongest control {best_control:+.3f}")
    print(f"  separation        {worst_match - best_control:+.3f}"
          "   (a match must beat an unrelated sentence by a clear margin)")

    return {
        "model": model_name,
        "dims": dims,
        "median_ms": median,
        "p95_ms": p95,
        "scores": scores,
        "separation": worst_match - best_control,
    }


def main() -> int:
    print("Embedding model comparison")
    print("English, Tagalog and Indonesian retrieval quality, plus query latency")

    results = [r for r in (evaluate(name) for name in CANDIDATES) if r]
    if not results:
        print("\nno models could be evaluated")
        return 1

    print(f"\n{'=' * 72}")
    print("Summary")
    print("=" * 72)
    print(f"{'model':<52}{'dim':>5}{'median':>9}{'sep':>8}")
    print("-" * 74)
    for r in results:
        short = r["model"].split("/")[-1]
        print(f"{short:<52}{r['dims']:>5}{r['median_ms']:>7.0f}ms{r['separation']:>+8.3f}")

    best = max(results, key=lambda r: r["separation"])
    print(f"\nWidest separation between real matches and unrelated text: {best['model']}")
    print("That is the model to index with, provided its latency is acceptable")
    print("inside the call loop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
