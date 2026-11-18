"""Performance headroom and slow-disk responsiveness on SYNTHETIC banks (Phase 9b, campaign J).

    QT_QPA_PLATFORM=offscreen python tools/perf_headroom.py --sizes 10000 30000 --output perf.json
    QT_QPA_PLATFORM=offscreen python tools/perf_headroom.py --sizes 10000 --slow-fsync-ms 150

For every size the same operations are timed through the real services and the real main window:
start-up (storage + window), next question, quick filters, search, counts, open editor, save
revision, approve-and-next, create / verify a backup, restore, full export (TXT + DOCX of every
subject). 10,000 questions are imported through the real importer; larger banks (beyond the
subjects' target counts, which the importer rightly refuses) are inserted through the repository,
so every constraint and trigger still applies.

``--slow-fsync-ms N`` re-runs the measurement in a child process with a tiny ``LD_PRELOAD`` shim that
delays every ``fsync``/``fdatasync`` by N ms — including SQLite's own — and reports how late a 50 ms
GUI heartbeat fired meanwhile (Linux only; it needs ``cc``). Numbers describe this machine only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SHIM = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <unistd.h>
static int delay_us(void) {
    const char *v = getenv("SLOW_FSYNC_US");
    return v ? atoi(v) : 0;
}
int fsync(int fd) {
    static int (*real)(int);
    if (!real) real = dlsym(RTLD_NEXT, "fsync");
    usleep(delay_us());
    return real(fd);
}
int fdatasync(int fd) {
    static int (*real)(int);
    if (!real) real = dlsym(RTLD_NEXT, "fdatasync");
    usleep(delay_us());
    return real(fd);
}
"""

# Budgets for 10,000 questions (the release target), as in tests/test_phase8_performance.py.
BUDGETS_10K_MS = {
    "next question (median of 50)": 60,
    "counts per subject": 150,
    "open editor": 400,
    "save revision": 600,
    "approve and next (median of 10)": 600,
    "create backup": 5_000,
    "verify one backup": 3_000,
    "restore": 15_000,
}


def build(root: Path, size: int):  # noqa: ANN201
    from bank import make_question, seed
    from synthetic import write_full_bank

    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.db.connection import open_database
    from vascqbank_desktop.domain.subjects import SUBJECTS
    from vascqbank_desktop.services import import_service as svc
    from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore
    from vascqbank_desktop.services.storage import initialize_storage

    paths = AppPaths(data_dir=root / f"data-{size}")
    if size == 10_000:
        source = root / "source-10000"
        write_full_bank(source)
        initialize_storage(paths)
        svc.commit_import(paths, svc.dry_run(paths, [source]))
    else:
        factor = -(-size // 10_000)
        seed(
            paths,
            (make_question(s.code, n) for s in SUBJECTS for n in range(1, s.target_count * factor + 1)),
        )
    conn = open_database(paths.database_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE questions SET status = 'APPROVED', approved_revision_id = current_revision_id, "
            "approved_at = '2026-11-18T00:00:00+00:00', lock_version = 1 WHERE id % 7 = 0"
        )
        conn.execute("COMMIT")
        total = conn.execute("SELECT count(*) FROM questions").fetchone()[0]
    finally:
        conn.close()
    ReviewerProfile(SettingsStore(paths.settings_file)).set_name("რამაზ აფხაძე")
    return paths, total


def measure(paths, heartbeat: bool) -> dict:  # noqa: ANN001, PLR0915
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from vascqbank_desktop.domain.subjects import SUBJECTS
    from vascqbank_desktop.importexport.docx_export import DocxEdition
    from vascqbank_desktop.services import restore_service
    from vascqbank_desktop.services.backup_service import create_manual_backup, verify_entry
    from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService
    from vascqbank_desktop.services.question_stats import bank_counts
    from vascqbank_desktop.services.storage import initialize_storage
    from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts
    from vascqbank_desktop.ui.main_window import MainWindow
    from vascqbank_desktop.ui.sidebar import PAGE_CATALOG
    from vascqbank_desktop.ui.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    register_bundled_fonts()
    app.setFont(build_ui_font())
    apply_theme(app)
    results: dict[str, float] = {}
    gaps: list[float] = []
    last = [time.perf_counter()]

    def beat() -> None:
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000 - 50)
        last[0] = now

    def timed(name: str, fn, repeat: int = 1) -> object:  # noqa: ANN001
        samples, value = [], None
        for _ in range(repeat):
            started = time.perf_counter()
            value = fn()
            samples.append((time.perf_counter() - started) * 1000)
        results[name] = round(statistics.median(samples), 1)
        return value

    def settle(view) -> None:  # noqa: ANN001
        while view.is_busy():
            QTest.qWait(5)

    window = timed(
        "start-up (storage + window)",
        lambda: MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=200),
    )
    window.resize(1280, 720)
    window.show()
    window.wait_for_background_work()
    timer = QTimer()
    timer.timeout.connect(beat)
    if heartbeat:
        timer.start(50)
    view = window.catalog_view
    window.show_page(PAGE_CATALOG)
    settle(view)
    timed("next question (median of 50)", view.next_question, repeat=50)
    for index in range(6):
        timed(f"quick filter {index + 1}", lambda i=index: (view.set_quick_filter(i), settle(view)))
    view.set_quick_filter(0)
    settle(view)

    def search() -> None:
        view.set_search_text("ვარიანტი ბ")
        view._commit_search()
        settle(view)
        view.set_search_text("")
        view._commit_search()
        settle(view)

    timed("search (without debounce) and clear", search)
    timed("counts per subject", lambda: bank_counts(paths), repeat=5)
    view.set_quick_filter(1)
    settle(view)
    timed("open editor", view.start_editing)
    view.editing.editor.stem_edit.setPlainText("შესწორება სიჩქარის გასაზომად")
    QTest.qWait(400)  # the autosaver writes a draft meanwhile
    timed("save revision", view.editing.editor.save)
    view.leave_editing()
    timed("approve and next (median of 10)", view.workflow.approve_and_next, repeat=10)
    entry = timed("create backup", lambda: create_manual_backup(paths))
    timed("verify one backup", lambda: verify_entry(entry))
    summary = restore_service.inspect_backup(entry.path)
    window.prepare_for_restore()
    restored = timed(
        "restore", lambda: restore_service.restore_backup(paths, entry.path, expected_sha256=summary.sha256)
    )
    timed("window refresh after restore", lambda: window.apply_restore(restored))
    destination = Path(tempfile.mkdtemp(prefix="vqb-perf-export-"))
    timed(
        "full export (TXT + DOCX, every subject)",
        lambda: ExportService(paths).run(
            ExportRequest(
                subjects=tuple(s.code for s in SUBJECTS),
                formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
                destination=destination,
                editions=(DocxEdition.REVIEW,),
            )
        ),
    )
    shutil.rmtree(destination, ignore_errors=True)
    timer.stop()
    window.close()
    if heartbeat and gaps:
        ordered = sorted(gaps)
        results["heartbeat lateness p50 (ms)"] = round(ordered[len(ordered) // 2], 1)
        results["heartbeat lateness p99 (ms)"] = round(ordered[int(len(ordered) * 0.99)], 1)
        results["heartbeat lateness max (ms)"] = round(ordered[-1], 1)
    return results


def child_main(args: argparse.Namespace) -> int:
    root = Path(tempfile.mkdtemp(prefix="vqb-perf-"))
    report: dict[str, object] = {"sizes": {}}
    for size in args.sizes:
        started = time.perf_counter()
        paths, total = build(root, size)
        built = round(time.perf_counter() - started, 1)
        print(f"[perf] {total} questions built in {built} s", flush=True)
        results = measure(paths, heartbeat=True)
        results["questions"] = total
        results["build (s)"] = built
        report["sizes"][str(total)] = results  # type: ignore[index]
        print(json.dumps(results, ensure_ascii=False, indent=1), flush=True)
    shutil.rmtree(root, ignore_errors=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sizes", type=int, nargs="+", default=[10_000, 30_000])
    parser.add_argument("--slow-fsync-ms", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("perf-headroom.json"))
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child or not args.slow_fsync_ms:
        return child_main(args)
    if not sys.platform.startswith("linux") or shutil.which("cc") is None:
        print("the slow-disk simulation needs Linux and a C compiler (cc)", file=sys.stderr)
        return 2
    build_dir = Path(tempfile.mkdtemp(prefix="vqb-slowfsync-"))
    (build_dir / "slowfsync.c").write_text(SHIM, encoding="utf-8")
    library = build_dir / "slowfsync.so"
    source = build_dir / "slowfsync.c"
    subprocess.run(["cc", "-shared", "-fPIC", "-O2", "-o", str(library), str(source), "-ldl"], check=True)
    env = {**os.environ, "LD_PRELOAD": str(library), "SLOW_FSYNC_US": str(args.slow_fsync_ms * 1000)}
    command = [
        sys.executable,
        __file__,
        "--child",
        "--output",
        str(args.output),
        "--sizes",
        *map(str, args.sizes),
    ]
    return subprocess.run(command, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
