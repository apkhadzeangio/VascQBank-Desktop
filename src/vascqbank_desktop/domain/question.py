"""Question content and the source-independent import representation.

Both import adapters (paired TXT and refined master) produce
:class:`ImportedQuestion`; everything downstream — validation, conflict
detection, persistence — works on this one type.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum

from vascqbank_desktop.domain.answers import OPTION_COUNT, AnswerLetter
from vascqbank_desktop.domain.question_id import QuestionId


def _sha256_json(payload: object) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class QuestionContent:
    """The editable part of a question: stem, five options, answer, explanation."""

    stem: str
    options: tuple[str, ...]
    correct: AnswerLetter
    explanation: str

    def __post_init__(self) -> None:
        if len(self.options) != OPTION_COUNT:
            raise ValueError(f"exactly {OPTION_COUNT} options required, got {len(self.options)}")
        if not isinstance(self.correct, AnswerLetter):
            raise TypeError("correct must be an AnswerLetter")

    def option(self, letter: AnswerLetter) -> str:
        return self.options[letter.index]

    @property
    def correct_text(self) -> str:
        return self.option(self.correct)

    def content_hash(self) -> str:
        """SHA-256 of the content; equal content ⇔ equal hash."""
        return _sha256_json([self.stem, list(self.options), self.correct.value, self.explanation])


class SourceKind(StrEnum):
    PAIRED_TXT = "PAIRED_TXT"
    REFINED_MASTER = "REFINED_MASTER"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    kind: SourceKind
    file: str
    line: int
    explanation_file: str | None = None
    explanation_line: int | None = None

    def describe(self) -> str:
        text = f"{self.file}:{self.line}"
        if self.explanation_file:
            text += f" + {self.explanation_file}:{self.explanation_line}"
        return text


@dataclass(frozen=True, slots=True)
class ImportedQuestion:
    """One valid question read from a source file, before it reaches the database.

    ``extras`` keeps every additional field of the source in order (for the
    refined master: academic sections, QA metadata, provenance) so nothing in
    the original is silently dropped.
    """

    qid: QuestionId
    content: QuestionContent
    source: SourceLocation
    extras: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def uid(self) -> str:
        return str(self.qid)

    def original_payload(self) -> dict[str, object]:
        """Complete, JSON-serialisable original content (stored immutably)."""
        return {
            "question_uid": self.uid,
            "subject_code": self.qid.subject_code,
            "source_number": self.qid.source_number,
            "source_kind": self.source.kind.value,
            "stem": self.content.stem,
            "options": {
                letter.value: text
                for letter, text in zip(AnswerLetter, self.content.options, strict=True)
            },
            "correct": self.content.correct.value,
            "explanation": self.content.explanation,
            "extras": [list(pair) for pair in self.extras],
        }

    def fingerprint(self) -> str:
        """Identifies the imported content (not the source location)."""
        payload = self.original_payload()
        payload.pop("source_kind")
        return _sha256_json(payload)
