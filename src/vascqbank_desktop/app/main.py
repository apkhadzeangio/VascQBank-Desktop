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
from pathlib import Path

from vascqbank_desktop import __version__
from vascqbank_desktop.app.logging_setup import configure_logging, install_excepthook
from vascqbank_desktop.config.constants import (
    APP_ID,
    APP_SLUG,
    WINDOWS_APP_USER_MODEL_ID,
)
from vascqbank_desktop.config.paths import AppPaths, resolve_app_paths
from vascqbank_desktop.db.errors import DatabaseError
from vascqbank_desktop.services.seed_service import locate_seed
from vascqbank_desktop.services.startup import Evidence, Outcome, StartupResult, prepare_storage
from vascqbank_desktop.services.storage import StorageStatus

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_INTEGRITY_PROBLEM = 1
EXIT_STORAGE_ERROR = 2
EXIT_RECOVERY_NEEDED = 3  # no database, but traces of an earlier one: nothing was created
EXIT_SEED_INVALID = 4  # the bundled seed failed verification: nothing was created

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
    parser.add_argument(
        "--seed-dir",
        metavar="PATH",
        help="folder with seed-manifest.json and the seed database (default: the bundled seed, if any)",
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
        "seed:              "
        + (
            f"{status.seed_version} ({status.seed_kind}, {status.seed_database_id})"
            if status.seed_version
            else "-"
        ),
    ]
    return "\n".join(lines)


def _safe_console() -> None:
    """Never crash on printing Georgian paths to a legacy Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="backslashreplace")


def run_self_check(paths: AppPaths, seed_dir: Path | None = None) -> int:
    _safe_console()
    try:
        result = prepare_storage(paths, seed_dir)
    except STORAGE_ERRORS as exc:
        log.exception("Self-check failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_STORAGE_ERROR
    if result.outcome is Outcome.RECOVERY_NEEDED:
        print("RECOVERY NEEDED: no database, but traces of an earlier one were found; nothing was created:")
        for evidence in result.evidence:
            print(f"  {evidence.kind.value}: {len(evidence.paths)}")
        return EXIT_RECOVERY_NEEDED
    if result.outcome is Outcome.SEED_INVALID:
        print(f"SEED INVALID: {result.seed_problem}; nothing was created", file=sys.stderr)
        return EXIT_SEED_INVALID
    status = result.status
    assert status is not None
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


def install_signal_shutdown(app, window) -> dict[int, object]:  # noqa: ANN001
    """Close the window — which writes any pending draft first — when the program is asked to stop.

    ``kill``, a systemd stop, a closing terminal (SIGHUP) or Ctrl+C would otherwise end the
    process at once and lose up to a second of typing. A refused close (the draft could not be
    written and the reviewer chose to keep the text) simply leaves the program running.
    Returns the handlers that were replaced, so a caller can restore them.
    """
    import signal

    from PySide6.QtCore import QTimer

    def handler(signum: int, _frame: object) -> None:
        log.info("Signal %s received: closing the window", signum)
        window.close()

    previous: dict[int, object] = {}
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            previous[int(number)] = signal.signal(number, handler)
        except (ValueError, OSError):  # not the main thread, or not supported on this platform
            log.debug("Could not install a handler for %s", name)
    # Python runs signal handlers only while the interpreter is executing: wake it regularly.
    pump = QTimer(app)
    pump.setInterval(250)
    pump.timeout.connect(lambda: None)
    pump.start()
    return previous


def ask_recovery(evidence: Sequence[Evidence], data_dir: Path, exit_after_ms: int = 0) -> str:
    """The Georgian recovery dialog: ``"open"``, ``"new"`` (confirmed twice) or ``"exit"``."""
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox

    from vascqbank_desktop.ui import strings

    lines = [strings.RECOVERY_TEXT, "", strings.RECOVERY_FOUND]
    lines += [f"• {strings.RECOVERY_EVIDENCE[e.kind.value].format(count=len(e.paths))}" for e in evidence]
    lines += ["", strings.RECOVERY_HOWTO]
    box = QMessageBox(QMessageBox.Icon.Warning, strings.RECOVERY_TITLE, "\n".join(lines))
    box.setDetailedText("\n".join(str(p) for e in evidence for p in e.paths))
    open_button = box.addButton(strings.RECOVERY_OPEN_FOLDER, QMessageBox.ButtonRole.AcceptRole)
    new_button = box.addButton(strings.RECOVERY_NEW, QMessageBox.ButtonRole.DestructiveRole)
    exit_button = box.addButton(strings.RECOVERY_EXIT, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(exit_button)
    if exit_after_ms:
        QTimer.singleShot(exit_after_ms, box.reject)
    box.exec()
    clicked = box.clickedButton()
    if clicked is open_button:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir)))
        return "open"
    if clicked is new_button:
        confirm = QMessageBox.question(
            None,
            strings.RECOVERY_CONFIRM_TITLE,
            strings.RECOVERY_CONFIRM_TEXT,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return "new" if confirm == QMessageBox.StandardButton.Yes else "exit"
    return "exit"


def startup(paths: AppPaths, seed_dir: Path | None, exit_after_ms: int = 0) -> StartupResult | int:
    """Prepare storage with Georgian feedback; an ``int`` is an exit code (nothing was created)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

    from vascqbank_desktop.ui import strings

    splash = None
    if seed_dir is not None and not paths.database_file.exists():
        splash = QLabel(strings.STARTUP_PREPARING)
        splash.setContentsMargins(28, 20, 28, 20)
        splash.show()
        QApplication.processEvents()
    try:
        result = prepare_storage(paths, seed_dir)
        if result.outcome is Outcome.RECOVERY_NEEDED:
            if splash is not None:
                splash.close()
            if ask_recovery(result.evidence, paths.data_dir, exit_after_ms) != "new":
                return EXIT_RECOVERY_NEEDED
            result = prepare_storage(paths, seed_dir, allow_new_database=True)
    finally:
        if splash is not None:
            splash.close()
    if result.outcome is Outcome.SEED_INVALID:
        box = QMessageBox(QMessageBox.Icon.Critical, strings.SEED_INVALID_TITLE, strings.SEED_INVALID_TEXT)
        box.setDetailedText(f"{result.seed_problem}\n\n{seed_dir}")
        if exit_after_ms:
            QTimer.singleShot(exit_after_ms, box.reject)
        box.exec()
        return EXIT_SEED_INVALID
    return result


def run_gui(
    paths: AppPaths, argv: Sequence[str], exit_after_ms: int = 0, seed_dir: Path | None = None
) -> int:
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
        prepared = startup(paths, seed_dir, exit_after_ms)
        if isinstance(prepared, int):
            return prepared
        status = prepared.status
        assert status is not None
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
    install_signal_shutdown(app, window)
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

    seed_dir = locate_seed(args.seed_dir)
    log.info("Seed: %s", seed_dir or "none (manual import)")
    if args.self_check:
        return run_self_check(paths, seed_dir)
    return run_gui(paths, [sys.argv[0], *argv], exit_after_ms=args.exit_after_ms, seed_dir=seed_dir)
