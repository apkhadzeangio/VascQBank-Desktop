"""Shared fixtures.

GUI tests run on Qt's ``offscreen`` platform by default so they work in CI and
on machines without a display. Set ``QT_QPA_PLATFORM`` explicitly (e.g. to
``xcb``, ``wayland`` or ``windows``) to run them against a real display.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402

# Georgian characters and a space: the most common real-world path hazards.
GEORGIAN_DIR_NAME = "მონაცემები ტესტი"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / GEORGIAN_DIR_NAME


@pytest.fixture
def app_paths(data_dir: Path) -> AppPaths:
    return AppPaths(data_dir=data_dir)
