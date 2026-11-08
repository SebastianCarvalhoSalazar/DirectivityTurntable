"""Tests de ``directivity.domain.angles``."""

from __future__ import annotations

import math

import pytest

from directivity.domain.angles import (
    STEPS_PER_REV,
    AngleOutOfRangeError,
    SweepPlan,
    degrees_to_steps,
    half_step_deg,
    quantize,
    steps_to_degrees,
    validate_angle,
)


class TestValidateAngle:
    @pytest.mark.parametrize("deg", [0.0, 360.0, -360.0, 123.4])
    def test_accepts_angles_within_limits(self, deg: float) -> None:
        assert validate_angle(deg) == deg

    @pytest.mark.parametrize("deg", [360.0001, -361.0, math.nan, math.inf, -math.inf])
    def test_rejects_out_of_range_or_non_finite(self, deg: float) -> None:
        with pytest.raises(AngleOutOfRangeError):
            validate_angle(deg)

    def test_custom_limit(self) -> None:
        with pytest.raises(AngleOutOfRangeError):
            validate_angle(100.0, max_abs_deg=90.0)


class TestQuantization:
    def test_five_degrees_is_not_an_integer_number_of_steps(self) -> None:
        # 5° = 28.44 pasos → 28 pasos → 4.92°, como dice el README.
        assert degrees_to_steps(5.0) == 28
        assert round(quantize(5.0), 2) == 4.92

    def test_rounds_half_away_from_zero_like_lround(self) -> None:
        half = 360.0 / STEPS_PER_REV / 2  # medio paso exacto
        assert degrees_to_steps(half) == 1
        assert degrees_to_steps(-half) == -1

    def test_full_turn_is_exactly_steps_per_rev(self) -> None:
        assert degrees_to_steps(360.0) == STEPS_PER_REV
        assert steps_to_degrees(STEPS_PER_REV) == 360.0

    @pytest.mark.parametrize("deg", [0.1, 4.92, 33.3, -270.7, 359.99])
    def test_error_never_exceeds_half_step(self, deg: float) -> None:
        assert abs(quantize(deg) - deg) <= half_step_deg() + 1e-12

    def test_absolute_rounding_does_not_accumulate(self) -> None:
        # Redondear cada giro de 5° por separado perdería ~5.6° por vuelta.
        steps = [degrees_to_steps(5.0 * i) for i in range(73)]
        assert steps[-1] == STEPS_PER_REV
        incremental = sum(round(5.0 * STEPS_PER_REV / 360.0) for _ in range(72))
        assert steps_to_degrees(STEPS_PER_REV - incremental) == pytest.approx(5.625)


class TestSweepPlan:
    def test_default_plan_has_73_points_from_0_to_360(self) -> None:
        targets = SweepPlan().targets()
        assert len(targets) == 73
        assert targets[0] == 0.0
        assert targets[-1] == 360.0

    def test_targets_are_not_accumulated(self) -> None:
        targets = SweepPlan(0.0, 360.0, 0.1).targets()
        assert len(targets) == 3601
        assert targets[-1] == pytest.approx(360.0, abs=1e-9)
        assert targets[1234] == 1234 * 0.1

    def test_never_overshoots_stop(self) -> None:
        # Con round() del cliente heredado, 360/11 = 32.7 → 33 pasos → 363° (fuera de rango).
        targets = SweepPlan(0.0, 360.0, 11.0).targets()
        assert targets[-1] == 352.0
        assert all(abs(t) <= 360.0 for t in targets)

    def test_negative_direction(self) -> None:
        assert SweepPlan(0.0, -20.0, -10.0).targets() == [0.0, -10.0, -20.0]

    def test_single_point(self) -> None:
        assert SweepPlan(30.0, 30.0, 5.0).targets() == [30.0]

    @pytest.mark.parametrize(
        ("start", "stop", "step"),
        [(0.0, 360.0, 0.0), (0.0, 360.0, -5.0), (0.0, 360.0, math.nan)],
    )
    def test_invalid_step(self, start: float, stop: float, step: float) -> None:
        with pytest.raises(ValueError):
            SweepPlan(start, stop, step)

    def test_out_of_range_limits(self) -> None:
        with pytest.raises(AngleOutOfRangeError):
            SweepPlan(0.0, 400.0, 5.0)
