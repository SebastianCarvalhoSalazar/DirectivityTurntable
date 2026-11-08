"""Pipeline de procesamiento: grabación → IR → IR compuertada (patrón Pipeline).

Cada etapa (``Stage``) es una función ``Signal → Signal``; el ``Pipeline`` las aplica en
orden. Agregar una etapa nueva (filtro, recorte, alineación) no requiere tocar el
caso de uso ni las etapas existentes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from directivity.domain.signals import ExcitationSignal, Signal
from directivity.domain.windows import TukeyWindow, Window

Stage = Callable[[Signal], Signal]
"""Etapa del pipeline: recibe una señal y devuelve otra."""


class Pipeline:
    """Composición ordenada de etapas ``Signal → Signal``.

    Args:
        stages: Etapas a aplicar, en orden. Una lista vacía da la identidad.
    """

    def __init__(self, stages: Sequence[Stage]) -> None:
        self._stages: tuple[Stage, ...] = tuple(stages)

    @property
    def stages(self) -> tuple[Stage, ...]:
        """Etapas del pipeline, en orden de ejecución."""
        return self._stages

    def __call__(self, signal: Signal) -> Signal:
        """Aplica todas las etapas en orden.

        Args:
            signal: Señal de entrada (normalmente la grabación).

        Returns:
            La salida de la última etapa (o la entrada si no hay etapas).
        """
        for stage in self._stages:
            signal = stage(signal)
        return signal


@dataclass(frozen=True)
class Deconvolve:
    """Etapa que obtiene la IR a partir de la grabación con una estrategia de excitación.

    Attributes:
        excitation: Estrategia de excitación que se usó para medir.
    """

    excitation: ExcitationSignal

    def __call__(self, recording: Signal) -> Signal:
        """Deconvoluciona la grabación.

        Args:
            recording: Grabación del micrófono.

        Returns:
            La respuesta al impulso.
        """
        return self.excitation.deconvolve(recording)


@dataclass(frozen=True)
class Gate:
    """Etapa de compuerta temporal: conserva el sonido directo y descarta reflexiones.

    El inicio debe fijarse con un retardo común a todos los ángulos (la distancia
    parlante-micrófono es constante), no con el pico de cada IR, y dejar un margen
    previo al sonido directo mayor que el borde suavizado de la ventana.

    Attributes:
        window: Estrategia de ventana.
        start_s: Inicio de la compuerta en segundos desde el instante 0 de la IR.
        length_s: Duración de la compuerta en segundos.
    """

    window: Window = field(default_factory=TukeyWindow)
    start_s: float = 0.0
    length_s: float = 0.010

    def __post_init__(self) -> None:
        """Valida los tiempos.

        Raises:
            ValueError: Si el inicio es negativo o la duración no es positiva.
        """
        if self.start_s < 0 or self.length_s <= 0:
            raise ValueError("la compuerta requiere start_s >= 0 y length_s > 0")

    @property
    def low_frequency_limit_hz(self) -> float:
        """Frecuencia mínima confiable (≈ 1 / duración de la compuerta), en Hz."""
        return 1.0 / self.length_s

    def __call__(self, ir: Signal) -> Signal:
        """Aplica la ventana sobre el tramo de la compuerta.

        Args:
            ir: Respuesta al impulso.

        Returns:
            La IR compuertada, de la misma longitud.

        Raises:
            ValueError: Si la compuerta no cabe dentro de la IR.
        """
        start = round(self.start_s * ir.sample_rate)
        length = round(self.length_s * ir.sample_rate)
        return self.window.apply(ir, start, length)


def default_pipeline(
    excitation: ExcitationSignal,
    window: Window | None = None,
    gate_start_s: float = 0.0,
    gate_length_s: float = 0.010,
) -> Pipeline:
    """Pipeline estándar: deconvolución seguida de compuerta temporal.

    Args:
        excitation: Estrategia de excitación usada en la medición.
        window: Ventana de la compuerta; por defecto ``TukeyWindow(0.25)``.
        gate_start_s: Inicio de la compuerta en segundos.
        gate_length_s: Duración de la compuerta en segundos.

    Returns:
        El pipeline ``[Deconvolve, Gate]``.
    """
    gate = Gate(window or TukeyWindow(), gate_start_s, gate_length_s)
    return Pipeline([Deconvolve(excitation), gate])
