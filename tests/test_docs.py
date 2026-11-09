"""Valida que los comandos documentados en el README sigan funcionando.

- Los comandos ``directivity … --simulate`` de los bloques de código se ejecutan de verdad.
- Todos los comandos ``directivity …`` (también los que usan la placa) se validan contra el
  parser de la CLI, así que una opción mal escrita o eliminada hace fallar el test.
- Las rutas ``tests/…`` y los atajos ``make …`` mencionados deben existir.
- Regresión: el contrato con la placa se documenta siempre con ``--port=``. Con espacio,
  pytest toma el puerto como ruta de tests y responde "unrecognized arguments".
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from directivity.adapters.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def bash_lines(markdown: str) -> list[str]:
    """Devuelve las líneas de comando de los bloques ```bash, sin comentarios finales."""
    lines = []
    for block in re.findall(r"```bash\n(.*?)```", markdown, flags=re.DOTALL):
        for raw in block.splitlines():
            command = raw.split(" #", 1)[0].strip()
            if command:
                lines.append(command)
    return lines


def directivity_commands(markdown: str) -> list[str]:
    """Comandos ``directivity …`` de los bloques de código y del código en línea."""
    inline = re.findall(r"`(directivity [^`]+)`", markdown)
    blocks = [line for line in bash_lines(markdown) if line.startswith("directivity ")]
    return sorted(set(inline + blocks))


def make_targets(makefile: str) -> set[str]:
    """Targets definidos en el Makefile."""
    return set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, flags=re.MULTILINE))


SIMULATED = sorted(
    {c for c in bash_lines(README) if c.startswith("directivity ") and "--simulate" in c}
)


def test_readme_documents_simulated_commands() -> None:
    assert len(SIMULATED) >= 3  # sweep, measure y selftest


@pytest.mark.parametrize("command", SIMULATED)
def test_simulated_commands_run(command: str, tmp_path: Path) -> None:
    args = shlex.split(command)[1:]
    result = subprocess.run(
        [sys.executable, "-m", "directivity", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("command", directivity_commands(README))
def test_every_directivity_command_parses(command: str) -> None:
    args = shlex.split(command.replace("…", ""))[1:]
    parser = build_parser()
    if len(args) == 1:  # mención en el texto del subcomando, p. ej. `directivity sweep`
        subcommands = next(a for a in parser._actions if a.dest == "command")
        assert args[0] in subcommands.choices  # type: ignore[operator]
        return
    try:
        parser.parse_args(args)  # SystemExit(2) si una opción no existe
    except SystemExit as exc:
        assert exc.code == 0, f"opción inválida en el README: {command}"  # --help sale con 0


@pytest.mark.parametrize("path", sorted(set(re.findall(r"\btests/[\w./]*\w", README))))
def test_mentioned_test_paths_exist(path: str) -> None:
    target = path.split("::")[0]
    assert (ROOT / target).exists(), f"el README menciona {path}, que no existe"


@pytest.mark.parametrize("target", sorted(set(re.findall(r"\bmake ([a-z][\w-]*)", README))))
def test_mentioned_make_targets_exist(target: str) -> None:
    assert target in make_targets(MAKEFILE), f"el README menciona `make {target}`"


@pytest.mark.parametrize(("name", "text"), [("README.md", README), ("Makefile", MAKEFILE)])
def test_hardware_contract_uses_port_with_equals(name: str, text: str) -> None:
    offending = [
        line
        for line in text.splitlines()
        if re.search(r"pytest -m hardware", line) and re.search(r"--port(?!=)", line)
    ]
    assert offending == [], f"{name}: usa --port= (con espacio pytest no reconoce la opción)"


def test_help_lists_every_documented_target() -> None:
    documented = {"check", "test", "cov", "format", "sim", "ports", "sweep", "hw", "selftest"}
    assert documented <= make_targets(MAKEFILE)
