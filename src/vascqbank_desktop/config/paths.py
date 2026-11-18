"""Cross-platform data locations.

Defaults
--------
* Windows: ``%LOCALAPPDATA%\\VascQBank-Desktop`` — deliberately *not* the
  Documents folder, which OneDrive often redirects; a live SQLite database in a
  cloud-synced folder risks corruption.
* Linux (incl. CachyOS): ``$XDG_DATA_HOME/vascqbank-desktop`` or
  ``~/.local/share/vascqbank-desktop``.
* macOS (not a target, but harmless): ``~/Library/Application Support/VascQBank-Desktop``.

The ``VASCQBANK_DATA_DIR`` environment variable or the ``--data-dir`` command
line option overrides the default. All paths are :class:`pathlib.Path`, so
Georgian characters and spaces in user names are handled natively.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from vascqbank_desktop.config.constants import (
    APP_ID,
    APP_SLUG,
    DATA_DIR_ENV_VAR,
    DATABASE_FILENAME,
    SEARCH_INDEX_FILENAME,
    SETTINGS_FILENAME,
)


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Every filesystem location the application writes to."""

    data_dir: Path

    @property
    def database_dir(self) -> Path:
        return self.data_dir / "database"

    @property
    def database_file(self) -> Path:
        return self.database_dir / DATABASE_FILENAME

    @property
    def search_index_file(self) -> Path:
        """Derived full-text index; safe to delete, rebuilt automatically."""
        return self.database_dir / SEARCH_INDEX_FILENAME

    @property
    def settings_file(self) -> Path:
        """Reviewer profile and working-session state (per data directory)."""
        return self.data_dir / SETTINGS_FILENAME

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def longest_internal_path(self) -> Path:
        """The longest path SQLite itself will ever be asked to open inside this data folder.

        A backup being written (``…-pre-migration-vNN.sqlite3.partial`` and the ``-journal`` SQLite
        adds while it writes) is the longest; restore temporaries and the WAL files are shorter.
        """
        return self.backups_dir / _LONGEST_SQLITE_NAME

    def ensure_directories(self) -> None:
        """Create all directories (idempotent)."""
        for directory in (
            self.data_dir,
            self.database_dir,
            self.backups_dir,
            self.exports_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


# Phase 9b (D21). SQLite refuses longer paths than its VFS allows: 512 bytes on POSIX (the unix VFS'
# MAX_PATHNAME; a Georgian letter is 3 bytes in UTF-8) and MAX_PATH = 260 UTF-16 units on Windows,
# whatever Python itself supports. Near that limit the database still opened but every backup
# failed, so a data folder whose longest internal path would not fit is refused at start-up.
_LONGEST_SQLITE_NAME = "vascqbank-20000101-000000-000000-pre-migration-v99.sqlite3.partial-journal"
MAX_SQLITE_PATH_BYTES = 500  # POSIX, UTF-8 bytes (SQLite: < 512, with a margin)
MAX_SQLITE_PATH_CHARS = 255  # Windows, UTF-16 code units (SQLite: < 260)


def data_dir_too_long(paths: AppPaths, *, windows: bool | None = None) -> bool:
    """Whether SQLite could not open every file the program creates in this data folder."""
    longest = str(paths.longest_internal_path().resolve())  # SQLite resolves symbolic links too
    if (sys.platform == "win32") if windows is None else windows:
        return len(longest.encode("utf-16-le")) // 2 > MAX_SQLITE_PATH_CHARS
    return len(os.fsencode(longest)) > MAX_SQLITE_PATH_BYTES


def default_data_dir(
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user default data directory for *platform*.

    Parameters are injectable so every branch can be unit-tested on any OS.
    """
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home

    if platform.startswith("win"):
        base = environ.get("LOCALAPPDATA") or environ.get("APPDATA")
        root = Path(base) if base else home / "AppData" / "Local"
        return root / APP_ID
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_ID

    xdg = environ.get("XDG_DATA_HOME", "").strip()
    # The XDG spec says relative values must be ignored.
    root = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return root / APP_SLUG


def resolve_app_paths(
    data_dir_override: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> AppPaths:
    """Resolve paths: explicit override > environment variable > platform default."""
    environ = os.environ if environ is None else environ
    if data_dir_override:
        data_dir = Path(data_dir_override)
    elif environ.get(DATA_DIR_ENV_VAR, "").strip():
        data_dir = Path(environ[DATA_DIR_ENV_VAR].strip())
    else:
        data_dir = default_data_dir(platform=platform, environ=environ, home=home)
    return AppPaths(data_dir=data_dir.expanduser().resolve())
