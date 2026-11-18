"""Re-open a written .docx and prove it says exactly what it should.

A successful ``save()`` proves nothing about the content, so after writing every document the
exporter calls :func:`verify_docx`, which

1. reopens the file with python-docx (the package and every part must parse);
2. checks the page set-up (A4), the footer's ``PAGE`` field and that every VascQBank style names
   the expected font in all four OOXML font slots;
3. walks the body paragraphs by **style** (not by guessing from text), rebuilds every question
   — ID, display number, stem, five options, which option is marked correct, explanation, answer
   key entry — and compares it, character for character, with the approved snapshot: none
   missing, none duplicated, none extra, continuous display numbers, the right correct answer;
4. for the exam edition, also proves that nothing reveals an answer: no "correct" option style,
   no marker, no explanation or key paragraphs, and no direct run formatting on any option.

This is a structural and textual check of the OOXML; it does not claim anything about how a
particular Word version will paginate or render the document.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm

from vascqbank_desktop.domain.answers import ANSWER_LETTERS
from vascqbank_desktop.importexport.docx_export import (
    CORRECT_MARKER,
    EXPLANATION_LABEL,
    PARAGRAPH_STYLES,
    S_EXPLANATION,
    S_EXPLANATION_LABEL,
    S_ID,
    S_KEY,
    S_MARKER,
    S_OPTION,
    S_OPTION_CORRECT,
    S_STEM,
    S_SUBJECT,
    DocxEdition,
    SubjectBlock,
    style_id,
)

_ID = re.compile(r"^(?P<number>\d+)\. (?P<uid>\S+)$")
_KEY = re.compile(r"^(?P<number>\d+)\. (?P<uid>\S+) — (?P<letter>[ა-ჰ])\) (?P<text>.*)$", re.DOTALL)
_OPTION = re.compile(r"^(?P<letter>[ა-ჰ])\) (?P<text>.*)$", re.DOTALL)

_ID_S, _STEM_S, _OPT_S, _OPT_OK_S = (
    style_id(S_ID),
    style_id(S_STEM),
    style_id(S_OPTION),
    style_id(S_OPTION_CORRECT),
)
_EXP_S, _LABEL_S, _KEY_S, _SUBJ_S = (
    style_id(S_EXPLANATION),
    style_id(S_EXPLANATION_LABEL),
    style_id(S_KEY),
    style_id(S_SUBJECT),
)
_MARKER_S = style_id(S_MARKER)


@dataclass(slots=True)
class _Found:
    number: int
    uid: str
    subject: str | None
    stem: list[str] = field(default_factory=list)
    options: list[tuple[str, str, bool, bool]] = field(default_factory=list)  # letter, text, style, marker
    labels: int = 0
    explanations: list[str] = field(default_factory=list)
    key: tuple[str, str] | None = None  # letter, option text


@dataclass(frozen=True, slots=True)
class DocxReport:
    questions: int
    paragraphs: int
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def _paragraph(p) -> tuple[str, str, bool, bool]:  # noqa: ANN001
    """(style id, text without marker runs, has a marker run, has direct run formatting)."""
    ppr = p.find(qn("w:pPr"))
    style = ""
    if ppr is not None:
        ps = ppr.find(qn("w:pStyle"))
        if ps is not None:
            style = ps.get(qn("w:val")) or ""
    parts: list[str] = []
    marker = direct = False
    for run in p.iter(qn("w:r")):
        rpr = run.find(qn("w:rPr"))
        if rpr is not None:
            rs = rpr.find(qn("w:rStyle"))
            if rs is not None and rs.get(qn("w:val")) == _MARKER_S:
                marker = True
                continue
            if any(child.tag != qn("w:rStyle") for child in rpr):
                direct = True
        for child in run:
            if child.tag == qn("w:t"):
                parts.append(child.text or "")
            elif child.tag in (qn("w:br"), qn("w:cr")):
                parts.append("\n")
            elif child.tag == qn("w:tab"):
                parts.append("\t")
    return style, "".join(parts), marker, direct


def _check_fonts(document, font: str, problems: list[str]) -> None:  # noqa: ANN001
    styles = document.styles
    for name in ("Normal", *PARAGRAPH_STYLES):
        try:
            element = styles[name].element
        except KeyError:
            problems.append(f"style {name!r} is missing")
            continue
        rpr = element.find(qn("w:rPr"))
        fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
        if fonts is None:
            problems.append(f"style {name!r} names no font")
            continue
        for slot in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            if fonts.get(qn(slot)) != font:
                problems.append(f"style {name!r}: font slot {slot} is {fonts.get(qn(slot))!r}, not {font!r}")


def verify_docx(
    path: Path, subjects: Sequence[SubjectBlock], edition: DocxEdition, *, font: str
) -> DocxReport:
    problems: list[str] = []
    try:
        document = Document(str(path))
    except Exception as exc:  # the file must at least open
        return DocxReport(0, 0, (f"the document cannot be opened: {exc}",))

    section = document.sections[0]
    if abs(section.page_width - Cm(21.0)) > Cm(0.05) or abs(section.page_height - Cm(29.7)) > Cm(0.05):
        problems.append("the page size is not A4")
    footer_xml = section.footer._element.xml
    if "PAGE" not in footer_xml or "fldChar" not in footer_xml:
        problems.append("the footer has no page-number field")
    _check_fonts(document, font, problems)

    found: list[_Found] = []
    current: _Found | None = None
    subject: str | None = None
    paragraphs = 0
    exam = edition is DocxEdition.EXAM
    for p in document.element.body.iter(qn("w:p")):
        paragraphs += 1
        style, text, marker, direct = _paragraph(p)
        if style == _SUBJ_S:
            subject = text.split(" — ", 1)[0]
            current = None
        elif style == _ID_S:
            match = _ID.match(text)
            if match is None:
                problems.append(f"unreadable question heading {text!r}")
                current = None
                continue
            current = _Found(int(match["number"]), match["uid"], subject)
            found.append(current)
        elif style == _KEY_S:
            match = _KEY.match(text)
            if match is None:
                problems.append(f"unreadable answer-key entry {text[:40]!r}")
                current = None
                continue
            current = _Found(
                int(match["number"]), match["uid"], subject, key=(match["letter"], match["text"])
            )
            found.append(current)
        elif style in (_STEM_S, _OPT_S, _OPT_OK_S, _EXP_S, _LABEL_S):
            if current is None:
                problems.append(f"text outside a question: {text[:40]!r}")
                continue
            if style == _STEM_S:
                current.stem.append(text)
            elif style == _LABEL_S:
                current.labels += 1
                if text != EXPLANATION_LABEL:
                    problems.append(f"{current.uid}: unexpected explanation label {text!r}")
            elif style == _EXP_S:
                current.explanations.append(text)
            else:
                match = _OPTION.match(text)
                if match is None:
                    problems.append(f"{current.uid}: unreadable option {text[:40]!r}")
                    continue
                current.options.append((match["letter"], match["text"], style == _OPT_OK_S, marker))
                if exam and direct:
                    problems.append(
                        f"{current.uid}: an option carries its own formatting in the exam edition"
                    )
        if exam and marker:
            problems.append("the exam edition contains a correct-answer marker")

    expected = [(block.code, q) for block in subjects for q in block.questions]
    if [f.uid for f in found] != [q.uid for _code, q in expected]:
        seen = [f.uid for f in found]
        missing = sorted({q.uid for _c, q in expected} - set(seen))
        extra = sorted(set(seen) - {q.uid for _c, q in expected})
        duplicated = sorted({u for u in seen if seen.count(u) > 1})
        problems.append(
            f"questions do not match the snapshot (missing {missing[:5]}, extra {extra[:5]}, "
            f"duplicated {duplicated[:5]}, {len(seen)} found, {len(expected)} expected)"
        )
    for position, (got, (code, q)) in enumerate(zip(found, expected, strict=False), start=1):
        problems.extend(_compare(got, position, code, q, edition))

    document_text = "\n".join(_paragraph(p)[1] for p in document.element.body.iter(qn("w:p")))
    if "�" in document_text:
        problems.append("the document contains replacement characters (lost text)")
    if exam and (CORRECT_MARKER in document_text or "✓" in document_text):
        problems.append("the exam edition shows the correct-answer marker text")
    return DocxReport(len(found), paragraphs, tuple(problems))


def _compare(got: _Found, position: int, code: str, q, edition: DocxEdition) -> list[str]:  # noqa: ANN001
    c = q.content
    uid = q.uid
    out: list[str] = []
    if got.number != position:
        out.append(f"{uid}: display number {got.number}, expected {position}")
    if got.subject != code:
        out.append(f"{uid}: listed under subject {got.subject!r}, expected {code!r}")
    if edition is DocxEdition.ANSWER_KEY:
        if got.key != (c.correct.value, c.options[c.correct.index]):
            out.append(f"{uid}: the answer key does not give the approved correct answer")
        if got.explanations != [c.explanation]:
            out.append(f"{uid}: the explanation differs from the approved one")
        if got.stem or got.options:
            out.append(f"{uid}: the answer key contains question text")
        return out
    if got.stem != [c.stem]:
        out.append(f"{uid}: the question text differs from the approved one")
    letters = tuple(o[0] for o in got.options)
    if letters != ANSWER_LETTERS:
        out.append(f"{uid}: options {letters}, expected five in ა…ე order")
    elif tuple(o[1] for o in got.options) != c.options:
        out.append(f"{uid}: an option text differs from the approved one")
    marked = [index for index, o in enumerate(got.options) if o[2] or o[3]]
    if edition is DocxEdition.REVIEW:
        if marked != [c.correct.index] or not all(o[2] == o[3] for o in got.options):
            out.append(f"{uid}: the marked correct answer is not {c.correct.value}")
        if got.explanations != [c.explanation] or got.labels != 1:
            out.append(f"{uid}: the explanation is missing or differs from the approved one")
    else:  # EXAM
        if marked:
            out.append(f"{uid}: the exam edition reveals the correct answer")
        if got.explanations or got.labels:
            out.append(f"{uid}: the exam edition contains an explanation")
    return out
