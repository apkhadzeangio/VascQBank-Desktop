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
