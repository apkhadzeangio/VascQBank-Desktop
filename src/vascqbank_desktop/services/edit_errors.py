"""Errors of the editing services, specific enough for the UI to explain them in Georgian."""

from __future__ import annotations

import sqlite3
from enum import StrEnum

from vascqbank_desktop.domain.editing import FieldProblem
from vascqbank_desktop.domain.review_status import ReviewStatus


class EditError(Exception):
    """Base class: the edit or draft write did **not** happen (nothing was changed)."""


class ReviewerRequired(EditError):
    """No reviewer name has been chosen on this computer."""


class ValidationFailed(EditError):
    def __init__(self, problems: list[FieldProblem]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} validation problem(s)")


class EditTargetMissing(EditError):
    def __init__(self, question_id: int) -> None:
        self.question_id = question_id
        super().__init__(f"question {question_id} does not exist")


class EditConflict(EditError):
    """The question was saved by someone else after the editor loaded it."""

    def __init__(
        self,
        question_id: int,
        expected_lock_version: int,
        current_lock_version: int,
        current_revision_no: int,
        current_status: ReviewStatus,
    ) -> None:
        self.question_id = question_id
        self.expected_lock_version = expected_lock_version
        self.current_lock_version = current_lock_version
        self.current_revision_no = current_revision_no
        self.current_status = current_status
        super().__init__(f"question {question_id} was changed (revision {current_revision_no})")


class InvalidTransition(EditError):
    """The action is not allowed from the question's current status (e.g. approving a rejected
    question, which must be reopened first)."""

    def __init__(self, question_id: int, action: str, status: ReviewStatus) -> None:
        self.question_id = question_id
        self.action = action
        self.status = status
        super().__init__(f"{action} is not allowed on question {question_id} in status {status.value}")


class ReasonRequired(EditError):
    """Rejecting a question needs a written reason."""


class UnresolvedDraft(EditError):
    """The question has an unsaved draft that differs from its saved text.

    Approving (or restoring over) would leave the reviewer unsure which text is meant, so the
    caller must first save the draft, discard it, or — for a restore — explicitly keep it.
    """

    def __init__(self, question_id: int, saved_at: str, reviewer_name: str) -> None:
        self.question_id = question_id
        self.saved_at = saved_at
        self.reviewer_name = reviewer_name
        super().__init__(f"question {question_id} has an unresolved draft ({saved_at}, {reviewer_name})")


class RestoreSourceInvalid(EditError):
    """The revision to restore does not exist or belongs to another question."""


class StorageProblem(StrEnum):
    DISK_FULL = "DISK_FULL"
    LOCKED = "LOCKED"  # another program or window holds the database
    READ_ONLY = "READ_ONLY"
    IO_ERROR = "IO_ERROR"
    OTHER = "OTHER"


class StorageFailure(EditError):
    """SQLite could not complete the write; the transaction was rolled back."""

    def __init__(self, problem: StorageProblem, detail: str) -> None:
        self.problem = problem
        self.detail = detail
        super().__init__(f"{problem.value}: {detail}")

    @classmethod
    def from_exception(cls, exc: BaseException) -> StorageFailure:
        text = str(exc)
        lowered = text.lower()
        if "disk is full" in lowered or "database or disk is full" in lowered:
            problem = StorageProblem.DISK_FULL
        elif "locked" in lowered or "busy" in lowered:
            problem = StorageProblem.LOCKED
        elif "readonly" in lowered or "read-only" in lowered:
            problem = StorageProblem.READ_ONLY
        elif "i/o error" in lowered or isinstance(exc, OSError):
            problem = StorageProblem.IO_ERROR
        else:
            problem = StorageProblem.OTHER
        return cls(problem, text)


STORAGE_ERRORS: tuple[type[BaseException], ...] = (sqlite3.Error, OSError)
