"""Configuración común de pytest: opción ``--port`` para los tests de hardware."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Agrega ``--port`` para correr los tests marcados ``hardware`` contra la placa."""
    parser.addoption(
        "--port",
        action="store",
        default=None,
        help=(
            "Puerto serial de la mesa para tests hardware: --port=/dev/tty.usbmodem1101"
            " (separado por espacio, pytest toma el puerto como ruta de tests)."
        ),
    )


@pytest.fixture
def hardware_port(request: pytest.FixtureRequest) -> str:
    """Devuelve el puerto de la placa o salta el test si no se indicó ``--port``."""
    port: str | None = request.config.getoption("--port")
    if port is None:
        pytest.skip("test de hardware: indica la placa con --port=<puerto>")
    return port
