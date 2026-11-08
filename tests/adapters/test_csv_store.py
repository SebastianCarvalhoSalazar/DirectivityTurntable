"""Tests de ``CsvResultStore``: escritura incremental y lectura de vuelta."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from directivity.adapters.csv_store import (
    MEASUREMENTS_FILE,
    PATTERN_FILE,
    CsvResultStore,
    level_column,
)
from directivity.domain.polar import MeasurementPoint, PolarPattern, octave_band_levels
from directivity.domain.signals import Signal

FS = 48_000
BANDS = (250.0, 1000.0, 4000.0)


def point(target: float, actual: float, gain: float, seed: int = 0) -> MeasurementPoint:
    """Punto con una IR ruidosa escalada por ``gain`` (valores no triviales)."""
    samples = gain * np.random.default_rng(seed).normal(0, 0.1, 2400)
    return MeasurementPoint(target, actual, Signal(samples, FS))


def read_rows(directory: Path) -> list[dict[str, str]]:
    """Lee ``measurements.csv`` con ``csv.DictReader``."""
    with (directory / MEASUREMENTS_FILE).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_round_trip_of_measurements(tmp_path: Path) -> None:
    store = CsvResultStore(tmp_path / "sesion", BANDS)
    points = [
        point(0.0, 0.0, 1.0, 0),
        point(5.0, 4.921875, 0.8, 1),
        point(10.0, 10.01953125, 0.5, 2),
    ]
    for p in points:
        store.append(p)

    rows = read_rows(store.directory)
    assert len(rows) == 3
    for i, (row, p) in enumerate(zip(rows, points, strict=True)):
        assert int(row["index"]) == i
        assert float(row["target_deg"]) == p.target_deg
        assert float(row["actual_deg"]) == p.actual_deg
        assert int(row["sample_rate"]) == FS
        ir = np.load(store.directory / row["ir_file"])
        assert ir.dtype == np.float32
        np.testing.assert_allclose(ir, p.ir.samples, rtol=1e-6, atol=1e-9)
        expected = octave_band_levels(p.ir, BANDS)
        for band in BANDS:
            assert float(row[level_column(band)]) == expected[band]


def test_append_writes_each_row_immediately(tmp_path: Path) -> None:
    store = CsvResultStore(tmp_path, BANDS)
    for k in range(1, 4):
        store.append(point(5.0 * k, 5.0 * k, 1.0, k))
        # sin cerrar ni finalizar el store, el disco ya tiene k filas y k IRs
        assert len(read_rows(tmp_path)) == k
        assert len(list(tmp_path.glob("ir_*.npy"))) == k


def test_pattern_csv_round_trip(tmp_path: Path) -> None:
    points = [point(0.0, 0.0, 1.0), point(90.0, 90.0, 0.5), point(180.0, 180.0, 0.0)]
    pattern = PolarPattern(points, BANDS)
    store = CsvResultStore(tmp_path, BANDS)
    store.save_pattern(pattern)

    with (tmp_path / PATTERN_FILE).open(newline="", encoding="utf-8") as handle:
        header, *rows = list(csv.reader(handle))
    assert header == ["band_hz", "0.0", "90.0", "180.0"]
    assert [float(row[0]) for row in rows] == list(BANDS)
    for row, band in zip(rows, BANDS, strict=True):
        values = [float(value) for value in row[1:]]
        assert values[0] == 0.0
        assert values[1] == pytest.approx(-6.02, abs=0.01)
        assert values[2] == -math.inf  # el silencio se guarda como -inf y se lee igual
        assert values == pattern.normalized(band)


def test_refuses_to_mix_sessions(tmp_path: Path) -> None:
    CsvResultStore(tmp_path, BANDS).append(point(0.0, 0.0, 1.0))
    with pytest.raises(FileExistsError):
        CsvResultStore(tmp_path, BANDS)


def test_creates_nested_directory(tmp_path: Path) -> None:
    store = CsvResultStore(tmp_path / "data" / "raw" / "2026-11-08T21-00-00")
    assert store.directory.is_dir()
    assert store.fieldnames[:5] == ["index", "target_deg", "actual_deg", "sample_rate", "ir_file"]
