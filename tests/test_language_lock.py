"""
The call stays in one language, including when things go wrong.

Getting the conversation into Taglish or Indonesian is the easy half. The
half that breaks is everything around it: the line said when the recogniser
returns nothing, the line said when the model is unreachable, and the voice
those lines are read in. Each of those was English by default, and each of
them fires at the moment the caller is already having a bad call.
"""

from __future__ import annotations

import pytest

from test_call_loop import (
    StubAgent,
    StubSpeaker,
    StubTranscriber,
    drain,
    listen_now,
)
from conftest import voiced as tone
from voice_agent.asr import detect_code_switching
from voice_agent.audio import silence
from voice_agent.call import CallSession, CallState
from voice_agent.pack import SERVICE_LINES, PackError, available_packs, load_pack
from voice_agent.speak import Speaker, is_native, voice_for

EXPECTED_LANGUAGE = {"en": "english", "fil": "tagalog", "id": "indonesian"}


class TestServiceLines:
    def test_every_pack_has_them(self):
        for name in available_packs():
            pack = load_pack(name)
            for line in SERVICE_LINES:
                assert pack.service.get(line), f"{name} has no {line} line"

    def test_a_pack_without_them_is_refused_at_load(self, tmp_path, monkeypatch):
        # Refused at load rather than discovered mid-call, where the only
        # symptom is the agent going quiet.
        import voice_agent.pack as pack_module

        source = (pack_module.PACK_DIR / "life_ph.yaml").read_text(encoding="utf-8")
        broken = source.replace("  not_understood:", "  disabled_line:")
        (tmp_path / "life_ph.yaml").write_text(broken, encoding="utf-8")
        monkeypatch.setattr(pack_module, "PACK_DIR", tmp_path)

        with pytest.raises(PackError, match="not_understood"):
            pack_module.load_pack("life_ph")

    @pytest.mark.parametrize("name", sorted(available_packs()))
    def test_they_are_in_the_language_of_the_market(self, name):
        # The check that matters. An English line in the Filipino pack loads
        # fine, reads fine, and is wrong only when a caller hears it.
        pack = load_pack(name)
        expected = EXPECTED_LANGUAGE[pack.language]
        for line in SERVICE_LINES:
            found = detect_code_switching(pack.service[line]).languages
            assert expected in found, \
                f"{name} {line} reads as {found or 'nothing'}, not {expected}"

    def test_they_are_not_shared_between_markets(self):
        # Copying the English line into every pack would satisfy a test that
        # only checked the key exists.
        for line in SERVICE_LINES:
            said = {load_pack(n).service[line] for n in available_packs()}
            assert len(said) == len(available_packs())

    def test_the_agent_returns_them_ready_to_speak(self):
        from voice_agent.agent import Agent

        agent = Agent("life_ph")
        for line in SERVICE_LINES:
            spoken = agent.service_line(line)
            assert spoken and "\n" not in spoken


class TestHandoverNotes:
    @pytest.mark.parametrize("pack_id,expected", [
        ("health_shield_en", "English"),
        ("life_ph", "Filipino"),
        ("multifinance_id", "Bahasa Indonesia"),
    ])
    def test_the_note_language_follows_the_market(self, pack_id, expected):
        from voice_agent.actions import Lead, note_language

        lead = Lead(lead_id="x", created_at="", business_unit="x",
                    pack_id=pack_id)
        assert note_language(lead) == expected

    def test_an_unknown_pack_does_not_stop_the_note_being_written(self):
        # The lead is already captured by this point. Losing the note over a
        # missing pack would be a worse outcome than a note in English.
        from voice_agent.actions import Lead, note_language

        lead = Lead(lead_id="x", created_at="", business_unit="x",
                    pack_id="no such pack")
        assert note_language(lead) == "English"


class TestVoices:
    @pytest.mark.parametrize("name", sorted(available_packs()))
    def test_each_market_speaks_in_a_native_voice(self, name):
        pack = load_pack(name)
        voice = voice_for(pack.language)
        assert is_native(pack.language, voice), \
            f"{name} would be read by {voice}"

    def test_filipino_and_indonesian_are_not_the_english_voice(self):
        english = voice_for("en")
        assert voice_for("fil") != english
        assert voice_for("id") != english

    def test_tagalog_and_filipino_reach_the_same_voice(self):
        # Whisper returns "tl", the packs say "fil", and they are the same
        # language.
        assert voice_for("tl") == voice_for("fil")

    def test_an_unknown_language_falls_back_rather_than_failing(self, caplog):
        # A call in an accented voice beats no call. Saying so in the log is
        # the other half, since otherwise a whole market runs on the wrong
        # voice with nothing to show for it.
        with caplog.at_level("ERROR"):
            assert voice_for("xx") == voice_for("en")
        assert "no native voice" in caplog.text

    def test_a_speaker_stays_on_the_language_it_was_built_for(self):
        speaker = Speaker("id")
        assert speaker.language == "id"
        assert is_native("id", speaker.voice)


@pytest.mark.asyncio
class TestTheCallLoopStaysInLanguage:
    async def _session(self, pack_id, transcriber):
        agent = StubAgent()
        agent.pack = load_pack(pack_id)
        session = CallSession(agent=agent, transcriber=transcriber,
                              speaker=StubSpeaker())
        await drain(session.start())
        listen_now(session)
        return session

    async def _speak_into(self, session):
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))

    @pytest.mark.parametrize("pack_id", ["life_ph", "multifinance_id"])
    async def test_a_failed_recognition_is_answered_in_the_market_language(
            self, pack_id):
        session = await self._session(pack_id, StubTranscriber(replies=[""]))
        await self._speak_into(session)

        assert session.record.failed_recognitions == 1
        spoken = " ".join(session.speaker.said)
        expected = EXPECTED_LANGUAGE[session.agent.pack.language]
        assert expected in detect_code_switching(spoken).languages

    @pytest.mark.parametrize("pack_id", ["life_ph", "multifinance_id"])
    async def test_an_unreachable_model_is_answered_in_the_market_language(
            self, pack_id):
        session = await self._session(pack_id, StubTranscriber(replies=["hello"]))
        session.agent.fail_on_respond = True
        await self._speak_into(session)

        spoken = " ".join(session.speaker.said)
        expected = EXPECTED_LANGUAGE[session.agent.pack.language]
        assert expected in detect_code_switching(spoken).languages

    async def test_an_unreachable_model_does_not_end_the_call(self):
        session = await self._session("life_ph", StubTranscriber(replies=["hello"]))
        session.agent.fail_on_respond = True
        await self._speak_into(session)

        assert session.state is CallState.LISTENING

    async def test_an_empty_reply_is_not_left_as_silence(self):
        # No exception is raised here. One provider answered, with nothing.
        # The caller hears the same thing either way, so it is handled the
        # same way.
        session = await self._session(
            "life_ph", StubTranscriber(replies=["hello"]))
        session.agent.replies = ["   "]
        await self._speak_into(session)

        assert session.speaker.said
        assert session.state is CallState.LISTENING

    async def test_the_failed_turn_is_marked_rather_than_stored_as_an_answer(self):
        session = await self._session("life_ph", StubTranscriber(replies=["hello"]))
        session.agent.fail_on_respond = True
        await self._speak_into(session)

        last = session.record.lines[-1]
        assert last["note"] == "model unavailable"
        # Not stored as a grounded answer, because it is not an answer.
        assert "grounded" not in last
        assert "citations" not in last


class TestRecognitionIsConfiguredPerMarket:
    @pytest.mark.asyncio
    async def test_the_market_reaches_the_recogniser(self):
        # The settings existed before this was wired up, and the call loop was
        # quietly running on a separate copy of them.
        seen = {}

        class Watching(StubTranscriber):
            def transcribe(self, wav, **kwargs):
                seen.update(kwargs)
                return super().transcribe(wav, **kwargs)

        agent = StubAgent()
        agent.pack = load_pack("life_ph")
        session = CallSession(agent=agent, transcriber=Watching(replies=["hi"]),
                              speaker=StubSpeaker())
        await drain(session.start())
        listen_now(session)
        for chunk in (tone(700), silence(1200)):
            await drain(session.on_audio(chunk))

        assert seen.get("business_unit") == "life_ph"

    def test_the_domain_hints_come_from_one_place(self):
        # There were two tables of these, and the one the call loop used had
        # already fallen behind.
        from voice_agent.asr import config_for
        from voice_agent.transcribe import hint_for

        for name in available_packs():
            unit = load_pack(name).business_unit
            assert hint_for(unit) == config_for(unit).prompt
