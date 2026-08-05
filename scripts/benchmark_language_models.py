"""
Compares the available Gemini models on the three things that decide the choice.

Speed matters because the model sits inside a live call. What counts is not how
long a full answer takes but how long until the first words arrive, because the
reply is streamed and speech synthesis starts on the first sentence. The caller
hears silence until that moment, so time-to-first-token is the number that maps
onto the experience.

Reasoning effort matters because the newer models deliberate before answering by
default. That is worth paying for in a difficult judgement and wasteful in a
scripted phone turn, so both settings are measured rather than assumed.

Language matters because two of the three markets are not English. A model that
answers a Taglish prompt in formal Tagalog, or in polished English, is unusable
for the Philippines flow at any speed. Those replies are printed rather than
scored, because it is a judgement that has to be read.

Usage:
    .venv\\Scripts\\python scripts/benchmark_language_models.py
"""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# Version-pinned rather than using a "latest" alias, so an upstream release
# cannot quietly change behaviour in the middle of the project.
CANDIDATES = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

RUNS = 3

# Deliberately the awkward case: the honest answer is that it does not know.
SHORT_PROMPT = (
    "A caller asks whether dental treatment is covered. You have no information "
    "about dental cover. Reply in one short sentence."
)

TAGLISH_PROMPT = (
    "Reply in natural Taglish, exactly one sentence, as a Filipino insurance "
    "agent speaking on the phone: tell the caller their premium payment is due "
    "on the fifteenth."
)

INDONESIAN_PROMPT = (
    "Reply in casual spoken Bahasa Indonesia, exactly one sentence, as a "
    "financing agent on the phone: remind the customer their installment is due "
    "in three days."
)


def build_config(thinking: bool):
    """Return a request config, or None to leave the model on its defaults."""
    from google.genai import types

    if thinking:
        return None
    return types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    )


def measure(client, model: str, thinking: bool) -> dict | None:
    """Time the wait before the first words and the wait for the whole reply."""
    first_token: list[float] = []
    complete: list[float] = []
    reply = ""

    try:
        config = build_config(thinking)
        for _ in range(RUNS):
            start = time.perf_counter()
            seen_first = False
            pieces: list[str] = []
            stream = client.models.generate_content_stream(
                model=model, contents=SHORT_PROMPT, config=config
            )
            for chunk in stream:
                text = chunk.text or ""
                if text and not seen_first:
                    first_token.append((time.perf_counter() - start) * 1000)
                    seen_first = True
                pieces.append(text)
            complete.append((time.perf_counter() - start) * 1000)
            reply = "".join(pieces).strip().replace("\n", " ")
            if not seen_first:
                first_token.append(complete[-1])
    except Exception as exc:
        return {"error": str(exc)[:70]}

    return {
        "ttft": statistics.median(first_token),
        "ttft_worst": max(first_token),
        "total": statistics.median(complete),
        "reply": reply,
    }


def main() -> int:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        print("GEMINI_API_KEY is not set in .env")
        return 1

    from google import genai

    client = genai.Client(api_key=key)

    print("Language model comparison")
    print("=" * 78)
    print("\nStreaming latency. First token is what the caller experiences as silence.\n")
    print(f"{'model':<24}{'thinking':<11}{'1st token':>11}{'worst':>9}{'full':>9}")
    print("-" * 78)

    usable: list[tuple[str, bool, float, str]] = []
    for model in CANDIDATES:
        for thinking in (True, False):
            label = "default" if thinking else "off"
            result = measure(client, model, thinking)
            if result is None or "error" in result:
                reason = result["error"] if result else "no result"
                print(f"{model:<24}{label:<11}  unavailable  {reason}")
                continue
            print(
                f"{model:<24}{label:<11}{result['ttft']:>9.0f}ms"
                f"{result['ttft_worst']:>7.0f}ms{result['total']:>7.0f}ms"
            )
            usable.append((model, thinking, result["ttft"], result["reply"]))

    if not usable:
        print("\nNo models responded.")
        return 1

    print("\nHonest answer when the information is missing")
    print("-" * 78)
    seen: set[str] = set()
    for model, thinking, _, reply in usable:
        if model in seen:
            continue
        seen.add(model)
        print(f"  {model}: {reply[:90]}")

    models_only = sorted({m for m, _, _, _ in usable})
    for label, prompt in (
        ("Taglish generation", TAGLISH_PROMPT),
        ("Bahasa Indonesia generation", INDONESIAN_PROMPT),
    ):
        print(f"\n{label}")
        print("-" * 78)
        for model in models_only:
            try:
                response = client.models.generate_content(
                    model=model, contents=prompt, config=build_config(False)
                )
                print(f"  {model}")
                print(f"    {(response.text or '').strip()}")
            except Exception as exc:
                print(f"  {model}: failed, {str(exc)[:60]}")

    fastest = min(usable, key=lambda row: row[2])
    print(f"\n{'=' * 78}")
    print(
        f"Lowest wait before speech: {fastest[0]} with thinking "
        f"{'on' if fastest[1] else 'off'}, {fastest[2]:.0f} ms"
    )
    print("Read the localized samples above before settling on this. Speed only")
    print("wins where the Taglish and Bahasa output is genuinely usable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
