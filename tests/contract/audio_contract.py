"""Batería de contrato del puerto ``AudioIO``.

Las subclases proveen un fixture ``audio``. Hoy corre contra ``SimulatedAudio``; un
adaptador real (p. ej. con ``sounddevice``) se agrega como otro parámetro del fixture.
"""

from __future__ import annotations

import numpy as np
import pytest

from directivity.domain.signals import LogSweep, Signal
from directivity.ports import AudioIO


def short_sweep(sample_rate: int) -> Signal:
    """Barrido corto (0.2 s) para que el contrato corra rápido también con hardware."""
    return LogSweep(f_start=100.0, f_stop=min(8000.0, sample_rate * 0.45), duration_s=0.2).generate(
        sample_rate
    )


class AudioContract:
    """Tests que definen el comportamiento observable de una interfaz de audio."""

    def test_sample_rate_is_positive(self, audio: AudioIO) -> None:
        assert audio.sample_rate > 0

    def test_recording_keeps_sample_rate(self, audio: AudioIO) -> None:
        recording = audio.play_and_record(short_sweep(audio.sample_rate))
        assert recording.sample_rate == audio.sample_rate

    def test_recording_is_at_least_as_long_as_excitation(self, audio: AudioIO) -> None:
        excitation = short_sweep(audio.sample_rate)
        assert len(audio.play_and_record(excitation)) >= len(excitation)

    def test_recording_is_finite(self, audio: AudioIO) -> None:
        recording = audio.play_and_record(short_sweep(audio.sample_rate))
        assert np.all(np.isfinite(recording.samples))

    def test_rejects_wrong_sample_rate(self, audio: AudioIO) -> None:
        other = audio.sample_rate // 2
        with pytest.raises(ValueError):
            audio.play_and_record(short_sweep(other))
