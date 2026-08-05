"""
Shared test helpers.

The audio generator here matters more than it looks. Voice activity detection
is built to recognise speech, and a pure sine tone is not speech: it has no
harmonic structure and no changing envelope, so the detector fires on the onset
and then goes quiet. Tests written against a sine wave pass for the wrong
reason, and they passed here for a while before anyone checked.

Measured against the detector: a pure 220 Hz tone registers on 4 frames out of
20. A stack of harmonics with a moving envelope and a little breath noise
registers on 20 out of 20, which is what real speech does.
"""

from __future__ import annotations

import numpy as np

from voice_agent.audio import SAMPLE_RATE


def voiced(ms: int, pitch: float = 140.0, amplitude: float = 9000.0,
           seed: int = 7) -> bytes:
    """Audio the detector treats as speech.

    A voice is a buzz at the pitch of the vocal folds, shaped by the mouth, with
    turbulent noise on top. All three parts are needed; leaving out the
    harmonics is what makes a sine wave undetectable as speech.
    """
    count = int(SAMPLE_RATE * ms / 1000)
    if count <= 0:
        return b""

    time = np.arange(count) / SAMPLE_RATE
    rng = np.random.default_rng(seed)

    # Syllable-rate loudness changes, around five a second.
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 5 * time)

    harmonics = sum((amplitude / k) * np.sin(2 * np.pi * pitch * k * time)
                    for k in range(1, 20))

    signal = envelope * harmonics + rng.normal(0, amplitude / 10, count)
    return np.clip(signal, -32000, 32000).astype(np.int16).tobytes()


def quiet(ms: int, amplitude: float = 30.0, seed: int = 3) -> bytes:
    """Room tone rather than digital silence, which is what a microphone sends."""
    count = int(SAMPLE_RATE * ms / 1000)
    if count <= 0:
        return b""
    rng = np.random.default_rng(seed)
    return rng.normal(0, amplitude, count).astype(np.int16).tobytes()
