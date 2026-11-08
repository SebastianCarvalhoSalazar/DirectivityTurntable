"""Audio simulado: una fuente con directividad conocida dentro de una sala sencilla.

La grabación es la excitación convolucionada con una IR sintética: el sonido directo,
con la ganancia que da el modelo de directividad para el ángulo actual de la mesa,
más reflexiones de sala opcionales (omnidireccionales) y ruido reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np

from directivity.domain.signals import Signal, linear_convolve

DirectivityModel = Callable[[float], float]
"""Ganancia de amplitud de la fuente en función del ángulo, en grados."""


def omni(angle_deg: float) -> float:
    """Fuente omnidireccional: misma ganancia en todos los ángulos.

    Args:
        angle_deg: Ángulo en grados (no se usa).

    Returns:
        1.0
    """
    return 1.0


def cardioid(angle_deg: float) -> float:
    """Fuente cardioide: ``0.5 + 0.5·cos θ`` (-6 dB a 90°, nula a 180°).

    Args:
        angle_deg: Ángulo en grados.

    Returns:
        La ganancia de amplitud.
    """
    return 0.5 + 0.5 * math.cos(math.radians(angle_deg))


MODELS: dict[str, DirectivityModel] = {"omni": omni, "cardioid": cardioid}
"""Modelos de directividad disponibles por nombre (para la CLI)."""


class SimulatedAudio:
    """Implementación del puerto ``AudioIO`` sin hardware.

    Args:
        angle_provider: Función que devuelve el ángulo actual de la fuente (p. ej.
            ``table.position``); así el audio no depende de la mesa concreta.
        directivity: Modelo de directividad de la fuente.
        sample_rate: Frecuencia de muestreo en Hz.
        delay_s: Retardo del sonido directo (distancia / velocidad del sonido), en s.
        reflections: Reflexiones de sala ``(retardo_s, ganancia)``, iguales en todo ángulo.
        noise_db: Nivel RMS del ruido en dB re 1 (escala completa); ``None`` sin ruido.
        tail_s: Silencio grabado después de la excitación y de la última llegada, en s.
        seed: Semilla del ruido (reproducible).

    Raises:
        ValueError: Si algún retardo es negativo o la frecuencia de muestreo no es positiva.
    """

    def __init__(
        self,
        angle_provider: Callable[[], float],
        directivity: DirectivityModel = omni,
        sample_rate: int = 48_000,
        delay_s: float = 0.003,
        reflections: Sequence[tuple[float, float]] = (),
        noise_db: float | None = -90.0,
        tail_s: float = 0.05,
        seed: int = 0,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("la frecuencia de muestreo debe ser positiva")
        if delay_s < 0 or tail_s < 0 or any(delay < 0 for delay, _ in reflections):
            raise ValueError("los retardos y la cola no pueden ser negativos")
        self._angle_provider = angle_provider
        self._directivity = directivity
        self._sample_rate = sample_rate
        self._delay_s = delay_s
        self._reflections = tuple(reflections)
        self._noise_db = noise_db
        self._tail_s = tail_s
        self._rng = np.random.default_rng(seed)

    @property
    def sample_rate(self) -> int:
        """Frecuencia de muestreo en Hz."""
        return self._sample_rate

    def impulse_response(self, angle_deg: float) -> Signal:
        """IR sintética de la sala para un ángulo de la fuente.

        Args:
            angle_deg: Ángulo de la fuente, en grados.

        Returns:
            La IR: directo con la ganancia del modelo más las reflexiones.
        """
        arrivals = [(self._delay_s, self._directivity(angle_deg)), *self._reflections]
        delays = [round(delay * self._sample_rate) for delay, _ in arrivals]
        ir = np.zeros(max(delays) + 1)
        for delay, (_, gain) in zip(delays, arrivals, strict=True):
            ir[delay] += gain
        return Signal(ir, self._sample_rate)

    def play_and_record(self, excitation: Signal) -> Signal:
        """Simula reproducir la excitación y grabar el micrófono.

        Args:
            excitation: Señal a reproducir, a ``sample_rate``.

        Returns:
            La grabación (excitación convolucionada con la IR, más ruido), con
            cola de silencio al final.

        Raises:
            ValueError: Si la excitación no está a ``sample_rate``.
        """
        if excitation.sample_rate != self._sample_rate:
            raise ValueError(
                f"la excitación está a {excitation.sample_rate} Hz y el audio a "
                f"{self._sample_rate} Hz"
            )
        ir = self.impulse_response(self._angle_provider())
        recorded = linear_convolve(excitation.samples, ir.samples)
        tail = np.zeros(round(self._tail_s * self._sample_rate))
        samples = np.concatenate([recorded, tail])
        if self._noise_db is not None:
            samples += self._rng.normal(0.0, 10 ** (self._noise_db / 20), len(samples))
        return Signal(samples, self._sample_rate)
