"""Cliente serial para la mesa giratoria DirectivityTurntable.

Uso rápido (barrido de prueba de 0° a 360° cada 5° y regreso a 0°):

    python turntable.py --port /dev/tty.usbmodem1101 --step 5

Uso como librería:

    from turntable import Turntable
    with Turntable("/dev/tty.usbmodem1101") as table:
        table.home()
        for angle in range(0, 361, 5):
            table.goto(angle)
            ...  # aquí va la medición acústica en ese ángulo
        table.goto(0)
        table.release()
"""

from __future__ import annotations

import argparse
import time

import serial


class TurntableError(RuntimeError):
    """Error devuelto por el firmware (respuesta 'ERR ...')."""


class Turntable:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 30.0):
        # timeout amplio: una vuelta completa a 5 RPM tarda ~12 s
        self._serial = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        time.sleep(0.5)
        self._serial.reset_input_buffer()
        if self._command("PING") != "PONG":
            raise TurntableError("La mesa no respondió al PING")

    def _command(self, line: str) -> str:
        self._serial.write((line + "\n").encode("ascii"))
        reply = self._serial.readline().decode("ascii", errors="replace").strip()
        if not reply:
            raise TurntableError(f"Sin respuesta a '{line}' (timeout)")
        if reply.startswith("ERR"):
            raise TurntableError(reply[4:] or reply)
        if not reply.startswith("OK"):
            raise TurntableError(f"Respuesta inesperada: {reply!r}")
        return reply[3:]

    def home(self) -> float:
        """Toma la posición actual como 0°."""
        return float(self._command("HOME"))

    def goto(self, degrees: float) -> float:
        """Va a un ángulo absoluto y devuelve el ángulo real alcanzado."""
        return float(self._command(f"GOTO {degrees:.4f}"))

    def move(self, degrees: float) -> float:
        """Gira de forma relativa y devuelve el ángulo real alcanzado."""
        return float(self._command(f"MOVE {degrees:.4f}"))

    def position(self) -> float:
        return float(self._command("POS"))

    def release(self) -> None:
        """Apaga las bobinas del motor."""
        self._command("RELEASE")

    def close(self) -> None:
        self._serial.close()

    def __enter__(self) -> "Turntable":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Barrido de prueba de la mesa giratoria")
    parser.add_argument("--port", required=True, help="Puerto serial del Arduino")
    parser.add_argument("--step", type=float, default=5.0, help="Paso angular en grados")
    parser.add_argument("--stop", type=float, default=360.0, help="Ángulo final en grados")
    parser.add_argument("--dwell", type=float, default=1.0, help="Pausa en cada ángulo (s)")
    args = parser.parse_args()

    with Turntable(args.port) as table:
        table.home()
        n_points = int(round(args.stop / args.step)) + 1
        for i in range(n_points):
            target = i * args.step
            reached = table.goto(target)
            print(f"objetivo {target:7.2f}°  ->  real {reached:7.2f}°")
            time.sleep(args.dwell)
        table.goto(0)
        table.release()
        print("Barrido terminado; la mesa volvió a 0°.")


if __name__ == "__main__":
    main()
