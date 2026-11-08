"""Tests de ``directivity.domain.windows``."""

from __future__ import annotations

import numpy as np
import pytest

from directivity.domain.signals import Signal
from directivity.domain.windows import RectangularWindow, TukeyWindow, Window

FS = 48_000
WINDOWS: list[Window] = [RectangularWindow(), TukeyWindow(0.0), TukeyWindow(0.3), TukeyWindow(1.0)]
IDS = ["rect", "tukey-0", "tukey-0.3", "tukey-1"]


def ones(n: int = 1000) -> Signal:
    """Señal constante de unos (deja ver los pesos de la ventana)."""
    return Signal(np.ones(n), FS)


@pytest.mark.parametrize("window", WINDOWS, ids=IDS)
class TestWindowStrategyProperties:
    """Propiedades comunes a toda ``Window`` (intercambiables sin tocar otros módulos)."""

    def test_keeps_length_and_sample_rate(self, window: Window) -> None:
        out = window.apply(ones(), 100, 400)
        assert len(out) == 1000
        assert out.sample_rate == FS

    def test_zeroes_outside_the_gate(self, window: Window) -> None:
        out = window.apply(ones(), 100, 400).samples
        assert not out[:100].any()
        assert not out[500:].any()

    def test_weights_are_between_0_and_1_and_symmetric(self, window: Window) -> None:
        gated = window.apply(ones(), 0, 401).samples[:401]
        assert np.min(gated) >= 0.0
        assert np.max(gated) == pytest.approx(1.0)
        np.testing.assert_allclose(gated, gated[::-1], atol=1e-12)

    def test_does_not_modify_input(self, window: Window) -> None:
        signal = ones()
        window.apply(signal, 0, 10)
        assert signal.samples.all()

    @pytest.mark.parametrize(("start", "length"), [(-1, 10), (0, 0), (995, 10)])
    def test_rejects_gate_outside_signal(self, window: Window, start: int, length: int) -> None:
        with pytest.raises(ValueError):
            window.apply(ones(), start, length)


class TestTukey:
    @pytest.mark.parametrize("n", [2, 11, 400, 401])
    def test_alpha_0_is_rectangular(self, n: int) -> None:
        np.testing.assert_array_equal(TukeyWindow(0.0).weights(n), RectangularWindow().weights(n))

    @pytest.mark.parametrize("n", [2, 11, 400, 401])
    def test_alpha_1_is_hann(self, n: int) -> None:
        np.testing.assert_allclose(TukeyWindow(1.0).weights(n), np.hanning(n), atol=1e-12)

    def test_flat_center(self) -> None:
        w = TukeyWindow(0.2).weights(1001)
        np.testing.assert_array_equal(w[101:900], 1.0)
        assert w[0] == 0.0
        assert w[50] < 1.0

    def test_edges_are_monotonic(self) -> None:
        w = TukeyWindow(0.5).weights(1000)
        assert np.all(np.diff(w[:250]) >= 0)
        assert np.all(np.diff(w[750:]) <= 0)

    def test_single_sample(self) -> None:
        np.testing.assert_array_equal(TukeyWindow(0.5).weights(1), [1.0])

    @pytest.mark.parametrize("alpha", [-0.1, 1.1])
    def test_invalid_alpha(self, alpha: float) -> None:
        with pytest.raises(ValueError):
            TukeyWindow(alpha)

    def test_gating_removes_a_reflection(self) -> None:
        ir = np.zeros(2000)
        ir[100], ir[900] = 1.0, 0.4  # directo y reflexión
        # borde = alpha·(600-1)/2 ≈ 75 muestras < 100: el directo cae en la zona plana
        gated = TukeyWindow(0.25).apply(Signal(ir, FS), 0, 600).samples
        assert gated[100] == pytest.approx(1.0)
        assert gated[900] == 0.0

    def test_direct_sound_inside_the_taper_is_attenuated(self) -> None:
        # Si la compuerta abre a menos de un borde del directo, lo atenúa: el inicio de
        # la compuerta debe dejar un margen mayor que alpha·(length-1)/2.
        ir = np.zeros(2000)
        ir[100] = 1.0
        gated = TukeyWindow(0.25).apply(Signal(ir, FS), 50, 600).samples
        assert gated[100] < 0.8
