"""
The web calling interface.

A browser page and a WebSocket, which is enough to make and take a call
without a phone number or a card on file. The browser handles the microphone,
echo cancellation and playback; the server handles everything else.

The protocol is deliberately two-shaped. Control messages are JSON text, audio
is raw binary in both directions. Base64 in JSON would have been simpler to
read and adds a third to every packet, which on a call is a third more delay
before anyone hears anything.

    .venv\\Scripts\\python -m voice_agent.server
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from core.config import settings
from core.logging_setup import setup_logging
from core.timing import RECORDER
from voice_agent.call import CallSession
from voice_agent.pack import available_packs

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="ConversaAI", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _startup() -> None:
    setup_logging()
    log.info("call interface ready", extra={"packs": available_packs()})


@app.get("/", response_class=HTMLResponse)
async def page() -> str:
    return (STATIC / "call.html").read_text(encoding="utf-8")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "packs": available_packs(),
        "model": settings.gemini_model,
        "recogniser": settings.groq_asr_model,
    })


@app.websocket("/call")
async def call(socket: WebSocket) -> None:
    """One call, for as long as the socket stays open."""
    await socket.accept()
    pack_id = socket.query_params.get("pack", "health_shield_en")
    trace = socket.query_params.get("trace", "web-call")

    try:
        session = CallSession(pack_id)
    except Exception as exc:
        await socket.send_text(json.dumps(
            {"kind": "error", "text": f"could not start: {exc}"}))
        await socket.close()
        return

    async def emit(event) -> None:
        if event.kind == "audio":
            # The label goes first so the page can show what is being said
            # before the audio for it arrives.
            await socket.send_text(json.dumps(
                {"kind": "speaking", "text": event.text}))
            await socket.send_bytes(event.audio)
            return
        await socket.send_text(json.dumps({
            "kind": event.kind, "text": event.text,
            "state": event.state, "detail": event.detail,
        }))

    try:
        # Warm-up happens before the greeting, so the connection costs land
        # while the caller is still reading the page.
        await socket.send_text(json.dumps({"kind": "status", "text": "connecting"}))
        marks = session.warmup()
        await socket.send_text(json.dumps(
            {"kind": "status", "text": "ready", "detail": marks}))

        async for event in session.start(trace=trace):
            await emit(event)

        while True:
            message = await socket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if (audio := message.get("bytes")) is not None:
                async for event in session.on_audio(audio):
                    await emit(event)
                continue

            if (text := message.get("text")) is not None:
                command = json.loads(text).get("command")
                if command == "hangup":
                    async for event in session.close():
                        await emit(event)
                    break

    except WebSocketDisconnect:
        log.info("caller hung up")
    except Exception as exc:
        log.exception("call failed")
        try:
            await socket.send_text(json.dumps({"kind": "error", "text": str(exc)[:200]}))
        except Exception:
            pass
    finally:
        summary = session.summary()
        summary["transcript"] = session.record.transcript()
        log.info("call finished", extra={"summary": summary})
        try:
            await socket.send_text(json.dumps({"kind": "summary", "detail": summary}))
            await socket.close()
        except Exception:
            pass


@app.get("/latency")
async def latency() -> JSONResponse:
    """What the calls so far have cost, per stage."""
    return JSONResponse({"stages": RECORDER.stats(), "table": RECORDER.summary()})


def main() -> int:
    import uvicorn

    setup_logging()
    print("=" * 72)
    print("ConversaAI call interface")
    print("=" * 72)
    print(f"\n  open  http://{settings.app_host}:{settings.app_port}")
    print(f"  packs {', '.join(available_packs())}")
    print("\n  For a public address, in another terminal:")
    print(f"    cloudflared tunnel --url http://localhost:{settings.app_port}")
    print()

    uvicorn.run(app, host=settings.app_host, port=settings.app_port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
