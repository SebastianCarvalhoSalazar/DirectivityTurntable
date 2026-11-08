"""Tests de ``directivity.domain.polar``: niveles por banda y patrón polar."""

from __future__ import annotations

import math

import numpy as np
import pytest

from directivity.domain.polar import (
    OCTAVE_CENTERS,
    MeasurementPoint,
    PolarPattern,
    band_edges,
    exact_center,
    octave_band_levels,
)
from directivity.domain.signals import Signal

FS = 48_000
N = 2**16
BANDS = (63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)


def from_magnitude(magnitude: np.ndarray, seed: int = 0) -> Signal:
    """Señal con un espectro de magnitud exacta y fases aleatorias (determinista)."""
    phases = np.random.default_rng(seed).uniform(0, 2 * np.pi, len(magnitude))
    return Signal(np.fft.irfft(magnitude * np.exp(1j * phases), N), FS)


def bins() -> np.ndarray:
    """Índices de los bins de una rfft de ``N`` muestras, con el 0 reemplazado por 1."""
    k = np.arange(N // 2 + 1, dtype=float)
    k[0] = 1.0
    return k


def impulse(gain: float, n: int = 4800) -> Signal:
    """IR de un impulso con ganancia ``gain`` (espectro plano)."""
    samples = np.zeros(n)
    samples[10] = gain
    return Signal(samples, FS)


class TestBands:
    @pytest.mark.parametrize(
        ("nominal", "exact"), [(63, 62.5), (125, 125.0), (1000, 1000.0), (16000, 16000.0)]
    )
    def test_exact_centers(self, nominal: float, exact: float) -> None:
        assert exact_center(nominal) == exact

    def test_edges_are_one_octave_apart(self) -> None:
        low, high = band_edges(1000.0)
        assert high / low == pytest.approx(2.0)
        assert math.sqrt(low * high) == pytest.approx(1000.0)


class TestOctaveBandLevels:
    def test_pink_noise_has_equal_energy_per_octave(self) -> None:
        levels = octave_band_levels(from_magnitude(1 / np.sqrt(bins())), BANDS)
        values = np.array(list(levels.values()))
        assert np.ptp(values) < 0.5  # ±0.25 dB alrededor de la media

    def test_white_noise_rises_3_db_per_octave(self) -> None:
        levels = list(octave_band_levels(from_magnitude(np.ones(N // 2 + 1)), BANDS).values())
        steps = np.diff(levels)
        np.testing.assert_allclose(steps, 10 * np.log10(2), atol=0.1)  # energía ∝ n.º de bins

    def test_1khz_tone_falls_in_1khz_band(self) -> None:
        t = np.arange(FS) / FS  # 1 s → bins de 1 Hz, el tono cae justo en un bin
        levels = octave_band_levels(Signal(np.sin(2 * np.pi * 1000 * t), FS), BANDS)
        assert max(levels, key=lambda band: levels[band]) == 1000.0
        others = [level for band, level in levels.items() if band != 1000.0]
        assert levels[1000.0] - max(others) > 60

    def test_gain_shifts_every_band_equally(self) -> None:
        base = octave_band_levels(impulse(1.0))
        double = octave_band_levels(impulse(2.0))
        for band in OCTAVE_CENTERS:
            assert double[band] - base[band] == pytest.approx(20 * np.log10(2), abs=1e-9)

    def test_silence_is_minus_infinity(self) -> None:
        assert octave_band_levels(Signal(np.zeros(4800), FS), (1000.0,))[1000.0] == -math.inf

    def test_band_above_nyquist_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Nyquist"):
            octave_band_levels(Signal(np.zeros(1600), 16_000), (8000.0,))

    def test_band_without_bins_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="bins"):
            octave_band_levels(Signal(np.zeros(16), FS), (63.0,))


def cardioid_pattern(angles: list[float], actual_offset: float = 0.0) -> PolarPattern:
    """Patrón de una fuente cardioide ideal: ganancia ``0.5 + 0.5·cos θ`` (θ real)."""
    points = []
    for target in angles:
        actual = target + actual_offset
        gain = 0.5 + 0.5 * math.cos(math.radians(actual))
        points.append(MeasurementPoint(target, actual, impulse(gain)))
    return PolarPattern(points)


class TestPolarPattern:
    def test_cardioid_is_minus_6_db_at_90_degrees(self) -> None:
        pattern = cardioid_pattern([0.0, 30.0, 60.0, 90.0, 120.0, 150.0])
        for band in pattern.bands:
            normalized = pattern.normalized(band)
            assert normalized[0] == 0.0
            assert normalized[3] == pytest.approx(-6.02, abs=0.05)
            assert normalized[2] == pytest.approx(20 * math.log10(0.75), abs=0.05)

    def test_angles_are_the_real_ones(self) -> None:
        points = [
            MeasurementPoint(0.0, 0.0, impulse(1.0)),
            MeasurementPoint(5.0, 4.921875, impulse(1.0)),
            MeasurementPoint(90.0, 90.0, impulse(0.5)),
        ]
        assert PolarPattern(points).angles() == [0.0, 4.921875, 90.0]

    def test_reference_is_the_point_whose_real_angle_is_closest_to_zero(self) -> None:
        # El barrido empieza en -90°; el eje está en la posición 2 y el último punto es 360°.
        pattern = cardioid_pattern([-90.0, -45.0, 0.0, 45.0, 90.0])
        assert pattern.reference_index == 2
        assert pattern.normalized(1000.0)[2] == 0.0
        assert pattern.normalized(1000.0)[0] == pytest.approx(-6.02, abs=0.05)

    def test_reference_uses_real_not_requested_angle(self) -> None:
        # pedido 0° pero la mesa quedó en 3°; pedido 2° y la mesa quedó en 0.18°
        points = [
            MeasurementPoint(0.0, 3.0, impulse(1.0)),
            MeasurementPoint(2.0, 0.18, impulse(0.8)),
        ]
        pattern = PolarPattern(points, centers=(1000.0,))
        assert pattern.reference_index == 1

    def test_360_wraps_to_zero(self) -> None:
        points = [
            MeasurementPoint(360.0, 360.0, impulse(1.0)),
            MeasurementPoint(90, 90, impulse(1)),
        ]
        assert PolarPattern(points, centers=(1000.0,)).reference_index == 0

    @pytest.mark.parametrize(
        ("actuals", "expected"),
        [((350.0, 100.0), 0), ((-350.0, 100.0), 0), ((-190.0, 100.0), 1), ((190.0, 100.0), 1)],
    )
    def test_reference_wraps_angles_beyond_180(
        self, actuals: tuple[float, float], expected: int
    ) -> None:
        # 350° y -350° están a 10° del eje; ±190° están a 170°, más lejos que 100°.
        points = [MeasurementPoint(a, a, impulse(1.0)) for a in actuals]
        assert PolarPattern(points, centers=(1000.0,)).reference_index == expected

    def test_levels_and_unknown_band(self) -> None:
        pattern = cardioid_pattern([0.0, 90.0])
        assert pattern.levels(1000.0)[0] - pattern.levels(1000.0)[1] == pytest.approx(
            6.02, abs=0.01
        )
        assert len(pattern.points) == 2
        with pytest.raises(KeyError):
            pattern.levels(1234.0)

    def test_requires_points(self) -> None:
        with pytest.raises(ValueError):
            PolarPattern([])
