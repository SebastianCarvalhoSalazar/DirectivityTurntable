"""Compila el firmware en el computador y lo compara con el emulador de Python.

El sketch real (``firmware/DirectivityTurntable/DirectivityTurntable.ino``) se compila
con un sustituto de ``Arduino.h`` (``tests/firmware/host``) que imita la semántica del
AVR. Cada test envía órdenes por "serial" y compara la traza completa (respuestas,
pasos del motor y pines) con la de ``FirmwareEmulator``. Así el emulador que usan los
tests de contrato de ``SerialTurntable`` no puede separarse del firmware sin que se note.

Requiere un compilador de C++ (``c++``); si no hay, los tests se saltan.
"""

from __future__ import annotations

import random
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.firmware_emulator import STEPS_PER_REV, FirmwareEmulator

ROOT = Path(__file__).resolve().parents[2]
SKETCH = ROOT / "firmware" / "DirectivityTurntable" / "DirectivityTurntable.ino"
HOST = Path(__file__).resolve().parent / "host"
COMPILER = shutil.which("c++")

pytestmark = pytest.mark.skipif(COMPILER is None, reason="no hay compilador de C++ (c++)")


@pytest.fixture(scope="module")
def firmware(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compila el sketch con advertencias estrictas tratadas como errores."""
    assert COMPILER is not None
    binary = tmp_path_factory.mktemp("firmware") / "turntable"
    subprocess.run(
        [
            COMPILER,
            "-std=gnu++11",  # el estándar por defecto de avr-gcc en el core de Arduino
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Werror",
            f"-I{HOST}",
            f'-DSKETCH="{SKETCH}"',
            str(HOST / "host_main.cpp"),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def run_firmware(binary: Path, raw: str) -> list[str]:
    """Ejecuta el firmware compilado con ``raw`` como entrada serial y devuelve la traza."""
    result = subprocess.run(
        [str(binary)], input=raw.encode("latin-1"), capture_output=True, check=True
    )
    return result.stdout.decode("latin-1").splitlines()


def run_emulator(raw: str) -> list[str]:
    """Pasa ``raw`` por el emulador y devuelve su traza."""
    emulator = FirmwareEmulator()
    emulator.feed(raw)
    return emulator.trace


def assert_same(binary: Path, lines: list[str], line_end: str = "\n") -> list[str]:
    """Verifica que firmware y emulador produzcan la misma traza; la devuelve."""
    raw = "".join(line + line_end for line in lines)
    trace = run_firmware(binary, raw)
    assert trace == run_emulator(raw)
    return trace


def test_compiles_without_warnings(firmware: Path) -> None:
    assert firmware.exists()


def test_startup_releases_coils(firmware: Path) -> None:
    assert run_firmware(firmware, "") == ["@dw 8 0", "@dw 9 0", "@dw 10 0", "@dw 11 0"]


def test_protocol_session(firmware: Path) -> None:
    trace = assert_same(
        firmware,
        ["PING", "HOME", "GOTO 5", "MOVE -100", "POS", "GOTO 361", "RELEASE", "POS", "GOTO 0"],
    )
    assert trace[4:] == [
        "OK PONG",
        "OK 0.00",
        "@step 28",
        "OK 4.92",
        "@step -569",
        "OK -95.10",
        "OK -95.10",
        "ERR fuera de rango (-360 a 360)",
        "@dw 8 0",
        "@dw 9 0",
        "@dw 10 0",
        "@dw 11 0",
        "OK RELEASED",
        "OK -95.10",
        "@step 541",
        "OK 0.00",
    ]


@pytest.mark.parametrize(
    "line",
    [
        "GOTO NAN",
        "goto nan",
        "MOVE NAN",
        "GOTO -nan",
        "GOTO 5abc",
        "GOTO 9O",
        "GOTO 5 10",
        "MOVE 1x",
    ],
)
def test_nan_and_trailing_text_are_rejected_without_moving(firmware: Path, line: str) -> None:
    trace = assert_same(firmware, [line])
    assert trace[4:] == ["ERR angulo invalido"]


@pytest.mark.parametrize(
    ("line", "reply"),
    [
        ("GOTO INF", "ERR fuera de rango (-360 a 360)"),
        ("GOTO -INFINITY", "ERR fuera de rango (-360 a 360)"),
        ("GOTO 1e400", "ERR fuera de rango (-360 a 360)"),
        ("GOTO 360.0001", "ERR fuera de rango (-360 a 360)"),
        ("GOTO 7  ", "OK 7.03"),  # espacios finales permitidos
        ("GOTO  +.5e1", "OK 4.92"),
        ("GOTO 0x10", "ERR angulo invalido"),  # avr-libc no acepta hexadecimales
        ("GOTO ", "ERR angulo invalido"),
        ("GOTO", "ERR comando desconocido"),
        ("FOO", "ERR comando desconocido"),
        ("X" * 40, "ERR linea demasiado larga"),
        ("GOTO " + "0" * 24 + "10", "OK 10.02"),  # 31 caracteres: el máximo
    ],
)
def test_edge_cases(firmware: Path, line: str, reply: str) -> None:
    assert assert_same(firmware, [line])[-1] == reply


def test_empty_lines_and_carriage_returns(firmware: Path) -> None:
    trace = assert_same(firmware, ["", "PING", "", "POS"], line_end="\r\n")
    assert trace[4:] == ["OK PONG", "OK 0.00"]


def test_non_ascii_bytes_are_handled(firmware: Path) -> None:
    trace = assert_same(firmware, ["gotó 5", "\xffPING", "ping"])
    assert trace[4:] == ["ERR comando desconocido", "ERR comando desconocido", "OK PONG"]


def test_every_position_and_half_step_up_to_twice_the_limit(firmware: Path) -> None:
    # ±2 vueltas: cubre todas las posiciones alcanzables y también el rechazo fuera de rango.
    lines = []
    for steps in range(-2 * STEPS_PER_REV, 2 * STEPS_PER_REV + 1):
        lines.append(f"GOTO {steps * 360 / STEPS_PER_REV:.10f}")
        half = (steps + 0.5) * 360 / STEPS_PER_REV
        if abs(half) <= 360:
            lines += [f"GOTO {half:.10f}", f"GOTO {half + 1e-6:.10f}", f"GOTO {half - 1e-6:.10f}"]
    trace = assert_same(firmware, lines)
    assert "OK 360.00" in trace
    assert "OK -360.00" in trace
    assert trace[-1] == "ERR fuera de rango (-360 a 360)"


def test_random_sessions(firmware: Path) -> None:
    rng = random.Random(2026)
    commands = []
    for _ in range(2000):
        verb = rng.choice(["GOTO", "goto", "MOVE", "move", "POS", "HOME", "RELEASE", "PING"])
        if verb.upper() in {"GOTO", "MOVE"}:
            value = rng.uniform(-400, 400)
            text = rng.choice(
                [
                    f"{value:.0f}",
                    f"{value:.{rng.randrange(1, 7)}f}",
                    f"{value:.3e}",
                    f" {value:.2f} ",
                ]
            )
            commands.append(f"{verb} {text}")
        else:
            commands.append(verb)
    assert_same(firmware, commands)
