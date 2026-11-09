"""Corre el contrato de ``Turntable`` contra todos los adaptadores.

- ``simulated``: ``SimulatedTurntable`` (siempre).
- ``serial-emulated``: ``SerialTurntable`` sobre el emulador del firmware (siempre).
- ``serial-hardware``: ``SerialTurntable`` sobre la placa real
  (``pytest -m hardware --port=/dev/tty.usbmodemXXXX``).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from directivity.adapters.serial_turntable import SerialTurntable
from directivity.adapters.simulated_turntable import SimulatedTurntable
from directivity.ports import Turntable
from tests.contract.turntable_contract import TurntableContract
from tests.support.firmware_emulator import FirmwareEmulator


def _make_table(request: pytest.FixtureRequest) -> Turntable:
    """Construye el adaptador indicado por el parámetro del fixture."""
    kind: str = request.param
    if kind == "simulated":
        return SimulatedTurntable()
    if kind == "serial-emulated":
        return SerialTurntable(FirmwareEmulator())
    port: str = request.getfixturevalue("hardware_port")
    return SerialTurntable.open(port)


@pytest.fixture(
    params=[
        "simulated",
        "serial-emulated",
        pytest.param("serial-hardware", marks=pytest.mark.hardware),
    ]
)
def table(request: pytest.FixtureRequest) -> Iterator[Turntable]:
    """Mesa inicializada en 0°; al terminar vuelve a 0°, apaga bobinas y cierra."""
    turntable = _make_table(request)
    try:
        turntable.home()
        yield turntable
    finally:
        try:
            turntable.goto(0.0)
            turntable.release()
        finally:
            turntable.close()


class TestTurntableContract(TurntableContract):
    """Aplica la batería de contrato a cada adaptador del fixture ``table``."""
