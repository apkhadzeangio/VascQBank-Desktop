"""Value types for browsing and searching the question catalog.

Everything here is immutable and free of Qt and SQLite, so the list model,
the detail panel and the services exchange plain data. A question is always
identified by its database id / immutable ``question_uid``; filters and sort
orders only decide *which* questions are listed and in what order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from vascqbank_desktop.domain.question import QuestionContent, SourceKind
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.search import Preview, split_terms


class SortOrder(StrEnum):
    NUMBER = "NUMBER"  # subject order, then original question number
    STATUS = "STATUS"  # pending, in review, rejected, approved — then as NUMBER
    MODIFIED = "MODIFIED"  # most recently modified first — then as NUMBER


class SearchMode(StrEnum):
    BROWSE = "BROWSE"  # no search text
    FTS = "FTS"  # FTS5 trigram index
    LIKE = "LIKE"  # substring scan of the current revisions


@dataclass(frozen=True, slots=True)
class CatalogFilter:
    subject_code: str | None = None
    status: ReviewStatus | None = None
    text: str = ""
    sort: SortOrder = SortOrder.NUMBER

    @property
    def terms(self) -> tuple[str, ...]:
        return split_terms(self.text)

    @property
    def is_search(self) -> bool:
        return bool(self.terms)


@dataclass(frozen=True, slots=True)
class QuestionSummary:
    """One catalog row: just enough to list a question, never its history."""

    id: int
    uid: str
    subject_code: str
    source_number: int
    status: ReviewStatus
    preview: Preview


@dataclass(frozen=True, slots=True)
class QuestionLocation:
    """Where a question sits, independent of any filter."""

    id: int
    uid: str
    subject_code: str
    source_number: int
    status: ReviewStatus


@dataclass(frozen=True, slots=True)
class QuestionDetail:
    """Everything the read-only detail panel shows for the current revision."""

    id: int
    uid: str
    subject_code: str
    subject_name_ka: str
    source_number: int
    status: ReviewStatus
    content: QuestionContent
    revision_id: int
    revision_no: int
    lock_version: int
    source_kind: SourceKind
    source_file: str
    source_line: int
    import_batch_id: int
    created_at: str  # UTC ISO-8601: when it was imported
    modified_at: str  # UTC ISO-8601
    approved_at: str | None = None


@dataclass(frozen=True, slots=True)
class ResultSet:
    """The ordered ids matching a filter; content is fetched lazily page by page."""

    ids: tuple[int, ...] = ()
    terms: tuple[str, ...] = ()
    mode: SearchMode = SearchMode.BROWSE
    _positions: dict[int, int] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_positions", {qid: i for i, qid in enumerate(self.ids)})

    def __len__(self) -> int:
        return len(self.ids)

    def position_of(self, question_id: int | None) -> int | None:
        return None if question_id is None else self._positions.get(question_id)

    def __contains__(self, question_id: object) -> bool:
        return question_id in self._positions

    def next_id(self, current: int | None) -> int | None:
        """The question after *current*; the first one if *current* is not listed."""
        if not self.ids:
            return None
        position = self.position_of(current)
        if position is None:
            return self.ids[0]
        return self.ids[position + 1] if position + 1 < len(self.ids) else None

    def previous_id(self, current: int | None) -> int | None:
        """The question before *current*; the last one if *current* is not listed."""
        if not self.ids:
            return None
        position = self.position_of(current)
        if position is None:
            return self.ids[-1]
        return self.ids[position - 1] if position > 0 else None


@dataclass(frozen=True, slots=True)
class RevisionInfo:
    """One row of a question's revision history (no text — see :class:`RevisionDetail`)."""

    id: int
    question_id: int
    revision_no: int
    origin: str  # IMPORT / EDIT / RESTORE
    author: str
    note: str | None
    created_at: str  # UTC ISO-8601
    is_current: bool
    was_approved: bool  # an APPROVE event is bound to exactly this revision
    from_status: ReviewStatus | None = None  # status change recorded with this revision, if any
    to_status: ReviewStatus | None = None
    event_note: str | None = None


@dataclass(frozen=True, slots=True)
class RevisionDetail:
    info: RevisionInfo
    content: QuestionContent
