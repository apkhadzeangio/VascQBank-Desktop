"""Word documents: structure, fonts, page set-up, editions, and the verifier catching tampering."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from docx import Document

from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.importexport.docx_export import (
    CORRECT_MARKER,
    DocxEdition,
    DocxMeta,
    SubjectBlock,
    build_docx,
)
from vascqbank_desktop.importexport.docx_verify import verify_docx

SCIENCE = "EVAR, CTA ≥ 70 % (ESVS 2024); ABI 0.45; 70 mmHg; α, β₂, Δp ≈ 15; 10⁹/ლ; CLTI; ±2 σ"


@dataclass
class Q:
    uid: str
    subject_code: str
    subject_name_ka: str
    source_number: int
    content: QuestionContent


def q(code: str, n: int, correct: AnswerLetter = AnswerLetter.C, stem: str | None = None) -> Q:
    return Q(
        f"{code}-{n:04d}",
        code,
        "სინთეზური თემა",
        n,
        QuestionContent(
            stem or f"სინთეზური კითხვა {n}:\n{SCIENCE}",
            tuple(f"ვარიანტი {letter} — {n}" for letter in "აბგდე"),
            correct,
            f"განმარტება {n}.\nმეორე ხაზი „ციტატით“.",
        ),
    )


BLOCKS = [
    SubjectBlock(
        "AAA_EVAR", "მუცლის აორტის ანევრიზმა", [q("AAA_EVAR", 105, AnswerLetter.A), q("AAA_EVAR", 200)]
    ),
    SubjectBlock(
        "ALI", "კიდურის მწვავე იშემია", [q("ALI", 42, AnswerLetter.E), q("ALI", 43, AnswerLetter.B)]
    ),
]
META = DocxMeta("20261118-000000-abcdef", "2026-11-18 12:00", "0" * 64, "0.6.0")


def write(tmp_path: Path, edition: DocxEdition, font: str = "Sylfaen", blocks=BLOCKS) -> Path:
    path = tmp_path / f"{edition.value}.docx"
    build_docx(blocks, edition, META, font=font).save(str(path))
    return path


@pytest.mark.parametrize("edition", list(DocxEdition))
def test_every_edition_verifies_against_its_source(tmp_path: Path, edition: DocxEdition) -> None:
    report = verify_docx(write(tmp_path, edition), BLOCKS, edition, font="Sylfaen")
    assert report.ok, report.problems
    assert report.questions == 4


def test_page_setup_fonts_and_page_numbers(tmp_path: Path) -> None:
    path = write(tmp_path, DocxEdition.REVIEW, font="DejaVu Sans")
    doc = Document(str(path))
    section = doc.sections[0]
    assert round(section.page_width.cm, 1) == 21.0 and round(section.page_height.cm, 1) == 29.7
    assert round(section.left_margin.cm, 1) == 2.2
    with zipfile.ZipFile(path) as archive:
        styles = archive.read("word/styles.xml").decode()
        footer = next(archive.read(n).decode() for n in archive.namelist() if n.startswith("word/footer"))
    for slot in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        assert f'{slot}="DejaVu Sans"' in styles
    assert "asciiTheme" not in styles.split("<w:style ")[0]  # document defaults use the font, not a theme
    assert 'w:val="ka-GE"' in styles
    assert "PAGE" in footer and "NUMPAGES" in footer and 'w:fldCharType="begin"' in footer
    assert verify_docx(path, BLOCKS, DocxEdition.REVIEW, font="DejaVu Sans").ok
    wrong = verify_docx(path, BLOCKS, DocxEdition.REVIEW, font="Sylfaen")
    assert any("font slot" in p for p in wrong.problems)


def test_review_edition_marks_exactly_the_correct_answer_in_words_and_style(tmp_path: Path) -> None:
    doc = Document(str(write(tmp_path, DocxEdition.REVIEW)))
    marked = [p for p in doc.paragraphs if CORRECT_MARKER in p.text]
    assert [p.text.split(")")[0] for p in marked] == ["ა", "გ", "ე", "ბ"]
    assert all(p.style.name == "VQB Option Correct" for p in marked)
    assert sum(p.style.name == "VQB Explanation" for p in doc.paragraphs) == 4
    headings = [p.text for p in doc.paragraphs if p.style.name == "VQB Subject"]
    assert headings == ["AAA_EVAR — მუცლის აორტის ანევრიზმა (2)", "ALI — კიდურის მწვავე იშემია (2)"]
    assert doc.paragraphs[
        [p.text for p in doc.paragraphs].index(headings[1])
    ].paragraph_format.page_break_before


def test_ids_and_continuous_display_numbers(tmp_path: Path) -> None:
    doc = Document(str(write(tmp_path, DocxEdition.EXAM)))
    ids = [p.text for p in doc.paragraphs if p.style.name == "VQB Question ID"]
    assert ids == ["1. AAA_EVAR-0105", "2. AAA_EVAR-0200", "3. ALI-0042", "4. ALI-0043"]
    key = Document(str(write(tmp_path, DocxEdition.ANSWER_KEY)))
    entries = [p.text for p in key.paragraphs if p.style.name == "VQB Key Entry"]
    assert entries[2] == "3. ALI-0042 — ე) ვარიანტი ე — 42"  # same numbers as the exam edition


def test_the_exam_edition_reveals_nothing(tmp_path: Path) -> None:
    path = write(tmp_path, DocxEdition.EXAM)
    with zipfile.ZipFile(path) as archive:
        everything = "".join(
            archive.read(n).decode("utf-8", "ignore") for n in archive.namelist() if n.endswith(".xml")
        )
    assert CORRECT_MARKER not in everything and "✓" not in everything
    assert "VQBOptionCorrect" not in archive_document(path) and "განმარტება 42" not in everything
    doc = Document(str(path))
    options = [p for p in doc.paragraphs if p.style.name.startswith("VQB Option")]
    assert len(options) == 20 and {p.style.name for p in options} == {"VQB Option"}
    assert all(r.bold is None and r.font.color.rgb is None for p in options for r in p.runs)


def archive_document(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode()


def test_text_survives_exactly(tmp_path: Path) -> None:
    doc = Document(str(write(tmp_path, DocxEdition.REVIEW)))
    stems = [p.text for p in doc.paragraphs if p.style.name == "VQB Stem"]
    assert stems[0] == f"სინთეზური კითხვა 105:\n{SCIENCE}"  # line break, symbols, Georgian intact
    assert "<w:br/>" in archive_document(tmp_path / "review.docx")


def _tamper(path: Path, old: str, new: str) -> None:
    with zipfile.ZipFile(path) as archive:
        parts = {n: archive.read(n) for n in archive.namelist()}
    parts["word/document.xml"] = parts["word/document.xml"].decode().replace(old, new, 1).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    path.write_bytes(buffer.getvalue())


@pytest.mark.parametrize(
    ("edition", "old", "new", "expected"),
    [
        (DocxEdition.REVIEW, "ვარიანტი ბ — 43", "ვარიანტი ბ — 44", "option text differs"),
        (DocxEdition.REVIEW, "3. ALI-0042", "3. ALI-0099", "do not match the snapshot"),
        (DocxEdition.REVIEW, "განმარტება 43.", "განმარტება 99.", "explanation"),
        (DocxEdition.REVIEW, "≥ 70 %", "> 70 %", "question text differs"),
        (DocxEdition.EXAM, 'w:val="VQBOption"/>', 'w:val="VQBOptionCorrect"/>', "reveals the correct answer"),
        (DocxEdition.ANSWER_KEY, "— ე) ვარიანტი ე — 42", "— ა) ვარიანტი ა — 42", "answer key does not give"),
        (DocxEdition.EXAM, "2. AAA_EVAR-0200", "2. AAA_EVAR-0105", "duplicated ['AAA_EVAR-0105']"),
    ],
)
def test_the_verifier_catches_every_kind_of_damage(tmp_path: Path, edition, old, new, expected) -> None:
    path = write(tmp_path, edition)
    _tamper(path, old, new)
    report = verify_docx(path, BLOCKS, edition, font="Sylfaen")
    assert not report.ok and any(expected in p for p in report.problems), report.problems


def test_a_question_skipped_in_the_document_is_detected(tmp_path: Path) -> None:
    shorter = [BLOCKS[0], SubjectBlock("ALI", "კიდურის მწვავე იშემია", BLOCKS[1].questions[:1])]
    path = tmp_path / "short.docx"
    build_docx(shorter, DocxEdition.REVIEW, META).save(str(path))
    report = verify_docx(path, BLOCKS, DocxEdition.REVIEW, font="Sylfaen")
    assert any("missing ['ALI-0043']" in p for p in report.problems)


def test_an_unreadable_file_is_reported_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip")
    report = verify_docx(path, BLOCKS, DocxEdition.REVIEW, font="Sylfaen")
    assert not report.ok and "cannot be opened" in report.problems[0]


def test_properties_never_claim_official_certification(tmp_path: Path) -> None:
    doc = Document(str(write(tmp_path, DocxEdition.EXAM)))
    text = "\n".join(p.text for p in doc.paragraphs[:6]).lower()
    assert "არ არის ოფიციალურად აკრედიტებული" in text
    assert doc.core_properties.author == "VascQBank Review Desktop"
    assert "ე)" not in (doc.core_properties.subject or "") and not doc.core_properties.comments
