"""Tests de la CLI (composition root)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from directivity import __version__
from directivity.adapters.cli import main, run_sweep
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
