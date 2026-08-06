"""
Audio handling and turn taking.

The hard part of a voice agent is not recognising speech, it is deciding when
the caller has finished. Cut too early and you interrupt someone drawing
breath mid-sentence. Wait too long and every reply feels sluggish. Both are
the same parameter, and there is no value that is right for everyone.

Voice activity detection runs on 20 millisecond frames. Speech is declared
after a few consecutive speech frames, which keeps a cough from opening a
turn, and the turn ends after a run of silence long enough to be a pause
rather than a breath.

Nothing here needs a network. The endpointer is a state machine over frames,
so it can be tested against synthetic audio.
"""

from __future__ import annotations

import io
import logging
import time
import wave
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)

# Fixed by the recogniser and the detector: both want 16 kHz mono 16-bit.
SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
CHANNELS = 1

# webrtcvad accepts 10, 20 or 30 ms frames only. 20 keeps the state machine
# responsive without spending too much time in Python per second of audio.
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * SAMPLE_WIDTH * FRAME_MS // 1000

# 0 lets through the most noise, 3 the least. 2 holds up on a laptop
# microphone in a room with a fan, which is the realistic test case.
VAD_AGGRESSIVENESS = 2

# How much speech opens a turn. 60 ms is short enough not to clip a word and
# long enough to ignore a keyboard press.
SPEECH_FRAMES_TO_START = 3

# 100 ms of detected voice across the whole recording before it counts as a
# turn. Checked at the end rather than the start, because the start has to stay
# sensitive: raising the three frames above clips the first consonant off every
# answer, and that is a cost paid on every turn to fix a problem that happens
# on a few.
#
# Started at eight frames and that rejected real speech on a live microphone.
# The voice detector is far less confident about a laptop microphone in a room
# than about clean audio, so a spoken sentence can carry only a handful of
# frames it is willing to call speech. Five still rejects the three-frame
# click that opens a turn from nothing, which is what this is for.
MIN_VOICED_FRAMES = 5

# How much silence closes it. 700 ms is a pause; 300 ms is drawing breath
# between clauses and cutting there produces half sentences.
# 700 ms of quiet before the turn is treated as over. Briefly 500, to make the
# agent quicker to answer, and that was the wrong lever: cutting 200 ms earlier
# ends the recording while people are still mid-sentence, and the recogniser
# answers a short clipped utterance with an invented one. "No" came back as
# "None of the above". Recognition quality is worth more than a fifth of a
# second, and the delay people actually notice is the half-duplex window while
# the agent is speaking, not this.
SILENCE_FRAMES_TO_END = 35

# Below this an utterance is a click or a cough, not a turn.
MIN_UTTERANCE_MS = 250

# Below this loudness there is nothing worth transcribing. Room tone and the
# tail of the agent's own voice reaching the microphone both sit well under
# it, and a recogniser handed near-silence does not return nothing: it returns
# "Thank you" or "..." with confidence, which the agent then answers.
#
# Was 220, which rejected a real caller at 188 on a laptop microphone. The
# figure was picked against synthesised audio, which is louder and more even
# than a person sitting a foot from a built-in microphone. Lowering it does
# let more of the agent's own voice through, and that is handled by matching
# what comes back against what was just said rather than by loudness, which
# is the better tool for it anyway.
#
# Then 150 rejected the same caller at 142 and 135. Chasing a live microphone
# downwards one reading at a time is not a method, so this is now set from what
# the microphone actually produces: room tone on a laptop sits around 20 to 40,
# and a person speaking normally a foot away sits above 120 even at their
# quietest. 100 is below anything spoken and comfortably above the room.
MIN_UTTERANCE_RMS = 100

# Above this something has gone wrong, or the caller is reading an essay.
# Either way the turn has to end so the agent can respond.
MAX_UTTERANCE_MS = 25_000


class Listening(Enum):
    IDLE = "idle"
    SPEAKING = "speaking"


@dataclass
class Utterance:
    """One turn of speech, ready to transcribe."""

    audio: bytes
    duration_ms: float
    started_at: float
    ended_by: str  # "silence", "length" or "close"

    @property
    def wav(self) -> bytes:
        return to_wav(self.audio)


@dataclass
class Endpointer:
    """Decides where one turn of speech ends and the next begins.

    Fed frames of audio, it returns an Utterance at the moment the caller
    stops. Between those moments it returns nothing.
    """

    aggressiveness: int = VAD_AGGRESSIVENESS
    speech_frames_to_start: int = SPEECH_FRAMES_TO_START
    silence_frames_to_end: int = SILENCE_FRAMES_TO_END

    state: Listening = Listening.IDLE
    _vad: object | None = field(default=None, repr=False)
    _speech_run: int = 0
    _silence_run: int = 0
    # How many frames in this recording the detector called speech, as opposed
    # to how long the recording is. The difference is the whole point: a fan
    # or a keyboard click produces a long recording with almost no voice in it.
    _voiced: int = 0
    _buffer: bytearray = field(default_factory=bytearray, repr=False)
    # Frames from just before speech was declared, so the first consonant is
    # not clipped off the front of the recording.
    _preroll: list[bytes] = field(default_factory=list, repr=False)
    _started_at: float = 0.0

    def __post_init__(self) -> None:
        import webrtcvad

        self._vad = webrtcvad.Vad(self.aggressiveness)

    @property
    def speaking(self) -> bool:
        return self.state is Listening.SPEAKING

    def _is_speech(self, frame: bytes) -> bool:
        if len(frame) != FRAME_BYTES:
            return False
        try:
            return self._vad.is_speech(frame, SAMPLE_RATE)
        except Exception:
            # A malformed frame should not end a call.
            return False

    def feed(self, frame: bytes) -> Utterance | None:
        """Add one frame. Returns an utterance when the caller has stopped."""
        speech = self._is_speech(frame)

        if self.state is Listening.IDLE:
            self._preroll.append(frame)
            # Just enough to recover the frames spent deciding this was speech.
            # More than that pads every turn with silence, which inflates short
            # bursts past the minimum length and makes a cough look like a word.
            if len(self._preroll) > self.speech_frames_to_start + 1:
                self._preroll.pop(0)

            self._speech_run = self._speech_run + 1 if speech else 0
            if self._speech_run >= self.speech_frames_to_start:
                self.state = Listening.SPEAKING
                self._started_at = time.perf_counter()
                self._buffer = bytearray(b"".join(self._preroll))
                self._preroll.clear()
                self._silence_run = 0
                self._voiced = self._speech_run
            return None

        self._buffer.extend(frame)
        self._silence_run = 0 if speech else self._silence_run + 1
        if speech:
            self._voiced += 1

        if self._silence_run >= self.silence_frames_to_end:
            return self._finish("silence")

        if self.duration_ms >= MAX_UTTERANCE_MS:
            return self._finish("length")

        return None

    def feed_stream(self, audio: bytes):
        """Feed arbitrary audio, splitting it into frames. Yields utterances."""
        self._pending = getattr(self, "_pending", bytearray())
        self._pending.extend(audio)
        while len(self._pending) >= FRAME_BYTES:
            frame = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            found = self.feed(frame)
            if found:
                yield found

    @property
    def duration_ms(self) -> float:
        return len(self._buffer) / (SAMPLE_RATE * SAMPLE_WIDTH) * 1000

    def _finish(self, reason: str) -> Utterance | None:
        audio = bytes(self._buffer)
        started = self._started_at
        voiced = self._voiced

        self.state = Listening.IDLE
        self._buffer = bytearray()
        self._speech_run = self._silence_run = self._voiced = 0
        self._preroll.clear()

        # Enough voice in the recording to be a word, rather than enough noise
        # to have started one. Sixty milliseconds of detected voice opens a
        # turn, which a fan or a keyboard click clears, and the recording that
        # followed was long enough and loud enough to reach the recogniser.
        # The recogniser does not return nothing for that: it returns a
        # plausible sentence, and the agent answered a question nobody asked.
        if voiced < MIN_VOICED_FRAMES:
            # Numbers in the message rather than only in the fields, because
            # the console formatter shows the message and tuning this needs
            # the counts.
            log.info(f"ignoring a recording with too little voice in it: "
                     f"{voiced} voiced frames, floor {MIN_VOICED_FRAMES}, "
                     f"{round(len(audio) / (SAMPLE_RATE * SAMPLE_WIDTH) * 1000)} ms")
            return None

        # Trailing silence goes before the length is judged. It is real audio
        # that says nothing, it costs recognition time, and counting it makes a
        # cough look like a sentence.
        if reason == "silence":
            trim = (self.silence_frames_to_end - 3) * FRAME_BYTES
            if len(audio) - trim > FRAME_BYTES:
                audio = audio[:len(audio) - trim]

        duration = len(audio) / (SAMPLE_RATE * SAMPLE_WIDTH) * 1000
        # These two were at debug, which meant a call where nothing was heard
        # produced a completely silent log and there was no way to tell a quiet
        # microphone from a broken one.
        if duration < MIN_UTTERANCE_MS:
            log.info("ignoring a burst too short to be speech",
                     extra={"ms": round(duration), "floor_ms": MIN_UTTERANCE_MS})
            return None

        loudness = rms(audio)
        if loudness < MIN_UTTERANCE_RMS:
            log.info(f"ignoring audio too quiet to be speech: rms "
                     f"{round(loudness)}, floor {MIN_UTTERANCE_RMS}, "
                     f"{round(duration)} ms")
            return None

        return Utterance(audio=audio, duration_ms=duration,
                         started_at=started, ended_by=reason)

    def flush(self) -> Utterance | None:
        """End the current turn early, for when the line drops mid-sentence."""
        if self.state is not Listening.SPEAKING:
            return None
        return self._finish("close")

    def reset(self) -> None:
        self.state = Listening.IDLE
        self._buffer = bytearray()
        self._preroll.clear()
        self._speech_run = self._silence_run = 0
        if hasattr(self, "_pending"):
            self._pending = bytearray()


# --- Format helpers ----------------------------------------------------------


def to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw samples in a WAV container.

    Recognition services want a container with a header. Building it in memory
    avoids a temporary file per turn.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def from_wav(data: bytes) -> tuple[bytes, int]:
    """Pull raw samples and their rate back out of a WAV container."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        return handle.readframes(handle.getnframes()), handle.getframerate()


def resample(pcm: bytes, source_rate: int, target_rate: int = SAMPLE_RATE) -> bytes:
    """Convert to the rate the detector and recogniser both require.

    Browsers capture at whatever their hardware runs at, commonly 48 kHz, and
    the detector rejects anything else outright rather than coping with it.

    Written with numpy rather than the standard library's audioop, which is
    deprecated in 3.12 and gone in 3.13. Where the rates divide evenly, which
    covers 48 kHz and 32 kHz, samples are averaged in groups. That averaging
    is a crude low pass filter, and without one, downsampling folds high
    frequencies back into the speech range as a metallic rasp that recognition
    handles noticeably worse.
    """
    if source_rate == target_rate or not pcm:
        return pcm

    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return pcm

    if source_rate % target_rate == 0:
        factor = source_rate // target_rate
        usable = samples.size - (samples.size % factor)
        if usable == 0:
            return b""
        grouped = samples[:usable].reshape(-1, factor).mean(axis=1)
        return grouped.astype(np.int16).tobytes()

    count = int(samples.size * target_rate / source_rate)
    if count == 0:
        return b""
    positions = np.linspace(0, samples.size - 1, count)
    interpolated = np.interp(positions, np.arange(samples.size), samples)
    return interpolated.astype(np.int16).tobytes()


def to_mono(pcm: bytes, channels: int) -> bytes:
    """Average interleaved channels down to one."""
    if channels == 1 or not pcm:
        return pcm

    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16)
    usable = samples.size - (samples.size % channels)
    if usable == 0:
        return b""
    averaged = samples[:usable].reshape(-1, channels).mean(axis=1)
    return averaged.astype(np.int16).tobytes()


def rms(pcm: bytes) -> float:
    """Loudness, as root mean square amplitude.

    The detector answers "does this sound like speech", which room tone and a
    speaker in the same room can both pass. This answers "is there enough
    signal here to be worth anything", which they do not.
    """
    if not pcm:
        return 0.0

    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples ** 2)))


def duration_ms(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    return len(pcm) / (sample_rate * SAMPLE_WIDTH) * 1000


def silence(ms: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00" * int(sample_rate * SAMPLE_WIDTH * ms / 1000)
