"""Tests del adaptador ``SerialTurntable`` (errores del protocolo y transporte)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

import pytest

from directivity.adapters.serial_turntable import PySerialTransport, SerialTurntable
from directivity.ports import TurntableError


class ScriptedTransport:
    """Transporte que responde con un guion fijo, para provocar respuestas malformadas.

    El emulador del firmware solo produce respuestas válidas; este doble cubre lo demás.
    """

    def __init__(self, replies: Iterable[str]) -> None:
        """Crea el transporte con las respuestas que devolverá en orden."""
        self.replies = deque(replies)
        self.sent: list[str] = []
        self.closed = False

    def write_line(self, line: str) -> None:
        """Registra la orden enviada."""
        self.sent.append(line)

    def read_line(self) -> str:
        """Devuelve la siguiente respuesta del guion o ``""`` (timeout)."""
        return self.replies.popleft() if self.replies else ""

    def close(self) -> None:
        """Marca el transporte como cerrado."""
        self.closed = True


def connected(*replies: str) -> tuple[SerialTurntable, ScriptedTransport]:
    """Devuelve una mesa ya conectada (PING respondido) y su transporte guionado."""
    transport = ScriptedTransport(["OK PONG", *replies])
    return SerialTurntable(transport), transport


class TestHandshake:
    def test_ping_ok(self) -> None:
        _, transport = connected()
        assert transport.sent == ["PING"]
        assert not transport.closed

    @pytest.mark.parametrize("reply", ["OK NOPE", "", "ERR ocupado", "basura"])
    def test_failed_ping_closes_transport(self, reply: str) -> None:
        transport = ScriptedTransport([reply])
        with pytest.raises(TurntableError):
            SerialTurntable(transport)
        assert transport.closed


class TestReplies:
    def test_err_becomes_turntable_error_with_message(self) -> None:
        table, _ = connected("ERR fuera de rango (-360 a 360)")
        with pytest.raises(TurntableError, match="fuera de rango"):
            table.position()

    def test_bare_err(self) -> None:
        table, _ = connected("ERR")
        with pytest.raises(TurntableError, match="sin mensaje"):
            table.position()

    def test_empty_reply_is_timeout(self) -> None:
        table, _ = connected("")
        with pytest.raises(TurntableError, match="timeout"):
            table.home()

    @pytest.mark.parametrize("reply", ["HELLO", "OKAY 5", "ERROR"])
    def test_unexpected_reply(self, reply: str) -> None:
        table, _ = connected(reply)
        with pytest.raises(TurntableError, match="inesperada"):
            table.position()

    @pytest.mark.parametrize("reply", ["OK", "OK abc"])
    def test_non_numeric_angle_is_turntable_error(self, reply: str) -> None:
        table, _ = connected(reply)
        with pytest.raises(TurntableError, match="ángulo inválido"):
            table.position()


class TestCommands:
    def test_command_formatting(self) -> None:
        table, transport = connected("OK 0.00", "OK 4.92", "OK 9.84", "OK 9.84", "OK RELEASED")
        assert table.home() == 0.0
        assert table.goto(5) == 4.92
        assert table.move(5) == 9.84
        assert table.position() == 9.84
        table.release()
        assert transport.sent == ["PING", "HOME", "GOTO 5.0000", "MOVE 5.0000", "POS", "RELEASE"]

    @pytest.mark.parametrize("target", [361.0, -400.0, float("nan")])
    def test_goto_validates_before_sending(self, target: float) -> None:
        table, transport = connected()
        with pytest.raises(TurntableError):
            table.goto(target)
        assert transport.sent == ["PING"]

    def test_move_rejects_non_finite_before_sending(self) -> None:
        table, transport = connected()
        with pytest.raises(TurntableError):
            table.move(float("inf"))
        assert transport.sent == ["PING"]

    def test_context_manager_closes(self) -> None:
        table, transport = connected()
        with table as same:
            assert same is table
        assert transport.closed


class TestPySerialTransport:
    def test_loopback_round_trip(self) -> None:
        transport = PySerialTransport("loop://", timeout=0.1, settle_s=0.0)
        try:
            transport.write_line("PING")
            assert transport.read_line() == "PING"
            assert transport.read_line() == ""  # timeout
        finally:
            transport.close()

    def test_open_fails_cleanly_when_peer_is_not_the_firmware(self) -> None:
        # loop:// devuelve el eco "PING", que no es "OK PONG".
        with pytest.raises(TurntableError, match="inesperada"):
            SerialTurntable.open("loop://", timeout=0.1)

    def test_unopenable_port_is_turntable_error(self) -> None:
        with pytest.raises(TurntableError, match="no se pudo abrir"):
            PySerialTransport("/dev/no-existe-directivity", settle_s=0.0)

    def test_io_errors_are_turntable_errors(self) -> None:
        transport = PySerialTransport("loop://", timeout=0.1, settle_s=0.0)
        transport.close()  # escribir o leer un puerto cerrado lanza SerialException
        with pytest.raises(TurntableError, match="escribir"):
            transport.write_line("PING")
        with pytest.raises(TurntableError, match="leer"):
            transport.read_line()
