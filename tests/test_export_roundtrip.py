"""TXT round trip: synthetic bank → edit → approve → export TXT → import into a fresh database.

Every approved question must come back with identical ID, question, options, correct answer and
explanation; nothing unapproved may appear; gaps in the numbering must survive untouched.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from bank import make_question, seed
from exportbank import APPROVED, NOT_APPROVED, Bank, build

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.importexport.docx_export import DocxEdition
from vascqbank_desktop.services import import_service
from vascqbank_desktop.services.approved_service import ApprovedQuestionsService
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService
from vascqbank_desktop.services.storage import initialize_storage


def reimport(txt_folder: Path, data_dir: Path) -> dict[str, QuestionContent]:
    fresh = AppPaths(data_dir=data_dir)
    initialize_storage(fresh)
    report = import_service.dry_run(fresh, [txt_folder])
    errors = [i for i in report.all_issues() if i.is_error]
    assert not errors, errors
    result = import_service.commit_import(fresh, report)
    assert result.invalid == 0
    catalog = CatalogService(fresh)
    contents = {}
    for uid in result.created:
        detail = catalog.detail_by_uid(uid)
        assert detail is not None
        contents[uid] = detail.content
    return contents


def export_txt(paths: AppPaths, dest: Path, subjects: tuple[str, ...]) -> Path:
    result = ExportService(paths).run(
        ExportRequest(subjects=subjects, formats=frozenset({ExportFormat.TXT}), destination=dest)
    )
    return result.folder / "TXT"


def test_the_approved_subset_round_trips_exactly(app_paths: AppPaths, tmp_path: Path) -> None:
    build(app_paths)
    folder = export_txt(app_paths, tmp_path / "out", ("ALI", "VENOUS"))
    back = reimport(folder, tmp_path / "fresh")
    approved = {q.uid: q.content for q in ApprovedQuestionsService(app_paths).snapshot().questions}
    assert set(back) == set(APPROVED) == set(approved)
    assert back == approved  # questions, five options, correct answers, explanations: identical
    assert not set(back) & set(NOT_APPROVED)  # pending, in-review and rejected stay out
    assert back["ALI-0003"].options[1] == "ღია რეკონსტრუქცია\nდისტალური ანასტომოზით"
    assert back["ALI-0003"].correct is AnswerLetter.B and "≥ 70 %" in back["ALI-0003"].stem


def test_a_single_subject_partial_export(app_paths: AppPaths, tmp_path: Path) -> None:
    build(app_paths)
    back = reimport(export_txt(app_paths, tmp_path / "out", ("VENOUS",)), tmp_path / "fresh")
    assert sorted(back) == ["VENOUS-0007", "VENOUS-0010"]  # numbers 7 and 10 kept, nothing renumbered


@pytest.mark.parametrize("seed_value", [1, 2, 3])
def test_randomised_banks_round_trip(app_paths: AppPaths, tmp_path: Path, seed_value: int) -> None:
    rng = random.Random(seed_value)
    words = [
        "არტერია",
        "EVAR",
        "CTA",
        "≥ 70 %",
        "mmHg",
        "α",
        "β₂",
        "ტრომბი",
        "„ციტატა“",
        "10⁹/ლ",
        "CLTI",
        "(ESVS)",
    ]

    def text(lines: int) -> str:
        return "\n".join(" ".join(rng.choice(words) for _ in range(rng.randint(2, 8))) for _ in range(lines))

    codes = ("ALI", "VENOUS", "AAA_EVAR")
    questions = []
    for code in codes:
        for number in sorted(rng.sample(range(1, 400), 25)):  # gaps everywhere
            questions.append(make_question(code, number, correct=AnswerLetter(rng.choice("აბგდე"))))
    seed(app_paths, questions)
    bank = Bank(app_paths)
    chosen = rng.sample([str(q.qid) for q in questions], 40)
    for uid in chosen[:25]:
        bank.edit(
            uid,
            stem=text(rng.randint(1, 3)),
            options=tuple(text(rng.randint(1, 2)) for _ in range(5)),
            correct=AnswerLetter(rng.choice("აბგდე")),
            explanation=text(rng.randint(1, 3)),
        )
    bank.approve(*chosen)
    subjects = tuple(rng.sample(codes, 2))
    back = reimport(export_txt(app_paths, tmp_path / "out", subjects), tmp_path / "fresh")
    expected = {
        q.uid: q.content
        for q in ApprovedQuestionsService(app_paths).snapshot().questions
        if q.subject_code in subjects
    }
    assert back == expected and len(back) == sum(1 for uid in chosen if uid.rsplit("-", 1)[0] in subjects)


def test_word_and_txt_of_one_export_describe_the_same_questions(app_paths: AppPaths, tmp_path: Path) -> None:
    build(app_paths)
    from docx import Document

    result = ExportService(app_paths).run(
        ExportRequest(
            subjects=("ALI", "VENOUS"),
            formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
            destination=tmp_path,
            editions=(DocxEdition.EXAM, DocxEdition.ANSWER_KEY),
            per_subject_docx=False,
            combined_docx=True,
        )
    )
    exam = Document(str(result.folder / "DOCX" / "VascQBank_All_Approved_exam.docx"))
    key = Document(str(result.folder / "DOCX" / "VascQBank_All_Approved_answer_key.docx"))
    exam_ids = [p.text.split(". ", 1)[1] for p in exam.paragraphs if p.style.name == "VQB Question ID"]
    key_ids = [
        p.text.split(". ", 1)[1].split(" — ")[0] for p in key.paragraphs if p.style.name == "VQB Key Entry"
    ]
    assert exam_ids == key_ids == list(APPROVED)
    back = reimport(result.folder / "TXT", tmp_path / "fresh")
    for paragraph in key.paragraphs:
        if paragraph.style.name == "VQB Key Entry":
            uid, rest = paragraph.text.split(". ", 1)[1].split(" — ", 1)
            assert rest.startswith(f"{back[uid].correct.value})")  # the key agrees with the TXT
