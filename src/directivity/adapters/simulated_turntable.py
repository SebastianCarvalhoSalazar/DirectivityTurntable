"""Mesa giratoria simulada en memoria, con la misma cuantización que el firmware."""

from __future__ import annotations

from directivity.domain.angles import (
    MAX_ABS_DEG,
    STEPS_PER_REV,
    AngleOutOfRangeError,
    degrees_to_steps,
    steps_to_degrees,
    validate_angle,
)
from directivity.ports import TurntableError


class SimulatedTurntable:
    """Implementación del puerto ``Turntable`` sin hardware.

    Lleva la posición en pasos absolutos y redondea cada destino contra el ángulo
    absoluto, igual que el firmware, así que reporta los mismos ángulos reales.

    Args:
        steps_per_rev: Pasos por vuelta del motor simulado.
        max_abs_deg: Límite de giro absoluto en grados.
    """

    def __init__(
        self, steps_per_rev: int = STEPS_PER_REV, max_abs_deg: float = MAX_ABS_DEG
    ) -> None:
        self._steps_per_rev = steps_per_rev
        self._max_abs_deg = max_abs_deg
        self._steps = 0
        self._released = True
        self._closed = False

    @property
    def is_released(self) -> bool:
        """Indica si las bobinas están apagadas."""
        return self._released

    @property
    def is_closed(self) -> bool:
        """Indica si se llamó a ``close()``."""
        return self._closed

    def home(self) -> float:
        """Toma la posición actual como 0°.

        Returns:
            0.0
        """
        self._steps = 0
        return self.position()

    def goto(self, degrees: float) -> float:
        """Gira (instantáneamente) hasta un ángulo absoluto.

        Args:
            degrees: Ángulo absoluto pedido, en grados.

        Returns:
            El ángulo real alcanzado, cuantizado al paso del motor.

        Raises:
            TurntableError: Si el ángulo no es finito o está fuera de rango.
        """
        try:
            validate_angle(degrees, self._max_abs_deg)
        except AngleOutOfRangeError as exc:
            raise TurntableError(str(exc)) from exc
        self._steps = degrees_to_steps(degrees, self._steps_per_rev)
        self._released = False
        return self.position()

    def position(self) -> float:
        """Devuelve el ángulo absoluto actual.

        Returns:
            Ángulo actual en grados.
        """
        return steps_to_degrees(self._steps, self._steps_per_rev)

    def release(self) -> None:
        """Apaga las bobinas simuladas; la posición se conserva."""
        self._released = True

    def close(self) -> None:
        """Marca la mesa como cerrada."""
        self._closed = True
