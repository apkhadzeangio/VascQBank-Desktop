"""The editorial workflow: which action moves a question to which status (ARCHITECTURE.md §9).

Pure rules, no I/O. The persistence layer applies them inside one transaction; the UI only asks
which actions are currently available.

::

    PENDING_REVIEW ── START_REVIEW ──▶ IN_REVIEW          (also: the first saved edit)
    PENDING_REVIEW / IN_REVIEW ── APPROVE ──▶ APPROVED    (bound to the current revision)
    PENDING_REVIEW / IN_REVIEW / APPROVED ── REJECT ──▶ REJECTED   (a reason is mandatory)
    APPROVED / REJECTED ── REOPEN ──▶ IN_REVIEW

Saving an edit or restoring an old revision changes the content and follows
:func:`~vascqbank_desktop.domain.review_status.status_after_edit`: an approved question
loses its approval (it was bound to the previous revision) and a rejected one stays rejected
until it is explicitly reopened.
"""

from __future__ import annotations

from enum import StrEnum

from vascqbank_desktop.domain.review_status import ReviewStatus


class ReviewAction(StrEnum):
    """Status-changing review actions; the value is the ``review_events.action`` code."""

    START_REVIEW = "START_REVIEW"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REOPEN = "REOPEN"


_P, _I, _A, _R = (
    ReviewStatus.PENDING_REVIEW,
    ReviewStatus.IN_REVIEW,
    ReviewStatus.APPROVED,
    ReviewStatus.REJECTED,
)

_TRANSITIONS: dict[tuple[ReviewStatus, ReviewAction], ReviewStatus] = {
    (_P, ReviewAction.START_REVIEW): _I,
    (_P, ReviewAction.APPROVE): _A,
    (_I, ReviewAction.APPROVE): _A,
    (_P, ReviewAction.REJECT): _R,
    (_I, ReviewAction.REJECT): _R,
    (_A, ReviewAction.REJECT): _R,
    (_A, ReviewAction.REOPEN): _I,
    (_R, ReviewAction.REOPEN): _I,
}

# Repeating these on a question that is already there is not an error and writes nothing.
_IDEMPOTENT: dict[ReviewAction, ReviewStatus] = {
    ReviewAction.START_REVIEW: _I,
    ReviewAction.APPROVE: _A,
}


def next_status(status: ReviewStatus, action: ReviewAction) -> ReviewStatus | None:
    """The status after *action*, or ``None`` if the action is not allowed from *status*."""
    return _TRANSITIONS.get((status, action))


def is_repeat(status: ReviewStatus, action: ReviewAction) -> bool:
    """Whether *action* would change nothing because the question is already in its result state."""
    return _IDEMPOTENT.get(action) is status


def allowed_actions(status: ReviewStatus) -> tuple[ReviewAction, ...]:
    """The actions a reviewer can take on a question in *status*, in display order."""
    return tuple(action for action in ReviewAction if (status, action) in _TRANSITIONS)


def requires_reason(action: ReviewAction) -> bool:
    """Rejecting needs a written reason; the others take an optional note."""
    return action is ReviewAction.REJECT


def revokes_approval(status: ReviewStatus, action: ReviewAction) -> bool:
    """Whether the action takes an approved question out of ``APPROVED``."""
    return status is ReviewStatus.APPROVED and action in (ReviewAction.REJECT, ReviewAction.REOPEN)


def is_exportable(status: ReviewStatus, approved_revision_id: int | None) -> bool:
    """The export rule (ARCHITECTURE.md §9): only ``APPROVED`` questions that carry an approved
    revision — and always *that* revision, never the current one or a draft."""
    return status is ReviewStatus.APPROVED and approved_revision_id is not None
