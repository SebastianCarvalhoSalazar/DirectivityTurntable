"""Almacenamiento de una sesión de medición en CSV (metadatos y niveles) y ``.npy`` (IRs).

Archivos dentro del directorio de la sesión:

- ``measurements.csv``: una fila por punto (se escribe apenas se mide).
- ``ir_000.npy``, ``ir_001.npy``…: IR de cada punto en ``float32``.
- ``pattern.csv``: niveles normalizados al eje, una fila por banda y una columna por ángulo.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from directivity.domain.polar import (
    OCTAVE_CENTERS,
    MeasurementPoint,
    PolarPattern,
    octave_band_levels,
)

MEASUREMENTS_FILE = "measurements.csv"
PATTERN_FILE = "pattern.csv"


def level_column(band: float) -> str:
    """Nombre de la columna de nivel de una banda (p. ej. ``level_1000_hz``).

    Args:
        band: Centro nominal de la banda, en Hz.

    Returns:
        El nombre de la columna.
    """
    return f"level_{band:g}_hz"


class CsvResultStore:
    """Implementación del puerto ``ResultStore`` sobre archivos en un directorio.

    El nombre del directorio (p. ej. con fecha y hora) lo decide quien crea el store,
    así el store es determinista.

    Args:
        directory: Directorio de la sesión; se crea si no existe.
        bands: Centros nominales de las bandas de octava que se guardan por punto.

    Raises:
        FileExistsError: Si el directorio ya contiene una sesión (``measurements.csv``).
    """

    def __init__(self, directory: Path, bands: Sequence[float] = OCTAVE_CENTERS) -> None:
        self._directory = Path(directory)
        self._bands = tuple(bands)
        if (self._directory / MEASUREMENTS_FILE).exists():
            raise FileExistsError(f"ya hay una sesión en {self._directory}")
        self._directory.mkdir(parents=True, exist_ok=True)
        self._count = 0

    @property
    def directory(self) -> Path:
        """Directorio de la sesión."""
        return self._directory

    @property
    def fieldnames(self) -> list[str]:
        """Columnas de ``measurements.csv``."""
        columns = ["index", "target_deg", "actual_deg", "sample_rate", "ir_file"]
        return columns + [level_column(band) for band in self._bands]

    def append(self, point: MeasurementPoint) -> None:
        """Guarda la IR del punto y agrega su fila a ``measurements.csv``.

        El archivo se abre y se cierra en cada llamada, así cada fila queda en disco
        aunque la medición se interrumpa después.

        Args:
            point: Punto medido.
        """
        ir_file = f"ir_{self._count:03d}.npy"
        np.save(self._directory / ir_file, point.ir.samples.astype(np.float32))
        levels = octave_band_levels(point.ir, self._bands)
        row: dict[str, object] = {
            "index": self._count,
            "target_deg": repr(point.target_deg),
            "actual_deg": repr(point.actual_deg),
            "sample_rate": point.ir.sample_rate,
            "ir_file": ir_file,
        }
        row.update({level_column(band): repr(levels[band]) for band in self._bands})
        path = self._directory / MEASUREMENTS_FILE
        is_new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames)
            if is_new:
                writer.writeheader()
            writer.writerow(row)
        self._count += 1

    def save_pattern(self, pattern: PolarPattern) -> None:
        """Escribe ``pattern.csv``: fila de encabezado con los ángulos reales y una fila por banda.

        Args:
            pattern: Patrón polar de la sesión.
        """
        with (self._directory / PATTERN_FILE).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["band_hz", *(repr(angle) for angle in pattern.angles())])
            for band in pattern.bands:
                writer.writerow([f"{band:g}", *(repr(level) for level in pattern.normalized(band))])
