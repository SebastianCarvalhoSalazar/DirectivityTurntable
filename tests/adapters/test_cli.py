"""Tests de la CLI (composition root)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from directivity import __version__
from directivity.adapters.cli import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_runs_as_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "directivity", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in result.stdout
