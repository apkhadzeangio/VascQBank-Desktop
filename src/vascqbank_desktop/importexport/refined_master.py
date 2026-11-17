"""Format B — ``REFINED_MASTER_QBANK.txt`` (academically refined master).

The grammar below was verified against the real file in VascQBank-10K
(``ACADEMIC_REVIEW/REFINED_MASTER_QBANK.txt``, inspected 2026-11-17: 384
records, LF line endings, no BOM, one identical field order in every record,
every section single-line except EVIDENCE, which may contain blank lines) and
matches VascQBank-Review's ``docs/UPSTREAM_FORMAT.md``::

    <bank header and topic counts>          (ignored)
    --------------------------------------------------
    TOPIC: AAA_EVAR
    ORIGINAL ID: 1
    SOURCE FINGERPRINT: <hex>
    SOURCE COMMIT: <git sha>
    STATUS: APPROVED
    QA SCORE: 94/100
    REVIEWED: <ISO timestamp>

    QUESTION:
    <text>

    OPTIONS:
    ა) …   ბ) …   *გ) …   დ) …   ე) …      (one per line)

    CORRECT ANSWER:
    გ) <same text as the starred option>

    ACADEMIC EXPLANATION:   CLINICAL REASONING:   DISTRACTOR ANALYSIS:
    EVIDENCE:               CLINICAL PEARL:       (each followed by its text)

    QA GATES: <inline value>

``ACADEMIC EXPLANATION`` becomes the question's explanation; every other field
is kept, in order, in ``ImportedQuestion.extras``. Upstream ``STATUS:
APPROVED`` means "refined"; it never grants reviewer approval in this app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter
from vascqbank_desktop.domain.question import (
    ImportedQuestion,
    QuestionContent,
    SourceKind,
    SourceLocation,
)
from vascqbank_desktop.domain.question_id import QuestionId
from vascqbank_desktop.domain.subjects import get_subject, is_known_subject
from vascqbank_desktop.importexport.common import (
    ParsedItem,
    ParseResult,
    join_lines,
    parse_option_line,
    read_source_file,
)
from vascqbank_desktop.validation.issues import IssueCode, has_errors, make_issue
from vascqbank_desktop.validation.question_rules import content_warnings

FILENAME = "REFINED_MASTER_QBANK.txt"
_SEPARATOR = re.compile(r"^-{20,}$")
_META_LINE = re.compile(r"^(?P<key>[A-Z][A-Z ]*[A-Z]): ?(?P<value>.*)$")

META_FIELDS: tuple[str, ...] = (
    "TOPIC",
    "ORIGINAL ID",
    "SOURCE FINGERPRINT",
    "SOURCE COMMIT",
    "STATUS",
    "QA SCORE",
    "REVIEWED",
)
SECTIONS: tuple[str, ...] = (
    "QUESTION",
    "OPTIONS",
    "CORRECT ANSWER",
    "ACADEMIC EXPLANATION",
    "CLINICAL REASONING",
    "DISTRACTOR ANALYSIS",
    "EVIDENCE",
    "CLINICAL PEARL",
)
TRAILER = "QA GATES"
_SECTION_HEADERS = {f"{name}:": name for name in SECTIONS}
REFINED_STATUS = "APPROVED"


@dataclass(slots=True)
class _Record:
    start_line: int
    meta: dict[str, tuple[str, int]] = field(default_factory=dict)
    meta_order: list[str] = field(default_factory=list)
    sections: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    section_order: list[str] = field(default_factory=list)
    trailer: str | None = None
    unexpected: list[tuple[int, str]] = field(default_factory=list)


def _split_records(lines: list[str]) -> list[_Record]:
    records: list[_Record] = []
    current: _Record | None = None
    section: str | None = None
    in_meta = False
    for line_no, line in enumerate(lines, start=1):
        if _SEPARATOR.match(line):
            current = _Record(start_line=line_no + 1)
            records.append(current)
            section, in_meta = None, True
            continue
        if current is None:
            continue  # bank header
        if current.trailer is not None:
            if line.strip():
                current.unexpected.append((line_no, line))
            continue
        if in_meta:
            if not line.strip():
                if current.meta_order:
                    in_meta = False
                continue
            meta = _META_LINE.match(line)
            if meta and meta.group("key") in META_FIELDS:
                current.meta[meta.group("key")] = (meta.group("value").strip(), line_no)
                current.meta_order.append(meta.group("key"))
                continue
            in_meta = False  # fall through: a section header without a blank line
        if line in _SECTION_HEADERS:
            section = _SECTION_HEADERS[line]
            current.sections[section] = []
            current.section_order.append(section)
            continue
        if line.startswith(f"{TRAILER}:"):
            current.trailer = line[len(TRAILER) + 1 :].strip()
            section = None
            continue
        if section is None:
            if line.strip():
                current.unexpected.append((line_no, line))
            continue
        current.sections[section].append((line, line_no))
    return [r for r in records if r.meta_order or r.section_order or r.unexpected]


def parse_master(path: Path) -> ParseResult:
    result = ParseResult(kind=SourceKind.REFINED_MASTER, label=path.name)
    text, source, issues = read_source_file(path)
    if source is not None:
        result.files.append(source)
    result.issues.extend(issues)
    if text is None:
        return result
    records = _split_records(text.split("\n"))
    if not records:
        result.issues.append(make_issue(IssueCode.MASTER_NO_RECORDS, file=path.name))
        return result

    identities: dict[tuple[str, int], list[int]] = {}
    for record in records:
        topic = record.meta.get("TOPIC", ("", 0))[0]
        number_text = record.meta.get("ORIGINAL ID", ("", 0))[0]
        if number_text.isdigit():
            identities.setdefault((topic, int(number_text)), []).append(record.start_line)
    for record in records:
        result.items.append(_build_item(record, path.name, identities))
    return result


def _build_item(
    record: _Record, file: str, identities: dict[tuple[str, int], list[int]]
) -> ParsedItem:
    topic, _ = record.meta.get("TOPIC", ("", record.start_line))
    number_text, _ = record.meta.get("ORIGINAL ID", ("", record.start_line))
    number = int(number_text) if number_text.isdigit() else 0
    known = is_known_subject(topic)
    qid = QuestionId(topic, number) if known and number > 0 else None
    uid = str(qid) if qid else None
    item = ParsedItem(qid=qid, location=f"{file}:{record.start_line}")

    def err(code: IssueCode, line: int | None = record.start_line, **params: object) -> None:
        item.issues.append(make_issue(code, file=file, line=line, qid=uid, **params))

    for name in META_FIELDS:
        if name not in record.meta:
            err(IssueCode.MASTER_FIELD_MISSING, field=name)
    for name in SECTIONS:
        if name not in record.sections:
            err(IssueCode.MASTER_FIELD_MISSING, field=name)
    if record.trailer is None:
        err(IssueCode.MASTER_FIELD_MISSING, field=TRAILER)
    present_meta = [m for m in META_FIELDS if m in record.meta_order]
    if record.meta_order != present_meta:
        err(IssueCode.MASTER_FIELD_ORDER, expected=" → ".join(present_meta),
            found=" → ".join(record.meta_order))
    present_sections = [s for s in SECTIONS if s in record.section_order]
    if record.section_order != present_sections:
        err(IssueCode.MASTER_FIELD_ORDER, expected=" → ".join(present_sections),
            found=" → ".join(record.section_order))
    for line_no, line in record.unexpected:
        err(IssueCode.MASTER_UNEXPECTED_LINE, line=line_no, text=line.strip()[:60])

    if topic and not known:
        err(IssueCode.UNKNOWN_SUBJECT, subject=topic)
    if "ORIGINAL ID" in record.meta and number <= 0:
        err(IssueCode.INVALID_NUMBER, number=number_text)
    if known and number > get_subject(topic).target_count:
        err(IssueCode.NUMBER_ABOVE_TARGET, number=number, target=get_subject(topic).target_count)
    duplicate_lines = identities.get((topic, number), [])
    if len(duplicate_lines) > 1:
        err(IssueCode.DUPLICATE_NUMBER, number=number, lines=", ".join(map(str, duplicate_lines)))
    status = record.meta.get("STATUS", (REFINED_STATUS, 0))[0]
    if status != REFINED_STATUS:
        err(IssueCode.MASTER_NOT_REFINED, status=status)

    def section_text(name: str) -> str:
        return join_lines([line for line, _ in record.sections.get(name, [])])

    stem = section_text("QUESTION")
    if "QUESTION" in record.sections and not stem:
        err(IssueCode.EMPTY_STEM)

    options = []
    for line, line_no in record.sections.get("OPTIONS", []):
        if not line.strip():
            continue
        option = parse_option_line(line, line_no)
        if option is None:
            err(IssueCode.OPTION_FORMAT, line=line_no)
            continue
        options.append(option)
    if "OPTIONS" in record.sections:
        letters = [o.letter for o in options]
        if len(options) != 5:
            err(IssueCode.OPTION_COUNT, count=len(options))
        elif tuple(letters) != ANSWER_LETTERS:
            err(IssueCode.OPTION_LETTERS, letters=", ".join(f"{x})" for x in letters))
        for option in options:
            if not option.marker_ok or "*" in option.text:
                err(IssueCode.MISPLACED_MARKER, line=option.line)
            if not option.text:
                err(IssueCode.EMPTY_OPTION, line=option.line, letter=option.letter)
    starred = [o for o in options if o.starred]
    if "OPTIONS" in record.sections:
        if not starred:
            err(IssueCode.NO_CORRECT_ANSWER)
        elif len(starred) > 1:
            err(IssueCode.MULTIPLE_CORRECT_ANSWERS, count=len(starred),
                letters=", ".join(f"{o.letter})" for o in starred))

    answer_lines = [(ln, no) for ln, no in record.sections.get("CORRECT ANSWER", []) if ln.strip()]
    if "CORRECT ANSWER" in record.sections and len(starred) == 1:
        answer = parse_option_line(answer_lines[0][0], answer_lines[0][1]) if len(answer_lines) == 1 else None
        if answer is None or answer.letter != starred[0].letter or answer.text != starred[0].text:
            shown = answer_lines[0][0].strip()[:40] if answer_lines else "—"
            err(IssueCode.MASTER_ANSWER_MISMATCH,
                line=answer_lines[0][1] if answer_lines else record.start_line,
                answer=shown, starred=starred[0].letter)

    explanation = section_text("ACADEMIC EXPLANATION")
    if "ACADEMIC EXPLANATION" in record.sections and not explanation:
        err(IssueCode.EMPTY_EXPLANATION)

    if has_errors(item.issues) or qid is None:
        return item

    option_texts = tuple(o.text for o in options)
    item.issues.extend(
        content_warnings(qid=uid or "", file=file, line=record.start_line, stem=stem,
                         options=option_texts, explanation=explanation, check_sentence_count=False)
    )
    extras: list[tuple[str, str]] = [
        (name, record.meta[name][0]) for name in META_FIELDS if name not in ("TOPIC", "ORIGINAL ID")
    ]
    extras += [
        (name, section_text(name))
        for name in ("CLINICAL REASONING", "DISTRACTOR ANALYSIS", "EVIDENCE", "CLINICAL PEARL")
    ]
    extras.append((TRAILER, record.trailer or ""))
    item.question = ImportedQuestion(
        qid=qid,
        content=QuestionContent(
            stem=stem,
            options=option_texts,
            correct=AnswerLetter.from_letter(starred[0].letter),
            explanation=explanation,
        ),
        source=SourceLocation(kind=SourceKind.REFINED_MASTER, file=file, line=record.start_line),
        extras=tuple(extras),
    )
    return item
