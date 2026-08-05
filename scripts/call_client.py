"""
Places a call against the running server, the way a browser would.

Speech is synthesised for each scripted line, streamed to the server in real
time as 20 millisecond frames, and the replies are collected as audio and text.
It exercises the whole path, including the endpointer, which a text harness
does not.

Produces a WAV of the call, a transcript and a summary, which are the recorded
evidence the assessment asks for.

    .venv\\Scripts\\python -m voice_agent.server        (in one terminal)
    .venv\\Scripts\\python scripts/call_client.py --script cooperative
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings  # noqa: E402
from voice_agent.audio import FRAME_BYTES, FRAME_MS, SAMPLE_RATE, silence, to_wav  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "results" / "calls"

# The five situations the assessment requires a voice agent to handle.
SCRIPTS = {
    "cooperative": [
        "Yes, now is a good time.",
        "I am looking for cover for myself and my wife.",
        "I am thirty five years old.",
        "Yes, I live in the Philippines.",
        "No, I am not in hospital.",
        "About sixty thousand a month.",
        "What does the Plus plan cover?",
    ],
    "objection": [
        "Yes, go ahead.",
        "I am forty two.",
        "Yes, I am based in Manila.",
        "No, I am not.",
        "Honestly, this sounds more expensive than I was expecting.",
        "I already have insurance through my employer anyway.",
    ],
    "conflicting": [
        "Sure, go ahead.",
        "I am twenty eight years old.",
        "Actually sorry, I meant sixty five.",
        "Yes, I live here.",
        "Am I still eligible for this plan?",
    ],
    "out_of_scope": [
        "Yes that is fine.",
        "Does the plan cover dental treatment?",
        "What is the capital of France?",
    ],
    "escalation": [
        "Yes okay.",
        "I am thirty years old.",
        "Actually, can I speak to a real person please?",
    ],
}


async def synthesise(text: str, voice: str) -> bytes:
    import edge_tts

    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


def to_pcm(mp3: bytes, rate: int = SAMPLE_RATE) -> bytes:
    if not mp3:
        return b""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(rate), "-ac", "1",
         "pipe:1"],
        input=mp3, capture_output=True, check=False)
    return result.stdout


class Call:
    """One call, recorded."""

    def __init__(self, name: str, pack: str, voice: str) -> None:
        self.name = name
        self.pack = pack
        self.voice = voice
        self.lines: list[dict] = []
        self.audio = bytearray()      # the whole call, both sides, in order
        self.summary: dict = {}
        self.waiting_audio: list[bytes] = []
        self.state = ""
        self.started = time.perf_counter()
        self.turn_latency: list[float] = []
        self.awaiting_reply = 0.0
        self.first_reply_at = 0.0

    def note(self, speaker: str, text: str, **detail) -> None:
        self.lines.append({"at": round(time.perf_counter() - self.started, 2),
                           "speaker": speaker, "text": text, **detail})
        marker = {"caller": "You", "agent": "Agent"}.get(speaker, speaker)
        flag = ""
        # Only meaningful where the turn went looking. Flagging a slot answer
        # as ungrounded makes an ordinary call look like it failed throughout.
        if detail.get("sought_knowledge"):
            flag = "  [grounded]" if detail.get("grounded") \
                else "  [no supporting record, agent said so]"
        print(f"  {marker:<6} {text}{flag}")
        for citation in detail.get("citations", [])[:2]:
            print(f"         cite: {citation}")


async def drain_replies(socket, call: Call, quiet_for: float = 2.5) -> None:
    """Collect everything the server sends until it goes quiet."""
    last = time.perf_counter()
    while True:
        try:
            message = await asyncio.wait_for(socket.recv(), timeout=0.4)
        except asyncio.TimeoutError:
            if call.state in ("listening", "ended") and \
                    time.perf_counter() - last > quiet_for:
                return
            continue
        except Exception:
            return

        last = time.perf_counter()
        if isinstance(message, bytes):
            # The moment the caller would first hear a voice.
            if call.awaiting_reply and not call.first_reply_at:
                call.first_reply_at = time.perf_counter()
            call.audio.extend(to_pcm(message))
            continue

        event = json.loads(message)
        kind = event.get("kind")
        if kind == "state":
            call.state = event.get("state", "")
        elif kind == "transcript":
            detail = dict(event.get("detail", {}))
            speaker = detail.pop("speaker", "?")
            call.note(speaker, event.get("text", ""), **detail)
        elif kind == "summary":
            call.summary = event.get("detail", {})
        elif kind == "status":
            print(f"  ...    {event.get('text')}")
        elif kind == "error":
            print(f"  ERROR  {event.get('text')}")
        elif kind == "ended":
            call.state = "ended"
            return


async def speak_line(socket, call: Call, text: str) -> None:
    """Send one caller line as real-time audio."""
    pcm = to_pcm(await synthesise(text, call.voice))
    # Not logged here. The server sends back what it actually heard, which is
    # the honest record, and logging both shows every line twice.
    call.audio.extend(pcm)

    # Paced like a real microphone. Sending it all at once would let the
    # endpointer see a whole turn in a single chunk, which never happens.
    for start in range(0, len(pcm), FRAME_BYTES):
        await socket.send(pcm[start:start + FRAME_BYTES])
        await asyncio.sleep(FRAME_MS / 1000)

    # The pause that tells the endpointer the turn is over.
    quiet = silence(900)
    for start in range(0, len(quiet), FRAME_BYTES):
        await socket.send(quiet[start:start + FRAME_BYTES])
        await asyncio.sleep(FRAME_MS / 1000)


async def place(name: str, lines: list[str], pack: str, voice: str,
                host: str) -> Call:
    import websockets

    call = Call(name, pack, voice)
    url = f"ws://{host}/call?pack={pack}&trace=call-{name}"

    print(f"\n{'=' * 78}")
    print(f"Call: {name}   pack {pack}")
    print("=" * 78)

    async with websockets.connect(url, max_size=None) as socket:
        await drain_replies(socket, call, quiet_for=1.5)

        for line in lines:
            await speak_line(socket, call, line)
            # Timed from the end of the caller's audio, not the start. Starting
            # the clock earlier measures how long the sentence took to say,
            # which is the caller's doing and not the agent's.
            finished_speaking = time.perf_counter()
            call.awaiting_reply = finished_speaking
            await drain_replies(socket, call)
            if call.first_reply_at:
                call.turn_latency.append(
                    (call.first_reply_at - finished_speaking) * 1000)
                call.first_reply_at = 0.0
            if call.state == "ended":
                break

        if call.state != "ended":
            await socket.send(json.dumps({"command": "hangup"}))
            await drain_replies(socket, call, quiet_for=2.0)

        # The summary is sent last, after the server has written the lead and
        # the handover note. That takes a few seconds, so a timeout here means
        # keep waiting rather than give up.
        deadline = time.perf_counter() + 25
        while time.perf_counter() < deadline and not call.summary:
            try:
                message = await asyncio.wait_for(socket.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
            if isinstance(message, str):
                event = json.loads(message)
                if event.get("kind") == "summary":
                    call.summary = event.get("detail", {})

    return call


def save(call: Call) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / call.name

    (stem.with_suffix(".wav")).write_bytes(to_wav(bytes(call.audio)))

    transcript = [f"Call: {call.name}", f"Pack: {call.pack}", ""]
    for line in call.lines:
        who = {"caller": "Caller", "agent": "Agent"}.get(line["speaker"], "?")
        transcript.append(f"[{line['at']:>6.2f}s] {who}: {line['text']}")
        for citation in line.get("citations", []):
            transcript.append(f"           source: {citation}")
        if line.get("sought_knowledge") and not line.get("grounded"):
            transcript.append("           (no supporting record, agent said so)")
    transcript += ["", "Summary", "-" * 40]
    for key, value in call.summary.items():
        if key != "transcript":
            transcript.append(f"{key}: {value}")
    stem.with_suffix(".txt").write_text("\n".join(transcript), encoding="utf-8")

    stem.with_suffix(".json").write_text(
        json.dumps({"name": call.name, "pack": call.pack, "lines": call.lines,
                    "summary": call.summary,
                    "turn_latency_ms": [round(t) for t in call.turn_latency]},
                   indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", choices=sorted(SCRIPTS) + ["all"],
                        default="cooperative")
    parser.add_argument("--pack", default="health_shield_en")
    parser.add_argument("--voice", default=settings.tts_voice_en)
    parser.add_argument("--host", default=f"{settings.app_host}:{settings.app_port}")
    args = parser.parse_args()

    names = sorted(SCRIPTS) if args.script == "all" else [args.script]
    calls = []
    for name in names:
        call = asyncio.run(place(name, SCRIPTS[name], args.pack, args.voice,
                                 args.host))
        save(call)
        calls.append(call)

        print(f"\n  saved  results/calls/{name}.wav .txt .json")
        if call.turn_latency:
            ordered = sorted(call.turn_latency)
            print(f"  turn latency: median {ordered[len(ordered) // 2]:.0f} ms")

    print(f"\n{'=' * 78}")
    print(f"{'call':<16}{'turns':>7}{'grounded':>10}{'unanswered':>12}"
          f"{'escalated':>12}")
    print("-" * 78)
    for call in calls:
        summary = call.summary
        grounded = sum(1 for line in call.lines if line.get("grounded"))
        print(f"{call.name:<16}{summary.get('turns', 0):>7}{grounded:>10}"
              f"{len(summary.get('unanswered', [])):>12}"
              f"{summary.get('escalated_to') or '-':>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
