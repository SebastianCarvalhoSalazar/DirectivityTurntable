"""Puntos de medición, niveles por banda de octava y patrón polar."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from directivity.domain.signals import Signal

OCTAVE_CENTERS: tuple[float, ...] = (63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0)
"""Centros nominales de banda de octava usados por defecto, en Hz."""


def exact_center(nominal_hz: float) -> float:
    """Centro exacto en base 2 de una banda nominal (p. ej. 63 → 62.5, 16000 → 16000).

    Args:
        nominal_hz: Centro nominal de la banda de octava, en Hz.

    Returns:
        ``1000 · 2^n``, con ``n`` el entero más cercano a ``log2(nominal / 1000)``.
    """
    return 1000.0 * 2.0 ** round(math.log2(nominal_hz / 1000.0))


def band_edges(nominal_hz: float) -> tuple[float, float]:
    """Límites inferior y superior de una banda de octava.

    Args:
        nominal_hz: Centro nominal de la banda, en Hz.

    Returns:
        ``(fc / √2, fc · √2)`` con ``fc`` el centro exacto.
    """
    center = exact_center(nominal_hz)
    return center / math.sqrt(2.0), center * math.sqrt(2.0)


def octave_band_levels(ir: Signal, centers: Sequence[float] = OCTAVE_CENTERS) -> dict[float, float]:
    """Nivel de energía de la IR en cada banda de octava, en dB.

    La energía de cada banda es la suma de ``|X[k]|²`` de los bins de la FFT dentro de
    ``[fc/√2, fc·√2)``. El nivel es relativo (dB re 1); solo importan las diferencias.

    Args:
        ir: Respuesta al impulso (normalmente compuertada).
        centers: Centros nominales de las bandas, en Hz.

    Returns:
        ``{centro_nominal: nivel_dB}``; ``-inf`` si la banda no tiene energía.

    Raises:
        ValueError: Si una banda supera la frecuencia de Nyquist o no contiene bins.
    """
    spectrum = np.fft.rfft(ir.samples)
    power = np.abs(spectrum) ** 2 / len(ir)
    freqs = np.fft.rfftfreq(len(ir), d=1.0 / ir.sample_rate)
    levels: dict[float, float] = {}
    for center in centers:
        low, high = band_edges(center)
        if high > ir.sample_rate / 2:
            raise ValueError(
                f"la banda de {center:g} Hz supera Nyquist ({ir.sample_rate / 2:g} Hz)"
            )
        in_band = (freqs >= low) & (freqs < high)
        if not in_band.any():
            raise ValueError(f"la banda de {center:g} Hz no contiene bins; alarga la señal")
        energy = float(np.sum(power[in_band]))
        levels[center] = 10.0 * math.log10(energy) if energy > 0 else -math.inf
    return levels


@dataclass(frozen=True, eq=False)
class MeasurementPoint:
    """Resultado de medir en un ángulo.

    Attributes:
        target_deg: Ángulo pedido a la mesa, en grados.
        actual_deg: Ángulo real informado por la mesa, en grados.
        ir: Respuesta al impulso procesada en ese ángulo.
    """

    target_deg: float
    actual_deg: float
    ir: Signal


def _wrap_deg(angle: float) -> float:
    """Lleva un ángulo a ``(-180, 180]`` grados."""
    wrapped = math.fmod(angle, 360.0)
    if wrapped > 180.0:
        wrapped -= 360.0
    elif wrapped <= -180.0:
        wrapped += 360.0
    return wrapped


class PolarPattern:
    """Patrón polar: nivel por banda de octava en cada ángulo real medido.

    Args:
        points: Puntos medidos, en el orden del barrido.
        centers: Centros nominales de las bandas de octava, en Hz.

    Raises:
        ValueError: Si no hay puntos o una banda no es válida para las IRs.
    """

    def __init__(
        self, points: Sequence[MeasurementPoint], centers: Sequence[float] = OCTAVE_CENTERS
    ) -> None:
        if not points:
            raise ValueError("un patrón polar necesita al menos un punto")
        self._points = tuple(points)
        self._bands = tuple(centers)
        self._levels = [octave_band_levels(p.ir, self._bands) for p in self._points]
        self._reference = min(
            range(len(self._points)), key=lambda i: abs(_wrap_deg(self._points[i].actual_deg))
        )

    @property
    def points(self) -> tuple[MeasurementPoint, ...]:
        """Puntos medidos, en el orden del barrido."""
        return self._points

    @property
    def bands(self) -> tuple[float, ...]:
        """Centros nominales de las bandas, en Hz."""
        return self._bands

    @property
    def reference_index(self) -> int:
        """Índice del punto de referencia: el de ángulo real más cercano a 0°."""
        return self._reference

    def angles(self) -> list[float]:
        """Ángulos REALES de cada punto, en grados.

        Returns:
            Los ángulos informados por la mesa, en el orden del barrido.
        """
        return [p.actual_deg for p in self._points]

    def levels(self, band: float) -> list[float]:
        """Nivel absoluto de una banda en cada ángulo.

        Args:
            band: Centro nominal de la banda, en Hz.

        Returns:
            Niveles en dB, en el orden de ``angles()``.

        Raises:
            KeyError: Si la banda no forma parte del patrón.
        """
        if band not in self._bands:
            raise KeyError(f"banda {band:g} Hz no calculada; disponibles: {self._bands}")
        return [levels[band] for levels in self._levels]

    def normalized(self, band: float) -> list[float]:
        """Nivel de una banda relativo al eje (punto de ángulo real más cercano a 0°).

        Args:
            band: Centro nominal de la banda, en Hz.

        Returns:
            Niveles en dB re eje, en el orden de ``angles()``.

        Raises:
            KeyError: Si la banda no forma parte del patrón.
        """
        levels = self.levels(band)
        reference = levels[self._reference]
        return [level - reference for level in levels]
