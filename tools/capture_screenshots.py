"""Render the real application window with SYNTHETIC data and save PNG screenshots.

    python tools/capture_screenshots.py OUTPUT_DIR [--questions 10000]

Builds a throw-away data folder (a temporary directory) holding the full synthetic
bank — all 12 subjects, 10,000 questions of artificial filler text, no real medical
content — gives some of them review statuses, and photographs the main pages on
Qt's offscreen platform. Nothing outside the temporary folder and OUTPUT_DIR is touched.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from bank import set_status  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from synthetic import explanation_block, question_block, write_full_bank, write_pair  # noqa: E402

from vascqbank_desktop import resources  # noqa: E402
from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import import_service  # noqa: E402
from vascqbank_desktop.services.backup_service import create_manual_backup  # noqa: E402
from vascqbank_desktop.services.settings_service import SavedSession, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.theme import apply_theme  # noqa: E402

SENTENCE = (
    "ეს ხელოვნური სატესტო ტექსტია, რომელიც მხოლოდ აჩვენებს, როგორ ეკეცება გრძელი კლინიკური "
    "სცენარი ფანჯრის სიგანეზე; მას რეალური სამედიცინო შინაარსი არ აქვს."
)


def pump(ms: int = 50) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    QCoreApplication.processEvents()


def wait_until(predicate, timeout_ms: int = 20_000) -> None:  # noqa: ANN001
    waited = 0
    while not predicate() and waited < timeout_ms:
        pump(25)
        waited += 25


def _make_first_ali_question_long(source: Path) -> None:
    """Swap ALI-0001 for a long, multi-paragraph synthetic question to show natural wrapping."""
    long_stem = "\n\n".join([SENTENCE] * 3 + ["რომელი დებულებაა სწორი ამ სინთეზურ მაგალითში?"])
    new_question = (
        f"1. {long_stem}\n"
        f"ა) გრძელი ვარიანტი: {SENTENCE}\n"
        "ბ) მოკლე ვარიანტი\n"
        f"*გ) სწორი, ხელოვნურად შედგენილი პასუხი: {SENTENCE}\n"
        "დ) ვარიანტი დ\n"
        "ე) ვარიანტი ე"
    )
    new_explanation = "1. გ) " + "\n\n".join([SENTENCE] * 3)
    for name, old, new in (
        ("ALI_questions.txt", question_block("ALI", 1), new_question),
        ("ALI_explanations.txt", explanation_block("ALI", 1), new_explanation),
    ):
        path = source / name
        text = path.read_text(encoding="utf-8")
        assert old in text, name
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def build_data(root: Path, total: int) -> AppPaths:
    paths = AppPaths(data_dir=root / "data")
    source = root / "source"
    if total >= 10_000:
        write_full_bank(source)
    else:  # a smaller bank for quick runs: spread over all subjects
        per = max(1, total // len(SUBJECTS))
        for subject in SUBJECTS:
            write_pair(source, subject.code, range(1, per + 1))
    _make_first_ali_question_long(source)
    initialize_storage(paths)
    report = import_service.dry_run(paths, [source])
    import_service.commit_import(paths, report)
    return paths


def give_statuses(paths: AppPaths) -> None:
    """Spread the four review statuses over the first few hundred questions."""
    for i in range(1, 400):
        uid = f"{SUBJECTS[i % len(SUBJECTS)].code}-{i:04d}"
        if i % 7 == 0:
            set_status(paths, uid, ReviewStatus.IN_REVIEW)
        elif i % 11 == 0:
            set_status(paths, uid, ReviewStatus.APPROVED)
        elif i % 29 == 0:
            set_status(paths, uid, ReviewStatus.REJECTED)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--questions", type=int, default=10_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vqb-shots-") as tmp:
        paths = build_data(Path(tmp), args.questions)
        give_statuses(paths)
        store = SettingsStore(paths.settings_file)
        store.set_reviewer_name("რამაზი აფხაძე")
        store.set_session(SavedSession("ALI", None, "ALI-0001"))
        create_manual_backup(paths)

        app = QApplication.instance() or QApplication(sys.argv[:1])
        register_bundled_fonts()
        app.setFont(build_ui_font())
        apply_theme(app)
        icon = QIcon(str(resources.resource_path("icons", "app.svg")))
        status = initialize_storage(paths)
        window = MainWindow(status, SUBJECTS, app_icon=icon, settings_store=store)
        window.resize(1360, 860)
        window.show()
        window.wait_for_background_work()
        pump(300)

        def shot(name: str) -> None:
            pump(250)
            path = args.output / name
            window.grab().save(str(path))
            print("saved", path)

        window.show_page("home")
        shot("01-home-dashboard.png")

        window.show_page("catalog")
        wait_until(lambda: window.catalog_view.current_uid == "ALI-0001")
        shot("02-catalog-long-question.png")

        window.resize(1360, 1000)
        window.catalog_view.go_to("ALI-0007")
        pump(100)
        shot("02b-catalog-question-with-correct-answer.png")
        window.resize(1360, 860)

        window.catalog_view.set_search_text("ვარიანტი ბ")
        wait_until(lambda: not window.catalog_view.is_busy())
        shot("03-search-results.png")

        window.catalog_view.set_search_text("")
        window.catalog_view.set_status(ReviewStatus.IN_REVIEW)
        window.open_subject("PAD_CLTI")
        pump(100)
        window.catalog_view.go_to("PAD_CLTI-0042")
        pump(100)
        shot("04-filtered-subject.png")

        window.show_page("backups")
        wait_until(lambda: not window.backup_view.is_busy())
        shot("05-backups.png")

        window.show_page("reviewer")
        shot("06-reviewer-profile.png")

        window.close()
        window.wait_for_background_work()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
