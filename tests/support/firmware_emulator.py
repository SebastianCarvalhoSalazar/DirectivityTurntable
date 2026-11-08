"""Emulador en Python del firmware ``DirectivityTurntable.ino`` (versión 2).

Implementa ``LineTransport`` para que ``SerialTurntable`` corra sin placa. Cada
bloque replica una función del ``.ino`` (se indica en los comentarios). En AVR
``float`` y ``double`` son de 32 bits, por eso toda la aritmética usa ``np.float32``.

Además de las respuestas, registra una traza (``trace``) con el mismo formato que
el firmware compilado en el computador (``tests/firmware/host``): ``@dw <pin> 0``
al apagar las bobinas y ``@step <pasos>`` en cada giro. ``tests/firmware`` compara
ambas trazas, así que si el firmware cambia y este archivo no, los tests fallan.
"""

from __future__ import annotations

import math
import re
import string
from collections import deque

import numpy as np

F32 = np.float32

# const long STEPS_PER_REV = 2048; const float MAX_ABS_DEG = 360.0f;
STEPS_PER_REV = 2048
MAX_ABS_DEG = F32(360.0)
# const uint8_t PIN_IN1..PIN_IN4 = 8..11;
COIL_PINS = (8, 9, 10, 11)
# const uint8_t LINE_BUFFER_SIZE = 32;
LINE_BUFFER_SIZE = 32
# const char GOTO_PREFIX[] = "GOTO "; const char MOVE_PREFIX[] = "MOVE ";
GOTO_PREFIX = "GOTO "
MOVE_PREFIX = "MOVE "

# Gramática de strtod en avr-libc: espacios (isspace), signo y luego INF, INFINITY,
# NAN o un decimal con exponente opcional. No acepta hexadecimales.
_STRTOD = re.compile(
    r"[ \t\n\v\f\r]*[+-]?(?:INFINITY|INF|NAN|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)",
    re.IGNORECASE,
)
# toupper() en la configuración regional "C" solo cambia las letras ASCII.
_ASCII_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)


def lround(value: np.float32) -> int:
    """Replica ``lround``: redondeo con empates alejándose de cero."""
    return int(math.copysign(math.floor(abs(float(value)) + 0.5), float(value)))


def degrees_to_steps(deg: np.float32) -> int:
    """``long degreesToSteps(float degrees)``: ``lround(degrees * STEPS_PER_REV / 360.0f)``."""
    return lround(F32(F32(deg * F32(STEPS_PER_REV)) / F32(360.0)))


def steps_to_degrees(steps: int) -> np.float32:
    """``float stepsToDegrees(long steps)``: ``steps * 360.0f / STEPS_PER_REV``."""
    return F32(F32(F32(steps) * F32(360.0)) / F32(STEPS_PER_REV))


def is_within_limits(deg: np.float32) -> bool:
    """``bool isWithinLimits(float degrees)``: falso también para NaN."""
    return bool(-MAX_ABS_DEG <= deg <= MAX_ABS_DEG)


def print_float(number: np.float32, digits: int = 2) -> str:
    """Replica ``Print::printFloat`` del core de Arduino (``Serial.println(x, 2)``)."""
    if math.isnan(number):
        return "nan"
    if math.isinf(number):
        return "inf"
    if number > 4294967040.0 or number < -4294967040.0:
        return "ovf"
    out = ""
    if number < 0.0:
        out += "-"
        number = F32(-number)
    rounding = F32(0.5)
    for _ in range(digits):
        rounding = F32(rounding / F32(10.0))
    number = F32(number + rounding)
    int_part = int(number)
    remainder = F32(number - F32(int_part))
    out += str(int_part)
    if digits > 0:
        out += "."
    for _ in range(digits):
        remainder = F32(remainder * F32(10.0))
        to_print = int(remainder)
        out += str(to_print)
        remainder = F32(remainder - F32(to_print))
    return out


def parse_angle(text: str) -> np.float32 | None:
    """``bool parseAngle(const char *text, float *degrees)``.

    Acepta un número de ``strtod`` seguido solo de espacios; rechaza NaN y texto
    sobrante. Un infinito se acepta (lo rechaza después la verificación de rango).
    """
    match = _STRTOD.match(text)
    if match is None:
        return None
    value = F32(float(match.group(0)))
    if math.isnan(value) or text[match.end() :].strip(" ") != "":
        return None
    return value


class FirmwareEmulator:
    """Firmware emulado que habla el protocolo de texto línea a línea.

    Attributes:
        position_steps: Posición absoluta en pasos (``long positionSteps``).
        coils_on: ``False`` tras ``RELEASE`` o al arrancar (``releaseCoils()`` en ``setup``).
        closed: ``True`` después de ``close()``.
        trace: Respuestas y eventos de hardware en orden, con el formato del firmware
            compilado en el computador.
    """

    def __init__(self) -> None:
        self.position_steps = 0
        self.coils_on = False
        self.closed = False
        self.trace: list[str] = []
        self._line = ""
        self._discarding = False
        self._replies: deque[str] = deque()
        self._release_coils()  # setup()

    # ------------------------------------------------------------ LineTransport
    def write_line(self, line: str) -> None:
        """Recibe una orden como si llegara por el puerto serial."""
        self.feed(line + "\n")

    def read_line(self) -> str:
        """Devuelve la siguiente respuesta o ``""`` (timeout) si no hay ninguna."""
        return self._replies.popleft() if self._replies else ""

    def close(self) -> None:
        """Cierra el enlace emulado."""
        self.closed = True

    # ------------------------------------------------------------ processChar / loop()
    def feed(self, raw: str) -> None:
        """Procesa caracteres crudos como ``loop()`` (para probar casos de borde)."""
        for c in raw:
            if c == "\r":
                continue
            if c == "\n":
                if self._discarding:
                    self._println("ERR linea demasiado larga")
                else:
                    self._handle_command(self._line)
                self._line = ""
                self._discarding = False
            elif self._discarding:
                pass
            elif len(self._line) < LINE_BUFFER_SIZE - 1:
                self._line += c
            else:
                self._discarding = True

    # ------------------------------------------------------------ handleCommand
    def _handle_command(self, line: str) -> None:
        line = line.translate(_ASCII_UPPER)
        if line == "":
            return
        if line == "PING":
            self._println("OK PONG")
        elif line == "HOME":
            self.position_steps = 0
            self._reply_position()
        elif line == "POS":
            self._reply_position()
        elif line == "RELEASE":
            self._release_coils()
            self._println("OK RELEASED")
        elif line.startswith(GOTO_PREFIX):
            value = parse_angle(line[len(GOTO_PREFIX) :])
            if value is None:
                self._println("ERR angulo invalido")
            else:
                self._move_to_degrees(value)
        elif line.startswith(MOVE_PREFIX):
            value = parse_angle(line[len(MOVE_PREFIX) :])
            if value is None:
                self._println("ERR angulo invalido")
            else:
                self._move_to_degrees(F32(steps_to_degrees(self.position_steps) + value))
        else:
            self._println("ERR comando desconocido")

    # ------------------------------------------------------------ moveToDegrees
    def _move_to_degrees(self, target_deg: np.float32) -> None:
        if not is_within_limits(target_deg):
            self._println("ERR fuera de rango (-360 a 360)")
            return
        target_steps = degrees_to_steps(target_deg)
        delta = target_steps - self.position_steps
        if delta != 0:
            self.trace.append(f"@step {delta}")
            self.coils_on = True  # motor.step(delta) energiza las bobinas
        self.position_steps = target_steps
        self._reply_position()

    def _release_coils(self) -> None:
        self.trace.extend(f"@dw {pin} 0" for pin in COIL_PINS)
        self.coils_on = False

    def _reply_position(self) -> None:
        self._println("OK " + print_float(steps_to_degrees(self.position_steps), 2))

    def _println(self, text: str) -> None:
        self._replies.append(text)
        self.trace.append(text)
