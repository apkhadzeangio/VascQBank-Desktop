"""Tests of the real entry point, run as separate processes."""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from vascqbank_desktop import __version__
from vascqbank_desktop.app import logging_setup
from vascqbank_desktop.app.logging_setup import configure_logging
from vascqbank_desktop.db.migrations import LATEST_VERSION


def _run(*args: str, env_extra: dict[str, str] | None = None, timeout: int = 60):
    env = os.environ.copy()
    env.pop("VASCQBANK_DATA_DIR", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "vascqbank_desktop", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        env=env,
    )


def test_version_flag() -> None:
    result = _run("--version")
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_self_check_creates_and_reopens_database(data_dir: Path) -> None:
    first = _run("--self-check", "--data-dir", str(data_dir))
    assert first.returncode == 0, first.stderr
    assert f"schema_version:    {LATEST_VERSION}" in first.stdout
    assert "created_new:       True" in first.stdout
    assert "integrity_ok:      True" in first.stdout
    assert (data_dir / "database" / "vascqbank.sqlite3").is_file()
    assert (data_dir / "logs" / "vascqbank-desktop.log").is_file()

    second = _run("--self-check", "--data-dir", str(data_dir))
    assert second.returncode == 0, second.stderr
    assert "created_new:       False" in second.stdout


def test_self_check_refuses_foreign_database(data_dir: Path) -> None:
    db_file = data_dir / "database" / "vascqbank.sqlite3"
    db_file.parent.mkdir(parents=True)
    raw = sqlite3.connect(db_file)
    raw.execute("CREATE TABLE other_program (x)")
    raw.commit()
    raw.close()
    before = db_file.read_bytes()

    result = _run("--self-check", "--data-dir", str(data_dir))
    assert result.returncode == 2
    assert "not created by VascQBank" in result.stderr
    assert db_file.read_bytes() == before


@pytest.mark.gui
def test_gui_starts_and_exits_cleanly(data_dir: Path) -> None:
    """Launch the real application window and let it quit itself."""
    result = _run(
        "--data-dir",
        str(data_dir),
        "--exit-after-ms",
        "1000",
        env_extra={"QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")},
    )
    if result.returncode != 0 and "Qt platform plugin" in result.stderr:
        pytest.skip(f"Qt platform plugin unavailable: {result.stderr.strip()[:200]}")
    assert result.returncode == 0, result.stderr
    log_text = (data_dir / "logs" / "vascqbank-desktop.log").read_text(encoding="utf-8")
    assert "Noto Sans Georgian" in log_text  # bundled fonts were registered
    assert "Traceback" not in log_text


@pytest.mark.gui
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX termination signals")
def test_the_real_application_closes_gracefully_on_sigterm(data_dir: Path) -> None:
    """``kill`` of the real program (own temporary data folder) ends it through the window's
    normal close path — exit code 0, no traceback — instead of killing it mid-write."""
    import signal
    import time

    env = os.environ.copy()
    env.pop("VASCQBANK_DATA_DIR", None)
    env["QT_QPA_PLATFORM"] = os.environ.get("QT_QPA_PLATFORM", "offscreen")
    log_file = data_dir / "logs" / "vascqbank-desktop.log"
    process = subprocess.Popen(
        [sys.executable, "-m", "vascqbank_desktop", "--data-dir", str(data_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (
            log_file.exists() and "Bundled font families" in log_file.read_text(encoding="utf-8")
        ):
            time.sleep(0.1)
        time.sleep(1.0)  # the window is up and the event loop is running
        process.send_signal(signal.SIGTERM)
        _out, err = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert process.returncode == 0, err
    text = log_file.read_text(encoding="utf-8")
    assert "Signal 15 received: closing the window" in text and "Traceback" not in text


def test_logging_survives_missing_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windowed Windows builds have sys.stderr = None."""
    monkeypatch.setattr(logging_setup.sys, "stderr", None)
    log_file = configure_logging(tmp_path / "logs")
    try:
        logging.getLogger("test").warning("ქართული ჩანაწერი")
        assert log_file is not None
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "ქართული ჩანაწერი" in log_file.read_text(encoding="utf-8")
    finally:
        configure_logging(None)
