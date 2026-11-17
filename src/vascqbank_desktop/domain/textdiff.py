"""Word-level comparison of two versions of a question (history, conflicts, outdated drafts)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from vascqbank_desktop.domain.editing import EditField, content_fields, normalize_content
from vascqbank_desktop.domain.question import QuestionContent

_TOKENS = re.compile(r"\s+|\S+")


class DiffKind(StrEnum):
    SAME = "SAME"
    REMOVED = "REMOVED"  # present in the old text only
    ADDED = "ADDED"  # present in the new text only


@dataclass(frozen=True, slots=True)
class DiffSegment:
    kind: DiffKind
    text: str


@dataclass(frozen=True, slots=True)
class FieldDiff:
    field: EditField
    old: str
    new: str
    segments: tuple[DiffSegment, ...]

    @property
    def changed(self) -> bool:
        return self.old != self.new


def diff_words(old: str, new: str) -> tuple[DiffSegment, ...]:
    """Word-level diff keeping all whitespace; concatenating SAME+REMOVED gives *old*,
    SAME+ADDED gives *new*."""
    a, b = _TOKENS.findall(old), _TOKENS.findall(new)
    segments: list[DiffSegment] = []

    def add(kind: DiffKind, text: str) -> None:
        if not text:
            return
        if segments and segments[-1].kind is kind:
            segments[-1] = DiffSegment(kind, segments[-1].text + text)
        else:
            segments.append(DiffSegment(kind, text))

    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            add(DiffKind.SAME, "".join(a[i1:i2]))
        else:
            add(DiffKind.REMOVED, "".join(a[i1:i2]))
            add(DiffKind.ADDED, "".join(b[j1:j2]))
    return tuple(segments)


def compare_contents(old: QuestionContent, new: QuestionContent) -> tuple[FieldDiff, ...]:
    """One :class:`FieldDiff` per field (question, options ა–ე, correct answer, explanation)."""
    before = content_fields(normalize_content(old))
    after = content_fields(normalize_content(new))
    return tuple(FieldDiff(f, before[f], after[f], diff_words(before[f], after[f])) for f in EditField)
