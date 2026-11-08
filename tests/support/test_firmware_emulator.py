"""Tests del emulador del firmware: casos de borde del ``.ino`` que el contrato no cubre."""

from __future__ import annotations

import numpy as np
import pytest

from tests.support.firmware_emulator import FirmwareEmulator, parse_number, print_float


def ask(emulator: FirmwareEmulator, line: str) -> str:
    """Envía una orden al emulador y devuelve su respuesta."""
    emulator.write_line(line)
    return emulator.read_line()


def test_commands_are_case_insensitive() -> None:
    emulator = FirmwareEmulator()
    assert ask(emulator, "ping") == "OK PONG"
    assert ask(emulator, "goto 5") == "OK 4.92"


def test_carriage_return_is_ignored() -> None:
    emulator = FirmwareEmulator()
    emulator.feed("POS\r\n")
    assert emulator.read_line() == "OK 0.00"


def test_empty_line_has_no_reply() -> None:
    emulator = FirmwareEmulator()
    assert ask(emulator, "") == ""


def test_long_line_is_discarded() -> None:
    emulator = FirmwareEmulator()
    assert ask(emulator, "GOTO " + "1" * 40) == "ERR linea demasiado larga"
    assert emulator.position_steps == 0
    assert ask(emulator, "PING") == "OK PONG"


def test_31_characters_still_fit() -> None:
    emulator = FirmwareEmulator()
    assert ask(emulator, "GOTO " + "0" * 24 + "10") == "OK 10.02"  # 56.89 pasos → 57


@pytest.mark.parametrize(
    ("line", "reply"),
    [
        ("GOTO abc", "ERR angulo invalido"),
        ("GOTO 361", "ERR fuera de rango (-360 a 360)"),
        ("FOO", "ERR comando desconocido"),
        ("GOTO", "ERR comando desconocido"),  # sin espacio no coincide con "GOTO "
    ],
)
def test_errors(line: str, reply: str) -> None:
    assert ask(FirmwareEmulator(), line) == reply


def test_move_is_relative_to_absolute_position() -> None:
    emulator = FirmwareEmulator()
    ask(emulator, "GOTO 90")
    assert ask(emulator, "MOVE -100") == "OK -10.02"
    assert ask(emulator, "MOVE -360") == "ERR fuera de rango (-360 a 360)"


def coils_on(emulator: FirmwareEmulator) -> bool:
    """Lee el estado de las bobinas (evita que mypy congele el atributo entre órdenes)."""
    return emulator.coils_on


def test_release_turns_coils_off_and_keeps_position() -> None:
    emulator = FirmwareEmulator()
    ask(emulator, "GOTO 45")
    assert coils_on(emulator)
    assert ask(emulator, "RELEASE") == "OK RELEASED"
    assert not coils_on(emulator)
    assert ask(emulator, "POS") == "OK 45.00"


@pytest.mark.parametrize(
    ("value", "text"),
    [(0.0, "0.00"), (4.921875, "4.92"), (-10.01953, "-10.02"), (360.0, "360.00"), (0.004, "0.00")],
)
def test_print_float_matches_arduino(value: float, text: str) -> None:
    assert print_float(np.float32(value)) == text


def test_parse_number_follows_strtod() -> None:
    assert parse_number(" 12.5xyz") == np.float32(12.5)
    assert parse_number("-1e1") == np.float32(-10.0)
    assert parse_number(".5") == np.float32(0.5)
    assert parse_number("x1") is None
