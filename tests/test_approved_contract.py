"""The read-side contract for the Phase 6 exporter: only the exact approved revision, only approved."""

from __future__ import annotations

import sqlite3

import pytest
from bank import approve, make_question, seed, set_status, simulate_edit, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.approved_service import ApprovedQuestionsService
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.draft_service import DraftService
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

REVIEWER = "რამაზ აფხაძე"


class Env:
    def __init__(self, paths: AppPaths) -> None:
        profile = ReviewerProfile(SettingsStore(paths.settings_file))
        profile.set_name(REVIEWER)
        self.paths = paths
        self.catalog = CatalogService(paths)
        self.edits = EditService(paths, profile)
        self.review = ReviewService(paths, profile)
        self.drafts = DraftService(paths)
        self.contract = ApprovedQuestionsService(paths)

    def detail(self, uid: str):
        detail = self.catalog.detail_by_uid(uid)
        assert detail is not None
        return detail

    def edit(self, uid: str, stem: str) -> None:
        d = self.detail(uid)
        content = QuestionContent(stem, d.content.options, d.content.correct, d.content.explanation)
        self.edits.save(d.id, d.lock_version, content)

    def approve(self, uid: str):
        d = self.detail(uid)
        return self.review.approve(d.id, d.lock_version)


@pytest.fixture
def env(app_paths: AppPaths) -> Env:
    seed(
        app_paths,
        [make_question("ALI", n) for n in range(1, 7)] + [make_question("VENOUS", n) for n in range(1, 4)],
    )
    return Env(app_paths)


def test_only_approved_questions_are_exportable(env: Env) -> None:
    env.approve("ALI-0001")  # approved
    env.edit("ALI-0002", "ა")  # in review
    d = env.detail("ALI-0003")
    env.review.reject(d.id, d.lock_version, "მიზეზი")  # rejected
    env.approve("ALI-0004")
    d = env.detail("ALI-0004")
    env.review.reopen(d.id, d.lock_version)  # approved, then reopened
    # ALI-0005 and ALI-0006 stay pending
    snapshot_ = env.contract.snapshot()
    assert snapshot_.uids == ("ALI-0001",)
    assert env.contract.count() == 1 and env.contract.approved_ids() == [env.detail("ALI-0001").id]


def test_the_exact_approved_revision_is_returned_not_a_later_or_earlier_one(env: Env) -> None:
    env.edit("ALI-0001", "პირველი ვერსია")
    first = env.approve("ALI-0001")
    approved = env.contract.get("ALI-0001")
    assert approved is not None and approved.revision_id == first.revision_id
    assert approved.revision_no == 2 and approved.content.stem == "პირველი ვერსია"
    env.edit("ALI-0001", "ბოლო, ჯერ არადამტკიცებული ვერსია")  # revokes the approval
    assert env.contract.get("ALI-0001") is None  # the later text is not approved, the earlier is superseded
    assert env.contract.snapshot().uids == ()
    second = env.approve("ALI-0001")
    approved = env.contract.get("ALI-0001")
    assert approved is not None and approved.revision_id == second.revision_id
    assert approved.revision_no == 3 and approved.content.stem == "ბოლო, ჯერ არადამტკიცებული ვერსია"


def test_an_unsaved_draft_never_reaches_the_export(env: Env) -> None:
    env.approve("ALI-0001")
    d = env.detail("ALI-0001")
    draft = QuestionContent("მონახაზი, რომელიც არ უნდა გავიდეს", d.content.options, d.content.correct, "e")
    env.drafts.save_draft(d.id, d.revision_id, d.lock_version, draft, REVIEWER)
    exported = env.contract.get("ALI-0001")
    assert exported is not None and exported.content == d.content
    assert all("მონახაზი" not in q.content.stem for q in env.contract.snapshot().questions)


def test_a_restore_revokes_the_approval_so_nothing_superseded_can_be_exported(env: Env) -> None:
    env.edit("ALI-0001", "მეორე")
    env.approve("ALI-0001")
    d = env.detail("ALI-0001")
    first = env.catalog.revisions(d.id)[-1]
    env.review.restore_revision(d.id, d.lock_version, first.id)
    assert env.contract.get("ALI-0001") is None
    assert env.contract.verify() == []


def test_the_record_carries_what_an_exporter_needs(env: Env) -> None:
    env.approve("VENOUS-0002")
    (q,) = env.contract.snapshot().questions
    source = env.detail("VENOUS-0002")
    assert (q.uid, q.subject_code, q.source_number) == ("VENOUS-0002", "VENOUS", 2)
    assert q.subject_name_ka == source.subject_name_ka and q.approved_by == REVIEWER
    assert q.content.correct == source.content.correct and q.content.explanation == source.content.explanation
    assert q.content.options == source.content.options and q.approved_at
    assert len(q.content_hash) == 64


def test_selection_by_subject_and_the_export_order(env: Env) -> None:
    for uid in ("VENOUS-0003", "ALI-0005", "ALI-0002", "VENOUS-0001"):
        env.approve(uid)
    everything = env.contract.snapshot()
    ali = env.contract.snapshot("ALI")
    venous = env.contract.snapshot("VENOUS")
    assert everything.uids == ("ALI-0002", "ALI-0005", "VENOUS-0001", "VENOUS-0003")  # subject order, number
    assert ali.uids == ("ALI-0002", "ALI-0005") and venous.uids == ("VENOUS-0001", "VENOUS-0003")
    assert env.contract.count("ALI") == 2 and env.contract.count("VENOUS") == 2
    assert env.contract.counts_by_subject() == {"ALI": 2, "VENOUS": 2}
    assert (
        env.contract.count("MESENTERIC_RENAL") == 0 and env.contract.snapshot("MESENTERIC_RENAL").uids == ()
    )
    ids = env.contract.approved_ids("ALI")
    assert ids == [env.detail("ALI-0002").id, env.detail("ALI-0005").id]


def test_the_fingerprint_changes_only_when_exported_content_changes(env: Env) -> None:
    env.approve("ALI-0001")
    a = env.contract.snapshot()
    assert env.contract.snapshot().fingerprint == a.fingerprint  # same state, same fingerprint
    env.edit("ALI-0002", "სხვა კითხვის რედაქტირება")  # not approved: not part of the export
    assert env.contract.snapshot().fingerprint == a.fingerprint
    env.approve("ALI-0002")
    b = env.contract.snapshot()
    assert b.fingerprint != a.fingerprint and len(b) == 2


def test_the_snapshot_is_read_only(env: Env) -> None:
    env.approve("ALI-0001")
    before = snapshot(env.paths)
    env.contract.snapshot()
    env.contract.get("ALI-0001")
    env.contract.verify()
    assert snapshot(env.paths) == before


def test_a_consistent_database_has_no_contract_violations(env: Env) -> None:
    env.approve("ALI-0001")
    env.approve("VENOUS-0001")
    env.edit("ALI-0002", "x")
    assert env.contract.verify() == []


def test_corruption_that_would_make_an_export_unsafe_is_reported(env: Env) -> None:
    set_status(env.paths, "ALI-0001", ReviewStatus.APPROVED)  # approved WITHOUT an APPROVE event
    approve(env.paths, "ALI-0002")
    simulate_edit(env.paths, "ALI-0002", stem="ა")  # current moved on, approval pointer left behind
    problems = {(v.uid, v.problem) for v in env.contract.verify()}
    assert ("ALI-0001", "no APPROVE event is bound to the approved revision") in problems
    assert ("ALI-0002", "approved revision is not the current revision") in problems


def test_the_database_itself_refuses_an_unapproved_question_with_an_approval_pointer(env: Env) -> None:
    d = env.detail("ALI-0001")
    conn = open_database(env.paths.database_file)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE questions SET approved_revision_id = ?, approved_at = 't' WHERE id = ?",
                (d.revision_id, d.id),
            )
    finally:
        conn.close()


def test_the_approved_repository_contains_no_write_statements() -> None:
    import inspect

    import vascqbank_desktop.db.approved_repository as module

    source = inspect.getsource(module).upper()
    for keyword in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "PRAGMA", " SET "):
        assert keyword not in source, keyword
