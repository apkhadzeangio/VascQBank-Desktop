"""Format A — paired TXT files (VascQBank-10K convention).

``<CODE>_questions.txt``::

    1. Question text (may continue on following lines)
    ა) Option
    ბ) Option
    *გ) Correct option      <- ASCII '*' immediately before the letter, exactly once
    დ) Option
    ე) Option

``<CODE>_explanations.txt``::

    1. გ) Explanation text (may continue on following lines)

Parsing is line-based: a numbered line starts a new question when it is the
first item, follows a blank line, or follows answer options. This tolerates
stray blank lines inside a question and multiline stems, options and
explanations, while still reporting every structural problem with its line.
"""

from __future__ import annotations

import re
from collections import defaultdict
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
    OptionLine,
    ParsedItem,
    ParseResult,
    join_lines,
    looks_like_wrong_option_format,
    parse_option_line,
    read_source_file,
)
from vascqbank_desktop.validation.issues import IssueCode, ValidationIssue, has_errors, make_issue
from vascqbank_desktop.validation.question_rules import content_warnings, numbering_warnings

FILENAME = re.compile(r"^(?P<code>[A-Za-z][A-Za-z0-9_]*?)_(?P<kind>questions|explanations)\.txt$")
_QUESTION_START = re.compile(r"^(?P<number>\d+)\.\s+(?P<text>.*)$")
_EXPLANATION_START = re.compile(r"^(?P<number>\d+)\.\s+(?P<rest>.*)$")
_EXPLANATION_HEAD = re.compile(r"^(?P<pre>[\s*]*)(?P<letter>[ა-ჰ])\)\s*(?P<text>.*)$")


@dataclass(slots=True)
class _RawQuestion:
    number_text: str
    line: int
    stem_lines: list[str] = field(default_factory=list)
    options: list[OptionLine] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass(slots=True)
class _RawExplanation:
    number: int
    line: int
    letter: str | None
    text_lines: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_questions_text(text: str, file: str) -> tuple[list[_RawQuestion], list[ValidationIssue]]:
    questions: list[_RawQuestion] = []
    file_issues: list[ValidationIssue] = []
    current: _RawQuestion | None = None
    saw_blank = True

    for line_no, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            saw_blank = True
            continue
        start = _QUESTION_START.match(line)
        if start and (current is None or saw_blank or current.options):
            missing_blank = current is not None and bool(current.options) and not saw_blank
            current = _RawQuestion(number_text=start.group("number"), line=line_no)
            if missing_blank:
                current.issues.append(make_issue(IssueCode.MISSING_BLANK_LINE, file=file, line=line_no))
            current.stem_lines.append(start.group("text"))
            questions.append(current)
            saw_blank = False
            continue
        saw_blank = False
        if current is None:
            file_issues.append(
                make_issue(IssueCode.UNEXPECTED_TEXT, file=file, line=line_no, text=_clip(line.strip()))
            )
            continue
        option = parse_option_line(line, line_no)
        if option is not None:
            current.options.append(option)
            continue
        if looks_like_wrong_option_format(line):
            current.issues.append(make_issue(IssueCode.OPTION_FORMAT, file=file, line=line_no))
            continue
        if current.options:
            # continuation of a multiline answer option
            current.options[-1].text = (current.options[-1].text + "\n" + line.strip()).strip()
        else:
            current.stem_lines.append(line)
    return questions, file_issues


def parse_explanations_text(
    text: str, file: str
) -> tuple[list[_RawExplanation], list[ValidationIssue]]:
    explanations: list[_RawExplanation] = []
    file_issues: list[ValidationIssue] = []
    current: _RawExplanation | None = None
    saw_blank = True

    for line_no, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            saw_blank = True
            continue
        start = _EXPLANATION_START.match(line)
        if start and (current is None or saw_blank):
            number = int(start.group("number"))
            head = _EXPLANATION_HEAD.match(start.group("rest"))
            current = _RawExplanation(number=number, line=line_no, letter=None)
            explanations.append(current)
            if head is None:
                current.issues.append(make_issue(IssueCode.EXPLANATION_FORMAT, file=file, line=line_no))
                current.text_lines.append(start.group("rest"))
            else:
                if "*" in head.group("pre") or head.group("text").lstrip().startswith("*"):
                    current.issues.append(
                        make_issue(IssueCode.EXPLANATION_MARKER, file=file, line=line_no)
                    )
                current.letter = head.group("letter")
                current.text_lines.append(head.group("text").lstrip("* "))
            saw_blank = False
            continue
        saw_blank = False
        if current is None:
            file_issues.append(
                make_issue(IssueCode.UNEXPECTED_TEXT, file=file, line=line_no, text=_clip(line.strip()))
            )
            continue
        current.text_lines.append(line)
    return explanations, file_issues


def parse_pair(
    subject_code: str,
    questions_path: Path | None,
    explanations_path: Path | None,
) -> ParseResult:
    """Parse and validate one subject's pair of files."""
    result = ParseResult(kind=SourceKind.PAIRED_TXT, label=subject_code)
    q_name = questions_path.name if questions_path else f"{subject_code}_questions.txt"
    e_name = explanations_path.name if explanations_path else f"{subject_code}_explanations.txt"

    if not is_known_subject(subject_code):
        for path in (questions_path, explanations_path):
            if path is not None:
                result.issues.append(
                    make_issue(IssueCode.UNKNOWN_SUBJECT, file=path.name, subject=subject_code)
                )
        return result
    if questions_path is None:
        result.issues.append(make_issue(IssueCode.MISSING_QUESTIONS_FILE, file=e_name, expected=q_name))
        return result
    if explanations_path is None:
        result.issues.append(
            make_issue(IssueCode.MISSING_EXPLANATIONS_FILE, file=q_name, expected=e_name)
        )
        return result

    q_text, q_file, q_issues = read_source_file(questions_path)
    e_text, e_file, e_issues = read_source_file(explanations_path)
    result.files.extend(f for f in (q_file, e_file) if f is not None)
    result.issues.extend(q_issues + e_issues)
    if q_text is None or e_text is None:
        return result

    target = get_subject(subject_code).target_count
    raw_questions, issues = parse_questions_text(q_text, q_name)
    result.issues.extend(issues)
    raw_explanations, issues = parse_explanations_text(e_text, e_name)
    result.issues.extend(issues)

    explanations_by_number: dict[int, list[_RawExplanation]] = defaultdict(list)
    for exp in raw_explanations:
        explanations_by_number[exp.number].append(exp)
    question_lines: dict[int, list[int]] = defaultdict(list)
    for raw in raw_questions:
        question_lines[int(raw.number_text)].append(raw.line)

    for raw in raw_questions:
        result.items.append(
            _build_item(raw, subject_code, target, q_name, e_name, explanations_by_number, question_lines)
        )

    valid_numbers = [int(r.number_text) for r in raw_questions if int(r.number_text) > 0]
    result.issues.extend(numbering_warnings(valid_numbers, file=q_name, qid_prefix=subject_code))
    for number, exps in sorted(explanations_by_number.items()):
        if number not in question_lines:
            for exp in exps:
                result.issues.append(
                    make_issue(IssueCode.ORPHAN_EXPLANATION, file=e_name, line=exp.line, number=number)
                )
    return result


def _build_item(
    raw: _RawQuestion,
    code: str,
    target: int,
    q_name: str,
    e_name: str,
    explanations_by_number: dict[int, list[_RawExplanation]],
    question_lines: dict[int, list[int]],
) -> ParsedItem:
    number = int(raw.number_text)
    location = f"{q_name}:{raw.line}"
    qid = QuestionId(code, number) if number > 0 else None
    uid = str(qid) if qid else None
    item = ParsedItem(qid=qid, location=location, issues=list(raw.issues))

    def err(code_: IssueCode, line: int | None = raw.line, file: str = q_name, **params: object) -> None:
        item.issues.append(make_issue(code_, file=file, line=line, qid=uid, **params))

    if number <= 0:
        err(IssueCode.INVALID_NUMBER, number=raw.number_text)
    elif number > target:
        err(IssueCode.NUMBER_ABOVE_TARGET, number=number, target=target)
    if len(question_lines.get(number, [])) > 1:
        err(IssueCode.DUPLICATE_NUMBER, number=number,
            lines=", ".join(map(str, question_lines[number])))

    stem = join_lines(raw.stem_lines)
    if not stem:
        err(IssueCode.EMPTY_STEM)
    if "*" in stem:
        item.issues.append(make_issue(IssueCode.STRAY_ASTERISK, file=q_name, line=raw.line, qid=uid))

    letters = [o.letter for o in raw.options]
    if len(raw.options) != 5:
        err(IssueCode.OPTION_COUNT, count=len(raw.options))
    elif tuple(letters) != ANSWER_LETTERS:
        err(IssueCode.OPTION_LETTERS, letters=", ".join(f"{x})" for x in letters))
    for option in raw.options:
        if not option.marker_ok or "*" in option.text:
            err(IssueCode.MISPLACED_MARKER, line=option.line)
        if not option.text.strip():
            err(IssueCode.EMPTY_OPTION, line=option.line, letter=option.letter)
    starred = [o for o in raw.options if o.starred]
    if not starred:
        err(IssueCode.NO_CORRECT_ANSWER)
    elif len(starred) > 1:
        err(IssueCode.MULTIPLE_CORRECT_ANSWERS, count=len(starred),
            letters=", ".join(f"{o.letter})" for o in starred))

    explanation_text = ""
    explanation_line: int | None = None
    exps = explanations_by_number.get(number, [])
    if not exps:
        err(IssueCode.MISSING_EXPLANATION, number=number)
    elif len(exps) > 1:
        err(IssueCode.DUPLICATE_EXPLANATION, file=e_name, line=exps[0].line, number=number,
            lines=", ".join(str(e.line) for e in exps))
    else:
        exp = exps[0]
        explanation_line = exp.line
        item.issues.extend(
            ValidationIssue(i.code, i.severity, i.message, i.file, i.line, uid) for i in exp.issues
        )
        explanation_text = join_lines(exp.text_lines)
        if not explanation_text:
            err(IssueCode.EMPTY_EXPLANATION, file=e_name, line=exp.line)
        if exp.letter and len(starred) == 1 and exp.letter != starred[0].letter:
            err(IssueCode.EXPLANATION_LETTER_MISMATCH, file=e_name, line=exp.line,
                explanation_letter=exp.letter, correct=starred[0].letter)

    if has_errors(item.issues) or qid is None:
        return item

    options = tuple(o.text for o in raw.options)
    item.issues.extend(
        content_warnings(qid=uid or "", file=q_name, line=raw.line, stem=stem, options=options,
                         explanation=explanation_text, check_sentence_count=True)
    )
    item.question = ImportedQuestion(
        qid=qid,
        content=QuestionContent(
            stem=stem,
            options=options,
            correct=AnswerLetter.from_letter(starred[0].letter),
            explanation=explanation_text,
        ),
        source=SourceLocation(
            kind=SourceKind.PAIRED_TXT,
            file=q_name,
            line=raw.line,
            explanation_file=e_name,
            explanation_line=explanation_line,
        ),
    )
    return item
