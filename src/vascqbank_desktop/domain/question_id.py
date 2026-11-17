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
