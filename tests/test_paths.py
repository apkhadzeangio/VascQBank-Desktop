from __future__ import annotations

import os
from pathlib import Path

import pytest

from vascqbank_desktop.config.constants import DATA_DIR_ENV_VAR, DATABASE_FILENAME
from vascqbank_desktop.config.paths import AppPaths, default_data_dir, resolve_app_paths

HOME = Path("/home/tester")


def test_windows_uses_localappdata() -> None:
    env = {"LOCALAPPDATA": r"C:\Users\რამაზი\AppData\Local"}
    result = default_data_dir(platform="win32", environ=env, home=HOME)
    assert result == Path(r"C:\Users\რამაზი\AppData\Local") / "VascQBank-Desktop"


def test_windows_falls_back_to_home_when_env_missing() -> None:
    result = default_data_dir(platform="win32", environ={}, home=HOME)
    assert result == HOME / "AppData" / "Local" / "VascQBank-Desktop"


@pytest.mark.skipif(os.name == "nt", reason="'/data/xdg' is not an absolute path under Windows path rules")
def test_linux_respects_absolute_xdg_data_home() -> None:
    result = default_data_dir(platform="linux", environ={"XDG_DATA_HOME": "/data/xdg"}, home=HOME)
    assert result == Path("/data/xdg/vascqbank-desktop")


def test_linux_ignores_relative_xdg_data_home() -> None:
    result = default_data_dir(platform="linux", environ={"XDG_DATA_HOME": "relative"}, home=HOME)
    assert result == HOME / ".local" / "share" / "vascqbank-desktop"


def test_linux_default() -> None:
    assert default_data_dir(platform="linux", environ={}, home=HOME) == (
        HOME / ".local" / "share" / "vascqbank-desktop"
    )


def test_macos_default() -> None:
    assert default_data_dir(platform="darwin", environ={}, home=HOME) == (
        HOME / "Library" / "Application Support" / "VascQBank-Desktop"
    )


def test_precedence_override_then_env_then_default(tmp_path: Path) -> None:
    env_dir = tmp_path / "from-env"
    cli_dir = tmp_path / "from-cli"
    env = {DATA_DIR_ENV_VAR: str(env_dir)}

    assert resolve_app_paths(cli_dir, environ=env).data_dir == cli_dir.resolve()
    assert resolve_app_paths(None, environ=env).data_dir == env_dir.resolve()
    default = resolve_app_paths(None, environ={}, platform="linux", home=tmp_path)
    assert default.data_dir == (tmp_path / ".local" / "share" / "vascqbank-desktop").resolve()


def test_blank_env_var_is_ignored(tmp_path: Path) -> None:
    paths = resolve_app_paths(None, environ={DATA_DIR_ENV_VAR: "  "}, platform="linux", home=tmp_path)
    assert paths.data_dir.name == "vascqbank-desktop"


def test_layout_and_directory_creation(app_paths: AppPaths) -> None:
    assert app_paths.database_file == app_paths.data_dir / "database" / DATABASE_FILENAME
    app_paths.ensure_directories()
    app_paths.ensure_directories()  # idempotent
    for directory in (
        app_paths.data_dir,
        app_paths.database_dir,
        app_paths.backups_dir,
        app_paths.exports_dir,
        app_paths.logs_dir,
    ):
        assert directory.is_dir()
    assert "მონაცემები" in str(app_paths.data_dir)
