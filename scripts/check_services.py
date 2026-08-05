"""
Confirms every service is reachable with the keys in .env.

Each check makes a real request rather than testing for a non-empty string. A
key that's expired, revoked or pasted with a stray space looks identical to a
missing one until something uses it.

Keys are masked in all output.

    .venv\\Scripts\\python scripts/check_services.py
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

problems: list[str] = []


def mask(value: str) -> str:
    """Enough of a key to tell two apart, never enough to use one."""
    if len(value) <= 12:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}  ({len(value)} chars)"


def heading(name: str) -> None:
    print(f"\n{name}")
    print("-" * len(name))


def require(var: str) -> str | None:
    value = (os.getenv(var) or "").strip()
    if not value:
        print(f"  {var} is empty")
        problems.append(f"{var} is not set in .env")
        return None
    print(f"  key        {mask(value)}")
    return value


def check_gemini() -> None:
    heading("Gemini  (conversation, qualification, signal extraction)")
    key = require("GEMINI_API_KEY")
    if not key:
        return

    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    budget = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)

        # Same config the call path uses, so this measures what a caller hears.
        config = None
        if budget >= 0:
            config = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=budget)
            )

        start = time.perf_counter()
        response = client.models.generate_content(
            model=model, contents="Reply with the single word: ready", config=config
        )
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  model      {model}")
        print(f"  thinking   {'off' if budget == 0 else f'budget {budget}'}")
        print(f"  reply      {(response.text or '').strip()[:40]!r}")
        print(f"  latency    {elapsed:.0f} ms")

        deep = os.getenv("GEMINI_DEEP_MODEL", "")
        if deep:
            print(f"  off-call   {deep}")
    except Exception as exc:
        detail = str(exc)[:160]
        print(f"  FAILED     {type(exc).__name__}: {detail}")
        if "RESOURCE_EXHAUSTED" in detail or "429" in detail:
            problems.append(
                "Gemini is rate limited right now. The key works; wait a minute and retry."
            )
        else:
            problems.append(f"Gemini call failed: {type(exc).__name__}")


def check_groq() -> None:
    heading("Groq  (speech to text, and spare conversation model)")
    key = require("GROQ_API_KEY")
    if not key:
        return

    llm_model = os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")
    asr_model = os.getenv("GROQ_ASR_MODEL", "whisper-large-v3-turbo")
    try:
        from groq import Groq

        client = Groq(api_key=key)
        start = time.perf_counter()
        completion = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            max_tokens=8,
        )
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  model      {llm_model}")
        print(f"  reply      {completion.choices[0].message.content.strip()[:40]!r}")
        print(f"  latency    {elapsed:.0f} ms")

        # Check the transcription model separately - a working chat model says
        # nothing about whether this account can use Whisper.
        names = {m.id for m in client.models.list().data}
        if asr_model in names:
            print(f"  speech     {asr_model} available")
        else:
            print(f"  speech     {asr_model} NOT available on this account")
            problems.append(f"Groq account cannot use {asr_model}")
    except Exception as exc:
        print(f"  FAILED     {type(exc).__name__}: {str(exc)[:160]}")
        problems.append(f"Groq call failed: {type(exc).__name__}")


def check_deepgram() -> None:
    heading("Deepgram  (streaming transcription for live nudges)")
    key = require("DEEPGRAM_API_KEY")
    if not key:
        return

    headers = {"Authorization": f"Token {key}"}
    try:
        start = time.perf_counter()
        with httpx.Client(timeout=20) as client:
            projects = client.get(
                "https://api.deepgram.com/v1/projects", headers=headers
            )
            elapsed = (time.perf_counter() - start) * 1000

            if projects.status_code != 200:
                print(f"  FAILED     HTTP {projects.status_code}: {projects.text[:120]}")
                problems.append(f"Deepgram rejected the key (HTTP {projects.status_code})")
                return

            entries = projects.json().get("projects", [])
            print(f"  projects   {len(entries)}")
            print(f"  latency    {elapsed:.0f} ms")

            # The one service with a balance that can run out mid-project.
            if not entries:
                print("  credit     no projects on this key")
                problems.append("Deepgram key has no project attached")
                return

            project_id = entries[0]["project_id"]
            balances = client.get(
                f"https://api.deepgram.com/v1/projects/{project_id}/balances",
                headers=headers,
            )
            if balances.status_code != 200:
                # A Member key transcribes fine but can't read billing. Not a
                # failure, just something to check manually.
                print(
                    f"  credit     not readable (HTTP {balances.status_code}), "
                    "check the balance in the Deepgram console"
                )
                return

            found = balances.json().get("balances", [])
            if not found:
                print("  credit     none reported")
                return
            for balance in found:
                amount = balance.get("amount", 0)
                units = balance.get("units", "")
                print(f"  credit     {amount:.2f} {units} remaining")
                if isinstance(amount, (int, float)) and amount < 5:
                    problems.append("Deepgram credit is nearly exhausted")
    except Exception as exc:
        print(f"  FAILED     {type(exc).__name__}: {str(exc)[:160]}")
        problems.append(f"Deepgram call failed: {type(exc).__name__}")


def check_tts() -> None:
    """No key needed, but the voices still have to exist."""
    heading("Speech synthesis  (no key required)")

    async def run() -> None:
        import edge_tts

        catalogue = await edge_tts.list_voices()
        available = {v["ShortName"] for v in catalogue}

        wanted = {
            "English": os.getenv("TTS_VOICE_EN", "en-US-AriaNeural"),
            "Filipino": os.getenv("TTS_VOICE_FIL", "fil-PH-BlessicaNeural"),
            "Indonesian": os.getenv("TTS_VOICE_ID", "id-ID-GadisNeural"),
        }
        for label, voice in wanted.items():
            if voice in available:
                print(f"  {label:<11}{voice}")
            else:
                print(f"  {label:<11}{voice}  NOT FOUND")
                problems.append(f"{label} voice {voice} is unavailable")

        # Actually synthesise something - a name in a list proves nothing.
        start = time.perf_counter()
        audio = bytearray()
        communicator = edge_tts.Communicate("Ready.", wanted["English"])
        async for chunk in communicator.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        elapsed = (time.perf_counter() - start) * 1000

        if audio:
            print(f"  synthesis  {len(audio)} bytes in {elapsed:.0f} ms")
        else:
            print("  synthesis  produced no audio")
            problems.append("speech synthesis returned nothing")

        total = len(available)
        fil = sum(1 for v in available if v.startswith("fil-PH"))
        idn = sum(1 for v in available if v.startswith("id-ID"))
        print(f"  catalogue  {total} voices, {fil} Filipino, {idn} Indonesian")

    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"  FAILED     {type(exc).__name__}: {str(exc)[:160]}")
        problems.append(f"speech synthesis failed: {type(exc).__name__}")


def main() -> int:
    print("=" * 62)
    print("Service connectivity check")
    print("=" * 62)

    if not (PROJECT_ROOT / ".env").exists():
        print("\nNo .env file. Copy .env.example to .env and add your keys.")
        return 1

    check_gemini()
    check_groq()
    check_deepgram()
    check_tts()

    heading("Result")
    if problems:
        print(f"  {len(problems)} problem(s):\n")
        for item in problems:
            print(f"    - {item}")
        return 1
    print("  all services reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
