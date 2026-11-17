"""Rules for editing a question: normalisation, validation, comparison.

Editing is plain text only — no formatting. Every field is normalised the same way as
imported text (no BOM, LF line endings, Unicode NFC) and trimmed, so a Georgian text that is
saved and read back is identical, and "did anything change?" is decided on normalised
content (saving an untouched question must not create a revision).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.validation.text import normalize_text

MAX_FIELD_LENGTH = 20_000  # characters per field; far above any real question
MAX_NOTE_LENGTH = 500


class EditField(StrEnum):
    STEM = "stem"
    OPTION_A = "option_a"
    OPTION_B = "option_b"
    OPTION_C = "option_c"
    OPTION_D = "option_d"
    OPTION_E = "option_e"
    CORRECT = "correct"
    EXPLANATION = "explanation"

    @property
    def option_letter(self) -> str | None:
        """ა…ე for an option field, otherwise ``None``."""
        return _OPTION_LETTERS.get(self)


OPTION_FIELDS: tuple[EditField, ...] = (
    EditField.OPTION_A,
    EditField.OPTION_B,
    EditField.OPTION_C,
    EditField.OPTION_D,
    EditField.OPTION_E,
)
_OPTION_LETTERS: dict[EditField, str] = dict(zip(OPTION_FIELDS, ANSWER_LETTERS, strict=True))


class ProblemKind(StrEnum):
    EMPTY = "EMPTY"
    TOO_LONG = "TOO_LONG"
    CONTROL_CHARACTERS = "CONTROL_CHARACTERS"
    NOTE_TOO_LONG = "NOTE_TOO_LONG"


@dataclass(frozen=True, slots=True)
class FieldProblem:
    field: EditField | None  # ``None`` for the edit note
    kind: ProblemKind


def normalize_field(text: str) -> str:
    """BOM removed, LF line endings, NFC, outer whitespace trimmed."""
    return normalize_text(text).strip()


def normalize_content(content: QuestionContent) -> QuestionContent:
    return QuestionContent(
        stem=normalize_field(content.stem),
        options=tuple(normalize_field(o) for o in content.options),
        correct=content.correct,
        explanation=normalize_field(content.explanation),
    )


def normalize_note(note: str | None) -> str | None:
    """A one-line note, or ``None`` when empty."""
    if note is None:
        return None
    flat = " ".join(normalize_text(note).split())
    return flat or None


def content_fields(content: QuestionContent) -> dict[EditField, str]:
    fields = {EditField.STEM: content.stem}
    fields.update({f: text for f, text in zip(OPTION_FIELDS, content.options, strict=True)})
    fields[EditField.CORRECT] = content.correct.value
    fields[EditField.EXPLANATION] = content.explanation
    return fields


def changed_fields(old: QuestionContent, new: QuestionContent) -> tuple[EditField, ...]:
    """Which fields differ, compared after normalisation."""
    before, after = content_fields(normalize_content(old)), content_fields(normalize_content(new))
    return tuple(f for f in EditField if before[f] != after[f])


def same_content(old: QuestionContent, new: QuestionContent) -> bool:
    return not changed_fields(old, new)


def _has_control_characters(text: str) -> bool:
    return any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text)


def validate_content(content: QuestionContent, note: str | None = None) -> list[FieldProblem]:
    """Everything that prevents saving a revision; an empty list means the content is valid.

    Checked on the normalised text: the stem, each of the five options and the explanation
    must be non-empty, free of control characters and within :data:`MAX_FIELD_LENGTH`. There
    is always exactly one correct answer (a single :class:`AnswerLetter`).
    """
    if not isinstance(content.correct, AnswerLetter):
        raise TypeError("correct must be an AnswerLetter")
    problems: list[FieldProblem] = []
    for field, text in content_fields(normalize_content(content)).items():
        if field is EditField.CORRECT:
            continue
        if not text:
            problems.append(FieldProblem(field, ProblemKind.EMPTY))
        elif len(text) > MAX_FIELD_LENGTH:
            problems.append(FieldProblem(field, ProblemKind.TOO_LONG))
        elif _has_control_characters(text):
            problems.append(FieldProblem(field, ProblemKind.CONTROL_CHARACTERS))
    cleaned_note = normalize_note(note)
    if cleaned_note is not None and len(cleaned_note) > MAX_NOTE_LENGTH:
        problems.append(FieldProblem(None, ProblemKind.NOTE_TOO_LONG))
    return problems
