"""Word (.docx) documents of approved questions — review, exam and answer-key editions.

Layout (python-docx, reusable paragraph styles, no per-run formatting except the visible
correct-answer marker of the review edition):

* A4, 2 cm top/bottom and 2.2 cm side margins; header with the document title; footer with
  ``გვერდი PAGE / NUMPAGES`` fields;
* every style sets the chosen Georgian-capable font on **all four** OOXML font slots (``ascii``,
  ``hAnsi``, ``cs``, ``eastAsia``) and the document defaults too, so Georgian, Latin terms
  (EVAR, CTA, mmHg), Greek letters and symbols use the same face;
* one heading per subject (a Heading 1, so Word's navigation pane lists the subjects); a new
  subject starts on a new page — individual questions never do;
* per question: ``12. ALI-0042`` (continuous display number + the immutable question ID), the
  question, the five options ``ა) …`` indented; the question, its ID and its options are kept
  together on a page where Word can (``keep with next``), long explanations may break;
* line breaks inside a field are written as Word line breaks, text exactly as approved.

Editions:

* ``REVIEW`` — options with the correct one marked by a style *and* the words „✓ სწორი პასუხი“,
  followed by the explanation;
* ``EXAM`` — question and options only; nothing reveals the answer (same style for every option);
* ``ANSWER_KEY`` — ``12. ALI-0042 — ბ) correct option`` and the explanation, numbered exactly as
  the exam edition of the same selection.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph

from vascqbank_desktop.domain.answers import ANSWER_LETTERS
from vascqbank_desktop.importexport.txt_export import ExportableQuestion


class DocxEdition(StrEnum):
    REVIEW = "review"
    EXAM = "exam"
    ANSWER_KEY = "answer_key"


EDITION_TITLES: dict[DocxEdition, str] = {
    DocxEdition.REVIEW: "განხილვის ვერსია — კითხვები, სწორი პასუხები და განმარტებები",
    DocxEdition.EXAM: "საგამოცდო ვერსია — კითხვები სწორი პასუხების მითითების გარეშე",
    DocxEdition.ANSWER_KEY: "პასუხების გასაღები — სწორი პასუხები და განმარტებები",
}

DEFAULT_FONT = "Sylfaen"
# One face must cover Georgian, Latin (EVAR, CTA, mmHg), Greek (α, β, Δ) and symbols (≥, ±, ⁹):
# Word puts ASCII in the "ascii" slot and everything else — Georgian *and* Greek — in "hAnsi", so a
# Georgian-only font (e.g. Noto Sans Georgian, no Latin digits or letters) renders mixed medical
# text badly; it was tried and rejected (ARCHITECTURE.md §22). Sylfaen ships with every Windows;
# DejaVu Sans with most Linux systems and LibreOffice.
FONT_CHOICES = ("Sylfaen", "DejaVu Sans")
CORRECT_MARKER = "✓ სწორი პასუხი"
EXPLANATION_LABEL = "განმარტება"

# Style names (python-docx derives the style id by removing the spaces).
S_TITLE = "VQB Title"
S_META = "VQB Meta"
S_SUBJECT = "VQB Subject"
S_ID = "VQB Question ID"
S_STEM = "VQB Stem"
S_OPTION = "VQB Option"
S_OPTION_CORRECT = "VQB Option Correct"
S_EXPLANATION_LABEL = "VQB Explanation Label"
S_EXPLANATION = "VQB Explanation"
S_KEY = "VQB Key Entry"
S_MARKER = "VQB Correct Marker"  # character style

PARAGRAPH_STYLES = (
    S_TITLE, S_META, S_SUBJECT, S_ID, S_STEM, S_OPTION, S_OPTION_CORRECT,
    S_EXPLANATION_LABEL, S_EXPLANATION, S_KEY,
)  # fmt: skip


def style_id(name: str) -> str:
    return name.replace(" ", "")


@dataclass(frozen=True, slots=True)
class SubjectBlock:
    code: str
    name_ka: str
    questions: Sequence[ExportableQuestion]  # in export order (source number)


@dataclass(frozen=True, slots=True)
class DocxMeta:
    export_id: str
    exported_at_local: str  # already formatted for people, e.g. 2026-11-18 14:05
    fingerprint: str
    app_version: str


def display_number_title(number: int, uid: str) -> str:
    return f"{number}. {uid}"


# --------------------------------------------------------------------------- fonts and styles
def _set_fonts(rpr, family: str) -> None:  # noqa: ANN001
    """Name *family* for every script slot and drop theme fonts (which would win otherwise)."""
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        fonts.attrib.pop(qn(attribute), None)
    for slot in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(slot), family)
    lang = rpr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        rpr.append(lang)
    lang.set(qn("w:val"), "ka-GE")
    lang.set(qn("w:bidi"), "ka-GE")
    lang.set(qn("w:eastAsia"), "ka-GE")


def _paragraph_style(document, name: str, *, base: str = "Normal", size: float = 11, bold: bool = False):  # noqa: ANN001, ANN202
    style = document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = document.styles[base]
    style.quick_style = True
    style.font.size = Pt(size)
    style.font.bold = bold
    _set_fonts(style.element.get_or_add_rPr(), document._vqb_font)  # type: ignore[attr-defined]
    return style


def _install_styles(document, family: str) -> None:  # noqa: ANN001
    document._vqb_font = family  # type: ignore[attr-defined]
    defaults = document.styles.element.find(qn("w:docDefaults"))
    if defaults is not None:
        rpr_default = defaults.find(qn("w:rPrDefault"))
        if rpr_default is not None:
            rpr = rpr_default.find(qn("w:rPr"))
            if rpr is None:
                rpr = OxmlElement("w:rPr")
                rpr_default.append(rpr)
            _set_fonts(rpr, family)
    normal = document.styles["Normal"]
    normal.font.size = Pt(11)
    _set_fonts(normal.element.get_or_add_rPr(), family)
    fmt = normal.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(3)
    fmt.line_spacing = 1.12
    fmt.widow_control = True

    title = _paragraph_style(document, S_TITLE, size=18, bold=True)
    title.paragraph_format.space_after = Pt(4)
    meta = _paragraph_style(document, S_META, size=9.5)
    meta.font.color.rgb = RGBColor(0x44, 0x4E, 0x5A)
    meta.paragraph_format.space_after = Pt(2)

    subject = _paragraph_style(document, S_SUBJECT, base="Heading 1", size=15, bold=True)
    subject.font.color.rgb = RGBColor(0x7A, 0x1F, 0x3D)
    subject.paragraph_format.space_before = Pt(6)
    subject.paragraph_format.space_after = Pt(10)
    subject.paragraph_format.keep_with_next = True

    qid = _paragraph_style(document, S_ID, size=10.5, bold=True)
    qid.font.color.rgb = RGBColor(0x2A, 0x33, 0x40)
    qid.paragraph_format.space_before = Pt(10)
    qid.paragraph_format.space_after = Pt(2)
    qid.paragraph_format.keep_with_next = True

    stem = _paragraph_style(document, S_STEM, size=11)
    stem.paragraph_format.space_after = Pt(4)
    stem.paragraph_format.keep_with_next = True
    stem.paragraph_format.keep_together = True

    for name, bold in ((S_OPTION, False), (S_OPTION_CORRECT, True)):
        option = _paragraph_style(document, name, size=11, bold=bold)
        option.paragraph_format.left_indent = Cm(0.9)
        option.paragraph_format.first_line_indent = Cm(-0.6)
        option.paragraph_format.space_after = Pt(1)
        if bold:
            option.font.color.rgb = RGBColor(0x1E, 0x6B, 0x3F)

    label = _paragraph_style(document, S_EXPLANATION_LABEL, size=9.5, bold=True)
    label.font.color.rgb = RGBColor(0x55, 0x60, 0x6E)
    label.paragraph_format.left_indent = Cm(0.3)
    label.paragraph_format.space_before = Pt(5)
    label.paragraph_format.space_after = Pt(0)
    label.paragraph_format.keep_with_next = True

    explanation = _paragraph_style(document, S_EXPLANATION, size=10.5)
    explanation.paragraph_format.left_indent = Cm(0.3)
    explanation.paragraph_format.space_after = Pt(6)

    key = _paragraph_style(document, S_KEY, size=11, bold=True)
    key.paragraph_format.space_before = Pt(8)
    key.paragraph_format.space_after = Pt(1)
    key.paragraph_format.keep_with_next = True

    marker = document.styles.add_style(S_MARKER, WD_STYLE_TYPE.CHARACTER)
    marker.font.bold = True
    marker.font.color.rgb = RGBColor(0x1E, 0x6B, 0x3F)
    _set_fonts(marker.element.get_or_add_rPr(), family)


# --------------------------------------------------------------------------- page furniture
def _add_field(paragraph, instruction: str, placeholder: str) -> None:  # noqa: ANN001
    def char(kind: str):  # noqa: ANN202
        run = paragraph.add_run()
        element = OxmlElement("w:fldChar")
        element.set(qn("w:fldCharType"), kind)
        run._r.append(element)

    char("begin")
    run = paragraph.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    run._r.append(instr)
    char("separate")
    paragraph.add_run(placeholder)
    char("end")


def _page_setup(document, title: str) -> None:  # noqa: ANN001
    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.0)
    section.left_margin = section.right_margin = Cm(2.2)
    section.header_distance = section.footer_distance = Cm(1.0)
    header = section.header.paragraphs[0]
    header.text = title
    header.style = document.styles[S_META]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer = section.footer.paragraphs[0]
    footer.style = document.styles[S_META]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("გვერდი ")
    _add_field(footer, "PAGE", "1")
    footer.add_run(" / ")
    _add_field(footer, "NUMPAGES", "1")


# --------------------------------------------------------------------------- the document
class _Writer:
    """Appends paragraphs in O(1) each.

    ``Document.add_paragraph`` searches the body for the final section properties before every
    insertion, which makes a 10,000-question document quadratic (≈ 20 s for 2,000 questions). Here
    each paragraph is inserted directly before the remembered ``w:sectPr`` and styles are
    written as ids directly (the python-docx style setter scans every style for each paragraph).
    """

    def __init__(self, document: DocumentObject) -> None:
        self.document = document
        self._body = document._body
        self._sect_pr = document.element.body.get_or_add_sectPr()
        known = {style.name for style in document.styles}
        self._ids = {name: style_id(name) for name in (*PARAGRAPH_STYLES, S_MARKER) if name in known}

    def add(self, text: str, style: str) -> Paragraph:
        element = OxmlElement("w:p")
        self._sect_pr.addprevious(element)
        element.get_or_add_pPr().style = self._ids[style]
        paragraph = Paragraph(element, self._body)
        if text:
            paragraph.add_run(text)
        return paragraph

    def add_marker(self, paragraph: Paragraph, text: str) -> None:
        run = paragraph.add_run(text)
        run._r.get_or_add_rPr().style = self._ids[S_MARKER]


def build_docx(
    subjects: Sequence[SubjectBlock],
    edition: DocxEdition,
    meta: DocxMeta,
    *,
    font: str = DEFAULT_FONT,
    progress: Callable[[int], None] | None = None,
) -> DocumentObject:
    """Build (but do not save) one document; *progress* receives the questions done so far."""
    document = Document()
    _install_styles(document, font)
    title = "VascQBank — დამტკიცებული კითხვები"
    _page_setup(document, f"{title} · {EDITION_TITLES[edition].split(' — ')[0]}")
    props = document.core_properties
    props.title = f"{title} ({EDITION_TITLES[edition].split(' — ')[0]})"
    props.subject = ", ".join(s.code for s in subjects)
    props.author = "VascQBank Review Desktop"
    props.comments = ""
    props.keywords = ""
    props.category = ""
    props.last_modified_by = "VascQBank Review Desktop"
    props.revision = 1

    total = sum(len(s.questions) for s in subjects)
    writer = _Writer(document)
    writer.add(title, S_TITLE)
    writer.add(EDITION_TITLES[edition], S_META)
    writer.add(
        f"თემები: {', '.join(f'{s.code} ({len(s.questions)})' for s in subjects)} · კითხვები: {total}",
        S_META,
    )
    writer.add(
        f"ექსპორტი: {meta.exported_at_local} · ექსპორტის ID: {meta.export_id} · "
        f"წყაროს ანაბეჭდი: {meta.fingerprint[:16]} · VascQBank Review Desktop {meta.app_version}",
        S_META,
    )
    writer.add(
        "კითხვის ID (მაგ. ALI-0042) მუდმივი საიდენტიფიკაციო ნომერია; მის წინ მოცემული რიგითი ნომერი "
        "მხოლოდ ამ დოკუმენტის ნუმერაციაა. დოკუმენტი შექმნილია დამტკიცებული რედაქციებიდან და არ არის "
        "ოფიციალურად აკრედიტებული ან სერტიფიცირებული საგამოცდო მასალა.",
        S_META,
    )

    number = 0
    for index, block in enumerate(subjects):
        heading = writer.add(f"{block.code} — {block.name_ka} ({len(block.questions)})", S_SUBJECT)
        if index > 0:
            heading.paragraph_format.page_break_before = True
        for question in block.questions:
            number += 1
            _add_question(writer, number, question, edition)
            if progress is not None:
                progress(number)
    return document


def _add_question(writer: _Writer, number: int, q: ExportableQuestion, edition: DocxEdition) -> None:
    c = q.content
    if edition is DocxEdition.ANSWER_KEY:
        letter = c.correct.value
        writer.add(f"{display_number_title(number, q.uid)} — {letter}) {c.options[c.correct.index]}", S_KEY)
        writer.add(c.explanation, S_EXPLANATION)
        return
    writer.add(display_number_title(number, q.uid), S_ID)
    writer.add(c.stem, S_STEM)
    review = edition is DocxEdition.REVIEW
    for index, (letter, text) in enumerate(zip(ANSWER_LETTERS, c.options, strict=True)):
        correct = review and index == c.correct.index
        paragraph = writer.add(f"{letter}) {text}", S_OPTION_CORRECT if correct else S_OPTION)
        if index < 4 or review:
            paragraph.paragraph_format.keep_with_next = True
        if correct:
            writer.add_marker(paragraph, f"   {CORRECT_MARKER}")
    if review:
        writer.add(EXPLANATION_LABEL, S_EXPLANATION_LABEL)
        writer.add(c.explanation, S_EXPLANATION)
