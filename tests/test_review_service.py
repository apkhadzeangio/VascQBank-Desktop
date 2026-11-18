"""Approve, reject, reopen, start review and restore: rules, conflicts, drafts, rollback."""

from __future__ import annotations

import sqlite3

import pytest
from bank import (
    approve,
    draft_row,
    events_of,
    make_question,
    question_row,
    revisions_of,
    seed,
    simulate_edit,
    snapshot,
)

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.editing import EditField, ProblemKind
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.draft_service import DraftService
from vascqbank_desktop.services.edit_errors import (
    EditConflict,
    EditTargetMissing,
    InvalidTransition,
    ReasonRequired,
    RestoreSourceInvalid,
    ReviewerRequired,
    StorageFailure,
    UnresolvedDraft,
    ValidationFailed,
)
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.question_stats import bank_counts
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

UID = "ALI-0003"
ORIGINAL_SQL = (
    "SELECT original_content_json, question_uid, source_fingerprint FROM questions WHERE question_uid = ?"
)
REVIEWER = "დავით ფარღალავა"


class Env:
    def __init__(self, paths: AppPaths, profile: ReviewerProfile) -> None:
        self.paths = paths
        self.profile = profile
        self.catalog = CatalogService(paths)
        self.edits = EditService(paths, profile)
        self.drafts = DraftService(paths)
        self.review = ReviewService(paths, profile)

    def detail(self, uid: str = UID):
        detail = self.catalog.detail_by_uid(uid)
        assert detail is not None
        return detail

    def edit(self, uid: str = UID, **changes: str):
        d = self.detail(uid)
        content = QuestionContent(
            changes.get("stem", d.content.stem),
            d.content.options,
            d.content.correct,
            changes.get("explanation", d.content.explanation),
        )
        return self.edits.save(d.id, d.lock_version, content)

    def approve(self, uid: str = UID, note: str | None = None):
        d = self.detail(uid)
        return self.review.approve(d.id, d.lock_version, note)

    def reject(self, uid: str = UID, reason: str = "ტერმინოლოგია არასწორია"):
        d = self.detail(uid)
        return self.review.reject(d.id, d.lock_version, reason)

    def reopen(self, uid: str = UID, note: str | None = None):
        d = self.detail(uid)
        return self.review.reopen(d.id, d.lock_version, note)

    def restore(self, revision_no: int, uid: str = UID, **kwargs):
        d = self.detail(uid)
        source = next(r for r in self.catalog.revisions(d.id) if r.revision_no == revision_no)
        return self.review.restore_revision(d.id, d.lock_version, source.id, **kwargs)


@pytest.fixture
def env(app_paths: AppPaths) -> Env:
    seed(app_paths, [make_question("ALI", n) for n in range(1, 8)] + [make_question("VENOUS", 1)])
    profile = ReviewerProfile(SettingsStore(app_paths.settings_file))
    profile.set_name(REVIEWER)
    return Env(app_paths, profile)


# ------------------------------------------------------------------ approval


def test_approval_from_pending_binds_the_current_revision(env: Env) -> None:
    before = question_row(env.paths, UID)
    result = env.approve()
    row = question_row(env.paths, UID)
    assert result.changed and result.status_before is ReviewStatus.PENDING_REVIEW
    assert row["status"] == "APPROVED"
    assert row["approved_revision_id"] == row["current_revision_id"] == result.revision_id
    assert row["approved_at"] == result.approved_at and row["approved_at"]
    assert row["lock_version"] == before["lock_version"] + 1 == result.lock_version
    assert row["revision_count"] == 1  # approving never creates a revision
    (event,) = events_of(env.paths, UID)[-1:]
    assert (event["action"], event["from_status"], event["to_status"]) == (
        "APPROVE",
        "PENDING_REVIEW",
        "APPROVED",
    )
    assert event["revision_id"] == row["current_revision_id"] and event["actor"] == REVIEWER


def test_approval_from_in_review_binds_the_edited_revision(env: Env) -> None:
    env.edit(stem="ახალი პირობა")
    result = env.approve(note="საბოლოო ვერსია")
    row = question_row(env.paths, UID)
    assert result.revision_no == 2 and row["approved_revision_id"] == row["current_revision_id"]
    events = events_of(env.paths, UID)
    assert [e["action"] for e in events] == ["IMPORTED", "EDITED", "APPROVE"]
    assert events[-1]["from_status"] == "IN_REVIEW" and events[-1]["note"] == "საბოლოო ვერსია"
    assert events[-1]["revision_id"] == revisions_of(env.paths, UID)[1]["id"]


def test_repeating_an_approval_writes_nothing(env: Env) -> None:
    first = env.approve()
    before = snapshot(env.paths)
    again = env.approve()
    assert not again.changed and again.status_after is ReviewStatus.APPROVED
    assert again.lock_version == first.lock_version and again.approved_at == first.approved_at
    assert snapshot(env.paths) == before  # no second event, no lock bump, no timestamp change
    assert [e["action"] for e in events_of(env.paths, UID)].count("APPROVE") == 1


def test_approval_never_happens_because_a_draft_was_autosaved(env: Env) -> None:
    d = env.detail()
    content = QuestionContent("ჯერ არ შენახული ტექსტი", d.content.options, d.content.correct, "e")
    env.drafts.save_draft(d.id, d.revision_id, d.lock_version, content, REVIEWER)
    assert question_row(env.paths, UID)["status"] == "PENDING_REVIEW"
    before = snapshot(env.paths)
    with pytest.raises(UnresolvedDraft) as info:
        env.approve()
    assert info.value.reviewer_name == REVIEWER
    assert snapshot(env.paths) == before
    assert draft_row(env.paths, UID) is not None  # the draft is untouched


def test_a_draft_that_only_repeats_the_saved_text_does_not_block_and_is_removed(env: Env) -> None:
    d = env.detail()
    env.drafts.save_draft(d.id, d.revision_id, d.lock_version, d.content, REVIEWER)
    result = env.approve()
    assert result.changed and result.draft_cleared and draft_row(env.paths, UID) is None


def test_after_the_draft_is_discarded_the_question_can_be_approved(env: Env) -> None:
    d = env.detail()
    other = QuestionContent("სხვა ტექსტი", d.content.options, d.content.correct, "e")
    env.drafts.save_draft(d.id, d.revision_id, d.lock_version, other, REVIEWER)
    with pytest.raises(UnresolvedDraft):
        env.approve()
    env.drafts.discard_draft(d.id, REVIEWER)
    assert env.approve().changed


def test_approving_an_approved_question_again_does_not_hide_a_later_draft(env: Env) -> None:
    """Phase 9b (D18, found by the model-based test): a draft written after the approval (e.g. in an
    earlier session) must not be answered with "already approved" — the reviewer could believe the
    new text was approved while the export keeps the old one."""
    env.approve()
    d = env.detail()
    later = QuestionContent("დამტკიცების შემდეგ დაწერილი ტექსტი", d.content.options, d.content.correct, "e")
    env.drafts.save_draft(d.id, d.revision_id, d.lock_version, later, REVIEWER)
    before = snapshot(env.paths)
    with pytest.raises(UnresolvedDraft):
        env.approve()
    assert snapshot(env.paths) == before and draft_row(env.paths, UID) is not None
    env.drafts.discard_draft(d.id, REVIEWER)
    assert env.approve().changed is False  # without the draft: the ordinary "already approved"


def test_saving_the_draft_first_makes_the_approval_apply_to_the_new_text(env: Env) -> None:
    d = env.detail()
    other = QuestionContent("შენახული და დამტკიცებული", d.content.options, d.content.correct, "e")
    env.drafts.save_draft(d.id, d.revision_id, d.lock_version, other, REVIEWER)
    env.edits.save(d.id, d.lock_version, other)  # the explicit save clears the identical draft
    result = env.approve()
    assert result.revision_no == 2
    assert env.catalog.revision(result.approved_revision_id).content.stem == "შენახული და დამტკიცებული"


@pytest.mark.parametrize(
    ("stem", "problem"),
    [("ა" * 20_001, ProblemKind.TOO_LONG), ("ტექსტი\x07კონტროლი", ProblemKind.CONTROL_CHARACTERS)],
)
def test_invalid_saved_content_blocks_the_approval(env: Env, stem: str, problem: ProblemKind) -> None:
    simulate_edit(env.paths, UID, stem=stem)  # arrives from outside the editor's own validation
    before = snapshot(env.paths)
    with pytest.raises(ValidationFailed) as info:
        env.approve()
    assert [(p.field, p.kind) for p in info.value.problems] == [(EditField.STEM, problem)]
    assert snapshot(env.paths) == before


def test_a_reviewer_name_is_required_for_every_action(env: Env, app_paths: AppPaths) -> None:
    anonymous = ReviewService(app_paths, _blank_profile(app_paths))
    d = env.detail()
    before = snapshot(app_paths)
    source = env.catalog.revisions(d.id)[0]
    for call in (
        lambda: anonymous.approve(d.id, d.lock_version),
        lambda: anonymous.reject(d.id, d.lock_version, "მიზეზი"),
        lambda: anonymous.reopen(d.id, d.lock_version),
        lambda: anonymous.start_review(d.id, d.lock_version),
        lambda: anonymous.restore_revision(d.id, d.lock_version, source.id),
    ):
        with pytest.raises(ReviewerRequired):
            call()
    assert snapshot(app_paths) == before


def _blank_profile(paths: AppPaths) -> ReviewerProfile:
    other = paths.settings_file.with_name("nobody.json")
    return ReviewerProfile(SettingsStore(other))


# ------------------------------------------------------------------ start review


def test_start_review_moves_pending_to_in_review_once(env: Env) -> None:
    d = env.detail()
    result = env.review.start_review(d.id, d.lock_version)
    assert result.changed and result.status_after is ReviewStatus.IN_REVIEW
    assert [e["action"] for e in events_of(env.paths, UID)] == ["IMPORTED", "START_REVIEW"]
    again = env.review.start_review(d.id, result.lock_version)
    assert not again.changed and len(events_of(env.paths, UID)) == 2
    env.approve()
    with pytest.raises(InvalidTransition):
        env.review.start_review(d.id, env.detail().lock_version)


# ------------------------------------------------------------------ rejection


def test_rejection_needs_a_reason_and_changes_nothing_without_one(env: Env) -> None:
    d = env.detail()
    before = snapshot(env.paths)
    for reason in ("", "   ", "\n\t", None):
        with pytest.raises(ReasonRequired):
            env.review.reject(d.id, d.lock_version, reason)  # type: ignore[arg-type]
    assert snapshot(env.paths) == before


def test_rejection_keeps_the_question_and_all_its_text_and_records_the_reason(env: Env) -> None:
    env.edit(stem="ტექსტი გადასახედია")
    revisions_before = revisions_of(env.paths, UID)
    result = env.reject(reason="  პასუხი ა\nარასწორია  ")
    row = question_row(env.paths, UID)
    assert result.changed and row["status"] == "REJECTED"
    assert row["approved_revision_id"] is None and row["approved_at"] is None
    assert revisions_of(env.paths, UID) == revisions_before  # nothing was rewritten or deleted
    event = events_of(env.paths, UID)[-1]
    assert (event["action"], event["from_status"], event["to_status"]) == ("REJECT", "IN_REVIEW", "REJECTED")
    assert event["note"] == "პასუხი ა არასწორია" and event["actor"] == REVIEWER
    assert env.catalog.detail_by_uid(UID) is not None  # still in the database


def test_rejecting_an_approved_question_clears_the_approval_but_not_its_history(env: Env) -> None:
    env.approve()
    result = env.reject()
    row = question_row(env.paths, UID)
    assert result.approval_revoked and result.revoked_revision_no == 1
    assert row["status"] == "REJECTED" and row["approved_revision_id"] is None
    assert [e["action"] for e in events_of(env.paths, UID)] == ["IMPORTED", "APPROVE", "REJECT"]


def test_a_rejected_question_cannot_be_approved_or_rejected_again_until_reopened(env: Env) -> None:
    env.reject()
    before = snapshot(env.paths)
    with pytest.raises(InvalidTransition) as info:
        env.approve()
    assert info.value.status is ReviewStatus.REJECTED and info.value.action == "APPROVE"
    with pytest.raises(InvalidTransition):
        env.reject()
    assert snapshot(env.paths) == before
    env.reopen()
    assert env.approve().changed


# ------------------------------------------------------------------ reopening


def test_reopening_an_approved_question(env: Env) -> None:
    env.approve()
    result = env.reopen(note="ახალი ლიტერატურა გამოვიდა")
    row = question_row(env.paths, UID)
    assert result.status_after is ReviewStatus.IN_REVIEW and result.approval_revoked
    assert row["status"] == "IN_REVIEW" and row["approved_revision_id"] is None and row["approved_at"] is None
    assert row["revision_count"] == 1
    events = events_of(env.paths, UID)
    assert [e["action"] for e in events] == ["IMPORTED", "APPROVE", "REOPEN"]
    assert events[-1]["note"] == "ახალი ლიტერატურა გამოვიდა" and events[-1]["from_status"] == "APPROVED"


def test_reopening_a_rejected_question(env: Env) -> None:
    env.reject()
    result = env.reopen()
    assert result.status_after is ReviewStatus.IN_REVIEW and not result.approval_revoked
    assert question_row(env.paths, UID)["status"] == "IN_REVIEW"
    assert events_of(env.paths, UID)[-1]["from_status"] == "REJECTED"


@pytest.mark.parametrize("prepare", [lambda e: None, lambda e: e.edit(stem="ა")])
def test_only_approved_or_rejected_questions_can_be_reopened(env: Env, prepare) -> None:
    prepare(env)
    before = snapshot(env.paths)
    with pytest.raises(InvalidTransition):
        env.reopen()
    assert snapshot(env.paths) == before


def test_reopening_and_approving_again_is_a_new_approval_of_the_same_revision(env: Env) -> None:
    first = env.approve()
    env.reopen()
    second = env.approve()
    assert second.revision_id == first.revision_id and second.changed
    assert [e["action"] for e in events_of(env.paths, UID)].count("APPROVE") == 2


# ------------------------------------------------------------------ concurrency


@pytest.mark.parametrize("action", ["approve", "reject", "reopen", "start", "restore"])
def test_a_stale_lock_version_is_a_conflict_and_changes_nothing(env: Env, action: str) -> None:
    if action == "reopen":
        approve(env.paths, UID)  # only an approved or rejected question can be reopened
    stale = env.detail()
    simulate_edit(env.paths, UID, stem="სხვის მიერ შეცვლილი")  # another window saved meanwhile
    before = snapshot(env.paths)
    source = env.catalog.revisions(stale.id)[-1]
    calls = {
        "approve": lambda: env.review.approve(stale.id, stale.lock_version),
        "reject": lambda: env.review.reject(stale.id, stale.lock_version, "მიზეზი"),
        "reopen": lambda: env.review.reopen(stale.id, stale.lock_version),
        "start": lambda: env.review.start_review(stale.id, stale.lock_version),
        "restore": lambda: env.review.restore_revision(stale.id, stale.lock_version, source.id),
    }
    with pytest.raises(EditConflict) as info:
        calls[action]()
    assert info.value.current_lock_version > info.value.expected_lock_version
    assert snapshot(env.paths) == before  # stale text was never approved, rejected or restored


def test_approving_what_the_reviewer_saw_not_what_arrived_later(env: Env) -> None:
    seen = env.detail()
    simulate_edit(env.paths, UID, stem="ტექსტი, რომელიც რეცენზენტს არ უნახავს")
    with pytest.raises(EditConflict):
        env.review.approve(seen.id, seen.lock_version)
    assert question_row(env.paths, UID)["status"] == "IN_REVIEW"


def test_a_missing_question_is_reported(env: Env) -> None:
    with pytest.raises(EditTargetMissing):
        env.review.approve(987_654, 0)


# ------------------------------------------------------------------ restore


def test_restoring_an_earlier_revision_appends_a_new_one(env: Env) -> None:
    original = env.detail().content
    env.edit(stem="მეორე რედაქცია")
    env.edit(stem="მესამე რედაქცია")
    before = revisions_of(env.paths, UID)
    result = env.restore(1, note="ორიგინალი უკეთესი იყო")
    assert result.changed and result.created_revision and result.revision_no == 4
    assert result.restored_from_revision_no == 1
    revisions = revisions_of(env.paths, UID)
    assert revisions[:3] == before  # no old revision was touched
    new = revisions[3]
    assert new["origin"] == "RESTORE" and new["restored_from_revision_id"] == revisions[0]["id"]
    assert new["author"] == REVIEWER and new["note"] == "ორიგინალი უკეთესი იყო"
    assert new["stem"] == original.stem and new["content_hash"] == revisions[0]["content_hash"]
    row = question_row(env.paths, UID)
    assert row["current_revision_id"] == new["id"] and row["status"] == "IN_REVIEW"
    event = events_of(env.paths, UID)[-1]
    assert event["action"] == "RESTORE" and event["revision_id"] == new["id"]
    assert event["note"] == "restored from revision 1; ორიგინალი უკეთესი იყო"


def test_restoring_the_current_text_creates_nothing(env: Env) -> None:
    env.edit(stem="ახალი ტექსტი")
    before = snapshot(env.paths)
    result = env.restore(2)  # the current revision
    assert not result.changed and not result.created_revision and result.revision_no == 2
    assert snapshot(env.paths) == before


def test_restoring_text_equal_to_the_current_one_creates_no_redundant_revision(env: Env) -> None:
    original = env.detail().content
    env.edit(stem="ახალი ტექსტი")
    env.edit(stem=original.stem)  # revision 3 repeats revision 1's text
    before = snapshot(env.paths)
    result = env.restore(1)
    assert not result.created_revision and len(revisions_of(env.paths, UID)) == 3
    assert snapshot(env.paths) == before


def test_restoring_on_an_approved_question_revokes_the_approval(env: Env) -> None:
    env.edit(stem="მეორე რედაქცია")
    env.approve()
    result = env.restore(1)
    row = question_row(env.paths, UID)
    assert result.approval_revoked and result.revoked_revision_no == 2
    assert row["status"] == "IN_REVIEW" and row["approved_revision_id"] is None
    assert row["approved_at"] is None and row["revision_count"] == 3
    assert [e["action"] for e in events_of(env.paths, UID)] == ["IMPORTED", "EDITED", "APPROVE", "RESTORE"]
    assert "approval of revision 2 revoked" in events_of(env.paths, UID)[-1]["note"]


def test_restoring_previously_approved_content_does_not_approve_it_again(env: Env) -> None:
    env.edit(stem="დამტკიცებული ტექსტი")
    approved = env.approve()
    env.edit(stem="მოგვიანებით შეცვლილი")
    result = env.restore(approved.revision_no)
    row = question_row(env.paths, UID)
    assert result.revision_no == 4 and row["status"] == "IN_REVIEW"
    assert row["approved_revision_id"] is None  # the reviewer must approve again, explicitly
    restored = env.catalog.revision(row["current_revision_id"])
    assert restored.content.stem == "დამტკიცებული ტექსტი" and restored.info.origin == "RESTORE"
    assert env.approve().revision_no == 4  # approving binds to the NEW revision


def test_restoring_on_a_rejected_question_keeps_it_rejected(env: Env) -> None:
    env.edit(stem="მეორე რედაქცია")
    env.reject()
    env.restore(1)
    assert question_row(env.paths, UID)["status"] == "REJECTED"


def test_a_revision_of_another_question_or_a_missing_one_cannot_be_restored(env: Env) -> None:
    d = env.detail()
    foreign = env.catalog.revisions(env.detail("ALI-0004").id)[0]
    before = snapshot(env.paths)
    for source_id in (foreign.id, 999_999):
        with pytest.raises(RestoreSourceInvalid):
            env.review.restore_revision(d.id, d.lock_version, source_id)
    assert snapshot(env.paths) == before


def test_restore_never_deletes_a_draft_and_asks_for_an_explicit_decision(env: Env) -> None:
    env.edit(stem="მეორე რედაქცია")
    d = env.detail()
    mine = QuestionContent("ჩემი მონახაზი", d.content.options, d.content.correct, "e")
    env.drafts.save_draft(d.id, d.revision_id, d.lock_version, mine, REVIEWER)
    before = snapshot(env.paths)
    with pytest.raises(UnresolvedDraft):
        env.restore(1)
    assert snapshot(env.paths) == before and draft_row(env.paths, UID) is not None
    result = env.restore(1, keep_draft=True)  # the reviewer explicitly chose to keep it
    assert result.created_revision and not result.draft_cleared
    kept = draft_row(env.paths, UID)
    assert kept is not None and kept["stem"] == "ჩემი მონახაზი"  # preserved, now "outdated"
    assert kept["base_revision_id"] == revisions_of(env.paths, UID)[1]["id"]


def test_a_draft_equal_to_the_restored_text_is_cleared_with_it(env: Env) -> None:
    original = env.detail().content
    env.edit(stem="მეორე რედაქცია")
    d = env.detail()
    env.drafts.save_draft(d.id, d.revision_id, d.lock_version, original, REVIEWER)
    result = env.restore(1)
    assert result.created_revision and result.draft_cleared and draft_row(env.paths, UID) is None


def test_the_original_imported_content_is_never_touched_by_any_action(env: Env) -> None:
    conn = sqlite3.connect(env.paths.database_file)
    original = conn.execute(
        ORIGINAL_SQL,
        (UID,),
    ).fetchone()
    first = revisions_of(env.paths, UID)[0]
    env.edit(stem="ა")
    env.approve()
    env.reject()
    env.reopen()
    env.restore(1)
    env.approve()
    after = conn.execute(
        ORIGINAL_SQL,
        (UID,),
    ).fetchone()
    conn.close()
    assert tuple(after) == tuple(original)
    assert revisions_of(env.paths, UID)[0] == first
    guard = sqlite3.connect(env.paths.database_file)
    try:
        with pytest.raises(sqlite3.IntegrityError):  # and the database itself refuses to change it
            guard.execute("UPDATE questions SET original_content_json = '{}' WHERE question_uid = ?", (UID,))
    finally:
        guard.close()


# ------------------------------------------------------------------ rollback and statistics


@pytest.mark.parametrize("action", ["APPROVE", "REJECT", "REOPEN", "START_REVIEW", "RESTORE"])
def test_a_failure_at_the_last_step_rolls_everything_back(env: Env, action: str) -> None:
    if action == "REOPEN":
        approve(env.paths, UID)
    if action == "RESTORE":
        env.edit(stem="მეორე რედაქცია")
    d = env.detail()
    conn = sqlite3.connect(env.paths.database_file)
    conn.execute(
        f"CREATE TRIGGER sabotage BEFORE INSERT ON review_events WHEN NEW.action = '{action}' "
        "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    conn.commit()
    conn.close()
    before = snapshot(env.paths)
    calls = {
        "APPROVE": lambda: env.review.approve(d.id, d.lock_version),
        "REJECT": lambda: env.review.reject(d.id, d.lock_version, "მიზეზი"),
        "REOPEN": lambda: env.review.reopen(d.id, d.lock_version),
        "START_REVIEW": lambda: env.review.start_review(d.id, d.lock_version),
        "RESTORE": lambda: env.review.restore_revision(
            d.id, d.lock_version, env.catalog.revisions(d.id)[-1].id
        ),
    }
    with pytest.raises(StorageFailure, match="simulated failure"):
        calls[action]()
    assert snapshot(env.paths) == before  # status, pointers, lock version, revisions, events: all intact
    sab = sqlite3.connect(env.paths.database_file)
    sab.execute("DROP TRIGGER sabotage")
    sab.commit()
    sab.close()
    assert calls[action]().action == action  # and the same call works once the problem is gone


def test_statistics_follow_every_decision(env: Env) -> None:
    def counts() -> dict[ReviewStatus, int]:
        bank = bank_counts(env.paths)
        return {status: bank.total_with(status) for status in ReviewStatus}

    assert counts() == {
        ReviewStatus.PENDING_REVIEW: 8,
        ReviewStatus.IN_REVIEW: 0,
        ReviewStatus.APPROVED: 0,
        ReviewStatus.REJECTED: 0,
    }
    env.approve("ALI-0001")
    env.edit("ALI-0002", stem="x")
    env.reject("ALI-0003")
    env.approve("ALI-0004")
    env.reopen("ALI-0004")
    assert counts() == {
        ReviewStatus.PENDING_REVIEW: 4,
        ReviewStatus.IN_REVIEW: 2,
        ReviewStatus.APPROVED: 1,
        ReviewStatus.REJECTED: 1,
    }
    ali = bank_counts(env.paths).subjects["ALI"]
    assert ali.total == 7 and ali.count(ReviewStatus.APPROVED) == 1
    assert bank_counts(env.paths).subjects["VENOUS"].count(ReviewStatus.PENDING_REVIEW) == 1


def test_a_second_instance_sees_the_decision_and_conflicts(env: Env) -> None:
    other = ReviewService(env.paths, env.profile)  # e.g. a second program window
    d = env.detail()
    env.review.approve(d.id, d.lock_version)
    with pytest.raises(EditConflict):
        other.reject(d.id, d.lock_version, "გვიანია")
