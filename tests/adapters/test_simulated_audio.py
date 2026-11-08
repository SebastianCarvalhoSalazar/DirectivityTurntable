"""Tests de ``SimulatedAudio`` y de los modelos de directividad."""

from __future__ import annotations

import math

import numpy as np
import pytest

from directivity.adapters.simulated_audio import MODELS, SimulatedAudio, cardioid, omni
from directivity.domain.signals import LogSweep

FS = 16_000
SWEEP = LogSweep(f_start=50.0, f_stop=7000.0, duration_s=0.3)


class TestModels:
    @pytest.mark.parametrize("angle", [0.0, 90.0, 180.0, -45.0])
    def test_omni_is_flat(self, angle: float) -> None:
        assert omni(angle) == 1.0

    @pytest.mark.parametrize(
        ("angle", "gain"), [(0.0, 1.0), (90.0, 0.5), (180.0, 0.0), (-90.0, 0.5), (360.0, 1.0)]
    )
    def test_cardioid(self, angle: float, gain: float) -> None:
        assert cardioid(angle) == pytest.approx(gain, abs=1e-12)

    def test_registry(self) -> None:
        assert set(MODELS) == {"omni", "cardioid"}


class TestSimulatedAudio:
    def test_gain_follows_the_current_angle(self) -> None:
        angle = [0.0]
        audio = SimulatedAudio(lambda: angle[0], cardioid, FS, delay_s=0.002, noise_db=None)
        peaks = []
        for value in (0.0, 90.0):
            angle[0] = value
            ir = SWEEP.deconvolve(audio.play_and_record(SWEEP.generate(FS)))
            peaks.append(ir.samples[32])  # 2 ms a 16 kHz
        assert 20 * math.log10(peaks[1] / peaks[0]) == pytest.approx(-6.02, abs=0.05)

    def test_impulse_response_has_direct_and_reflections(self) -> None:
        audio = SimulatedAudio(lambda: 0.0, omni, FS, delay_s=0.001, reflections=[(0.005, 0.25)])
        ir = audio.impulse_response(0.0).samples
        assert ir[16] == 1.0
        assert ir[80] == 0.25
        assert np.count_nonzero(ir) == 2

    def test_reflections_do_not_depend_on_angle(self) -> None:
        audio = SimulatedAudio(
            lambda: 0.0, cardioid, FS, delay_s=0.001, reflections=[(0.005, 0.25)]
        )
        assert audio.impulse_response(180.0).samples[80] == 0.25
        assert audio.impulse_response(180.0).samples[16] == pytest.approx(0.0, abs=1e-12)

    def test_recording_length_includes_ir_and_tail(self) -> None:
        audio = SimulatedAudio(lambda: 0.0, sample_rate=FS, delay_s=0.01, tail_s=0.1, noise_db=None)
        excitation = SWEEP.generate(FS)
        assert len(audio.play_and_record(excitation)) == len(excitation) + 160 + 1600

    def test_noise_is_reproducible_and_at_requested_level(self) -> None:
        def record(seed: int) -> np.ndarray:
            audio = SimulatedAudio(lambda: 180.0, cardioid, FS, noise_db=-40.0, seed=seed)
            return audio.play_and_record(SWEEP.generate(FS)).samples

        first, again, other = record(1), record(1), record(2)
        np.testing.assert_array_equal(first, again)
        assert not np.array_equal(first, other)
        # a 180° la cardioide es nula: la grabación es solo ruido
        assert 20 * math.log10(np.std(first)) == pytest.approx(-40.0, abs=0.5)

    @pytest.mark.parametrize(
        "kwargs",
        [{"sample_rate": 0}, {"delay_s": -0.001}, {"tail_s": -1.0}, {"reflections": [(-0.1, 0.5)]}],
    )
    def test_invalid_parameters(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            SimulatedAudio(lambda: 0.0, **kwargs)  # type: ignore[arg-type]
