"""Punto de arranque por línea de comandos (composition root)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from directivity import __version__


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta la CLI.

    Args:
        argv: Argumentos de línea de comandos; ``None`` usa ``sys.argv``.

    Returns:
        Código de salida del proceso.
    """
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
