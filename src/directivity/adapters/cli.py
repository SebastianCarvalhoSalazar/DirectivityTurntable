"""Punto de arranque por línea de comandos (composition root).

Es el único módulo que instancia adaptadores concretos y los conecta.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack, closing
from datetime import datetime
from pathlib import Path

from directivity import __version__
from directivity.adapters.csv_store import CsvResultStore
from directivity.adapters.serial_turntable import SerialTurntable
from directivity.adapters.simulated_audio import MODELS, SimulatedAudio
from directivity.adapters.simulated_turntable import SimulatedTurntable
from directivity.application.measure_directivity import MeasureDirectivity
from directivity.domain.angles import SweepPlan
from directivity.domain.pipeline import default_pipeline
from directivity.domain.polar import (
    OCTAVE_CENTERS,
    MeasurementPoint,
    band_edges,
    octave_band_levels,
)
from directivity.domain.signals import LogSweep
from directivity.domain.windows import TukeyWindow
from directivity.ports import Turntable, TurntableError

HARDWARE_DWELL_S = 1.0
"""Pausa por defecto en cada ángulo con la mesa real (``sweep``)."""

HARDWARE_SETTLE_S = 0.5
"""Pausa por defecto tras cada giro antes de medir con la mesa real (``measure``)."""

SIM_DELAY_S = 0.003
"""Retardo del sonido directo simulado (parlante a ~1 m del micrófono)."""

SIM_REFLECTIONS = ((0.012, 0.4),)
"""Reflexión simulada del piso: llega 9 ms después del directo; la compuerta la elimina."""

SIM_NOISE_DB = -80.0
"""Ruido de fondo simulado, en dB re escala completa."""

REPORT_BAND_HZ = 1000.0
"""Banda que se muestra en pantalla durante la medición."""


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
    sweep.set_defaults(handler=_cmd_sweep)

    measure = commands.add_parser(
        "measure",
        help="medir el patrón de directividad (la fuente debe apuntar al micrófono al inicio)",
    )
    _add_turntable_options(measure)
    _add_sweep_options(measure, default_step=10.0)
    measure.add_argument(
        "--source",
        choices=sorted(MODELS),
        default="cardioid",
        help="modelo de la fuente simulada (solo con --simulate)",
    )
    measure.add_argument("--out", type=Path, default=Path("data/raw"), help="carpeta de sesiones")
    measure.add_argument("--sample-rate", type=int, default=48_000, help="frecuencia de muestreo")
    measure.add_argument(
        "--sweep-duration", type=float, default=1.0, help="duración del barrido (s)"
    )
    measure.add_argument("--gate-start", type=float, default=0.0, help="inicio de la compuerta (s)")
    measure.add_argument(
        "--gate-length", type=float, default=0.010, help="duración de la compuerta (s)"
    )
    measure.add_argument(
        "--settle",
        type=float,
        default=None,
        help=f"pausa tras cada giro en s ({HARDWARE_SETTLE_S} con la placa, 0 simulado)",
    )
    measure.set_defaults(handler=_cmd_measure)
    return parser


def _add_turntable_options(parser: argparse.ArgumentParser) -> None:
    """Agrega la elección de mesa: ``--port`` (real) o ``--simulate``.

    Args:
        parser: Parser del subcomando.
    """
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="puerto serial del Arduino, p. ej. /dev/tty.usbmodem1101")
    source.add_argument("--simulate", action="store_true", help="usar la mesa simulada")


def _add_sweep_options(parser: argparse.ArgumentParser, default_step: float = 5.0) -> None:
    """Agrega las opciones del plan de barrido.

    Args:
        parser: Parser del subcomando.
        default_step: Paso angular por defecto, en grados.
    """
    parser.add_argument("--start", type=float, default=0.0, help="ángulo inicial en grados")
    parser.add_argument("--stop", type=float, default=360.0, help="ángulo final en grados")
    parser.add_argument("--step", type=float, default=default_step, help="paso angular en grados")


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
        try:
            table.goto(0.0)
        finally:
            table.release()
    out("Barrido terminado; la mesa volvió a 0°.")


def format_db(level: float) -> str:
    """Formatea un nivel en dB con 2 decimales, sin mostrar ``-0.00``.

    Args:
        level: Nivel en dB.

    Returns:
        El nivel con ancho fijo de 7 caracteres.
    """
    return f"{round(level, 2) + 0.0:7.2f}"


def session_name() -> str:
    """Nombre de la carpeta de una sesión nueva: fecha y hora local.

    Returns:
        Por ejemplo ``20261108T213000``.
    """
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def bands_for(sample_rate: int) -> tuple[float, ...]:
    """Bandas de octava por defecto que caben bajo Nyquist a esta frecuencia de muestreo.

    Args:
        sample_rate: Frecuencia de muestreo en Hz.

    Returns:
        Centros nominales de las bandas utilizables.
    """
    return tuple(band for band in OCTAVE_CENTERS if band_edges(band)[1] <= sample_rate / 2)


def _print_point(point: MeasurementPoint) -> None:
    """Muestra el avance: ángulo pedido, ángulo real y nivel en la banda de referencia."""
    level = octave_band_levels(point.ir, (REPORT_BAND_HZ,))[REPORT_BAND_HZ]
    print(
        f"objetivo {point.target_deg:7.2f}°  ->  real {point.actual_deg:7.2f}°"
        f"   L({REPORT_BAND_HZ / 1000:g} kHz) {level:7.2f} dB"
    )


def _cmd_sweep(args: argparse.Namespace) -> int:
    """Subcomando ``sweep``: barrido de prueba de la mesa, sin audio.

    Args:
        args: Argumentos de la línea de comandos.

    Returns:
        Código de salida.
    """
    plan = SweepPlan(args.start, args.stop, args.step)
    dwell = args.dwell
    if dwell is None:
        dwell = 0.0 if args.simulate else HARDWARE_DWELL_S
    with closing(open_turntable(args)) as table:
        run_sweep(table, plan, dwell)
    return 0


def _cmd_measure(args: argparse.Namespace) -> int:
    """Subcomando ``measure``: mide el patrón polar y guarda la sesión.

    Args:
        args: Argumentos de la línea de comandos.

    Returns:
        Código de salida.
    """
    if not args.simulate:
        print(
            "error: medir con la placa requiere un adaptador de audio real, que aún no existe "
            "(ver docs/MIGRATION_PLAN.md, sección 11). Usa --simulate o `directivity sweep`.",
            file=sys.stderr,
        )
        return 1
    plan = SweepPlan(args.start, args.stop, args.step)
    sample_rate: int = args.sample_rate
    excitation = LogSweep(
        f_start=20.0, f_stop=min(20_000.0, 0.45 * sample_rate), duration_s=args.sweep_duration
    )
    pipeline = default_pipeline(excitation, TukeyWindow(), args.gate_start, args.gate_length)
    bands = bands_for(sample_rate)
    session = args.out / session_name()
    settle = args.settle
    if settle is None:
        settle = 0.0 if args.simulate else HARDWARE_SETTLE_S

    with ExitStack() as stack:
        table = stack.enter_context(closing(SimulatedTurntable()))
        audio = SimulatedAudio(
            angle_provider=table.position,
            directivity=MODELS[args.source],
            sample_rate=sample_rate,
            delay_s=SIM_DELAY_S,
            reflections=SIM_REFLECTIONS,
            noise_db=SIM_NOISE_DB,
        )
        store = CsvResultStore(session, bands)
        use_case = MeasureDirectivity(
            table, audio, store, excitation, pipeline, bands, settle_s=settle
        )
        pattern = use_case.execute(plan, on_point=_print_point)

    print(f"\nPatrón a {REPORT_BAND_HZ:g} Hz (dB re eje):")
    for angle, level in zip(pattern.angles(), pattern.normalized(REPORT_BAND_HZ), strict=True):
        print(f"  {angle:7.2f}°  {format_db(level)} dB")
    print(f"Sesión guardada en {session}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta la CLI.

    Args:
        argv: Argumentos de línea de comandos; ``None`` usa ``sys.argv``.

    Returns:
        Código de salida del proceso: 0 éxito, 1 error, 130 interrumpido con Ctrl-C.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except (TurntableError, ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrumpido; la mesa volvió a 0° y se apagaron las bobinas", file=sys.stderr)
        return 130
