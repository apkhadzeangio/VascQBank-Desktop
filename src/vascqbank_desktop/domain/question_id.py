"""Immutable question identity.

A question is identified by its subject code plus its original source number,
formatted as ``<CODE>-<NNNN>`` (e.g. ``ALI-0001``). This is the same format the
VascQBank-Review web application uses (``formatExternalId``), so an ID means
the same question in both tools. Content hashes are used only to detect
upstream changes, never as identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from vascqbank_desktop.domain.subjects import is_known_subject

_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]{1,31})-(\d{4,})$")


@dataclass(frozen=True, slots=True, order=True)
class QuestionId:
    subject_code: str
    source_number: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,31}", self.subject_code):
            raise ValueError(f"Invalid subject code: {self.subject_code!r}")
        if isinstance(self.source_number, bool) or not isinstance(self.source_number, int):
            raise TypeError("source_number must be an int")
        if self.source_number <= 0:
            raise ValueError(f"Invalid source number: {self.source_number}")

    def __str__(self) -> str:
        return format_question_id(self.subject_code, self.source_number)

    @classmethod
    def parse(cls, text: str) -> QuestionId:
        match = _PATTERN.fullmatch(text)
        if match is None:
            raise ValueError(f"Invalid question ID: {text!r}")
        qid = cls(match.group(1), int(match.group(2)))
        # Reject non-canonical forms such as "ALI-00001".
        if str(qid) != text:
            raise ValueError(f"Non-canonical question ID: {text!r}")
        return qid


def format_question_id(subject_code: str, source_number: int) -> str:
    digits = f"{source_number:04d}" if source_number < 10_000 else str(source_number)
    return f"{subject_code}-{digits}"


class ReferenceProblem(StrEnum):
    EMPTY = "EMPTY"
    NEEDS_SUBJECT = "NEEDS_SUBJECT"
    UNKNOWN_SUBJECT = "UNKNOWN_SUBJECT"
    BAD_FORMAT = "BAD_FORMAT"


class InvalidReference(ValueError):
    """What the reviewer typed into "go to question" is not a usable reference."""

    def __init__(self, problem: ReferenceProblem, text: str) -> None:
        self.problem = problem
        self.text = text
        super().__init__(f"{problem.value}: {text!r}")


# subject code (lazy, so "ALI0042" and "PAD_CLTI-0001" both split correctly),
# optional separator, number.
_REFERENCE = re.compile(r"([A-Z][A-Z0-9_]*?)[\s_\-–—]*(\d{1,7})")
_MAX_REFERENCE_LENGTH = 64


def _upper_ascii(text: str) -> str:
    # str.upper() would turn Georgian letters into Mtavruli capitals; subject
    # codes are ASCII, so only ASCII letters are folded.
    return "".join(c.upper() if "a" <= c <= "z" else c for c in text)


def parse_reference(text: str, default_subject: str | None = None) -> QuestionId:
    """Interpret what a reviewer typed as a question reference.

    Accepted: the full ID in any case and with tolerant separators
    (``ALI-0042``, ``ali 42``, ``PAD_CLTI-1``) or a bare question number, which
    is resolved inside *default_subject* (the subject currently open).
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        raise InvalidReference(ReferenceProblem.EMPTY, text)
    if len(cleaned) > _MAX_REFERENCE_LENGTH:
        raise InvalidReference(ReferenceProblem.BAD_FORMAT, text)
    cleaned = _upper_ascii(cleaned)

    if cleaned.isascii() and cleaned.isdigit():
        if not default_subject:
            raise InvalidReference(ReferenceProblem.NEEDS_SUBJECT, text)
        code, number = default_subject, int(cleaned)
    else:
        match = _REFERENCE.fullmatch(cleaned)
        if match is None:
            raise InvalidReference(ReferenceProblem.BAD_FORMAT, text)
        code, number = match.group(1), int(match.group(2))
    if number <= 0:
        raise InvalidReference(ReferenceProblem.BAD_FORMAT, text)
    if not is_known_subject(code):
        raise InvalidReference(ReferenceProblem.UNKNOWN_SUBJECT, text)
    return QuestionId(code, number)
