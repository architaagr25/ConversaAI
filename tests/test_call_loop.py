"""
Call loop tests, including barge-in.

The whole session runs on stubs here. What is being tested is when the agent
stops talking, what happens when recognition fails, and whether a caller who
interrupts is heard, none of which need a microphone.
"""

from __future__ import annotations

import pytest

from conftest import voiced as tone
from voice_agent.audio import FRAME_MS, silence
from voice_agent.call import BARGE_IN_FRAMES, CallSession, CallState
from voice_agent.speak import Speech, split_for_speech
from voice_agent.transcribe import Transcript


class StubSpeaker:
    def __init__(self):
        self.said: list[str] = []

    async def stream(self, text, voice=None):
        for piece in split_for_speech(text):
            self.said.append(piece)
            yield Speech(text=piece, audio=b"AUDIO", milliseconds=10.0, voice="stub")


class StubTranscriber:
    def __init__(self, replies=None):
        self.replies = list(replies or ["I am thirty five years old"])
        self.calls = 0

    def transcribe(self, wav, **kwargs):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else ""
        return Transcript(text=text, milliseconds=50.0, audio_ms=1000.0,
                          error="" if text else "nothing recognised")

    def warmup(self):
        return 0.0


class StubTurn:
    def __init__(self, agent_text, escalated_to="", grounded=True):
        self.agent = agent_text
        self.caller = ""
        self.grounded = grounded
        self.citations = ["src#x"] if grounded else []
        self.escalated_to = escalated_to
        self.timings = {}
        self.slots_filled = {}


class StubAgent:
    def __init__(self, replies=None, pack_language="en"):
        from voice_agent.pack import load_pack
        self.pack = load_pack("health_shield_en")
        self.replies = list(replies or ["Thanks. And how old are you?"])
        self.conversation = type("C", (), {
            "pack_id": "stub", "turns": [], "slots": {}, "corrections": [],
            "assessment": None, "escalated_to": "", "unanswered": [],
            "citations": [],
        })()
        self.escalate_next = ""

    def greeting(self):
        return "Hello, this is a test call. Is now a good time?"

    def respond(self, text, trace=""):
        reply = self.replies.pop(0) if self.replies else "Alright."
        turn = StubTurn(reply, escalated_to=self.escalate_next)
        self.conversation.turns.append(turn)
        if self.escalate_next:
            self.conversation.escalated_to = self.escalate_next
        return turn

    def closing_line(self):
        return "Thanks for your time."

    def warmup(self):
        return {}


@pytest.fixture
def session():
    return CallSession(agent=StubAgent(), transcriber=StubTranscriber(),
                       speaker=StubSpeaker())


async def drain(generator):
    return [event async for event in generator]


class TestSentenceSplitting:
    def test_a_reply_is_split_into_sentences(self):
        pieces = split_for_speech(
            "Dental is not covered on any plan. The only exception is "
            "reconstructive work after an accident. Does that help?")
        assert len(pieces) == 3

    def test_a_short_opener_is_merged_forward(self):
        # "Got it." alone produces a click and a pause that reads as hesitation.
        pieces = split_for_speech("Got it. Are you currently living in the "
                                  "Philippines at the moment?")
        assert len(pieces) == 1

    def test_a_long_sentence_is_cut_at_a_comma(self):
        long = ("The Plus plan covers hospital admission and day surgery, "
                "it includes eight outpatient consultations each year, "
                "and it adds an annual physical examination at no extra cost "
                "which many members find useful for early detection.")
        pieces = split_for_speech(long)
        assert len(pieces) > 1
        assert all(len(p) < 240 for p in pieces)

    def test_formatting_is_stripped_before_speaking(self):
        assert "*" not in " ".join(split_for_speech("Your **premium** is due."))

    def test_empty_text(self):
        assert split_for_speech("") == []


@pytest.mark.asyncio
class TestCallFlow:
    async def test_the_call_opens_with_a_greeting(self, session):
        events = await drain(session.start())
        assert any(e.kind == "audio" for e in events)
        assert session.state is CallState.LISTENING

    async def test_speech_produces_a_reply(self, session):
        await drain(session.start())
        events = []
        for chunk in (tone(700), silence(1200)):
            events += await drain(session.on_audio(chunk))
        assert any(e.kind == "transcript" for e in events)
        assert any(e.kind == "audio" for e in events)
        assert session.state is CallState.LISTENING

    async def test_the_transcript_records_both_sides(self, session):
        await drain(session.start())
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))
        transcript = session.record.transcript()
        assert "agent:" in transcript and "caller:" in transcript

    async def test_an_escalation_ends_the_call(self, session):
        session.agent.escalate_next = "ESC-REQUEST"
        await drain(session.start())
        events = []
        for chunk in (tone(700), silence(1200)):
            events += await drain(session.on_audio(chunk))
        assert any(e.kind == "ended" for e in events)
        assert session.state is CallState.ENDED

    async def test_audio_after_the_call_ends_is_ignored(self, session):
        session.state = CallState.ENDED
        assert await drain(session.on_audio(tone(700))) == []


@pytest.mark.asyncio
class TestRecognitionFailure:
    async def test_the_caller_is_asked_to_repeat(self):
        session = CallSession(agent=StubAgent(),
                              transcriber=StubTranscriber(replies=[""]),
                              speaker=StubSpeaker())
        await drain(session.start())
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))
        assert session.record.failed_recognitions == 1
        assert any("say it again" in s for s in session.speaker.said)

    async def test_the_call_carries_on(self):
        # Dropping the line because one utterance was not understood is far
        # worse than asking once more.
        session = CallSession(agent=StubAgent(),
                              transcriber=StubTranscriber(replies=["", "I am 35"]),
                              speaker=StubSpeaker())
        await drain(session.start())
        for chunk in (tone(700), silence(1200), tone(700), silence(1200)):
            await drain(session.on_audio(chunk))
        assert session.state is CallState.LISTENING
        assert session.transcriber.calls == 2


@pytest.mark.asyncio
class TestBargeIn:
    async def test_sustained_speech_interrupts(self, session):
        session.state = CallState.SPEAKING
        events = await drain(session.on_audio(tone(400)))
        assert any(e.kind == "barge_in" for e in events)
        assert session.state is CallState.LISTENING

    async def test_a_brief_noise_does_not_interrupt(self, session):
        # The microphone is hearing the agent too, and echo cancellation is
        # good but not perfect. A short burst must not end a reply mid-word.
        # Kept well under the threshold because the detector reports speech for
        # about four frames after it has actually stopped.
        session.state = CallState.SPEAKING
        events = await drain(session.on_audio(tone(FRAME_MS * 2) + silence(300)))
        assert not any(e.kind == "barge_in" for e in events)
        assert session.state is CallState.SPEAKING

    async def test_the_threshold_allows_for_detector_hangover(self):
        # Eight frames sounds strict and is not: half of it is hangover, which
        # left interrupting barely harder than starting a turn.
        from voice_agent.audio import SPEECH_FRAMES_TO_START
        from voice_agent.call import VAD_HANGOVER_FRAMES
        real_speech_needed = BARGE_IN_FRAMES - VAD_HANGOVER_FRAMES
        assert real_speech_needed >= SPEECH_FRAMES_TO_START * 2

    async def test_silence_during_a_reply_does_not_interrupt(self, session):
        session.state = CallState.SPEAKING
        events = await drain(session.on_audio(silence(2000)))
        assert not any(e.kind == "barge_in" for e in events)

    async def test_interrupting_is_harder_than_starting_a_turn(self):
        from voice_agent.audio import SPEECH_FRAMES_TO_START
        assert BARGE_IN_FRAMES > SPEECH_FRAMES_TO_START

    async def test_the_reply_stops_when_interrupted(self, session):
        session._cancel_speaking = True
        events = await drain(session._speak("One. Two. Three. Four."))
        assert len(events) == 0

    async def test_an_interruption_is_counted(self, session):
        session.state = CallState.SPEAKING
        await drain(session.on_audio(tone(400)))
        assert session.record.barge_ins == 1


@pytest.mark.asyncio
class TestClosing:
    async def test_closing_says_goodbye_and_ends(self, session):
        await drain(session.start())
        events = await drain(session.close())
        assert any(e.kind == "ended" for e in events)
        assert session.state is CallState.ENDED

    async def test_closing_twice_is_harmless(self, session):
        await drain(session.start())
        await drain(session.close())
        assert await drain(session.close()) == []

    async def test_the_summary_reports_the_call(self, session):
        await drain(session.start())
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))
        summary = session.summary()
        assert summary["turns"] == 1
        assert "barge_ins" in summary
