"""The editorial state machine as pure rules."""

from __future__ import annotations

import pytest

from vascqbank_desktop.domain.review_status import ReviewStatus, edit_revokes_approval, status_after_edit
from vascqbank_desktop.domain.workflow import (
    ReviewAction,
    allowed_actions,
    is_exportable,
    is_repeat,
    next_status,
    requires_reason,
    revokes_approval,
)

P, IR, A, R = (
    ReviewStatus.PENDING_REVIEW,
    ReviewStatus.IN_REVIEW,
    ReviewStatus.APPROVED,
    ReviewStatus.REJECTED,
)


@pytest.mark.parametrize(
    ("status", "action", "expected"),
    [
        (P, ReviewAction.START_REVIEW, IR),
        (P, ReviewAction.APPROVE, A),
        (IR, ReviewAction.APPROVE, A),
        (P, ReviewAction.REJECT, R),
        (IR, ReviewAction.REJECT, R),
        (A, ReviewAction.REJECT, R),
        (A, ReviewAction.REOPEN, IR),
        (R, ReviewAction.REOPEN, IR),
    ],
)
def test_the_allowed_transitions(status, action, expected) -> None:
    assert next_status(status, action) is expected


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (IR, ReviewAction.START_REVIEW),
        (A, ReviewAction.START_REVIEW),
        (R, ReviewAction.START_REVIEW),
        (A, ReviewAction.APPROVE),
        (R, ReviewAction.APPROVE),
        (R, ReviewAction.REJECT),
        (P, ReviewAction.REOPEN),
        (IR, ReviewAction.REOPEN),
    ],
)
def test_every_other_combination_is_refused(status, action) -> None:
    assert next_status(status, action) is None


def test_the_table_covers_every_status_and_action() -> None:
    covered = {(s, a) for s in ReviewStatus for a in ReviewAction if next_status(s, a) is not None}
    assert len(covered) == 8
    assert allowed_actions(P) == (ReviewAction.START_REVIEW, ReviewAction.APPROVE, ReviewAction.REJECT)
    assert allowed_actions(IR) == (ReviewAction.APPROVE, ReviewAction.REJECT)
    assert allowed_actions(A) == (ReviewAction.REJECT, ReviewAction.REOPEN)
    assert allowed_actions(R) == (ReviewAction.REOPEN,)


def test_repeating_start_or_approve_is_not_an_error_but_a_no_op() -> None:
    assert is_repeat(IR, ReviewAction.START_REVIEW) and is_repeat(A, ReviewAction.APPROVE)
    assert not is_repeat(P, ReviewAction.START_REVIEW) and not is_repeat(P, ReviewAction.APPROVE)
    assert not any(
        is_repeat(s, ReviewAction.REJECT) or is_repeat(s, ReviewAction.REOPEN) for s in ReviewStatus
    )


def test_only_rejection_requires_a_reason() -> None:
    assert [a for a in ReviewAction if requires_reason(a)] == [ReviewAction.REJECT]


def test_actions_that_take_an_approved_question_out_of_approved() -> None:
    assert revokes_approval(A, ReviewAction.REJECT) and revokes_approval(A, ReviewAction.REOPEN)
    assert not revokes_approval(IR, ReviewAction.REJECT) and not revokes_approval(R, ReviewAction.REOPEN)
    assert not revokes_approval(P, ReviewAction.APPROVE)


def test_event_codes_match_the_database_check_constraint() -> None:
    assert {a.value for a in ReviewAction} == {"START_REVIEW", "APPROVE", "REJECT", "REOPEN"}


def test_content_changes_follow_the_edit_rule() -> None:
    assert status_after_edit(P) is IR and status_after_edit(IR) is IR
    assert status_after_edit(A) is IR and edit_revokes_approval(A)
    assert status_after_edit(R) is R  # only an explicit reopen leaves REJECTED


@pytest.mark.parametrize(
    ("status", "pointer", "exportable"),
    [
        (A, 7, True),
        (A, None, False),
        (P, None, False),
        (IR, None, False),
        (R, None, False),
        (P, 7, False),
        (IR, 7, False),
        (R, 7, False),
    ],
)
def test_export_eligibility(status, pointer, exportable) -> None:
    assert is_exportable(status, pointer) is exportable
