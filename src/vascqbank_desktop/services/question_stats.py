"""Question counts per subject and status, for the sidebar and home page."""

from __future__ import annotations

from dataclasses import dataclass, field

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import question_repository as repo
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import SUBJECTS


@dataclass(frozen=True, slots=True)
class SubjectCounts:
    code: str
    total: int = 0
    by_status: dict[ReviewStatus, int] = field(default_factory=dict)

    def count(self, status: ReviewStatus) -> int:
        return self.by_status.get(status, 0)


@dataclass(frozen=True, slots=True)
class BankCounts:
    subjects: dict[str, SubjectCounts]

    @property
    def total(self) -> int:
        return sum(s.total for s in self.subjects.values())

    def total_with(self, status: ReviewStatus) -> int:
        return sum(s.count(status) for s in self.subjects.values())


@dataclass(frozen=True, slots=True)
class ProgressSummary:
    """Review progress of the whole bank or of one subject (always real database counts)."""

    total: int
    by_status: dict[ReviewStatus, int]

    def count(self, status: ReviewStatus) -> int:
        return self.by_status.get(status, 0)

    @property
    def approved_share(self) -> float:
        """Fraction of the questions that are approved (0.0 when there are none)."""
        return self.count(ReviewStatus.APPROVED) / self.total if self.total else 0.0


def summarize(bank: BankCounts, subject_code: str | None = None) -> ProgressSummary:
    """Progress for *subject_code*, or for the whole bank when it is ``None``."""
    if subject_code is None:
        return ProgressSummary(bank.total, {s: bank.total_with(s) for s in ReviewStatus})
    counts = bank.subjects.get(subject_code, SubjectCounts(subject_code))
    return ProgressSummary(counts.total, {s: counts.count(s) for s in ReviewStatus})


def bank_counts(paths: AppPaths) -> BankCounts:
    conn = open_database(paths.database_file)
    try:
        raw = repo.count_by_subject_and_status(conn)
    finally:
        conn.close()
    subjects: dict[str, SubjectCounts] = {}
    for subject in SUBJECTS:
        statuses = {ReviewStatus(k): v for k, v in raw.get(subject.code, {}).items()}
        subjects[subject.code] = SubjectCounts(subject.code, sum(statuses.values()), statuses)
    return BankCounts(subjects)
