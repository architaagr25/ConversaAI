"""
Exercises the shared foundation against the real services.

Unit tests cover the logic, this covers the wiring: config loads, a reply
streams, queries embed in all three languages, and the latency table prints.

Those numbers are the floor for everything built on top.

    .venv\\Scripts\\python scripts/verify_core.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import RECORDER, get_embedder, live, settings, setup_logging  # noqa: E402
from core.timing import Stopwatch, track  # noqa: E402

TRACE = "startup-check"


def check_configuration() -> bool:
    print("Configuration")
    print("-" * 62)
    print(f"  live model     {settings.gemini_model}")
    print(f"  off-call model {settings.gemini_deep_model}")
    print(f"  deliberation   {'off' if settings.gemini_thinking_budget == 0 else 'on'}")
    print(f"  embeddings     {settings.embedding_provider} / {settings.embedding_model}")
    print(f"  retrieval      top {settings.retrieval_top_k}, "
          f"floor {settings.retrieval_min_score}")
    print(f"  cache          {settings.cache_path}")

    missing = settings.missing_keys()
    if missing:
        print(f"\n  missing credentials: {', '.join(missing)}")
        return False
    print("  credentials    all present")
    return True


def check_streaming() -> None:
    """Time the wait before the first word - the silence a caller hears."""
    print("\nStreaming a reply")
    print("-" * 62)

    prompt = (
        "A caller asks whether their policy covers dental treatment. You have "
        "no information about dental cover. Reply in one short sentence."
    )

    # First request also pays for client construction and the handshake, which
    # is several times the model's own latency. Warming up first means the
    # numbers below describe a turn mid-call, not the very first one.
    cold_ms = live.warmup()
    print(f"  connection warm-up {cold_ms:.0f} ms  (paid once, before any call)")

    reply = ""
    for run in range(1, 4):
        watch = Stopwatch()
        pieces: list[str] = []
        first_token_ms = None

        for piece in live.stream(prompt, trace=f"{TRACE}-{run}"):
            if first_token_ms is None:
                first_token_ms = watch.mark("first token")
            pieces.append(piece)

        reply = "".join(pieces).strip()
        if first_token_ms is None:
            print(f"  turn {run}: no output received")
            continue
        print(f"  turn {run}: first word after {first_token_ms:>6.0f} ms, "
              f"whole reply {watch.elapsed_ms:>6.0f} ms")

    print(f"\n  last reply         {reply[:78]}")

    from core.llm import clean_for_speech

    if clean_for_speech(reply) != reply:
        print("  note: formatting was stripped before this would reach a voice")


def check_embeddings() -> None:
    print("\nEmbedding a query in each market's language")
    print("-" * 62)

    embedder = get_embedder()
    print(f"  signature   {embedder.signature}")

    queries = {
        "English": "What is the waiting period for pre-existing conditions?",
        "Taglish": "Magkano po ang premium ko kung monthly ang bayad?",
        "Indonesian": "Berapa cicilan per bulan kalau tenor 24 bulan?",
    }
    vectors = {}
    for label, text in queries.items():
        with track("embed_query", trace=TRACE, detail=label):
            vectors[label] = embedder.encode_query(text)
        print(f"  {label:<11}{len(vectors[label])} dimensions")

    # Sanity check the vectors carry meaning: an English premium question should
    # sit closer to its Taglish form than to an Indonesian installment question.
    english_reference = embedder.encode_query("How much is my monthly premium?")
    close = float(english_reference @ vectors["Taglish"])
    far = float(english_reference @ vectors["Indonesian"])
    print(f"\n  premium question vs its Taglish form   {close:+.3f}")
    print(f"  premium question vs a different topic  {far:+.3f}")
    print("  " + ("cross-language matching works"
                  if close > far else "WARNING: languages are not aligning"))


def main() -> int:
    setup_logging(quiet_console=True)

    print("=" * 62)
    print("Foundation check")
    print("=" * 62)

    if not check_configuration():
        return 1

    try:
        check_streaming()
        check_embeddings()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {str(exc)[:200]}")
        return 1

    print("\nMeasured latency")
    print("-" * 62)
    print(RECORDER.summary())
    print("\nThese are the floor for every call built on this foundation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
