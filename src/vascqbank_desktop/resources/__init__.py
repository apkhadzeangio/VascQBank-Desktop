"""Bundled resources (fonts, icons) accessed via ``importlib.resources``.

Using ``importlib.resources`` instead of ``__file__`` arithmetic keeps lookups
working from a source checkout, an installed wheel and a PyInstaller bundle.
"""

from __future__ import annotations

import atexit
from contextlib import ExitStack
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

_PACKAGE = __name__
_exit_stack = ExitStack()
atexit.register(_exit_stack.close)


def _traversable(*parts: str) -> Traversable:
    node = resources.files(_PACKAGE)
    for part in parts:
        node = node / part
    return node


def resource_bytes(*parts: str) -> bytes:
    return _traversable(*parts).read_bytes()


def resource_path(*parts: str) -> Path:
    """Return a real filesystem path, extracting to a temp file if needed."""
    return _exit_stack.enter_context(resources.as_file(_traversable(*parts)))


def font_files() -> list[str]:
    """Names of bundled ``.ttf`` files, sorted."""
    folder = _traversable("fonts")
    return sorted(p.name for p in folder.iterdir() if p.name.lower().endswith(".ttf"))
