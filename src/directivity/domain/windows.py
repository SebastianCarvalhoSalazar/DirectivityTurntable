"""Ventanas de compuerta temporal (patrón Strategy).

Una ``Window`` aísla un tramo de la respuesta al impulso (típicamente el sonido
directo, antes de la primera reflexión) y pone a cero el resto. La señal resultante
conserva la longitud y la frecuencia de muestreo de la original.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from directivity.domain.signals import FloatArray, Signal


class Window(Protocol):
    """Estrategia de ventana para la compuerta temporal."""

    def apply(self, ir: Signal, start: int, length: int) -> Signal:
        """Aplica la ventana al tramo ``[start, start + length)`` y anula el resto.

        Args:
            ir: Respuesta al impulso.
            start: Primera muestra de la compuerta.
            length: Número de muestras de la compuerta.

        Returns:
            Una señal de la misma longitud y frecuencia de muestreo que ``ir``.
        """
        ...


def _gate(ir: Signal, start: int, weights: FloatArray) -> Signal:
    """Multiplica el tramo que empieza en ``start`` por ``weights`` y anula el resto.

    Args:
        ir: Respuesta al impulso.
        start: Primera muestra de la compuerta.
        weights: Pesos de la ventana; su longitud es la de la compuerta.

    Returns:
        La señal compuertada, de la misma longitud que ``ir``.

    Raises:
        ValueError: Si la compuerta está vacía o no cabe dentro de la señal.
    """
    length = len(weights)
    if start < 0 or length <= 0 or start + length > len(ir):
        raise ValueError(
            f"compuerta [{start}, {start + length}) fuera de una señal de {len(ir)} muestras"
        )
    out = np.zeros(len(ir))
    out[start : start + length] = ir.samples[start : start + length] * weights
    return ir.with_samples(out)


@dataclass(frozen=True)
class RectangularWindow:
    """Compuerta sin suavizado: conserva el tramo tal cual."""

    def weights(self, length: int) -> FloatArray:
        """Pesos de la ventana.

        Args:
            length: Número de muestras.

        Returns:
            Un array de unos.
        """
        return np.ones(length)

    def apply(self, ir: Signal, start: int, length: int) -> Signal:
        """Conserva ``[start, start + length)`` y anula el resto.

        Args:
            ir: Respuesta al impulso.
            start: Primera muestra de la compuerta.
            length: Número de muestras de la compuerta.

        Returns:
            La señal compuertada, de la misma longitud que ``ir``.
        """
        return _gate(ir, start, self.weights(length))


@dataclass(frozen=True)
class TukeyWindow:
    """Ventana de Tukey (coseno truncado): plana en el centro, con bordes de medio Hann.

    Attributes:
        alpha: Fracción de la ventana ocupada por los bordes suavizados; 0 equivale a
            la rectangular y 1 a la de Hann.
    """

    alpha: float = 0.25

    def __post_init__(self) -> None:
        """Valida ``alpha``.

        Raises:
            ValueError: Si ``alpha`` no está en [0, 1].
        """
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError("alpha debe estar en [0, 1]")

    def weights(self, length: int) -> FloatArray:
        """Pesos de la ventana (misma definición que ``scipy.signal.windows.tukey``).

        Args:
            length: Número de muestras.

        Returns:
            Los pesos, simétricos y con máximo 1.
        """
        if length == 1 or self.alpha == 0.0:
            return np.ones(length)
        n = np.arange(length)
        edge = self.alpha * (length - 1) / 2.0
        w = np.ones(length)
        rising = n < edge
        falling = n > (length - 1) - edge
        w[rising] = 0.5 * (1 + np.cos(np.pi * (n[rising] / edge - 1)))
        w[falling] = 0.5 * (1 + np.cos(np.pi * ((n[falling] - (length - 1)) / edge + 1)))
        return w

    def apply(self, ir: Signal, start: int, length: int) -> Signal:
        """Aplica la ventana al tramo ``[start, start + length)`` y anula el resto.

        Args:
            ir: Respuesta al impulso.
            start: Primera muestra de la compuerta.
            length: Número de muestras de la compuerta.

        Returns:
            La señal compuertada, de la misma longitud que ``ir``.
        """
        return _gate(ir, start, self.weights(length))
