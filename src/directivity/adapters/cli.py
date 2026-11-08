"""Punto de arranque por línea de comandos (composition root).

Es el único módulo que instancia adaptadores concretos y los conecta.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import closing

from directivity import __version__
from directivity.adapters.serial_turntable import SerialTurntable
from directivity.adapters.simulated_turntable import SimulatedTurntable
from directivity.domain.angles import AngleOutOfRangeError, SweepPlan
from directivity.ports import Turntable, TurntableError

HARDWARE_DWELL_S = 1.0
"""Pausa por defecto en cada ángulo con la mesa real."""


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos de la CLI.

    Returns:
        El parser con todas las opciones y subcomandos.
    """
    parser = argparse.ArgumentParser(
        prog="directivity",
        description="Mesa giratoria para medir patrones de directividad.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<comando>")

    sweep = commands.add_parser("sweep", help="barrido de prueba de la mesa (sin audio)")
    _add_turntable_options(sweep)
    _add_sweep_options(sweep)
    sweep.add_argument(
        "--dwell",
        type=float,
        default=None,
        help=f"pausa en cada ángulo en s (por defecto {HARDWARE_DWELL_S} con la placa, 0 simulado)",
    )
    return parser


def _add_turntable_options(parser: argparse.ArgumentParser) -> None:
    """Agrega la elección de mesa: ``--port`` (real) o ``--simulate``.

    Args:
        parser: Parser del subcomando.
    """
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="puerto serial del Arduino, p. ej. /dev/tty.usbmodem1101")
    source.add_argument("--simulate", action="store_true", help="usar la mesa simulada")


def _add_sweep_options(parser: argparse.ArgumentParser) -> None:
    """Agrega las opciones del plan de barrido.

    Args:
        parser: Parser del subcomando.
    """
    parser.add_argument("--start", type=float, default=0.0, help="ángulo inicial en grados")
    parser.add_argument("--stop", type=float, default=360.0, help="ángulo final en grados")
    parser.add_argument("--step", type=float, default=5.0, help="paso angular en grados")


def open_turntable(args: argparse.Namespace) -> Turntable:
    """Crea el adaptador de mesa pedido en la línea de comandos.

    Args:
        args: Argumentos con ``simulate`` y ``port``.

    Returns:
        Una mesa simulada o una conectada por serial.

    Raises:
        TurntableError: Si la mesa real no responde.
    """
    if args.simulate:
        return SimulatedTurntable()
    return SerialTurntable.open(args.port)


def run_sweep(
    table: Turntable,
    plan: SweepPlan,
    dwell_s: float,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Recorre el plan imprimiendo el ángulo pedido y el real; termina en 0° y liberada.

    Args:
        table: Mesa giratoria.
        plan: Ángulos a visitar.
        dwell_s: Pausa en cada ángulo, en segundos.
        out: Función que recibe cada línea de salida.
        sleep: Función de espera (inyectable para tests).

    Raises:
        TurntableError: Si la mesa falla; aun así se intenta volver a 0° y liberar.
    """
    table.home()
    try:
        for target in plan.targets():
            reached = table.goto(target)
            out(f"objetivo {target:7.2f}°  ->  real {reached:7.2f}°")
            sleep(dwell_s)
    finally:
        table.goto(0.0)
        table.release()
    out("Barrido terminado; la mesa volvió a 0°.")


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta la CLI.

    Args:
        argv: Argumentos de línea de comandos; ``None`` usa ``sys.argv``.

    Returns:
        Código de salida del proceso (0 éxito, 1 error de la mesa o del plan).
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        plan = SweepPlan(args.start, args.stop, args.step)
        dwell = (
            args.dwell if args.dwell is not None else (0.0 if args.simulate else HARDWARE_DWELL_S)
        )
        with closing(open_turntable(args)) as table:
            run_sweep(table, plan, dwell)
    except (TurntableError, AngleOutOfRangeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
