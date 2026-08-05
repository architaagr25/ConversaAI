"""
The call loop: listening, thinking and speaking, and what happens when those
overlap.

Barge-in is the part that decides whether the call feels like a conversation.
A caller who interrupts expects the agent to stop, and an agent that finishes
its sentence first is talking over somebody, which is worse than being slow.

Interrupting is held to a stricter standard than starting a turn. While the
agent is speaking, the caller's microphone is also picking up the agent, and
echo cancellation in a browser is good but not perfect. A single frame that
looks like speech ends the reply mid-word for no reason, so it takes a longer
run of speech to interrupt than it does to begin a turn in silence.

Everything is injectable, so the whole state machine can be tested without a
microphone, a recogniser or a network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator

from core.timing import Stopwatch
from voice_agent.agent import Agent
from voice_agent.audio import Endpointer, Utterance
from voice_agent.speak import Speaker
from voice_agent.transcribe import Transcriber, hint_for

log = logging.getLogger(__name__)

# Frames of speech needed to interrupt the agent, against the three that open a
# turn in silence. The microphone is hearing the agent as well as the caller,
# and echo cancellation in a browser is good but not perfect.
#
# The detector holds on for about four frames after speech actually stops, so a
# run of eight counts roughly four frames of real speech and four of hangover.
# That is barely stricter than opening a turn, which was not the intention.
# Twelve leaves around 160 ms of genuine speech, which is a syllable or two:
# short enough to feel responsive, long enough that a cough does not cut the
# agent off mid-word.
VAD_HANGOVER_FRAMES = 4
BARGE_IN_FRAMES = 12


class CallState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ENDED = "ended"


@dataclass
class Event:
    kind: str
    text: str = ""
    audio: bytes = b""
    state: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class CallRecord:
    """Everything that happened, for the transcript and the summary."""

    lines: list[dict] = field(default_factory=list)
    barge_ins: int = 0
    failed_recognitions: int = 0

    def add(self, speaker: str, text: str, **detail) -> None:
        self.lines.append({"speaker": speaker, "text": text, **detail})

    def transcript(self) -> str:
        return "\n".join(f"{line['speaker']}: {line['text']}" for line in self.lines)


class CallSession:
    """One call, from greeting to close."""

    def __init__(self, pack_id: str = "health_shield_en",
                 agent: Agent | None = None,
                 transcriber: Transcriber | None = None,
                 speaker: Speaker | None = None) -> None:
        self.agent = agent or Agent(pack_id)
        self.transcriber = transcriber or Transcriber()
        self.speaker = speaker or Speaker(self.agent.pack.language)

        self.endpointer = Endpointer()
        self.state = CallState.IDLE
        self.record = CallRecord()
        self.trace = ""

        self._speech_run = 0
        self._cancel_speaking = False

    # -- lifecycle ------------------------------------------------------------

    async def start(self, trace: str = "") -> AsyncIterator[Event]:
        """Greet the caller and begin listening."""
        self.trace = trace
        self.state = CallState.SPEAKING
        self._cancel_speaking = False
        yield Event("state", state=self.state.value)

        greeting = self.agent.greeting()
        self.record.add("agent", greeting)
        async for event in self._speak(greeting):
            yield event

        self.state = CallState.LISTENING
        yield Event("state", state=self.state.value)

    def warmup(self) -> dict:
        """Pay every connection cost before the caller says anything."""
        watch = Stopwatch()
        marks = self.agent.warmup()
        self.transcriber.warmup()
        watch.mark("recogniser")
        return {**marks, **watch.marks}

    # -- audio in -------------------------------------------------------------

    async def on_audio(self, chunk: bytes) -> AsyncIterator[Event]:
        """Feed microphone audio. Yields whatever that causes to happen."""
        if self.state in (CallState.ENDED, CallState.IDLE):
            return

        if self.state is CallState.SPEAKING:
            async for event in self._watch_for_interruption(chunk):
                yield event
            return

        if self.state is CallState.THINKING:
            # Audio arriving while a reply is being written is kept, so a
            # caller who carries straight on is not cut off.
            self.endpointer.feed_stream(chunk)
            return

        for utterance in self.endpointer.feed_stream(chunk):
            async for event in self._handle(utterance):
                yield event

    async def _watch_for_interruption(self, chunk: bytes) -> AsyncIterator[Event]:
        """Decide whether the caller is interrupting or the microphone is echoing."""
        from voice_agent.audio import FRAME_BYTES

        pending = getattr(self, "_interrupt_buffer", bytearray())
        pending.extend(chunk)
        self._interrupt_buffer = pending

        while len(pending) >= FRAME_BYTES:
            frame = bytes(pending[:FRAME_BYTES])
            del pending[:FRAME_BYTES]
            if self.endpointer._is_speech(frame):
                self._speech_run += 1
            else:
                self._speech_run = 0

            if self._speech_run >= BARGE_IN_FRAMES:
                self._speech_run = 0
                self._cancel_speaking = True
                self.record.barge_ins += 1
                log.info("caller interrupted")
                yield Event("barge_in")
                self.state = CallState.LISTENING
                yield Event("state", state=self.state.value)
                # The frames that caused the interruption are the start of
                # what the caller is saying, so listening resumes from here.
                self.endpointer.reset()
                return

    # -- one turn -------------------------------------------------------------

    async def _handle(self, utterance: Utterance) -> AsyncIterator[Event]:
        self.state = CallState.THINKING
        self._cancel_speaking = False
        yield Event("state", state=self.state.value)

        transcript = self.transcriber.transcribe(
            utterance.wav,
            prompt=hint_for(self.agent.pack.business_unit),
            audio_ms=utterance.duration_ms,
            trace=self.trace,
        )

        if not transcript:
            # Recognition produced nothing. Asking the caller to repeat is the
            # right response; dropping the line is not.
            self.record.failed_recognitions += 1
            log.info("nothing recognised", extra={"reason": transcript.error})
            reply = "Sorry, I did not catch that. Could you say it again?"
            self.record.add("agent", reply, note="recognition failed")
            self.state = CallState.SPEAKING
            yield Event("state", state=self.state.value)
            async for event in self._speak(reply):
                yield event
            self.state = CallState.LISTENING
            yield Event("state", state=self.state.value)
            return

        self.record.add("caller", transcript.text,
                        asr_ms=round(transcript.milliseconds),
                        audio_ms=round(utterance.duration_ms))
        yield Event("transcript", text=transcript.text, detail={"speaker": "caller"})

        turn = self.agent.respond(transcript.text, trace=self.trace)
        self.record.add("agent", turn.agent, grounded=turn.grounded,
                        citations=turn.citations, escalated=turn.escalated_to,
                        timings=turn.timings)
        yield Event("transcript", text=turn.agent,
                    detail={"speaker": "agent", "grounded": turn.grounded,
                            "citations": turn.citations})

        self.state = CallState.SPEAKING
        yield Event("state", state=self.state.value)
        async for event in self._speak(turn.agent):
            yield event

        if turn.escalated_to:
            self.state = CallState.ENDED
            yield Event("state", state=self.state.value)
            yield Event("ended", text=turn.escalated_to,
                        detail={"reason": "escalated"})
            return

        self.state = CallState.LISTENING
        yield Event("state", state=self.state.value)

    async def _speak(self, text: str) -> AsyncIterator[Event]:
        """Emit audio piece by piece, stopping if the caller interrupts.

        The cancel flag is cleared when a turn begins, not here. Clearing it on
        entry would discard an interruption that arrived while the reply was
        still being written, which is exactly when an impatient caller
        interrupts.
        """
        async for speech in self.speaker.stream(text):
            if self._cancel_speaking:
                log.debug("stopped speaking part way through")
                break
            yield Event("audio", text=speech.text, audio=speech.audio,
                        detail={"ms": round(speech.milliseconds)})

    # -- ending ---------------------------------------------------------------

    async def close(self) -> AsyncIterator[Event]:
        """Say goodbye and finish."""
        if self.state is CallState.ENDED:
            return

        # A caller who was mid-sentence when the call ended still said
        # something worth keeping.
        leftover = self.endpointer.flush()
        if leftover:
            transcript = self.transcriber.transcribe(
                leftover.wav, audio_ms=leftover.duration_ms)
            if transcript:
                self.record.add("caller", transcript.text, note="at hang up")

        closing = self.agent.closing_line()
        self.record.add("agent", closing)
        self.state = CallState.SPEAKING
        async for event in self._speak(closing):
            yield event

        self.state = CallState.ENDED
        yield Event("state", state=self.state.value)
        yield Event("ended", detail={"reason": "closed"})

    def summary(self) -> dict:
        conversation = self.agent.conversation
        assessment = conversation.assessment
        return {
            "pack": conversation.pack_id,
            "turns": len(conversation.turns),
            "collected": conversation.slots,
            "corrections": conversation.corrections,
            "eligible": assessment.eligible if assessment else None,
            "decided": assessment.decided if assessment else False,
            "decline_reason": assessment.decline_reason if assessment else "",
            "requirements": assessment.requirements if assessment else [],
            "missing": assessment.missing if assessment else [],
            "escalated_to": conversation.escalated_to,
            "unanswered": conversation.unanswered,
            "citations": conversation.citations,
            "barge_ins": self.record.barge_ins,
            "failed_recognitions": self.record.failed_recognitions,
        }
