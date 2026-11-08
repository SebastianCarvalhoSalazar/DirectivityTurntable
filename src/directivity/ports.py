"""Puertos del sistema, definidos con ``typing.Protocol``.

Un puerto describe lo que el caso de uso necesita de algo externo (motor, audio,
disco) sin decir cómo se implementa. Los adaptadores lo cumplen de forma estructural:
no heredan de estas clases.
"""

from __future__ import annotations

from typing import Protocol

from directivity.domain.polar import MeasurementPoint, PolarPattern
from directivity.domain.signals import Signal


class TurntableError(RuntimeError):
    """Falla de la mesa giratoria: error del firmware, timeout o ángulo inválido.

    Forma parte del contrato del puerto: todos los adaptadores lanzan esta excepción.
    """


class Turntable(Protocol):
    """Mesa giratoria capaz de ir a ángulos absolutos e informar el ángulo real."""

    def home(self) -> float:
        """Toma la posición actual como 0°.

        Returns:
            El nuevo ángulo actual (0.0).
        """
        ...

    def goto(self, degrees: float) -> float:
        """Gira hasta un ángulo absoluto.

        Args:
            degrees: Ángulo absoluto pedido, en grados.

        Returns:
            El ángulo REAL alcanzado (cuantizado al paso del motor).

        Raises:
            TurntableError: Si el ángulo está fuera de rango o la mesa falla.
        """
        ...

    def position(self) -> float:
        """Devuelve el ángulo absoluto actual en grados.

        Returns:
            Ángulo actual en grados.
        """
        ...

    def release(self) -> None:
        """Apaga las bobinas del motor; la posición lógica se conserva."""
        ...

    def close(self) -> None:
        """Libera los recursos de la conexión."""
        ...


class AudioIO(Protocol):
    """Interfaz de audio que reproduce una excitación y graba la respuesta a la vez."""

    @property
    def sample_rate(self) -> int:
        """Frecuencia de muestreo de reproducción y grabación, en Hz."""
        ...

    def play_and_record(self, excitation: Signal) -> Signal:
        """Reproduce la excitación por el parlante y graba el micrófono.

        Args:
            excitation: Señal a reproducir, a ``sample_rate``.

        Returns:
            La grabación, a ``sample_rate`` y al menos tan larga como la excitación
            (incluye la cola para capturar el retardo y la reverberación).

        Raises:
            ValueError: Si la excitación no está a ``sample_rate``.
        """
        ...


class ResultStore(Protocol):
    """Almacenamiento de los resultados de una sesión de medición."""

    def append(self, point: MeasurementPoint) -> None:
        """Guarda un punto apenas se mide (si la medición se corta, no se pierde).

        Args:
            point: Punto medido.
        """
        ...

    def save_pattern(self, pattern: PolarPattern) -> None:
        """Guarda el patrón polar completo al final de la sesión.

        Args:
            pattern: Patrón polar calculado con todos los puntos.
        """
        ...
