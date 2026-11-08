"""Adaptador de la mesa real: protocolo de texto del firmware sobre un enlace serial.

Protocolo (una orden por línea, 115200 baudios): ``PING``, ``HOME``, ``GOTO <grados>``,
``MOVE <grados>``, ``POS`` y ``RELEASE``. Las respuestas empiezan con ``OK`` o ``ERR``.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Protocol

import serial

from directivity.domain.angles import AngleOutOfRangeError, validate_angle
from directivity.ports import TurntableError

DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT_S = 30.0
"""Timeout amplio: el giro más largo (-360° a 360°) tarda ~24 s a 5 RPM."""


class LineTransport(Protocol):
    """Enlace orientado a líneas de texto (costura interna del adaptador)."""

    def write_line(self, line: str) -> None:
        """Envía una línea; el transporte agrega el terminador.

        Args:
            line: Texto de la orden, sin salto de línea.
        """
        ...

    def read_line(self) -> str:
        """Lee una línea de respuesta.

        Returns:
            La línea sin espacios ni terminador; ``""`` si se agotó el timeout.
        """
        ...

    def close(self) -> None:
        """Cierra el enlace."""
        ...


class PySerialTransport:
    """``LineTransport`` sobre pyserial.

    Args:
        url: Puerto (``/dev/tty.usbmodem1101``) o URL de pyserial (``loop://``).
        baudrate: Velocidad en baudios.
        timeout: Tiempo máximo de espera por respuesta, en segundos.
        settle_s: Pausa tras abrir el puerto antes de vaciar el buffer de entrada.

    Raises:
        TurntableError: Si el puerto no se puede abrir. Los errores de E/S posteriores
            (p. ej. el USB desconectado) también se traducen a ``TurntableError``.
    """

    def __init__(
        self,
        url: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT_S,
        settle_s: float = 0.5,
    ) -> None:
        try:
            self._serial = serial.serial_for_url(url, baudrate=baudrate, timeout=timeout)
        except OSError as exc:  # serial.SerialException deriva de OSError
            raise TurntableError(f"no se pudo abrir el puerto {url!r}: {exc}") from exc
        time.sleep(settle_s)
        self._serial.reset_input_buffer()

    def write_line(self, line: str) -> None:
        """Envía ``line`` con salto de línea final, codificada en ASCII.

        Args:
            line: Texto de la orden, sin salto de línea.
        """
        try:
            self._serial.write((line + "\n").encode("ascii"))
        except OSError as exc:
            raise TurntableError(f"falla al escribir en el puerto serial: {exc}") from exc

    def read_line(self) -> str:
        """Lee hasta el salto de línea o hasta el timeout.

        Returns:
            La línea sin espacios ni terminador; ``""`` si se agotó el timeout.
        """
        try:
            raw: bytes = self._serial.readline()
        except OSError as exc:
            raise TurntableError(f"falla al leer del puerto serial: {exc}") from exc
        return raw.decode("ascii", errors="replace").strip()

    def close(self) -> None:
        """Cierra el puerto serial."""
        self._serial.close()


class SerialTurntable:
    """Mesa giratoria real controlada por el firmware ``DirectivityTurntable.ino``.

    Al construirse verifica la conexión con ``PING``; si falla, cierra el transporte.

    Args:
        transport: Enlace de líneas ya abierto (pyserial o el emulador del firmware).

    Raises:
        TurntableError: Si la mesa no responde ``PONG``.
    """

    def __init__(self, transport: LineTransport) -> None:
        self._transport = transport
        try:
            if self._command("PING") != "PONG":
                raise TurntableError("la mesa no respondió PONG al PING")
        except BaseException:
            transport.close()
            raise

    @classmethod
    def open(
        cls,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> SerialTurntable:
        """Abre el puerto serial y conecta con la mesa.

        Args:
            port: Puerto serial, p. ej. ``/dev/tty.usbmodem1101``.
            baudrate: Velocidad en baudios.
            timeout: Tiempo máximo de espera por respuesta, en segundos.

        Returns:
            Una mesa conectada y verificada con ``PING``.

        Raises:
            TurntableError: Si la mesa no responde correctamente.
        """
        return cls(PySerialTransport(port, baudrate=baudrate, timeout=timeout))

    def _command(self, line: str) -> str:
        """Envía una orden y devuelve el contenido de la respuesta ``OK``.

        Args:
            line: Orden del protocolo, p. ej. ``"GOTO 5.0000"``.

        Returns:
            El texto que sigue a ``OK`` (``""`` si no hay contenido).

        Raises:
            TurntableError: Ante ``ERR``, timeout o una respuesta inesperada.
        """
        self._transport.write_line(line)
        reply = self._transport.read_line()
        if not reply:
            raise TurntableError(f"sin respuesta a {line!r} (timeout)")
        if reply == "ERR" or reply.startswith("ERR "):
            raise TurntableError(reply[4:] or "error sin mensaje del firmware")
        if reply == "OK" or reply.startswith("OK "):
            return reply[3:]
        raise TurntableError(f"respuesta inesperada a {line!r}: {reply!r}")

    def _command_angle(self, line: str) -> float:
        """Envía una orden cuya respuesta es un ángulo.

        Args:
            line: Orden del protocolo.

        Returns:
            El ángulo informado por la mesa, en grados.

        Raises:
            TurntableError: Si la orden falla o la respuesta no es un número.
        """
        payload = self._command(line)
        try:
            return float(payload)
        except ValueError:
            raise TurntableError(
                f"ángulo inválido en la respuesta a {line!r}: {payload!r}"
            ) from None

    def home(self) -> float:
        """Toma la posición actual como 0°.

        Returns:
            El ángulo informado por la mesa (0.0).
        """
        return self._command_angle("HOME")

    def goto(self, degrees: float) -> float:
        """Gira hasta un ángulo absoluto.

        Args:
            degrees: Ángulo absoluto pedido, en grados.

        Returns:
            El ángulo real alcanzado.

        Raises:
            TurntableError: Si el ángulo está fuera de rango o la mesa falla.
        """
        try:
            validate_angle(degrees)
        except AngleOutOfRangeError as exc:
            raise TurntableError(str(exc)) from exc
        return self._command_angle(f"GOTO {degrees:.4f}")

    def move(self, degrees: float) -> float:
        """Gira de forma relativa (no forma parte del puerto ``Turntable``).

        Args:
            degrees: Giro relativo en grados; el firmware valida el destino.

        Returns:
            El ángulo real alcanzado.

        Raises:
            TurntableError: Si el giro no es finito, el destino queda fuera de rango o
                la mesa falla.
        """
        try:
            validate_angle(degrees, max_abs_deg=2 * 360.0)
        except AngleOutOfRangeError as exc:
            raise TurntableError(str(exc)) from exc
        return self._command_angle(f"MOVE {degrees:.4f}")

    def position(self) -> float:
        """Devuelve el ángulo absoluto actual.

        Returns:
            Ángulo actual en grados.
        """
        return self._command_angle("POS")

    def release(self) -> None:
        """Apaga las bobinas del motor."""
        self._command("RELEASE")

    def close(self) -> None:
        """Cierra el transporte."""
        self._transport.close()

    def __enter__(self) -> SerialTurntable:
        """Permite usar la mesa con ``with``.

        Returns:
            La propia mesa.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Cierra el transporte al salir del bloque ``with``."""
        self.close()
