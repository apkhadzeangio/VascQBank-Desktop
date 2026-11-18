"""Phase 9 Word/TXT accuracy audit on SYNTHETIC questions with demanding text.

Long fields (thousands of characters), Georgian mixed with Latin abbreviations, numerals, units,
Greek letters, sub/superscripts, typographic quotes, dashes, comparison and ± signs, and multiline
options. For one export:

* the DOCX package is a valid ZIP whose XML parts are well-formed UTF-8 without control characters;
* python-docx reads every question back (IDs in order, exact stems/options/explanations);
* when ``pandoc`` is installed, an *independent* DOCX reader is used as a second opinion and every
  line of every approved text must appear in its plain-text rendering;
* the TXT files are UTF-8 without BOM with LF line ends and re-import to identical questions.

No claim is made about Microsoft Word or LibreOffice rendering: neither is used here.
"""

from __future__ import annotations

import shutil
import subprocess
import unicodedata
import zipfile
from pathlib import Path
from xml.dom import minidom

import pytest
from bank import make_question, seed
from test_export_roundtrip import reimport

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.editing import MAX_FIELD_LENGTH
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.importexport.docx_export import DocxEdition
from vascqbank_desktop.services.approved_service import ApprovedQuestionsService
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

docx = pytest.importorskip("docx")

PHRASES = (
    "სინთეზური ტექსტი",
    "EVAR/TEVAR",
    "ABI = 0.45",
    "≥ 70 %",
    "± 2 σ",
    "Δp ≈ 15 mmHg",
    "β₂-აგონისტი",
    "10⁹/ლ",
    "„ციტატა“",
    "— ტირე —",
    "IIb კლასი (ESVS 2024)",
    "p < 0.05",
    "1/3 & 2/3",
    "α–ω",
)


def long_text(seed_value: int, length: int) -> str:
    words, i = [], seed_value
    while sum(len(w) + 1 for w in words) < length:
        words.append(PHRASES[i % len(PHRASES)])
        i += 7
    text = " ".join(words)[:length].rstrip()
    return text.rstrip("&<").rstrip()


def contents() -> dict[int, QuestionContent]:
    return {
        1: QuestionContent(
            long_text(1, 6_000) + "?",
            tuple(long_text(n, 400) for n in range(2, 7)),
            AnswerLetter.C,
            long_text(9, 12_000),
        ),
        2: QuestionContent(
            "მრავალხაზიანი სინთეზური კითხვა:\nპირველი ხაზი — EVAR\nმეორე ხაზი: ≥ 70 % და p < 0.05?",
            (
                "ვარიანტი ა\nგაგრძელება ახალ ხაზზე",
                "ვარიანტი ბ (β₂)",
                "ვარიანტი გ — „ციტატა“",
                "ვარიანტი დ: 10⁹/ლ",
                "ვარიანტი ე ± 2 σ",
            ),
            AnswerLetter.E,
            "სინთეზური განმარტება.\nმეორე ხაზი: Δp ≈ 15 mmHg.",
        ),
        3: QuestionContent(
            "მოკლე სინთეზური კითხვა?",
            ("ა1", "ბ2", "გ3", "დ4", "ე5"),
            AnswerLetter.A,
            "მოკლე.",
        ),
    }


@pytest.fixture
def exported(app_paths: AppPaths, tmp_path: Path):  # noqa: ANN201 - (paths, ExportResult)
    assert max(len(contents()[1].explanation), len(contents()[1].stem)) < MAX_FIELD_LENGTH
    seed(app_paths, [make_question("ALI", n) for n in (1, 2, 3, 4)])
    profile = ReviewerProfile(SettingsStore(app_paths.settings_file))
    profile.set_name("სინთეზური რეცენზენტი")
    catalog, edits, review = (
        CatalogService(app_paths),
        EditService(app_paths, profile),
        ReviewService(app_paths, profile),
    )
    for number, content in contents().items():
        d = catalog.detail_by_uid(f"ALI-{number:04d}")
        edits.save(d.id, d.lock_version, content)
        d = catalog.detail_by_uid(f"ALI-{number:04d}")
        review.approve(d.id, d.lock_version)
    result = ExportService(app_paths).run(
        ExportRequest(
            subjects=("ALI",),
            formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
            destination=tmp_path / "out",
            editions=tuple(DocxEdition),
            per_subject_docx=True,
            combined_docx=True,
        )
    )
    return app_paths, result


def docx_files(result) -> list[Path]:  # noqa: ANN001
    files = sorted((result.folder / "DOCX").glob("*.docx"))
    assert len(files) == 2 * len(DocxEdition)  # per subject + combined, every edition
    return files


def test_docx_packages_are_well_formed(exported) -> None:  # noqa: ANN001
    _paths, result = exported
    for path in docx_files(result):
        with zipfile.ZipFile(path) as package:
            assert package.testzip() is None
            names = package.namelist()
            assert "[Content_Types].xml" in names and "word/document.xml" in names
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    raw = package.read(name)
                    text = raw.decode("utf-8")  # strict: invalid UTF-8 would raise
                    assert not [c for c in text if unicodedata.category(c) == "Cc" and c not in "\t\n\r"]
                    minidom.parseString(raw)  # well-formed XML


def test_python_docx_reads_back_every_field_exactly(exported) -> None:  # noqa: ANN001
    paths, result = exported
    approved = {q.uid: q.content for q in ApprovedQuestionsService(paths).snapshot().questions}
    assert sorted(approved) == ["ALI-0001", "ALI-0002", "ALI-0003"]  # ALI-0004 is pending: excluded
    review = docx.Document(str(result.folder / "DOCX" / "VascQBank_All_Approved_review.docx"))
    text = "\n".join(p.text for p in review.paragraphs)
    for content in approved.values():
        for field in (content.stem, *content.options, content.explanation):
            for line in field.splitlines():
                assert line in text, line[:80]
    assert "ALI-0004" not in text


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
def test_an_independent_reader_sees_the_same_text(exported) -> None:  # noqa: ANN001
    paths, result = exported
    approved = ApprovedQuestionsService(paths).snapshot().questions
    for path in docx_files(result):
        rendered = subprocess.run(
            ["pandoc", "--from=docx", "--to=plain", "--wrap=none", str(path)],
            check=True,
            capture_output=True,
            timeout=120,
        ).stdout.decode("utf-8")
        flat = " ".join(rendered.split())
        for q in approved:
            assert q.uid in flat, (path.name, q.uid)
        if "review" in path.name or "exam" in path.name:
            for q in approved:
                fields = (q.content.stem, *q.content.options)
                if "review" in path.name:
                    fields += (q.content.explanation,)
                for field in fields:
                    for line in field.splitlines():
                        assert " ".join(line.split()) in flat, (path.name, line[:80])
        if "exam" in path.name:  # the exam edition never reveals explanations
            for q in approved:
                assert " ".join(q.content.explanation.splitlines()[0].split()) not in flat


def test_txt_encoding_and_exact_reimport(exported, tmp_path: Path) -> None:  # noqa: ANN001
    paths, result = exported
    for path in (result.folder / "TXT").iterdir():
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
        raw.decode("utf-8")
    back = reimport(result.folder / "TXT", tmp_path / "fresh")
    expected = {q.uid: q.content for q in ApprovedQuestionsService(paths).snapshot().questions}
    assert back == expected
