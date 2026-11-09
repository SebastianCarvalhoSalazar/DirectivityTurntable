"""Tests de la CLI (composition root)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from directivity import __version__
from directivity.adapters import cli
from directivity.adapters.cli import main, run_sweep
from directivity.adapters.simulated_audio import SimulatedAudio
from directivity.adapters.simulated_turntable import SimulatedTurntable
from directivity.domain.angles import SweepPlan
from directivity.ports import TurntableError


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "sweep" in capsys.readouterr().out


def test_runs_as_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "directivity", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in result.stdout


class TestSweep:
    def test_simulated_sweep_prints_73_points_ending_at_360(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["sweep", "--simulate", "--step", "5"]) == 0
        lines = capsys.readouterr().out.splitlines()
        points = [line for line in lines if line.startswith("objetivo")]
        assert len(points) == 73
        assert points[0] == "objetivo    0.00°  ->  real    0.00°"
        assert points[-1].endswith("real  360.00°")
        assert lines[-1] == "Barrido terminado; la mesa volvió a 0°."

    def test_requires_port_or_simulate(self) -> None:
        with pytest.raises(SystemExit):
            main(["sweep"])

    def test_invalid_plan_is_reported(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["sweep", "--simulate", "--stop", "400"]) == 1
        assert "fuera de rango" in capsys.readouterr().err

    def test_bad_port_is_reported(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["sweep", "--port", "/dev/no-existe-directivity"]) == 1
        assert "error:" in capsys.readouterr().err

    def test_returns_to_zero_and_releases_on_failure(self) -> None:
        class FailingAt90(SimulatedTurntable):
            def goto(self, degrees: float) -> float:
                if degrees == 90.0:
                    raise TurntableError("atasco")
                return super().goto(degrees)

        table = FailingAt90()
        with pytest.raises(TurntableError):
            run_sweep(table, SweepPlan(0, 180, 45), dwell_s=0.0, out=lambda _: None)
        assert table.position() == 0.0
        assert table.is_released


class TestMeasure:
    FAST = ("--sample-rate", "16000", "--sweep-duration", "0.2")

    def test_simulated_measurement_creates_session(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["measure", "--simulate", "--step", "30", "--out", str(tmp_path), *self.FAST])
        assert code == 0
        sessions = list(tmp_path.iterdir())
        assert len(sessions) == 1
        files = {path.name for path in sessions[0].iterdir()}
        assert {"measurements.csv", "pattern.csv"} <= files
        assert len([name for name in files if name.endswith(".npy")]) == 13  # 0..360 cada 30
        out = capsys.readouterr().out
        assert "90.00°    -6.02 dB" in out
        assert f"Sesión guardada en {sessions[0]}" in out

    def test_omni_source_is_flat(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = ["measure", "--simulate", "--source", "omni", "--step", "90", "--out", str(tmp_path)]
        assert main([*args, *self.FAST]) == 0
        report = capsys.readouterr().out.split("dB re eje):")[1]
        assert report.count(" 0.00 dB") == 5

    def test_hardware_measurement_needs_real_audio(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["measure", "--port", "/dev/no-existe", "--out", str(tmp_path)]) == 1
        assert "adaptador de audio real" in capsys.readouterr().err
        assert not any(tmp_path.iterdir())  # no se creó ninguna sesión

    def test_gate_longer_than_recording_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = [
            "measure",
            "--simulate",
            "--step",
            "90",
            "--gate-length",
            "5",
            "--out",
            str(tmp_path),
        ]
        assert main([*args, *self.FAST]) == 1
        assert "compuerta" in capsys.readouterr().err

    def test_existing_session_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = ["measure", "--simulate", "--step", "180", "--out", str(tmp_path), *self.FAST]
        monkeypatch.setattr(cli, "session_name", lambda: "fija")
        assert main(args) == 0
        assert main(args) == 1
        assert "ya hay una sesión" in capsys.readouterr().err

    def test_ctrl_c_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def interrupt(self: object, excitation: object) -> object:
            raise KeyboardInterrupt

        monkeypatch.setattr(SimulatedAudio, "play_and_record", interrupt)
        args = ["measure", "--simulate", "--step", "90", "--out", str(tmp_path), *self.FAST]
        assert main(args) == 130
        assert "interrumpido" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("level", "text"), [(-0.001, "   0.00"), (-6.0206, "  -6.02"), (float("-inf"), "   -inf")]
    )
    def test_format_db(self, level: float, text: str) -> None:
        assert cli.format_db(level) == text

    def test_bands_for_sample_rate(self) -> None:
        assert cli.bands_for(48_000)[-1] == 8000.0
        assert cli.bands_for(16_000)[-1] == 4000.0


class TestSelftest:
    def test_simulated_selftest_passes_and_writes_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["selftest", "--simulate", "--quick", "--out", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "RESULTADO: OK (4/5 ok, 1 no aplica)" in out
        (session,) = tmp_path.iterdir()
        document = json.loads((session / cli.SELFTEST_REPORT_FILE).read_text(encoding="utf-8"))
        assert document["passed"] is True
        assert document["turntable"] == "simulada"
        assert document["quick"] is True
        assert {"date", "plan", "steps_per_rev", "rpm", "version", "python"} <= set(document)
        assert len(list(session.glob("ir_*.npy"))) == 5  # 0..180 cada 45°

    def test_bad_port_is_reported(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["selftest", "--port", "/dev/no-existe", "--out", str(tmp_path)]) == 1
        assert "no se pudo abrir el puerto" in capsys.readouterr().err

    def test_format_report_marks_failures(self) -> None:
        from directivity.application.self_test import Check, SelfTestReport, Thresholds

        report = SelfTestReport(
            (
                Check("A", 1.0, "< 2", "ms", "ok"),
                Check("B", None, "n/a", "ms", "skip", "motivo"),
                Check("C", 9.0, "< 2", "dB", "fail"),
            ),
            {},
            Thresholds(),
        )
        lines = cli.format_report(report)
        assert "✅" in lines[1] and "⏭" in lines[2] and "(motivo)" in lines[2] and "❌" in lines[3]
        assert lines[-1] == "RESULTADO: FALLÓ (1/3 ok, 1 no aplica)"
