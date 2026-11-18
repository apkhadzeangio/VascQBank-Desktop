"""Paired-TXT export: the VascQBank-10K source convention, written back exactly.

For one subject::

    <CODE>_questions.txt                 <CODE>_explanations.txt

    42. Question text                    42. ბ) Explanation text.
    ა) Option A                          (continuation lines follow as written)
    *ბ) Correct option B
    გ) Option C                          43. დ) …
    დ) Option D
    ე) Option E

    43. …

* the original source number is written (``42.``), never a renumbered position, so a partial
  export has gaps — the importer accepts them;
* exactly one ``*`` directly before the correct letter; five options in ა…ე order;
* UTF-8 without BOM, LF line ends, one blank line between questions;
* text is written exactly as approved — no Markdown, no added characters.

The format has no escape mechanism, so a few texts cannot be written *unambiguously* (a blank
line inside a field would split the question, a continuation line that looks like ``ბ) …``
would be read as an option, ``*`` inside an option would be read as a marker …).
:func:`txt_problems` finds those before anything is written; the exporter refuses such a TXT
export and names the questions instead of changing their text. After writing, the files are
parsed back with the real importer and compared field by field (``export_service``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from vascqbank_desktop.domain.answers import ANSWER_LETTERS
from vascqbank_desktop.domain.question import QuestionContent


class ExportableQuestion(Protocol):
    """What the writers need (``ApprovedQuestion`` satisfies it)."""

    uid: str
    subject_code: str
    subject_name_ka: str
    source_number: int
    content: QuestionContent


class TxtProblem(StrEnum):
    BLANK_LINE = "BLANK_LINE"  # an empty line inside a field would end the question
    ASTERISK = "ASTERISK"  # '*' in the question or an option is read as an answer marker
    LOOKS_LIKE_OPTION = "LOOKS_LIKE_OPTION"  # a continuation line would be read as an answer option
    LOOKS_LIKE_NUMBER = "LOOKS_LIKE_NUMBER"  # a continuation line of an option would start a question
    EDGE_SPACES = "EDGE_SPACES"  # spaces at a line edge that the reader trims
    LEADING_MARKER = "LEADING_MARKER"  # an explanation starting with '*'


@dataclass(frozen=True, slots=True)
class TxtIssue:
    uid: str
    field: str  # stem / option_a … option_e / explanation
    problem: TxtProblem


QUESTIONS_SUFFIX = "_questions.txt"
EXPLANATIONS_SUFFIX = "_explanations.txt"

_OPTION_LIKE = re.compile(r"^[\s*]*[ა-ჰ]\)")
_WRONG_OPTION_LIKE = re.compile(r"^[\s*]*[აბგდე](?:[.:]\s+\S|\*\))")
_NUMBERED = re.compile(r"^\d+\.\s")


def questions_filename(code: str) -> str:
    return f"{code}{QUESTIONS_SUFFIX}"


def explanations_filename(code: str) -> str:
    return f"{code}{EXPLANATIONS_SUFFIX}"


def _field_problems(uid: str, name: str, text: str, *, option: bool, explanation: bool) -> list[TxtIssue]:
    found: list[TxtProblem] = []
    lines = text.split("\n")
    if any(not line.strip() for line in lines):
        found.append(TxtProblem.BLANK_LINE)
    if not explanation and "*" in text:
        found.append(TxtProblem.ASTERISK)
    if explanation and text.lstrip().startswith("*"):
        found.append(TxtProblem.LEADING_MARKER)
    continuation = lines[1:]
    if not explanation and any(
        _OPTION_LIKE.match(line) or _WRONG_OPTION_LIKE.match(line) for line in continuation
    ):
        found.append(TxtProblem.LOOKS_LIKE_OPTION)
    if option and any(_NUMBERED.match(line) for line in continuation):
        found.append(TxtProblem.LOOKS_LIKE_NUMBER)
    if any(line != line.rstrip() for line in lines) or (
        option and any(line != line.lstrip() for line in lines)
    ):
        found.append(TxtProblem.EDGE_SPACES)
    return [TxtIssue(uid, name, problem) for problem in dict.fromkeys(found)]


def txt_problems(questions: Iterable[ExportableQuestion]) -> list[TxtIssue]:
    """Texts that the paired-TXT format cannot carry unchanged (empty list = all can)."""
    issues: list[TxtIssue] = []
    for q in questions:
        c = q.content
        issues += _field_problems(q.uid, "stem", c.stem, option=False, explanation=False)
        for letter_field, text in zip(
            ("option_a", "option_b", "option_c", "option_d", "option_e"), c.options, strict=True
        ):
            issues += _field_problems(q.uid, letter_field, text, option=True, explanation=False)
        issues += _field_problems(q.uid, "explanation", c.explanation, option=False, explanation=True)
    return issues


def _ordered(questions: Sequence[ExportableQuestion]) -> list[ExportableQuestion]:
    return sorted(questions, key=lambda q: q.source_number)


def render_questions(questions: Sequence[ExportableQuestion]) -> str:
    """The ``<CODE>_questions.txt`` text of one subject's questions."""
    blocks: list[str] = []
    for q in _ordered(questions):
        c = q.content
        stem = c.stem.split("\n")
        lines = [f"{q.source_number}. {stem[0]}", *stem[1:]]
        for index, (letter, text) in enumerate(zip(ANSWER_LETTERS, c.options, strict=True)):
            option = text.split("\n")
            marker = "*" if index == c.correct.index else ""
            lines.append(f"{marker}{letter}) {option[0]}")
            lines.extend(option[1:])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def render_explanations(questions: Sequence[ExportableQuestion]) -> str:
    """The ``<CODE>_explanations.txt`` text of one subject's questions."""
    blocks: list[str] = []
    for q in _ordered(questions):
        c = q.content
        text = c.explanation.split("\n")
        blocks.append("\n".join([f"{q.source_number}. {c.correct.value}) {text[0]}", *text[1:]]))
    return "\n\n".join(blocks) + "\n"
