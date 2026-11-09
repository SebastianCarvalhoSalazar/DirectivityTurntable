"""Tests del caso de uso ``SelfTest`` (autoprueba de punta a punta)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from directivity.adapters.csv_store import CsvResultStore
from directivity.adapters.serial_turntable import SerialTurntable
from directivity.adapters.simulated_audio import SimulatedAudio, cardioid, omni
from directivity.adapters.simulated_turntable import SimulatedTurntable
from directivity.application.self_test import QUICK_TIMING_TARGETS, SelfTest, Thresholds
from directivity.domain.angles import STEPS_PER_REV, SweepPlan, degrees_to_steps
from directivity.domain.pipeline import default_pipeline
from directivity.domain.signals import LogSweep
from directivity.ports import Turntable
from tests.application.test_measure_directivity import FailingAudio
from tests.support.firmware_emulator import FirmwareEmulator

FS = 16_000
BANDS = (250.0, 500.0, 1000.0, 2000.0)
SWEEP = LogSweep(f_start=50.0, f_stop=7000.0, duration_s=0.2)
STEP_S = 60.0 / (STEPS_PER_REV * 5.0)  # 5 RPM, como el firmware
PLAN = SweepPlan(0.0, 180.0, 45.0)


class FakeClock:
    """Reloj controlado por el test."""

    def __init__(self) -> None:
        """Arranca en 0 s."""
        self.now = 0.0

    def __call__(self) -> float:
        """Devuelve la hora actual en segundos."""
        return self.now


class TimedTurntable(SimulatedTurntable):
    """Mesa simulada que hace avanzar un reloj falso como lo haría la placa real.

    Cada orden suma ``latency_s`` y cada giro ``pasos * step_s``.
    """

    def __init__(self, clock: FakeClock, step_s: float = STEP_S, latency_s: float = 0.001) -> None:
        """Crea la mesa con el reloj que debe hacer avanzar."""
        super().__init__()
        self.clock, self.step_s, self.latency_s = clock, step_s, latency_s

    def goto(self, degrees: float) -> float:
        """Gira y avanza el reloj según los pasos recorridos."""
        steps = abs(degrees_to_steps(degrees) - degrees_to_steps(self.position()))
        self.clock.now += self.latency_s + steps * self.step_s
        return super().goto(degrees)

    def position(self) -> float:
        """Devuelve la posición y avanza el reloj la latencia de una consulta."""
        self.clock.now += self.latency_s
        return super().position()


def make_selftest(
    tmp_path: Path,
    table: Turntable,
    clock: FakeClock | None = None,
    reference: object = cardioid,
    audio: SimulatedAudio | None = None,
) -> SelfTest:
    """Arma la autoprueba con audio simulado cardioide y almacenamiento en ``tmp_path``."""
    audio = audio or SimulatedAudio(
        angle_provider=table.position, directivity=cardioid, sample_rate=FS, noise_db=-80.0
    )
    return SelfTest(
        turntable=table,
        audio=audio,
        store=CsvResultStore(tmp_path, BANDS),
        excitation=SWEEP,
        pipeline=default_pipeline(SWEEP, gate_length_s=0.010),
        reference_gain=reference,  # type: ignore[arg-type]
        bands=BANDS,
        settle_s=0.0,
        clock=clock or FakeClock(),
        sleep=lambda _s: None,
    )


def statuses(report: object) -> dict[str, str]:
    """Mapea nombre de check → estado."""
    return {check.name: check.status for check in report.checks}  # type: ignore[attr-defined]


class TestAllChecksPass:
    def test_with_realistic_timing(self, tmp_path: Path) -> None:
        clock = FakeClock()
        report = make_selftest(tmp_path, TimedTurntable(clock), clock).run(PLAN, STEP_S)
        assert report.passed
        assert set(statuses(report).values()) == {"ok"}
        stepping = report.metrics["stepping"]
        assert stepping["s_per_step"] == pytest.approx(STEP_S, rel=1e-6)
        assert stepping["rpm_effective"] == pytest.approx(5.0, rel=1e-6)
        assert report.metrics["latency_ms"]["p95"] == pytest.approx(1.0)

    def test_step_check_is_skipped_without_expected_time(self, tmp_path: Path) -> None:
        report = make_selftest(tmp_path, SimulatedTurntable()).run(PLAN, None)
        check = next(c for c in report.checks if c.name == "Tiempo por paso")
        assert check.status == "skip"
        assert check.measured is None
        assert report.passed  # un skip no es una falla

    def test_angle_check_against_emulated_firmware(self, tmp_path: Path) -> None:
        # El firmware informa con 2 decimales y redondea los empates a su manera:
        # la tolerancia de 0.011° lo absorbe.
        table = SerialTurntable(FirmwareEmulator())
        report = make_selftest(tmp_path, table).run(PLAN, None, QUICK_TIMING_TARGETS)
        assert statuses(report)["Ángulo reportado vs modelo"] == "ok"
        assert report.metrics["stepping"]["max_angle_error_deg"] <= 0.011

    def test_pattern_error_is_small_and_points_are_stored(self, tmp_path: Path) -> None:
        report = make_selftest(tmp_path, SimulatedTurntable()).run(PLAN, None)
        assert report.metrics["measurement"]["points"] == 5
        assert report.metrics["measurement"]["max_error_db"] < 0.1
        assert len(list(tmp_path.glob("ir_*.npy"))) == 5

    def test_report_is_json_serializable(self, tmp_path: Path) -> None:
        report = make_selftest(tmp_path, SimulatedTurntable()).run(PLAN, None)
        document = json.loads(json.dumps(report.to_dict()))
        assert document["passed"] is True
        assert [c["status"] for c in document["checks"]].count("skip") == 1
        assert document["thresholds"]["latency_p95_ms"] == 20.0


class TestFailures:
    def test_wrong_reference_fails_the_pattern_check(self, tmp_path: Path) -> None:
        report = make_selftest(tmp_path, SimulatedTurntable(), reference=omni).run(PLAN, None)
        assert statuses(report)["Medición completa (5 puntos)"] == "fail"
        assert not report.passed

    def test_slow_link_fails_the_latency_check(self, tmp_path: Path) -> None:
        clock = FakeClock()
        table = TimedTurntable(clock, latency_s=0.050)
        report = make_selftest(tmp_path, table, clock).run(PLAN, STEP_S)
        assert statuses(report)["Latencia serial (p95)"] == "fail"

    def test_wrong_motor_speed_fails_the_step_check(self, tmp_path: Path) -> None:
        clock = FakeClock()
        table = TimedTurntable(clock, step_s=STEP_S * 1.10)  # 10 % más lento
        report = make_selftest(tmp_path, table, clock).run(PLAN, STEP_S)
        check = next(c for c in report.checks if c.name == "Tiempo por paso")
        assert check.status == "fail"
        assert "+10" in check.detail

    def test_audio_failure_parks_the_table(self, tmp_path: Path) -> None:
        table = SimulatedTurntable()
        audio = FailingAudio(
            fail_on=2,
            error=RuntimeError("sin audio"),
            angle_provider=table.position,
            sample_rate=FS,
        )
        with pytest.raises(RuntimeError):
            make_selftest(tmp_path, table, audio=audio).run(PLAN, None)
        assert table.position() == 0.0
        assert table.is_released

    def test_band_must_be_measured(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="banda"):
            SelfTest(
                SimulatedTurntable(),
                SimulatedAudio(lambda: 0.0, sample_rate=FS),
                CsvResultStore(tmp_path, BANDS),
                SWEEP,
                default_pipeline(SWEEP),
                cardioid,
                bands=(250.0,),
                thresholds=Thresholds(band_hz=1000.0),
            )


def test_pattern_error_ignores_nulls(tmp_path: Path) -> None:
    # A 180° la cardioide es nula (ruido puro): no debe contar en el error.
    plan = SweepPlan(0.0, 180.0, 90.0)
    report = make_selftest(tmp_path, SimulatedTurntable()).run(plan, None)
    assert math.isfinite(report.metrics["measurement"]["max_error_db"])
    assert report.metrics["measurement"]["max_error_db"] < 0.1
