"""Tests de ``directivity.domain.signals``: ``Signal`` y la estrategia ``LogSweep``."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from directivity.domain.signals import ExcitationSignal, LogSweep, Signal, linear_convolve

FS = 48_000


def db(value: float) -> float:
    """Convierte una relación de amplitud a dB."""
    return float(20 * np.log10(abs(value)))


def apply_ir(excitation: Signal, taps: dict[int, float], tail: int = 4800) -> Signal:
    """Simula una grabación: convoluciona la excitación con una IR de deltas ``{retardo: g}``."""
    ir = np.zeros(max(taps) + 1)
    for delay, gain in taps.items():
        ir[delay] = gain
    recorded = linear_convolve(excitation.samples, ir)
    return Signal(np.concatenate([recorded, np.zeros(tail)]), excitation.sample_rate)


@dataclass(frozen=True)
class DiracExcitation:
    """Excitación trivial (un impulso) definida solo en los tests.

    Muestra que cualquier clase con ``generate`` y ``deconvolve`` sirve como estrategia
    sin tocar el dominio (OCP) y que cumple las mismas propiedades que ``LogSweep`` (LSP).
    """

    length: int = 1024

    def generate(self, sample_rate: int) -> Signal:
        """Impulso unitario seguido de ceros."""
        samples = np.zeros(self.length)
        samples[0] = 1.0
        return Signal(samples, sample_rate)

    def deconvolve(self, recording: Signal) -> Signal:
        """Con un impulso, la grabación ya es la IR."""
        return recording


class TestSignal:
    def test_is_immutable_and_float64(self) -> None:
        signal = Signal(np.array([1, 2, 3]), 8000)
        assert signal.samples.dtype == np.float64
        with pytest.raises(ValueError):
            signal.samples[0] = 5.0

    def test_copies_input(self) -> None:
        data = np.zeros(4)
        signal = Signal(data, 8000)
        data[0] = 1.0
        assert signal.samples[0] == 0.0

    def test_length_and_duration(self) -> None:
        signal = Signal(np.zeros(24_000), FS)
        assert len(signal) == 24_000
        assert signal.duration_s == 0.5

    def test_with_samples_keeps_sample_rate(self) -> None:
        assert Signal(np.zeros(3), 8000).with_samples([1.0, 2.0]).sample_rate == 8000

    @pytest.mark.parametrize(
        ("samples", "sample_rate"), [(np.zeros((2, 3)), 8000), (np.zeros(3), 0)]
    )
    def test_rejects_invalid(self, samples: np.ndarray, sample_rate: int) -> None:
        with pytest.raises(ValueError):
            Signal(samples, sample_rate)


class TestLinearConvolve:
    def test_matches_numpy_convolve(self) -> None:
        rng = np.random.default_rng(0)
        a, b = rng.standard_normal(100), rng.standard_normal(37)
        np.testing.assert_allclose(linear_convolve(a, b), np.convolve(a, b), atol=1e-10)


class TestLogSweep:
    def test_generate_length_amplitude_and_fades(self) -> None:
        sweep = LogSweep(duration_s=1.0, amplitude=0.5).generate(FS)
        assert len(sweep) == FS
        assert sweep.sample_rate == FS
        assert np.max(np.abs(sweep.samples)) <= 0.5
        assert sweep.samples[0] == 0.0
        assert abs(sweep.samples[-1]) < 1e-3

    def test_instantaneous_frequency_grows_exponentially(self) -> None:
        sweep = LogSweep(f_start=100.0, f_stop=10_000.0, duration_s=1.0, fade_s=0.0)
        samples = sweep.generate(FS).samples
        crossings = np.flatnonzero(np.diff(np.signbit(samples)))
        # en la mitad del barrido la frecuencia es la media geométrica: 1 kHz
        middle = crossings[(crossings > FS // 2 - 2400) & (crossings < FS // 2 + 2400)]
        freq = (len(middle) - 1) / 2 / ((middle[-1] - middle[0]) / FS)
        assert freq == pytest.approx(1000.0, rel=0.05)

    @pytest.mark.parametrize(("delay", "gain"), [(0, 1.0), (480, 0.5), (1234, -0.1), (7, 2.0)])
    def test_recovers_delay_and_gain(self, delay: int, gain: float) -> None:
        sweep = LogSweep(duration_s=1.0)
        ir = sweep.deconvolve(apply_ir(sweep.generate(FS), {delay: gain}))
        peak = int(np.argmax(np.abs(ir.samples)))
        assert abs(peak - delay) <= 1
        assert db(ir.samples[peak]) - db(gain) == pytest.approx(0.0, abs=0.1)
        assert np.sign(ir.samples[peak]) == np.sign(gain)

    def test_output_has_recording_length_and_rate(self) -> None:
        sweep = LogSweep(duration_s=0.5)
        recording = apply_ir(sweep.generate(FS), {100: 1.0})
        ir = sweep.deconvolve(recording)
        assert len(ir) == len(recording)
        assert ir.sample_rate == FS

    def test_separates_direct_sound_and_reflection(self) -> None:
        sweep = LogSweep(duration_s=1.0)
        ir = sweep.deconvolve(apply_ir(sweep.generate(FS), {200: 1.0, 1400: 0.3}))
        assert ir.samples[200] == pytest.approx(1.0, abs=0.012)
        assert ir.samples[1400] == pytest.approx(0.3, abs=0.012)

    def test_robust_to_noise(self) -> None:
        sweep = LogSweep(duration_s=1.0)
        recording = apply_ir(sweep.generate(FS), {300: 1.0})
        noise = np.random.default_rng(1).normal(0, 0.005, len(recording))  # ~-40 dB
        ir = sweep.deconvolve(recording.with_samples(recording.samples + noise))
        assert int(np.argmax(np.abs(ir.samples))) == 300
        assert db(ir.samples[300]) == pytest.approx(0.0, abs=0.1)

    def test_harmonic_distortion_is_pushed_to_negative_time(self) -> None:
        sweep = LogSweep(duration_s=1.0)
        x = sweep.generate(FS)
        distorted = x.with_samples(x.samples + 0.2 * x.samples**2)  # 2.º armónico fuerte
        recording = distorted.with_samples(np.concatenate([distorted.samples, np.zeros(4800)]))
        ir = sweep.deconvolve(recording)
        assert db(ir.samples[0]) == pytest.approx(0.0, abs=0.1)
        # lejos del pico (> 5 ms), la parte causal queda limpia de armónicos
        assert db(np.max(np.abs(ir.samples[240:]))) < -40

    def test_f_stop_must_fit_under_nyquist(self) -> None:
        with pytest.raises(ValueError, match="Nyquist"):
            LogSweep(f_stop=20_000.0).generate(32_000)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"f_start": 0.0},
            {"f_start": 1000.0, "f_stop": 500.0},
            {"duration_s": 0.0},
            {"fade_s": 1.5, "duration_s": 2.0},
            {"amplitude": 1.5},
        ],
    )
    def test_invalid_parameters(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            LogSweep(**kwargs)


@pytest.mark.parametrize(
    "excitation",
    [LogSweep(duration_s=0.5), DiracExcitation()],
    ids=["log-sweep", "dirac"],
)
class TestExcitationStrategyProperties:
    """Propiedades que toda ``ExcitationSignal`` debe cumplir (intercambiables, OCP/LSP)."""

    def test_self_deconvolution_is_unit_impulse_at_zero(self, excitation: ExcitationSignal) -> None:
        x = excitation.generate(FS)
        ir = excitation.deconvolve(x.with_samples(np.concatenate([x.samples, np.zeros(1000)])))
        assert int(np.argmax(np.abs(ir.samples))) == 0
        assert ir.samples[0] == pytest.approx(1.0, abs=1e-6)

    def test_delay_is_preserved(self, excitation: ExcitationSignal) -> None:
        ir = excitation.deconvolve(apply_ir(excitation.generate(FS), {250: 0.7}))
        assert int(np.argmax(np.abs(ir.samples))) == 250
