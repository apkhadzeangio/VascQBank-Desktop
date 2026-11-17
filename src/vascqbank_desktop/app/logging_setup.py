"""Logging configuration.

A rotating UTF-8 log file lives in ``<data dir>/logs``. A console handler is
added only when a console exists: in a windowed Windows build (``pythonw`` or a
PyInstaller ``--windowed`` EXE) ``sys.stderr`` is ``None``, and writing to it
would crash the application.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

LOG_FILENAME = "vascqbank-desktop.log"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(logs_dir: Path | None, level: int = logging.INFO) -> Path | None:
    """Configure the root logger; return the log file path if one is in use."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT)
    log_file: Path | None = None

    if logs_dir is not None:
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_file = logs_dir / LOG_FILENAME
            file_handler = RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            log_file = None

    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        console.setLevel(logging.WARNING)
        root.addHandler(console)

    if not root.handlers:
        root.addHandler(logging.NullHandler())
    return log_file


def _log_uncaught(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logging.getLogger("vascqbank_desktop").critical(
        "Uncaught exception", exc_info=(exc_type, exc, tb)
    )


def install_excepthook() -> None:
    """Route uncaught exceptions (including ones raised in Qt slots) to the log."""
    sys.excepthook = _log_uncaught
