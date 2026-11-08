"""Tests del caso de uso ``MeasureDirectivity`` con adaptadores simulados."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from directivity.adapters.csv_store import MEASUREMENTS_FILE, PATTERN_FILE, CsvResultStore
from directivity.adapters.simulated_audio import SimulatedAudio, cardioid
from directivity.adapters.simulated_turntable import SimulatedTurntable
from directivity.application.measure_directivity import MeasureDirectivity
from directivity.domain.angles import SweepPlan, quantize
from directivity.domain.pipeline import default_pipeline
from directivity.domain.polar import MeasurementPoint
from directivity.domain.signals import LogSweep, Signal
from directivity.ports import TurntableError

FS = 16_000
BANDS = (250.0, 500.0, 1000.0, 2000.0)
SWEEP = LogSweep(f_start=50.0, f_stop=7000.0, duration_s=0.2)


class FailingAudio(SimulatedAudio):
    """Audio simulado que falla en la llamada número ``fail_on`` (1-based)."""

    def __init__(self, fail_on: int, error: BaseException, **kwargs: object) -> None:
        """Crea el audio que lanzará ``error`` en la llamada ``fail_on``."""
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.fail_on = fail_on
        self.error = error
        self.calls = 0

    def play_and_record(self, excitation: Signal) -> Signal:
        """Graba normalmente hasta la llamada ``fail_on``, donde lanza el error."""
        self.calls += 1
        if self.calls == self.fail_on:
            raise self.error
        return super().play_and_record(excitation)


def make_use_case(
    tmp_path: Path, audio: SimulatedAudio | None = None, table: SimulatedTurntable | None = None
) -> tuple[MeasureDirectivity, SimulatedTurntable, list[float]]:
    """Arma el caso de uso con adaptadores simulados; devuelve también las pausas pedidas."""
    table = table or SimulatedTurntable()
    audio = audio or SimulatedAudio(
        angle_provider=table.position,
        directivity=cardioid,
        sample_rate=FS,
        delay_s=0.003,
        reflections=[(0.015, 0.4)],
        noise_db=-80.0,
    )
    sleeps: list[float] = []
    use_case = MeasureDirectivity(
        turntable=table,
        audio=audio,
        store=CsvResultStore(tmp_path, BANDS),
        excitation=SWEEP,
        pipeline=default_pipeline(SWEEP, gate_start_s=0.0, gate_length_s=0.010),
        bands=BANDS,
        settle_s=0.25,
        sleep=sleeps.append,
    )
    return use_case, table, sleeps


def rows(directory: Path) -> list[dict[str, str]]:
    """Filas de ``measurements.csv``."""
    with (directory / MEASUREMENTS_FILE).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class TestFullySimulatedMeasurement:
    def test_recovers_cardioid_at_1khz_within_half_db(self, tmp_path: Path) -> None:
        use_case, _, _ = make_use_case(tmp_path)
        pattern = use_case.execute(SweepPlan(0.0, 180.0, 10.0))

        for angle, level in zip(pattern.angles(), pattern.normalized(1000.0), strict=True):
            if angle <= 150.0:
                theory = 20 * math.log10(cardioid(angle))  # evaluada en el ángulo REAL
                assert level == pytest.approx(theory, abs=0.5), f"{angle}°"

    def test_reflection_is_removed_by_the_gate(self, tmp_path: Path) -> None:
        use_case, _, _ = make_use_case(tmp_path)
        pattern = use_case.execute(SweepPlan(0.0, 0.0, 10.0))
        ir = pattern.points[0].ir.samples
        assert abs(ir[48]) == pytest.approx(1.0, abs=0.02)  # directo a 3 ms
        assert not ir[160:].any()  # la reflexión a 15 ms quedó fuera de la compuerta

    def test_stores_every_point_and_the_pattern(self, tmp_path: Path) -> None:
        use_case, _, _ = make_use_case(tmp_path)
        use_case.execute(SweepPlan(0.0, 90.0, 30.0))
        stored = rows(tmp_path)
        assert [float(r["target_deg"]) for r in stored] == [0.0, 30.0, 60.0, 90.0]
        assert [float(r["actual_deg"]) for r in stored] == [quantize(a) for a in (0, 30, 60, 90)]
        assert (tmp_path / PATTERN_FILE).exists()

    def test_reports_progress_and_settles_after_each_move(self, tmp_path: Path) -> None:
        use_case, _, sleeps = make_use_case(tmp_path)
        seen: list[MeasurementPoint] = []
        use_case.execute(SweepPlan(0.0, 20.0, 10.0), on_point=seen.append)
        assert [p.target_deg for p in seen] == [0.0, 10.0, 20.0]
        assert seen[1].actual_deg == quantize(10.0)
        assert sleeps == [0.25, 0.25, 0.25]

    def test_ends_at_zero_with_coils_released(self, tmp_path: Path) -> None:
        use_case, table, _ = make_use_case(tmp_path)
        use_case.execute(SweepPlan(0.0, 90.0, 45.0))
        assert table.position() == 0.0
        assert table.is_released


class TestFailures:
    @pytest.mark.parametrize(
        "error", [RuntimeError("tarjeta de audio desconectada"), KeyboardInterrupt()]
    )
    def test_audio_failure_on_third_point_parks_the_table(
        self, tmp_path: Path, error: BaseException
    ) -> None:
        table = SimulatedTurntable()
        audio = FailingAudio(
            fail_on=3, error=error, angle_provider=table.position, sample_rate=FS, noise_db=None
        )
        use_case, _, _ = make_use_case(tmp_path, audio=audio, table=table)

        with pytest.raises(type(error)):
            use_case.execute(SweepPlan(0.0, 90.0, 10.0))

        assert table.position() == 0.0
        assert table.is_released
        assert len(rows(tmp_path)) == 2  # los dos puntos medidos quedaron guardados
        assert not (tmp_path / PATTERN_FILE).exists()

    def test_coils_are_released_even_if_returning_home_fails(self, tmp_path: Path) -> None:
        class StuckOnReturn(SimulatedTurntable):
            def goto(self, degrees: float) -> float:
                if degrees == 0.0 and self.position() != 0.0:
                    raise TurntableError("atascada")
                return super().goto(degrees)

        table = StuckOnReturn()
        use_case, _, _ = make_use_case(tmp_path, table=table)
        with pytest.raises(TurntableError, match="atascada"):
            use_case.execute(SweepPlan(0.0, 20.0, 10.0))
        assert table.is_released
