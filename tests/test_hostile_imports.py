"""Phase 9b, campaign C — crafted TXT import folders.

Huge lines, NUL and other control characters, invalid UTF-8, legacy encodings mixed in, UTF-16,
BOM, CR-only and CRLF line ends, empty files, odd file names, 100,000 questions and the same IDs in
two selected folders. The dry run must never crash, must report every problem in Georgian, must
never mark a question *valid* that the editor and the approval would reject (D16), and a commit
must import exactly the valid questions. Synthetic data in temporary folders only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from synthetic import explanation_block, question_block, write_pair

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.editing import MAX_FIELD_LENGTH, validate_content
from vascqbank_desktop.services import import_service
from vascqbank_desktop.services.import_service import Outcome
from vascqbank_desktop.services.storage import initialize_storage
from vascqbank_desktop.validation.issues import IssueCode

GEORGIAN = range(0x10D0, 0x1100)
GOOD_Q = ("\n\n".join(question_block("ALI", n) for n in range(1, 4)) + "\n").encode("utf-8")
GOOD_E = ("\n\n".join(explanation_block("ALI", n) for n in range(1, 4)) + "\n").encode("utf-8")


@pytest.fixture
def paths(app_paths: AppPaths) -> AppPaths:
    initialize_storage(app_paths)
    return app_paths


def _pair(folder: Path, questions: bytes, explanations: bytes = GOOD_E, code: str = "ALI") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{code}_questions.txt").write_bytes(questions)
    (folder / f"{code}_explanations.txt").write_bytes(explanations)
    return folder


def _georgian(text: str) -> bool:
    return any(ord(c) in GEORGIAN for c in text)


def _questions(paths: AppPaths) -> int:
    conn = sqlite3.connect(paths.database_file)
    try:
        return int(conn.execute("SELECT count(*) FROM questions").fetchone()[0])
    finally:
        conn.close()


def _report_is_sound(report: import_service.DryRunReport) -> None:
    for issue in report.all_issues():
        assert _georgian(issue.message), issue
    for candidate in report.candidates:  # nothing "valid" that the editor/approval would refuse
        if candidate.outcome is Outcome.NEW:
            assert candidate.question is not None
            assert validate_content(candidate.question.content) == [], candidate.uid


# ----------------------------------------------------------------------------------- D16
def test_control_characters_make_a_question_invalid_at_import(paths: AppPaths, tmp_path: Path) -> None:
    folder = _pair(tmp_path / "nul", GOOD_Q.replace(b"\n", b"\x00\n", 1))
    report = import_service.dry_run(paths, [folder])
    _report_is_sound(report)
    bad = [c for c in report.candidates if c.uid == "ALI-0001"][0]
    assert bad.outcome is Outcome.INVALID
    assert IssueCode.FIELD_CONTROL_CHARACTERS in {i.code for i in bad.issues}
    assert report.counts.eligible == 2
    import_service.commit_import(paths, report)
    assert _questions(paths) == 2


def test_a_field_beyond_the_editor_limit_is_invalid_at_import(paths: AppPaths, tmp_path: Path) -> None:
    huge = GOOD_Q + b"x" * (MAX_FIELD_LENGTH + 1) + b"\n"  # continues the last option of ALI-0003
    report = import_service.dry_run(paths, [_pair(tmp_path / "huge", huge)])
    _report_is_sound(report)
    bad = [c for c in report.candidates if c.uid == "ALI-0003"][0]
    assert bad.outcome is Outcome.INVALID and IssueCode.FIELD_TOO_LONG in {i.code for i in bad.issues}
    assert report.counts.eligible == 2


def test_a_20_megabyte_line_is_handled_quickly_and_refused(paths: AppPaths, tmp_path: Path) -> None:
    folder = _pair(tmp_path / "20mb", GOOD_Q + b"x" * (20 * 1024 * 1024) + b"\n")
    started = time.perf_counter()
    report = import_service.dry_run(paths, [folder])
    assert time.perf_counter() - started < 30
    _report_is_sound(report)
    assert report.counts.eligible == 2


# ----------------------------------------------------------------------------------- encodings, line ends
@pytest.mark.parametrize(
    ("name", "questions", "explanations", "eligible"),
    [
        ("invalid-utf8", GOOD_Q[:50] + b"\xff\xfe\xc3" + GOOD_Q[50:], GOOD_E, 0),
        ("cp1251-mixed-in", GOOD_Q + "Привет".encode("cp1251"), GOOD_E, 0),
        ("utf16", GOOD_Q.decode("utf-8").encode("utf-16"), GOOD_E, 0),
        ("bom", b"\xef\xbb\xbf" + GOOD_Q, b"\xef\xbb\xbf" + GOOD_E, 3),
        ("cr-only", GOOD_Q.replace(b"\n", b"\r"), GOOD_E.replace(b"\n", b"\r"), 3),
        ("crlf", GOOD_Q.replace(b"\n", b"\r\n"), GOOD_E.replace(b"\n", b"\r\n"), 3),
        ("empty", b"", b"", 0),
        ("only-whitespace", b" \n\t\n\r\n", b"\n", 0),
        ("truncated-mid-character", GOOD_Q[:-3] + "ი".encode()[:2], GOOD_E, 0),
    ],
)
def test_encodings_and_line_ends(
    paths: AppPaths, tmp_path: Path, name: str, questions: bytes, explanations: bytes, eligible: int
) -> None:
    report = import_service.dry_run(paths, [_pair(tmp_path / name, questions, explanations)])
    _report_is_sound(report)
    assert report.counts.eligible == eligible, [i.message for i in report.all_issues()][:3]
    if eligible == 0:
        assert report.all_issues(), "refused without saying why"


def test_line_end_variants_import_identical_text(paths: AppPaths, tmp_path: Path) -> None:
    texts = []
    for name, sep in (("lf", b"\n"), ("crlf", b"\r\n"), ("cr", b"\r")):
        report = import_service.dry_run(
            paths, [_pair(tmp_path / name, GOOD_Q.replace(b"\n", sep), GOOD_E.replace(b"\n", sep))]
        )
        texts.append([c.question.content for c in report.candidates if c.question])
    assert texts[0] == texts[1] == texts[2]


# ----------------------------------------------------------------------------------- names and volume
def test_odd_file_names_are_skipped_with_a_warning(paths: AppPaths, tmp_path: Path) -> None:
    folder = _pair(tmp_path / "names", GOOD_Q)
    for odd in ("..ALI_questions.txt", "ALI_questions.txt‮.txt", "ALI questions.txt", "ali_questions.TXT"):
        (folder / odd).write_bytes(GOOD_Q)
    report = import_service.dry_run(paths, [folder])
    _report_is_sound(report)
    assert report.counts.eligible == 3
    assert {i.code for i in report.general_issues} <= {IssueCode.FILE_UNRECOGNIZED, IssueCode.DUPLICATE_FILE}


def test_the_same_ids_in_two_selected_folders_never_import_twice(paths: AppPaths, tmp_path: Path) -> None:
    first = _pair(tmp_path / "a", GOOD_Q)
    second = _pair(tmp_path / "b", GOOD_Q)
    report = import_service.dry_run(paths, [first, second])
    _report_is_sound(report)
    if report.counts.eligible:
        import_service.commit_import(paths, report)
    assert _questions(paths) in (0, 3)  # refused as a whole, or each question exactly once
    conn = sqlite3.connect(paths.database_file)
    try:
        assert conn.execute(
            "SELECT count(*) FROM (SELECT question_uid FROM questions GROUP BY 1 HAVING count(*) > 1)"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_one_hundred_thousand_questions_are_parsed_and_bounded(paths: AppPaths, tmp_path: Path) -> None:
    folder = tmp_path / "100k"
    write_pair(folder, "ALI", range(1, 100_001))
    started = time.perf_counter()
    report = import_service.dry_run(paths, [folder])
    assert time.perf_counter() - started < 120
    assert report.counts.total_parsed == 100_000
    assert report.counts.eligible == 900  # only up to the subject's target count
    assert IssueCode.NUMBER_ABOVE_TARGET in {i.code for i in report.all_issues()}
