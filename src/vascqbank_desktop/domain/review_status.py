"""Review statuses.

The codes and Georgian labels match the VascQBank-Review web application so
that data and terminology stay consistent between the two tools. Allowed
transitions are designed in ARCHITECTURE.md and implemented in Phase 2.
"""

from __future__ import annotations

from enum import StrEnum


class ReviewStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    @property
    def label_ka(self) -> str:
        return _LABELS_KA[self]


_LABELS_KA: dict[ReviewStatus, str] = {
    ReviewStatus.PENDING_REVIEW: "განხილვის მოლოდინში",
    ReviewStatus.IN_REVIEW: "განხილვის პროცესში",
    ReviewStatus.APPROVED: "დამტკიცებული",
    ReviewStatus.REJECTED: "უარყოფილი",
}


def status_after_edit(status: ReviewStatus) -> ReviewStatus:
    """The status a question has after a *new revision is saved* (ARCHITECTURE.md §9).

    A first edit moves ``PENDING_REVIEW`` to ``IN_REVIEW``; editing an ``APPROVED`` question
    revokes the approval (it was bound to an exact revision) and also gives ``IN_REVIEW``.
    ``IN_REVIEW`` stays; ``REJECTED`` stays until it is explicitly reopened. Typing in a
    draft never calls this: only a saved revision can change a status.
    """
    if status in (ReviewStatus.PENDING_REVIEW, ReviewStatus.APPROVED):
        return ReviewStatus.IN_REVIEW
    return status


def edit_revokes_approval(status: ReviewStatus) -> bool:
    return status is ReviewStatus.APPROVED
