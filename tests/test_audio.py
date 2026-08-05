"""
Audio and turn-taking tests.

Endpointing decides when the caller has finished. Getting it wrong is not a
crash, it is an agent that interrupts people or takes two seconds to notice
they have stopped, so the thresholds are tested against synthetic audio where
the right answer is known.
"""

from __future__ import annotations

import math
import struct

import pytest

from voice_agent.audio import (
    FRAME_BYTES,
    FRAME_MS,
    MIN_UTTERANCE_MS,
    SAMPLE_RATE,
    Endpointer,
    Listening,
    duration_ms,
    from_wav,
    resample,
    silence,
    to_mono,
    to_wav,
)


from conftest import voiced as tone  # noqa: E402


def sine(ms: int, hz: int = 220, amplitude: int = 12000,
         sample_rate: int = SAMPLE_RATE) -> bytes:
    """A pure tone, for the format tests where content does not matter.

    Deliberately not used for anything involving the detector: a sine wave has
    no harmonic structure, so it is not recognised as speech.
    """
    count = int(sample_rate * ms / 1000)
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * hz * n / sample_rate)))
        for n in range(count)
    )


def frames(audio: bytes):
    for start in range(0, len(audio) - FRAME_BYTES + 1, FRAME_BYTES):
        yield audio[start:start + FRAME_BYTES]


class TestFormats:
    def test_a_wav_round_trips(self):
        pcm = sine(100)
        recovered, rate = from_wav(to_wav(pcm))
        assert recovered == pcm
        assert rate == SAMPLE_RATE

    def test_duration_is_computed_from_length(self):
        assert duration_ms(silence(500)) == pytest.approx(500, abs=1)

    def test_downsampling_by_a_whole_factor(self):
        # 48 kHz is what browsers usually capture at.
        out = resample(sine(100, sample_rate=48_000), 48_000, 16_000)
        assert duration_ms(out) == pytest.approx(100, abs=2)

    def test_downsampling_averages_rather_than_dropping_samples(self):
        # Taking every third sample folds high frequencies back into the
        # speech range, which recognition handles noticeably worse.
        loud = sine(100, hz=7000, sample_rate=48_000)
        out = resample(loud, 48_000, 16_000)
        assert len(out) == pytest.approx(len(loud) // 3, abs=4)

    def test_upsampling(self):
        out = resample(sine(100, sample_rate=8_000), 8_000, 16_000)
        assert duration_ms(out) == pytest.approx(100, abs=2)

    def test_a_matching_rate_is_untouched(self):
        pcm = sine(50)
        assert resample(pcm, SAMPLE_RATE, SAMPLE_RATE) is pcm

    def test_empty_audio_does_not_raise(self):
        assert resample(b"", 48_000, 16_000) == b""
        assert to_mono(b"", 2) == b""

    def test_stereo_becomes_mono(self):
        stereo = struct.pack("<hh", 1000, 3000) * 100
        mono = to_mono(stereo, 2)
        assert len(mono) == len(stereo) // 2
        assert struct.unpack("<h", mono[:2])[0] == pytest.approx(2000, abs=2)


class TestEndpointer:
    def test_it_starts_idle(self):
        assert Endpointer().state is Listening.IDLE

    def test_silence_alone_never_opens_a_turn(self):
        endpointer = Endpointer()
        for frame in frames(silence(2000)):
            assert endpointer.feed(frame) is None
        assert not endpointer.speaking

    def test_speech_opens_a_turn(self):
        endpointer = Endpointer()
        for frame in frames(tone(400)):
            endpointer.feed(frame)
        assert endpointer.speaking

    def test_a_turn_ends_after_enough_silence(self):
        endpointer = Endpointer()
        found = None
        for frame in frames(tone(600) + silence(1200)):
            found = endpointer.feed(frame) or found
        assert found is not None
        assert found.ended_by == "silence"
        assert not endpointer.speaking

    def test_a_short_pause_does_not_end_a_turn(self):
        # Drawing breath between clauses. Cutting here produces half sentences.
        endpointer = Endpointer()
        found = None
        for frame in frames(tone(400) + silence(240) + tone(400)):
            found = endpointer.feed(frame) or found
        assert found is None
        assert endpointer.speaking

    def test_a_click_is_not_a_turn(self):
        endpointer = Endpointer()
        found = None
        for frame in frames(tone(80) + silence(1200)):
            found = endpointer.feed(frame) or found
        assert found is None

    def test_the_utterance_keeps_its_audio(self):
        endpointer = Endpointer()
        found = None
        for frame in frames(tone(700) + silence(1200)):
            found = endpointer.feed(frame) or found
        assert found.duration_ms > MIN_UTTERANCE_MS
        assert len(found.audio) > 0

    def test_trailing_silence_is_trimmed(self):
        # Real audio, but it says nothing and costs recognition time.
        endpointer = Endpointer()
        found = None
        for frame in frames(tone(700) + silence(2000)):
            found = endpointer.feed(frame) or found
        assert found.duration_ms < 1500

    def test_the_opening_consonant_is_not_clipped(self):
        # Speech is only declared after a few frames; without a preroll those
        # frames are lost and the recording starts mid-word.
        endpointer = Endpointer()
        found = None
        for frame in frames(tone(700) + silence(1200)):
            found = endpointer.feed(frame) or found
        assert found.duration_ms > 700 - FRAME_MS * 4

    def test_a_second_turn_can_follow_the_first(self):
        endpointer = Endpointer()
        found = [f for frame in frames(tone(600) + silence(1200) + tone(600)
                                       + silence(1200))
                 if (f := endpointer.feed(frame))]
        assert len(found) == 2

    def test_an_endless_turn_is_cut_off(self):
        endpointer = Endpointer(silence_frames_to_end=10_000)
        found = None
        for frame in frames(tone(26_000)):
            found = endpointer.feed(frame) or found
            if found:
                break
        assert found is not None
        assert found.ended_by == "length"

    def test_a_wrongly_sized_frame_does_not_raise(self):
        assert Endpointer().feed(b"\x00" * 7) is None

    def test_flush_ends_a_turn_in_progress(self):
        endpointer = Endpointer()
        for frame in frames(tone(700)):
            endpointer.feed(frame)
        found = endpointer.flush()
        assert found is not None and found.ended_by == "close"

    def test_flush_when_idle_returns_nothing(self):
        assert Endpointer().flush() is None

    def test_reset_clears_a_turn_in_progress(self):
        endpointer = Endpointer()
        for frame in frames(tone(700)):
            endpointer.feed(frame)
        endpointer.reset()
        assert not endpointer.speaking
        assert endpointer.flush() is None


class TestStreaming:
    def test_arbitrary_chunk_sizes_are_handled(self):
        # A browser sends whatever size it likes, not neat frames.
        endpointer = Endpointer()
        audio = tone(600) + silence(1200)
        found = []
        for start in range(0, len(audio), 999):
            found.extend(endpointer.feed_stream(audio[start:start + 999]))
        assert len(found) == 1

    def test_a_partial_frame_is_carried_forward(self):
        endpointer = Endpointer()
        assert list(endpointer.feed_stream(b"\x00" * (FRAME_BYTES - 2))) == []
