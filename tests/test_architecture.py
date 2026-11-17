"""Static checks that keep the layers separated and the app offline.

These parse import statements with ``ast`` instead of importing modules, so a
violation is reported even in code paths the other tests never execute.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import vascqbank_desktop

PACKAGE_ROOT = Path(vascqbank_desktop.__file__).parent
PKG = "vascqbank_desktop"

# layer -> import prefixes it must never use
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "config": ("PySide6", "sqlite3", f"{PKG}.db", f"{PKG}.services", f"{PKG}.ui", f"{PKG}.app"),
    "domain": ("PySide6", "sqlite3", f"{PKG}.db", f"{PKG}.services", f"{PKG}.ui", f"{PKG}.app",
               f"{PKG}.importexport"),
    "validation": ("PySide6", "sqlite3", f"{PKG}.db", f"{PKG}.services", f"{PKG}.ui", f"{PKG}.app"),
    "importexport": ("PySide6", f"{PKG}.ui", f"{PKG}.app"),
    "db": ("PySide6", f"{PKG}.services", f"{PKG}.ui", f"{PKG}.app"),
    "services": ("PySide6", f"{PKG}.ui", f"{PKG}.app"),
    "ui": ("sqlite3", f"{PKG}.db", f"{PKG}.app"),
}

# The application is offline by design: no networking modules anywhere.
NETWORK_MODULES = ("socket", "ssl", "urllib", "http", "ftplib", "smtplib", "requests",
                   "httpx", "aiohttp", "PySide6.QtNetwork", "PySide6.QtWebEngineCore")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _layer_files(layer: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_dependencies(layer: str) -> None:
    files = _layer_files(layer)
    assert files, f"layer {layer!r} has no modules"
    violations = [
        f"{path.relative_to(PACKAGE_ROOT)} imports {module}"
        for path in files
        for module in sorted(_imports(path))
        for prefix in FORBIDDEN[layer]
        if _matches(module, prefix)
    ]
    assert not violations, "\n".join(violations)


def test_no_network_imports() -> None:
    violations = [
        f"{path.relative_to(PACKAGE_ROOT)} imports {module}"
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        for module in sorted(_imports(path))
        for prefix in NETWORK_MODULES
        if _matches(module, prefix)
    ]
    assert not violations, "\n".join(violations)


def test_all_source_files_are_utf8() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        path.read_bytes().decode("utf-8")  # raises on any non-UTF-8 byte


def test_only_the_repository_inserts_questions() -> None:
    """Questions enter the database only through the import repository."""
    allowed = {PACKAGE_ROOT / "db" / "question_repository.py"}
    offenders = [
        str(path.relative_to(PACKAGE_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if path not in allowed
        and "insert into questions" in " ".join(path.read_text(encoding="utf-8").lower().split())
    ]
    assert not offenders, offenders
