"""Turning the remembered working session into a valid starting point.

The saved session (subject, status filter, last question) was valid when it
was written; by the next launch the database may have changed — a question's
status moved on, a subject was emptied, the database was replaced. This module
decides what to show instead of failing, and says why:

1. an unknown or now-empty subject is dropped (all subjects are shown);
2. the remembered question is selected when it still matches the filter;
3. if it exists but no longer matches (for example it was approved while the
   filter showed "in review"), the *closest following* question that does match
   is selected — the reviewer continues from the same place — or the closest
   preceding one when nothing follows;
4. if the filter matches nothing at all, the status filter is cleared and the
   remembered question itself is shown;
5. if the question no longer exists, the first question of the filter is shown.

Nothing is written: restoring a session never touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.settings_service import SavedSession


class ContextNotice(StrEnum):
    NONE = "NONE"
    SUBJECT_RESET = "SUBJECT_RESET"  # saved subject unknown or without questions
    QUESTION_FILTERED = "QUESTION_FILTERED"  # exists, but not in the filter: nearest shown
    FILTER_RESET = "FILTER_RESET"  # the filter matched nothing and was cleared
    QUESTION_MISSING = "QUESTION_MISSING"  # no longer in the database


@dataclass(frozen=True, slots=True)
class RestoredContext:
    subject_code: str | None = None
    status: ReviewStatus | None = None
    question_id: int | None = None
    requested_uid: str | None = None  # what had been remembered
    notice: ContextNotice = ContextNotice.NONE


def restore_context(catalog: CatalogService, saved: SavedSession) -> RestoredContext:
    subject, status = saved.subject_code, saved.status
    notice = ContextNotice.NONE

    if subject is not None and catalog.count(subject, None) == 0:
        subject, notice = None, ContextNotice.SUBJECT_RESET

    uid = saved.question_uid
    if uid is None:
        return RestoredContext(subject, status, None, None, notice)

    location = catalog.locate(uid)
    if location is None:
        first = catalog.first_id(subject, status)
        if first is None and status is not None:
            status, notice = None, ContextNotice.FILTER_RESET
            first = catalog.first_id(subject, None)
        if first is None and subject is not None:
            subject = None
            first = catalog.first_id(None, status)
        question_missing = ContextNotice.QUESTION_MISSING
        return RestoredContext(subject, status, first, uid, question_missing)

    matches = (subject is None or location.subject_code == subject) and (
        status is None or location.status == status
    )
    if matches:
        return RestoredContext(subject, status, location.id, uid, notice)

    nearest = catalog.nearest_id(uid, subject, status)
    if nearest is not None:
        return RestoredContext(subject, status, nearest, uid, ContextNotice.QUESTION_FILTERED)
    return RestoredContext(location.subject_code, None, location.id, uid, ContextNotice.FILTER_RESET)
