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
    def __init__(self, replies=None, error=""):
        self.replies = list(replies or ["I am thirty five years old"])
        self.error = error
        self.calls = 0

    def transcribe(self, wav, **kwargs):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else ""
        return Transcript(text=text, milliseconds=50.0, audio_ms=1000.0,
                          error=self.error or ("" if text else "nothing recognised"))

    def warmup(self):
        return 0.0


class StubTurn:
    def __init__(self, agent_text, escalated_to="", grounded=True):
        self.agent = agent_text
        self.caller = ""
        self.grounded = grounded
        self.sought_knowledge = grounded
        self.said_it_did_not_know = False
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
        # Set to make responding fail, for the paths that only happen when the
        # model is unreachable.
        self.fail_on_respond = False

    def greeting(self):
        return "Hello, this is a test call. Is now a good time?"

    def respond(self, text, trace=""):
        if self.fail_on_respond:
            raise RuntimeError("both providers unavailable")
        reply = self.replies.pop(0) if self.replies else "Alright."
        turn = StubTurn(reply, escalated_to=self.escalate_next)
        self.conversation.turns.append(turn)
        if self.escalate_next:
            self.conversation.escalated_to = self.escalate_next
        return turn

    def service_line(self, kind):
        return self.pack.service[kind]

    def closing_line(self):
        return "Thanks for your time."

    def warmup(self):
        return {}


@pytest.fixture
def session():
    return CallSession(agent=StubAgent(), transcriber=StubTranscriber(),
                       speaker=StubSpeaker())


@pytest.fixture
def duplex_session():
    """A session on headphones, where interrupting is allowed."""
    return CallSession(agent=StubAgent(), transcriber=StubTranscriber(),
                       speaker=StubSpeaker(), allow_barge_in=True)


async def drain(generator):
    return [event async for event in generator]


def listen_now(session):
    """Skip the settle window.

    After speaking, the session stays deaf for as long as the reply lasts plus
    a moment for the sound to leave the room. Tests run faster than real time,
    so that window has to be stepped over rather than waited out.
    """
    session._discard_bytes = 0
    return session


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
        listen_now(session)
        events = []
        for chunk in (tone(700), silence(1200)):
            events += await drain(session.on_audio(chunk))
        assert any(e.kind == "transcript" for e in events)
        assert any(e.kind == "audio" for e in events)
        assert session.state is CallState.LISTENING

    async def test_the_transcript_records_both_sides(self, session):
        await drain(session.start())
        listen_now(session)
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))
        transcript = session.record.transcript()
        assert "agent:" in transcript and "caller:" in transcript

    async def test_an_escalation_ends_the_call(self, session):
        session.agent.escalate_next = "ESC-REQUEST"
        await drain(session.start())
        listen_now(session)
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
        listen_now(session)
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))
        assert session.record.failed_recognitions == 1
        assert any("say it again" in s for s in session.speaker.said)

    async def test_silence_produces_no_reply_at_all(self):
        # The caller said nothing, so there is nothing to repeat. Asking them
        # to say it again is its own loop: the room stays quiet, the recogniser
        # returns another artefact, and the agent asks again.
        session = CallSession(agent=StubAgent(),
                              transcriber=StubTranscriber(replies=[""],
                                                          error="silence"),
                              speaker=StubSpeaker())
        await drain(session.start())
        said_before = len(session.speaker.said)
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))
        assert len(session.speaker.said) == said_before
        assert session.state is CallState.LISTENING
        assert session.record.failed_recognitions == 0

    async def test_the_call_carries_on(self):
        # Dropping the line because one utterance was not understood is far
        # worse than asking once more.
        session = CallSession(agent=StubAgent(),
                              transcriber=StubTranscriber(replies=["", "I am 35"]),
                              speaker=StubSpeaker())
        await drain(session.start())
        for _ in range(2):
            # Between turns, as a caller waiting for the agent to finish would.
            listen_now(session)
            for chunk in (tone(700), silence(1200)):
                await drain(session.on_audio(chunk))
        assert session.state is CallState.LISTENING
        assert session.transcriber.calls == 2


@pytest.mark.asyncio
class TestBargeIn:
    async def test_sustained_speech_interrupts(self, duplex_session):
        session = duplex_session
        session.state = CallState.SPEAKING
        events = await drain(session.on_audio(tone(400)))
        assert any(e.kind == "barge_in" for e in events)
        assert session.state is CallState.LISTENING

    async def test_a_brief_noise_does_not_interrupt(self, duplex_session):
        session = duplex_session
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

    async def test_silence_during_a_reply_does_not_interrupt(self, duplex_session):
        session = duplex_session
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

    async def test_an_interruption_is_counted(self, duplex_session):
        session = duplex_session
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
        listen_now(session)
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))
        summary = session.summary()
        assert summary["turns"] == 1
        assert "barge_ins" in summary


@pytest.mark.asyncio
class TestInterrupting:
    """Starting to talk has to reach the browser, or it cannot stop playing.

    The watcher on the speaking state almost never sees an interruption. The
    server hands a whole reply over in a fraction of a second and the browser
    takes seconds to play it, so by the time the caller has heard enough to
    want to interrupt, the session is back in the listening state. The signal
    has to come from a turn opening there instead.
    """

    async def test_starting_to_talk_tells_the_browser_to_stop(
            self, duplex_session):
        await drain(duplex_session.start())
        events = await drain(duplex_session.on_audio(tone(400)))
        assert any(e.kind == "barge_in" for e in events)
        assert duplex_session.record.barge_ins == 1

    async def test_it_is_announced_once_per_turn_not_once_per_chunk(
            self, duplex_session):
        await drain(duplex_session.start())
        events = []
        for _ in range(3):
            events += await drain(duplex_session.on_audio(tone(400)))
        assert sum(1 for e in events if e.kind == "barge_in") == 1

    async def test_nothing_is_announced_on_speakers(self, session):
        # Half duplex. The browser has the microphone switched off while the
        # agent speaks, so there is nothing to interrupt and no event to send.
        await drain(session.start())
        listen_now(session)
        events = await drain(session.on_audio(tone(400)))
        assert not any(e.kind == "barge_in" for e in events)


class TestTheAgentDoesNotAnswerItself:
    """Audio matching what the agent just said is the speakers, not a caller.

    The deaf window covers the reply while it plays. This covers what gets
    through anyway: room reverb, a slow speaker, a phrase caught on the tail.
    Observed live as the agent hearing "and the plus, max" from its own
    description of the plans, over and over.
    """

    def _session(self, spoken, barge_in=False):
        session = CallSession(agent=StubAgent(), transcriber=StubTranscriber(),
                              speaker=StubSpeaker(), allow_barge_in=barge_in)
        session._last_spoken = spoken
        return session

    def test_a_phrase_from_the_last_reply_is_treated_as_echo(self):
        session = self._session(
            "We offer the Essential, Plus and Max plans.")
        assert session._is_own_voice("and the Plus, Max")

    def test_a_one_word_answer_is_never_echo(self):
        # "Yes" and "no" appear inside the agent's own sentences constantly.
        # Discarding them would throw away the commonest reply on the call.
        session = self._session("Yes, that plan covers it. Are you in Manila?")
        assert not session._is_own_voice("Yes")
        assert not session._is_own_voice("No")

    def test_a_two_word_answer_is_never_echo(self):
        session = self._session("Are you in Manila or somewhere else?")
        assert not session._is_own_voice("In Manila")

    def test_a_whole_sentence_from_the_caller_is_not_echo(self):
        session = self._session(
            "The Plus plan covers hospital treatment and a rider.")
        assert not session._is_own_voice(
            "Does the Plus plan cover hospital treatment and a rider for my wife")

    def test_a_real_question_using_the_agents_words_is_not_echo(self):
        # A caller repeating a term on purpose has to be heard.
        session = self._session("We offer Essential, Plus and Max.")
        assert not session._is_own_voice("What about Max please")

    def test_nothing_is_echo_before_the_agent_has_spoken(self):
        session = self._session("")
        assert not session._is_own_voice("and the plus max")

    def test_interruption_turns_the_check_off(self):
        # On headphones the microphone is not hearing the agent, and the
        # caller is allowed to talk over it.
        session = self._session("We offer Essential, Plus and Max plans.",
                                barge_in=True)
        assert not session._is_own_voice("and the Plus, Max")
