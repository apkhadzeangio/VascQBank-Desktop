"""Seeded random "monkey" over the REAL main window, on a SYNTHETIC bank (Phase 9b, campaign I).

    QT_QPA_PLATFORM=offscreen python tools/gui_monkey.py --minutes 30 --seed 7 --output monkey.json

Unlike ``tools/soak_test.py`` (which calls the controllers' own methods), the monkey acts like an
impatient person: it sends real key events for every documented shortcut and some random keys,
clicks any visible, enabled button of the current page, types Georgian text into whatever has the
focus, switches pages, resizes the window, and answers every dialog that appears with a random
choice (message boxes: a random button; reason dialogs: a random reason or cancel; the restore
dialog: sometimes it confirms a restore; file dialogs: always cancelled — the monkey never reaches
outside its temporary folder). "Quit" and "open folder" are never pressed.

Every 20 s and at the end it checks the database: ``integrity_check``, ``foreign_key_check``,
approval bound to the approved revision and to the latest APPROVE event, contiguous revisions, no
approved question without its revision, and the window's counts equal to a recount. Any Python
exception (also inside Qt slots), any ERROR log record and any Qt warning about deleted objects or
running threads is a failure. The JSON report lists the actions, dialogs, checks and failures; the
exit status is 0 only without failures. Nothing here talks to the network.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sqlite3
import sys
import tempfile
import time
import traceback
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from synthetic import write_pair  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import import_service  # noqa: E402
from vascqbank_desktop.services.backup_service import create_manual_backup  # noqa: E402
from vascqbank_desktop.services.question_stats import bank_counts  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402

REVIEWER = "მაიმუნი რეცენზენტი"
TYPED = ("ქართული ტექსტი ", "ᲛᲗᲐᲕᲠᲣᲚᲘ ", "10 მგ ", "EVAR ", "‍", "😀", "ა́", "  ", "\n")
NEVER_PRESS = re.compile(r"საქაღალდ|გასვლა|დახურვა პროგრამ")  # open-folder / quit buttons
BAD_QT = re.compile(
    r"Signal source has been deleted|Internal C\+\+ object|QThread: Destroyed|already deleted"
    r"|QObject::killTimer",
    re.I,
)
SHORTCUTS = [  # (key, modifiers) as text, sent as real key events
    ("Right", "Alt"), ("Left", "Alt"), ("Return", "Ctrl"), ("Return", "Ctrl+Shift"),
    ("E", "Ctrl"), ("S", "Ctrl"), ("Escape", ""), ("H", "Ctrl"), ("R", "Ctrl+Shift"),
    ("O", "Ctrl+Shift"), ("I", "Ctrl+Shift"), ("B", "Ctrl+Shift"), ("F", "Ctrl"), ("G", "Ctrl"),
    ("L", "Ctrl"), ("1", "Alt"), ("2", "Alt"), ("3", "Alt"), ("4", "Alt"), ("5", "Alt"), ("6", "Alt"),
    ("1", "Ctrl"), ("2", "Ctrl"), ("3", "Ctrl"), ("4", "Ctrl"), ("5", "Ctrl"), ("F1", ""), ("Tab", ""),
    ("Down", ""), ("Up", ""), ("PageDown", ""), ("Space", ""), ("Return", ""), ("I", "Ctrl"),
    ("E", "Ctrl+Shift"),
]  # fmt: skip


def build_bank(workdir: Path, per_subject: int) -> AppPaths:
    source = workdir / "source"
    for subject in SUBJECTS[:4]:
        write_pair(source, subject.code, range(1, per_subject + 1))
    paths = AppPaths(data_dir=workdir / "მონაცემები")
    initialize_storage(paths)
    import_service.commit_import(paths, import_service.dry_run(paths, [source]))
    ReviewerProfile(SettingsStore(paths.settings_file)).set_name(REVIEWER)
    create_manual_backup(paths)
    return paths


def check_database(paths: AppPaths) -> list[str]:
    problems: list[str] = []
    conn = sqlite3.connect(paths.database_file)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("integrity_check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            problems.append("foreign_key_check failed")
        bad = conn.execute(
            "SELECT q.question_uid FROM questions q WHERE q.status = 'APPROVED' AND ("
            " q.approved_revision_id IS NULL OR q.approved_revision_id <> ("
            "  SELECT e.revision_id FROM review_events e WHERE e.question_id = q.id AND e.action = 'APPROVE'"
            "  ORDER BY e.id DESC LIMIT 1))"
        ).fetchall()
        if bad:
            problems.append(f"approval not bound to its APPROVE event: {bad[:5]}")
        gaps = conn.execute(
            "SELECT question_id FROM question_revisions GROUP BY question_id"
            " HAVING max(revision_no) <> count(*) OR min(revision_no) <> 1"
        ).fetchall()
        if gaps:
            problems.append(f"revision numbers not contiguous: {gaps[:5]}")
        current = conn.execute(
            "SELECT q.question_uid FROM questions q WHERE q.current_revision_id <> ("
            " SELECT r.id FROM question_revisions r WHERE r.question_id = q.id"
            " ORDER BY r.revision_no DESC LIMIT 1)"
        ).fetchall()
        if current:
            problems.append(f"current revision is not the newest: {current[:5]}")
        recount = dict(
            ((s, st), n)
            for s, st, n in conn.execute("SELECT subject_code, status, count(*) FROM questions GROUP BY 1, 2")
        )
    finally:
        conn.close()
    counts = bank_counts(paths)
    for code, subject in counts.subjects.items():
        for status, number in subject.by_status.items():
            if recount.get((code, status.value), 0) != number:
                problems.append(f"count mismatch {code}/{status.value}")
    return problems


def run(args: argparse.Namespace) -> dict:  # noqa: PLR0915 - one linear script
    from PySide6.QtCore import QThreadPool, QTimer, qInstallMessageHandler
    from PySide6.QtGui import QKeySequence
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QAbstractButton,
        QApplication,
        QCheckBox,
        QDialog,
        QFileDialog,
        QInputDialog,
        QLineEdit,
        QMessageBox,
        QPlainTextEdit,
        QTextEdit,
    )

    from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts
    from vascqbank_desktop.ui.localization import install_translations
    from vascqbank_desktop.ui.main_window import MainWindow
    from vascqbank_desktop.ui.sidebar import PAGE_BACKUPS, PAGE_CATALOG, PAGE_EXPORT, PAGE_HOME, PAGE_REVIEWER
    from vascqbank_desktop.ui.theme import apply_theme
    from vascqbank_desktop.ui.workflow_widgets import ReasonDialog

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="vqb-monkey-"))
    paths = build_bank(workdir, args.per_subject)
    rng = random.Random(args.seed)
    failures: list[str] = []
    qt_messages: list[str] = []
    counts: Counter[str] = Counter()
    dialogs: Counter[str] = Counter()

    class ErrorLog(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR:
                failures.append(f"log {record.name}: {record.getMessage()}"[:400])

    logging.getLogger().addHandler(ErrorLog(logging.ERROR))
    sys.excepthook = lambda kind, value, tb: failures.append(
        "".join(traceback.format_exception(kind, value, tb))[-1500:]
    )

    def on_qt_message(_mode, _context, message: str) -> None:  # noqa: ANN001
        if BAD_QT.search(message):
            qt_messages.append(message[:300])

    qInstallMessageHandler(on_qt_message)
    app = QApplication.instance() or QApplication([])
    register_bundled_fonts()
    app.setFont(build_ui_font())
    apply_theme(app)
    install_translations(app)

    # File dialogs would leave the temporary folder: always "cancelled".
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")  # type: ignore[method-assign]
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))  # type: ignore[method-assign]
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))  # type: ignore[method-assign]
    QFileDialog.exec = lambda self: 0  # type: ignore[method-assign]
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))  # type: ignore[method-assign]
    QInputDialog.getText = staticmethod(lambda *a, **k: ("მაიმუნის მიზეზი", rng.random() < 0.7))  # type: ignore[method-assign]

    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=200, auto_backup=True)
    window.resize(1280, 720)
    window.show()
    window.activateWindow()

    def answer_dialog() -> None:
        widget = app.activeModalWidget()
        if widget is None or not widget.isVisible():
            return
        name = type(widget).__name__
        dialogs[name] += 1
        if isinstance(widget, QMessageBox):
            buttons = [b for b in widget.buttons() if b.isEnabled() and not NEVER_PRESS.search(b.text())]
            if buttons:
                rng.choice(buttons).click()
            else:
                widget.reject()
            return
        if isinstance(widget, ReasonDialog):
            if rng.random() < 0.6:
                widget.edit.setPlainText(rng.choice(["ტერმინი არასწორია", "", "x" * 600]))
                widget.confirm_button.click()
                if widget.isVisible():
                    widget.reject()
            else:
                widget.reject()
            return
        if isinstance(widget, QDialog):
            # e.g. the restore, import, compare or shortcuts dialog: a few random clicks, then close
            for _ in range(rng.randint(0, 4)):
                if not widget.isVisible():
                    return
                boxes = [c for c in widget.findChildren(QCheckBox) if c.isVisible() and c.isEnabled()]
                buttons = [
                    b
                    for b in widget.findChildren(QAbstractButton)
                    if b.isVisible()
                    and b.isEnabled()
                    and not isinstance(b, QCheckBox)
                    and not NEVER_PRESS.search(b.text())
                ]
                choice = rng.random()
                if boxes and choice < 0.3:
                    rng.choice(boxes).click()
                elif buttons:
                    rng.choice(buttons).click()
                QTest.qWait(rng.randint(20, 300))
            if widget.isVisible():
                widget.reject()

    watchdog = QTimer()
    watchdog.timeout.connect(answer_dialog)
    watchdog.start(120)

    def key_event() -> None:
        key, mods = rng.choice(SHORTCUTS)
        text = f"{mods}+{key}" if mods else key
        sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
        combo = sequence[0]
        target = QApplication.focusWidget() or window
        QTest.keyClick(target, combo.key(), combo.keyboardModifiers())
        counts[f"key {mods}+{key}" if mods else f"key {key}"] += 1

    def click_button() -> None:
        page = window.pages.currentWidget()
        buttons = [
            b
            for b in page.findChildren(QAbstractButton)
            if b.isVisible() and b.isEnabled() and not NEVER_PRESS.search(b.text() or b.toolTip())
        ]
        if buttons:
            button = rng.choice(buttons)
            counts[f"click {type(page).__name__}"] += 1
            button.click()

    def type_text() -> None:
        target = QApplication.focusWidget()
        if isinstance(target, QPlainTextEdit | QTextEdit):
            target.insertPlainText(rng.choice(TYPED))
            counts["type"] += 1
        elif isinstance(target, QLineEdit):
            target.insert(rng.choice(["ALI-0003", "ვარიანტი", "12", "ᲐᲑᲒ", ""]))
            counts["type line"] += 1

    def switch_page() -> None:
        pages = [PAGE_HOME, PAGE_CATALOG, PAGE_CATALOG, PAGE_BACKUPS, PAGE_REVIEWER, PAGE_EXPORT]
        window.show_page(rng.choice(pages))
        counts["page"] += 1

    def resize() -> None:
        window.resize(*rng.choice([(1280, 720), (1366, 768), (1920, 1080), (1000, 650)]))
        counts["resize"] += 1

    actions = [(key_event, 45), (click_button, 25), (type_text, 15), (switch_page, 8), (resize, 2)]
    functions = [a for a, _ in actions]
    weights = [w for _, w in actions]
    checks: list[dict] = []
    started = time.monotonic()
    deadline = started + args.minutes * 60
    next_check = started + 20
    steps = 0
    while time.monotonic() < deadline:
        try:
            rng.choices(functions, weights)[0]()
        except Exception:  # noqa: BLE001 - recorded, the run goes on
            failures.append(traceback.format_exc()[-1500:])
        steps += 1
        QTest.qWait(rng.choice([0, 5, 20, 60]))
        if time.monotonic() >= next_check:
            next_check = time.monotonic() + 20
            problems = check_database(paths)
            checks.append({"t": round(time.monotonic() - started), "steps": steps, "problems": problems})
            failures += [f"check: {p}" for p in problems]
            elapsed = round(time.monotonic() - started)
            print(f"[monkey] {elapsed} s, {steps} steps, problems: {problems}", flush=True)
    window.close()
    drained = QThreadPool.globalInstance().waitForDone(60_000)
    final = check_database(paths)
    failures += [f"final check: {p}" for p in final]
    failures += [f"qt: {m}" for m in qt_messages]
    if not drained:
        failures.append("the thread pool did not drain after closing the window")
    return {
        "seed": args.seed,
        "minutes": args.minutes,
        "steps": steps,
        "actions": dict(counts.most_common()),
        "dialogs": dict(dialogs),
        "checks": len(checks),
        "final_check": final,
        "failures": failures,
        "passed": not failures,
        "workdir": str(workdir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--per-subject", type=int, default=60)
    parser.add_argument("--output", type=Path, default=Path("monkey-report.json"))
    parser.add_argument("--workdir")
    args = parser.parse_args(argv)
    report = run(args)
    sys.excepthook = sys.__excepthook__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("steps", "checks", "dialogs", "passed")}, ensure_ascii=False))
    for failure in report["failures"][:15]:
        print("FAILURE:", failure.splitlines()[-1] if failure else failure)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
