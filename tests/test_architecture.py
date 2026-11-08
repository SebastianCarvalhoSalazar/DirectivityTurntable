"""Verifica la regla de dependencias entre capas recorriendo los ``import`` con ``ast``.

Regla: ``domain`` solo usa la biblioteca estándar y numpy; ``ports`` agrega el dominio;
``application`` agrega los puertos. Solo ``adapters`` puede usar cualquier cosa.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = "directivity"
SRC = Path(__file__).resolve().parents[1] / "src" / PACKAGE

ALLOWED: dict[str, set[str]] = {
    "domain": {"numpy", f"{PACKAGE}.domain"},
    "ports": {"numpy", f"{PACKAGE}.domain", f"{PACKAGE}.ports"},
    "application": {
        "numpy",
        f"{PACKAGE}.domain",
        f"{PACKAGE}.ports",
        f"{PACKAGE}.application",
    },
}


def layer_of(path: Path, root: Path) -> str | None:
    """Devuelve la capa de un archivo (``domain``, ``ports``…) o ``None`` si no tiene reglas."""
    first = path.relative_to(root).parts[0]
    name = first.removesuffix(".py")
    return name if name in ALLOWED else None


def imported_modules(path: Path, root: Path) -> list[str]:
    """Lista los módulos importados por un archivo, con los relativos resueltos."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package_parts = [PACKAGE, *path.relative_to(root).parent.parts]
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                modules.append(".".join([*base, *([node.module] if node.module else [])]))
            elif node.module:
                modules.append(node.module)
    return modules


def is_allowed(module: str, allowed: set[str]) -> bool:
    """Indica si un módulo importado está permitido para una capa."""
    top = module.split(".")[0]
    if top in sys.stdlib_module_names:
        return True
    return any(module == a or module.startswith(a + ".") for a in allowed)


def violations(root: Path) -> list[str]:
    """Recorre ``root`` y devuelve las importaciones que rompen la regla de capas."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        layer = layer_of(path, root)
        if layer is None:
            continue
        for module in imported_modules(path, root):
            if not is_allowed(module, ALLOWED[layer]):
                found.append(f"{path.relative_to(root)} ({layer}) importa {module}")
    return found


def test_layers_respect_dependency_rule() -> None:
    assert violations(SRC) == []


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("domain/bad.py", "import serial\n"),
        ("domain/bad.py", "from directivity.adapters import cli\n"),
        ("domain/bad.py", "from ..ports import Turntable\n"),
        ("ports.py", "from directivity.application import x\n"),
        ("application/bad.py", "from ..adapters.cli import main\n"),
    ],
)
def test_checker_detects_violations(tmp_path: Path, relative: str, source: str) -> None:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    assert len(violations(tmp_path)) == 1


def test_checker_accepts_allowed_imports(tmp_path: Path) -> None:
    (tmp_path / "application").mkdir()
    (tmp_path / "application" / "ok.py").write_text(
        "from __future__ import annotations\nimport time\nimport numpy as np\n"
        "from ..domain import angles\nfrom directivity.ports import Turntable\n",
        encoding="utf-8",
    )
    assert violations(tmp_path) == []
