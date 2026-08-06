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
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator

from core.timing import Stopwatch
from voice_agent.agent import Agent
from voice_agent.audio import SAMPLE_RATE, SAMPLE_WIDTH, Endpointer, Utterance
from voice_agent.speak import Speaker
from voice_agent.asr import MarketTranscriber

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

# Added to the length of the reply before listening resumes, for the tail of
# the sound still in the room.
SETTLE_SECONDS = 0.4

# Roughly how long a piece of synthesised audio lasts. The bytes arrive far
# faster than they play, so the length of the file is what matters, not how
# long it took to make.
MP3_BYTES_PER_SECOND = 4000


def _audio_length_ms(audio: bytes) -> float:
    return len(audio) / MP3_BYTES_PER_SECOND * 1000 if audio else 0.0


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
                 transcriber: MarketTranscriber | None = None,
                 speaker: Speaker | None = None,
                 allow_barge_in: bool = False) -> None:
        self.agent = agent or Agent(pack_id)
        # Recognition settings come from the market rather than from here, so
        # the language hint and the domain terms are decided in one place.
        self.transcriber = transcriber or MarketTranscriber()
        self.speaker = speaker or Speaker(self.agent.pack.language)

        self.endpointer = Endpointer()
        self.state = CallState.IDLE
        self.record = CallRecord()
        self.trace = ""

        self._speech_run = 0
        self._cancel_speaking = False
        self._turn_number = 0

        # Half duplex unless the caller says they are on headphones. On
        # speakers the microphone hears the agent, the recogniser turns that
        # into words, and the agent answers itself. Enforced here as well as in
        # the browser, because the browser can be an old cached page and this
        # cannot.
        self.allow_barge_in = allow_barge_in
        # Counted in bytes rather than seconds. Audio arrives buffered and in
        # bursts, so a wall clock window either expires before the buffered
        # echo is read or swallows the caller's next sentence, depending on
        # timing nobody controls. Bytes line up with the audio itself.
        self._discard_bytes = 0
        self._discard_until = 0.0

    # -- lifecycle ------------------------------------------------------------

    async def start(self, trace: str = "") -> AsyncIterator[Event]:
        """Greet the caller and begin listening."""
        self.trace = trace
        self.state = CallState.SPEAKING
        self._cancel_speaking = False
        yield Event("state", state=self.state.value)

        greeting = self.agent.greeting()
        self.record.add("agent", greeting)
        # Emitted as text as well as audio, so the greeting appears in the
        # transcript rather than only being heard.
        yield Event("transcript", text=greeting, detail={"speaker": "agent"})
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
            if not self.allow_barge_in:
                # Not listening at all. Anything arriving now is the agent's
                # own voice coming back through the room.
                return
            async for event in self._watch_for_interruption(chunk):
                yield event
            return

        if self.state is CallState.THINKING:
            # Audio arriving while a reply is being written is kept, so a
            # caller who carries straight on is not cut off.
            self.endpointer.feed_stream(chunk)
            return

        # Throw away as much audio as the agent's own reply lasted. That audio
        # is the reply coming back through the room, and the caller has not
        # started talking yet because they are still listening to it.
        #
        # Bounded two ways, and whichever comes first wins. A browser streams
        # continuously, so counting bytes matches the audio exactly. Anything
        # that stops sending while it waits, which is what the test client
        # does, would leave the byte budget untouched forever and go
        # permanently deaf, so the clock releases it instead.
        if self._discard_bytes > 0:
            self._discard_bytes -= len(chunk)
            if time.monotonic() < self._discard_until:
                return
            self._discard_bytes = 0

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

        # A trace per turn, not per call. Sharing one across a whole call makes
        # the end-to-end figure the length of the call, which is true and
        # useless: what matters is how long one answer took.
        self._turn_number += 1
        turn_trace = f"{self.trace}-t{self._turn_number}"

        transcript = self.transcriber.transcribe(
            utterance.wav,
            business_unit=self.agent.pack.business_unit,
            audio_ms=utterance.duration_ms,
            trace=turn_trace,
        )

        if transcript.error == "silence":
            # There was no speech, only room tone or the tail of the agent's
            # own voice. Saying "sorry, I did not catch that" here is its own
            # loop: the caller said nothing, so there is nothing to repeat.
            self.state = CallState.LISTENING
            yield Event("state", state=self.state.value)
            return

        if not transcript:
            # Recognition produced nothing. Asking the caller to repeat is the
            # right response; dropping the line is not.
            self.record.failed_recognitions += 1
            log.info("nothing recognised", extra={"reason": transcript.error})
            reply = self.agent.service_line("not_understood")
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

        # Two ways this fails, and both end with the caller hearing nothing.
        # Either both providers are gone and it raises, or one answers with an
        # empty string and it does not. Silence reads as the call having
        # dropped, so both get a line in the caller's own language. Neither is
        # recorded as an answer, because neither is one.
        turn = None
        try:
            turn = self.agent.respond(transcript.text, trace=turn_trace)
        except Exception:
            log.exception("could not produce a reply")

        if turn is None or not turn.agent.strip():
            if turn is not None:
                log.error("the model returned an empty reply")
            reply = self.agent.service_line("trouble")
            self.record.add("agent", reply, note="model unavailable")
            self.state = CallState.SPEAKING
            yield Event("state", state=self.state.value)
            async for event in self._speak(reply):
                yield event
            self.state = CallState.LISTENING
            yield Event("state", state=self.state.value)
            return

        self.record.add("agent", turn.agent, grounded=turn.grounded,
                        citations=turn.citations, escalated=turn.escalated_to,
                        timings=turn.timings)
        yield Event("transcript", text=turn.agent,
                    detail={"speaker": "agent", "grounded": turn.grounded,
                            "sought_knowledge": turn.sought_knowledge,
                            # Whether the agent actually declined to answer, as
                            # opposed to simply not needing a record for this
                            # turn. Retrieval runs on nearly every turn now, so
                            # "nothing matched" on a slot answer is normal and
                            # flagging it made an ordinary call look broken.
                            "refused": turn.said_it_did_not_know,
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
        spoken_ms = 0.0
        async for speech in self.speaker.stream(text):
            if self._cancel_speaking:
                log.debug("stopped speaking part way through")
                break
            spoken_ms += _audio_length_ms(speech.audio)
            yield Event("audio", text=speech.text, audio=speech.audio,
                        detail={"ms": round(speech.milliseconds)})

        # The audio is handed over faster than it plays, so the room carries it
        # for roughly as long as it lasts. Ignore that much incoming audio,
        # plus a little for the tail.
        if not self.allow_barge_in:
            seconds = spoken_ms / 1000 + SETTLE_SECONDS
            self._discard_bytes = int(seconds * SAMPLE_RATE * SAMPLE_WIDTH)
            self._discard_until = time.monotonic() + seconds
            self.endpointer.reset()

    # -- ending ---------------------------------------------------------------

    async def close(self, say_goodbye: bool = True) -> AsyncIterator[Event]:
        """Finish the call, optionally with a closing line.

        A caller who pressed hang up has decided they are done, and
        synthesising a goodbye for them takes several seconds during which
        nothing else happens, including writing the lead.
        """
        if self.state is CallState.ENDED:
            return

        # A caller who was mid-sentence when the call ended still said
        # something worth keeping.
        leftover = self.endpointer.flush()
        if leftover:
            transcript = self.transcriber.transcribe(
                leftover.wav, business_unit=self.agent.pack.business_unit,
                audio_ms=leftover.duration_ms)
            if transcript:
                self.record.add("caller", transcript.text, note="at hang up")

        closing = self.agent.closing_line()
        self.record.add("agent", closing, spoken=say_goodbye)
        if say_goodbye:
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
