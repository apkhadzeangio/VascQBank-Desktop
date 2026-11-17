"""Application entry point.

Usage::

    python -m vascqbank_desktop                 # start the desktop application
    python -m vascqbank_desktop --self-check    # prepare storage, print a report, no GUI
    python -m vascqbank_desktop --version
    python -m vascqbank_desktop --data-dir PATH # use another data folder

Qt is imported only when the GUI starts, so ``--self-check`` and ``--version``
also work on machines without a display.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sqlite3
import sys
from collections.abc import Sequence

from vascqbank_desktop import __version__
from vascqbank_desktop.app.logging_setup import configure_logging, install_excepthook
from vascqbank_desktop.config.constants import (
    APP_ID,
    APP_SLUG,
    WINDOWS_APP_USER_MODEL_ID,
)
from vascqbank_desktop.config.paths import AppPaths, resolve_app_paths
from vascqbank_desktop.db.errors import DatabaseError
from vascqbank_desktop.services.storage import StorageStatus, initialize_storage

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_INTEGRITY_PROBLEM = 1
EXIT_STORAGE_ERROR = 2

STORAGE_ERRORS: tuple[type[BaseException], ...] = (DatabaseError, OSError, sqlite3.Error)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vascqbank-desktop",
        description="VascQBank Review Desktop — offline review of Georgian vascular surgery exam questions.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        help="data folder to use instead of the per-user default",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="prepare local storage, print a diagnostic report and exit without opening a window",
    )
    # Automated smoke tests: start the real GUI and quit after N milliseconds.
    parser.add_argument("--exit-after-ms", type=int, default=0, help=argparse.SUPPRESS)
    return parser


def format_self_check(status: StorageStatus) -> str:
    lines = [
        f"VascQBank Review Desktop {__version__}",
        f"data_dir:          {status.data_dir}",
        f"database:          {status.database_path}",
        f"schema_version:    {status.schema_version} (latest {status.latest_schema_version})",
        f"created_new:       {status.created_new}",
        f"applied:           {', '.join(map(str, status.applied_migrations)) or '-'}",
        f"database_id:       {status.database_id}",
        f"integrity_ok:      {status.integrity_ok}",
        f"sqlite_version:    {status.sqlite_version}",
        f"fts5_trigram:      {status.fts5_trigram}",
    ]
    return "\n".join(lines)


def _safe_console() -> None:
    """Never crash on printing Georgian paths to a legacy Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="backslashreplace")


def run_self_check(paths: AppPaths) -> int:
    _safe_console()
    try:
        status = initialize_storage(paths)
    except STORAGE_ERRORS as exc:
        log.exception("Self-check failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_STORAGE_ERROR
    print(format_self_check(status))
    return EXIT_OK if status.integrity_ok else EXIT_INTEGRITY_PROBLEM


def _set_windows_app_id() -> None:
    """Group the taskbar button under our icon instead of python.exe."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def run_gui(paths: AppPaths, argv: Sequence[str], exit_after_ms: int = 0) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from vascqbank_desktop import resources
    from vascqbank_desktop.domain.subjects import SUBJECTS
    from vascqbank_desktop.ui import strings
    from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts
    from vascqbank_desktop.ui.main_window import MainWindow
    from vascqbank_desktop.ui.theme import apply_theme

    _set_windows_app_id()
    QApplication.setApplicationName(APP_ID)
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName(APP_SLUG)

    app = QApplication.instance() or QApplication(list(argv))
    families = register_bundled_fonts()
    log.info("Bundled font families: %s", ", ".join(families) or "none")
    app.setFont(build_ui_font())
    apply_theme(app)

    icon = QIcon(str(resources.resource_path("icons", "app.svg")))
    app.setWindowIcon(icon)

    try:
        status = initialize_storage(paths)
    except STORAGE_ERRORS as exc:
        log.exception("Storage initialisation failed")
        box = QMessageBox(QMessageBox.Icon.Critical, strings.STARTUP_ERROR_TITLE, strings.STARTUP_ERROR_TEXT)
        box.setDetailedText(f"{type(exc).__name__}: {exc}\n\n{paths.database_file}")
        if exit_after_ms:
            QTimer.singleShot(exit_after_ms, box.reject)
        box.exec()
        return EXIT_STORAGE_ERROR

    window = MainWindow(status, SUBJECTS, app_icon=icon)
    window.show()
    if exit_after_ms > 0:
        QTimer.singleShot(exit_after_ms, app.quit)
    return int(app.exec())


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    paths = resolve_app_paths(args.data_dir)
    log_file = configure_logging(paths.logs_dir)
    install_excepthook()
    log.info("Starting %s %s (data dir: %s, log: %s)", APP_ID, __version__, paths.data_dir, log_file)

    if args.self_check:
        return run_self_check(paths)
    return run_gui(paths, [sys.argv[0], *argv], exit_after_ms=args.exit_after_ms)
