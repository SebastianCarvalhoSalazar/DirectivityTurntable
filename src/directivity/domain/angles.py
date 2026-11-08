"""Ángulos de la mesa: cuantización a pasos, límites de giro y planes de barrido.

La mesa lleva la posición en pasos absolutos y redondea cada destino contra el
ángulo absoluto, así que el error de cuantización nunca supera medio paso ni se
acumula entre giros. Este módulo reproduce esa aritmética en Python puro.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

STEPS_PER_REV: int = 2048
"""Pasos por vuelta nominales del 28BYJ-48 en este montaje (igual que el firmware)."""

MAX_ABS_DEG: float = 360.0
"""Límite de giro absoluto para no enrollar el cable del parlante."""


class AngleOutOfRangeError(ValueError):
    """El ángulo pedido no es finito o queda fuera de ±``MAX_ABS_DEG``."""


def validate_angle(degrees: float, max_abs_deg: float = MAX_ABS_DEG) -> float:
    """Comprueba que un ángulo absoluto esté dentro de los límites de la mesa.

    Args:
        degrees: Ángulo absoluto en grados.
        max_abs_deg: Límite simétrico permitido, en grados.

    Returns:
        El mismo ángulo, para poder encadenar la llamada.

    Raises:
        AngleOutOfRangeError: Si el ángulo no es finito o supera el límite.
    """
    if not math.isfinite(degrees):
        raise AngleOutOfRangeError(f"ángulo no finito: {degrees!r}")
    if abs(degrees) > max_abs_deg:
        raise AngleOutOfRangeError(
            f"ángulo {degrees:g}° fuera de rango (-{max_abs_deg:g} a {max_abs_deg:g})"
        )
    return degrees


def _round_half_away(value: float) -> int:
    """Redondea al entero más cercano alejándose de cero en los empates, como ``lround``."""
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def degrees_to_steps(degrees: float, steps_per_rev: int = STEPS_PER_REV) -> int:
    """Convierte un ángulo absoluto en el número de pasos absoluto más cercano.

    Args:
        degrees: Ángulo absoluto en grados.
        steps_per_rev: Pasos por vuelta completa del motor.

    Returns:
        Posición absoluta en pasos (redondeo con empates alejándose de cero).
    """
    return _round_half_away(degrees * steps_per_rev / 360.0)


def steps_to_degrees(steps: int, steps_per_rev: int = STEPS_PER_REV) -> float:
    """Convierte una posición absoluta en pasos a grados.

    Args:
        steps: Posición absoluta en pasos.
        steps_per_rev: Pasos por vuelta completa del motor.

    Returns:
        Ángulo absoluto en grados.
    """
    return steps * 360.0 / steps_per_rev


def quantize(degrees: float, steps_per_rev: int = STEPS_PER_REV) -> float:
    """Devuelve el ángulo que la mesa alcanza realmente al pedir ``degrees``.

    Args:
        degrees: Ángulo absoluto pedido, en grados.
        steps_per_rev: Pasos por vuelta completa del motor.

    Returns:
        El ángulo cuantizado al paso más cercano, en grados.
    """
    return steps_to_degrees(degrees_to_steps(degrees, steps_per_rev), steps_per_rev)


def half_step_deg(steps_per_rev: int = STEPS_PER_REV) -> float:
    """Error máximo de cuantización, en grados (medio paso).

    Args:
        steps_per_rev: Pasos por vuelta completa del motor.

    Returns:
        La mitad del ángulo de un paso.
    """
    return 180.0 / steps_per_rev


@dataclass(frozen=True)
class SweepPlan:
    """Plan de barrido angular: de ``start`` a ``stop`` (incluido) cada ``step`` grados.

    Attributes:
        start: Ángulo inicial en grados.
        stop: Ángulo final en grados; se incluye si cae en la grilla.
        step: Paso angular en grados; su signo debe llevar de ``start`` a ``stop``.
    """

    start: float = 0.0
    stop: float = 360.0
    step: float = 5.0

    def __post_init__(self) -> None:
        """Valida el plan al construirlo.

        Raises:
            ValueError: Si el paso es cero o no avanza de ``start`` hacia ``stop``.
            AngleOutOfRangeError: Si ``start`` o ``stop`` quedan fuera de los límites.
        """
        validate_angle(self.start)
        validate_angle(self.stop)
        if self.step == 0 or not math.isfinite(self.step):
            raise ValueError("el paso angular debe ser finito y distinto de cero")
        if (self.stop - self.start) * self.step < 0:
            raise ValueError("el signo del paso no lleva de start a stop")

    def targets(self) -> list[float]:
        """Calcula los ángulos objetivo del barrido.

        Cada objetivo se calcula como ``start + i * step`` (sin acumular errores de
        coma flotante) y nunca se pasa de ``stop``.

        Returns:
            Lista de ángulos objetivo en grados, en orden de visita.
        """
        n_steps = math.floor((self.stop - self.start) / self.step + 1e-9)
        return [self.start + i * self.step for i in range(n_steps + 1)]
