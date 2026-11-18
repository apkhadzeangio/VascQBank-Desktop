"""Phase 9b, campaign G — Georgian text survives the whole pipeline exactly.

import (TXT, also with CRLF) → edit → save revision → approve → export TXT + DOCX → re-import the
exported TXT into a NEW database. Texts: combining marks, ZWJ / ZWNJ, NBSP, an RTL mark, emoji
(including a ZWJ sequence), Mkhedruli and Mtavruli, mixed Latin/Georgian, tabs, a 15,000-character
field, a one-word explanation. Every field must come back character for character (the program's
documented normalisation — NFC, LF, trimmed — applied once at the entrance, never again).

The DOCX is read back by python-docx and, where installed, by an **independent** reader: LibreOffice
(DOCX → plain text, DOCX → PDF; ``pdffonts`` lists the fonts really used for the PDF). Rendering is
not judged by eye here; a person must still look at the documents in Word and LibreOffice
(docs/MANUAL_TEST_PLAN.md). Synthetic text only.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import unicodedata
import zipfile
from pathlib import Path

import pytest
from docx import Document

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter
from vascqbank_desktop.domain.editing import normalize_field
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.importexport.docx_export import DocxEdition
from vascqbank_desktop.services import import_service
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore
from vascqbank_desktop.services.storage import initialize_storage

LONG = ("ძალიან გრძელი ტექსტი, Latin words, 12,5 მმ ≥ 3 სმ; " * 400)[:15_000].strip()
TRICKY = {
    "combining": "ა́ბ̀გ̂ — ქართული ასოები დიაკრიტიკით",
    "zwj-zwnj": "ა‍ბ‌გ — ნულოვანი სიგანის შემაერთებელი",
    "nbsp": "10 მგ/კგ — განუყოფელი ჰარი",
    "rtl-mark": "ტექსტი‏ მარჯვნიდან მარცხნივ ნიშნით",
    "emoji": "გული ❤️ და ექიმი 👩‍⚕️ 😀",
    "mtavruli": "ᲛᲗᲐᲕᲠᲣᲚᲘ და მხედრული ერთად — ᲐᲑᲒ abg",
    "mixed": "EVAR-ის შემდეგ type II endoleak; AAA > 5,5 სმ (ESVS 2024)",
    "tab": "სვეტი 1\tსვეტი 2\tსვეტი 3",
    "long": LONG,
}


def _content(stem: str, explanation: str = "ერთი") -> QuestionContent:
    options = tuple(f"{letter}) ვარიანტი — {stem[:24]}" for letter in ANSWER_LETTERS)
    return QuestionContent(stem, tuple(o.split(") ", 1)[1] for o in options), AnswerLetter("ბ"), explanation)


def _write_sources(folder: Path, line_end: str) -> None:
    folder.mkdir(parents=True)
    questions, explanations = [], []
    for number, stem in enumerate(TRICKY.values(), start=1):
        content = _content(f"საწყისი №{number}: {stem[:40]}")
        lines = [f"{number}. {content.stem}"]
        lines += [
            ("*" if letter == "ბ" else "") + f"{letter}) {text}"
            for letter, text in zip(ANSWER_LETTERS, content.options, strict=True)
        ]
        questions.append("\n".join(lines))
        explanations.append(f"{number}. ბ) სინთეზური განმარტება №{number}.")
    (folder / "ALI_questions.txt").write_bytes(
        ("\n\n".join(questions) + "\n").replace("\n", line_end).encode()
    )
    (folder / "ALI_explanations.txt").write_bytes(
        ("\n\n".join(explanations) + "\n").replace("\n", line_end).encode()
    )


def _bank(root: Path, name: str, sources: Path) -> AppPaths:
    paths = AppPaths(data_dir=root / name)
    initialize_storage(paths)
    report = import_service.dry_run(paths, [sources])
    assert report.counts.eligible == len(TRICKY), [i.message for i in report.all_issues()][:3]
    import_service.commit_import(paths, report)
    ReviewerProfile(SettingsStore(paths.settings_file)).set_name("ფიდელობის რეცენზენტი")
    return paths


def _stored(paths: AppPaths) -> dict[str, QuestionContent]:
    catalog = CatalogService(paths)
    out = {}
    for number in range(1, len(TRICKY) + 1):
        detail = catalog.detail_by_uid(f"ALI-{number:04d}")
        assert detail is not None
        out[detail.uid] = detail.content
    return out


@pytest.fixture(scope="module", params=["lf", "crlf"])
def pipeline(request, tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp(f"fidelity-{request.param}")
    _write_sources(root / "sources", "\r\n" if request.param == "crlf" else "\n")
    paths = _bank(root, "მონაცემები", root / "sources")
    profile = ReviewerProfile(SettingsStore(paths.settings_file))
    edits, review, catalog = EditService(paths, profile), ReviewService(paths, profile), CatalogService(paths)
    expected: dict[str, QuestionContent] = {}
    for number, (kind, text) in enumerate(TRICKY.items(), start=1):
        uid = f"ALI-{number:04d}"
        detail = catalog.detail_by_uid(uid)
        explanation = "ერთი" if kind != "long" else LONG
        new = QuestionContent(
            f"{text}",
            tuple(f"{o} — {kind}" for o in detail.content.options),
            detail.content.correct,
            explanation,
        )
        saved = edits.save(detail.id, detail.lock_version, new)
        review.approve(detail.id, saved.lock_version)
        expected[uid] = QuestionContent(
            normalize_field(new.stem), tuple(normalize_field(o) for o in new.options), new.correct,
            normalize_field(new.explanation),
        )  # fmt: skip
    result = ExportService(paths).run(
        ExportRequest(
            subjects=("ALI",),
            formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
            destination=root / "ექსპორტი",
            editions=(DocxEdition.REVIEW,),
        )
    )
    return {"root": root, "paths": paths, "expected": expected, "export": result}


def test_the_database_holds_exactly_what_was_saved(pipeline) -> None:
    assert _stored(pipeline["paths"]) == pipeline["expected"]
    for content in pipeline["expected"].values():
        for text in (content.stem, *content.options, content.explanation):
            assert unicodedata.is_normalized("NFC", text)


def test_exported_txt_reimports_identically_into_a_new_database(pipeline) -> None:
    txt = pipeline["export"].folder / "TXT"
    copy = pipeline["root"] / "reimport-source"
    shutil.copytree(txt, copy)
    raw = (copy / "ALI_questions.txt").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw  # UTF-8 without BOM, LF only
    again = _bank(pipeline["root"], "ხელახალი იმპორტი", copy)
    assert _stored(again) == pipeline["expected"]


def _docx_paragraph_text(path: Path) -> str:
    return "\n".join(p.text for p in Document(str(path)).paragraphs)


def test_python_docx_reads_every_field_back(pipeline) -> None:
    docx = next((pipeline["export"].folder / "DOCX").glob("*.docx"))
    with zipfile.ZipFile(docx) as archive:
        assert archive.testzip() is None
    text = _docx_paragraph_text(docx)
    for content in pipeline["expected"].values():
        for field in (content.stem, *content.options, content.explanation):
            for line in field.split("\n"):
                assert line in text, line[:60]


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice (soffice) is not installed")
def test_libreoffice_reads_the_same_text_and_renders_a_pdf(pipeline, tmp_path: Path) -> None:
    docx = next((pipeline["export"].folder / "DOCX").glob("*.docx"))
    profile = tmp_path / "lo-profile"
    base = ["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--norestore"]
    out = tmp_path / "out"
    for target in ("txt:Text (encoded):UTF8", "pdf"):
        done = subprocess.run(
            [*base, "--convert-to", target, "--outdir", str(out), str(docx)], capture_output=True, text=True,
            timeout=300,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr[-2000:]
    text = (out / (docx.stem + ".txt")).read_text(encoding="utf-8-sig")
    missing = []
    for content in pipeline["expected"].values():
        for field in (content.stem, *content.options, content.explanation):
            for line in field.split("\n"):
                if line not in text:
                    missing.append(line[:80])
    assert not missing, missing[:10]
    pdf = out / (docx.stem + ".pdf")
    assert pdf.stat().st_size > 1000
    if shutil.which("pdffonts"):
        fonts = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True, timeout=60).stdout
        print("\n[fidelity] fonts LibreOffice embedded:\n" + fonts)
        assert "yes" in fonts  # fonts are embedded (no reliance on the reader's machine)


def test_no_field_was_touched_in_the_database_by_the_export(pipeline) -> None:
    conn = sqlite3.connect(pipeline["paths"].database_file)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    assert _stored(pipeline["paths"]) == pipeline["expected"]
