"""Batería de contrato del puerto ``Turntable``.

Cualquier adaptador (simulado, serial con emulador, serial con la placa) debe pasarla.
Las subclases proveen un fixture ``table`` ya inicializado con ``home()``, y su
teardown debe dejar la mesa en 0° y con las bobinas apagadas (por el cable).
"""

from __future__ import annotations

import math

import pytest

from directivity.domain.angles import half_step_deg
from directivity.ports import Turntable, TurntableError

PRINT_ROUNDING_DEG = 0.005
"""El firmware informa los ángulos con 2 decimales."""

TOLERANCE_DEG = half_step_deg() + PRINT_ROUNDING_DEG


class TurntableContract:
    """Tests que definen el comportamiento observable de una mesa giratoria."""

    def test_home_sets_position_to_zero(self, table: Turntable) -> None:
        table.goto(10.0)
        assert table.home() == pytest.approx(0.0, abs=PRINT_ROUNDING_DEG)
        assert table.position() == pytest.approx(0.0, abs=PRINT_ROUNDING_DEG)

    @pytest.mark.parametrize("target", [0.0, 5.0, 4.92, -90.0, 123.456, 359.9, -359.9, 360.0])
    def test_goto_reaches_target_within_half_step(self, table: Turntable, target: float) -> None:
        reached = table.goto(target)
        assert abs(reached - target) <= TOLERANCE_DEG
        assert table.position() == reached

    def test_full_turn_in_5_degree_steps_does_not_accumulate_error(self, table: Turntable) -> None:
        reached = 0.0
        for i in range(1, 73):
            reached = table.goto(5.0 * i)
            assert abs(reached - 5.0 * i) <= TOLERANCE_DEG
        assert reached == 360.0

    @pytest.mark.parametrize("target", [361.0, -361.0, 720.0, math.nan, math.inf])
    def test_goto_out_of_range_raises_and_keeps_position(
        self, table: Turntable, target: float
    ) -> None:
        before = table.goto(45.0)
        with pytest.raises(TurntableError):
            table.goto(target)
        assert table.position() == before

    def test_goto_works_after_release_and_keeps_position(self, table: Turntable) -> None:
        before = table.goto(45.0)
        table.release()
        assert table.position() == before
        reached = table.goto(90.0)
        assert abs(reached - 90.0) <= TOLERANCE_DEG
