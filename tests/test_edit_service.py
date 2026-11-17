"""Saving an edit: every field, Unicode, status rules, conflicts, rollback, disk-full, locks."""

from __future__ import annotations

import sqlite3
import threading
import unicodedata

import pytest
from bank import approve, make_question, question_row, seed, set_status, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter
from vascqbank_desktop.domain.editing import EditField, ProblemKind
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services import draft_service, edit_service
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.draft_service import DiscardReason, DraftService
from vascqbank_desktop.services.edit_errors import (
    EditConflict,
    EditTargetMissing,
    ReviewerRequired,
    StorageFailure,
    StorageProblem,
    ValidationFailed,
)
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

UID = "ALI-0003"


@pytest.fixture
def env(app_paths: AppPaths):
    seed(app_paths, [make_question("ALI", n) for n in range(1, 8)] + [make_question("VENOUS", 1)])
    store = SettingsStore(app_paths.settings_file)
    profile = ReviewerProfile(store)
    profile.set_name("დავით ფარღალავა")
    catalog = CatalogService(app_paths)
    return app_paths, EditService(app_paths, profile), DraftService(app_paths), catalog, profile


def current(catalog: CatalogService, uid: str = UID):
    detail = catalog.detail_by_uid(uid)
    assert detail is not None
    return detail


def edited(detail, **changes) -> QuestionContent:
    c = detail.content
    return QuestionContent(
        stem=changes.get("stem", c.stem),
        options=changes.get("options", c.options),
        correct=changes.get("correct", c.correct),
        explanation=changes.get("explanation", c.explanation),
    )


# ------------------------------------------------------------------ editing every field


@pytest.mark.parametrize(
    "field",
    ["stem", "option_a", "option_b", "option_c", "option_d", "option_e", "correct", "explanation"],
)
def test_each_field_can_be_edited_and_nothing_else_changes(env, field: str) -> None:
    paths, edits, _drafts, catalog, _ = env
    before = current(catalog)
    changes: dict = {}
    if field == "stem":
        changes["stem"] = "ახალი კითხვა: კაროტიდული სტენოზი"
    elif field == "explanation":
        changes["explanation"] = "ახალი განმარტება"
    elif field == "correct":
        changes["correct"] = (
            AnswerLetter.A if before.content.correct is not AnswerLetter.A else AnswerLetter.B
        )
    else:
        index = "abcde".index(field[-1])
        options = list(before.content.options)
        options[index] = f"შეცვლილი პასუხი {field[-1]}"
        changes["options"] = tuple(options)

    result = edits.save(before.id, before.lock_version, edited(before, **changes), "შესწორება")
    after = current(catalog)

    assert result.created and result.revision_no == 2 and after.revision_no == 2
    assert after.lock_version == before.lock_version + 1 == result.lock_version
    expected = edited(before, **changes)
    assert after.content == expected
    assert after.uid == before.uid and after.source_number == before.source_number  # identity untouched
    assert (after.status, result.status_after) == (ReviewStatus.IN_REVIEW, ReviewStatus.IN_REVIEW)
    first = catalog.revision(catalog.revisions(before.id)[-1].id)  # revision 1, the import
    assert first is not None and first.content == before.content and first.info.origin == "IMPORT"


def test_all_fields_at_once_and_the_review_event(env) -> None:
    paths, edits, _d, catalog, _ = env
    before = current(catalog)
    content = QuestionContent(
        "ახალი კითხვა",
        ("ა-ახალი", "ბ-ახალი", "გ-ახალი", "დ-ახალი", "ე-ახალი"),
        AnswerLetter.E,
        "ახალი განმარტება",
    )
    result = edits.save(before.id, before.lock_version, content, "ყველა ველი")
    assert current(catalog).content == content

    conn = sqlite3.connect(paths.database_file)
    conn.row_factory = sqlite3.Row
    try:
        event = conn.execute(
            "SELECT * FROM review_events WHERE question_id = ? ORDER BY id DESC LIMIT 1", (before.id,)
        ).fetchone()
        revision = conn.execute(
            "SELECT * FROM question_revisions WHERE id = ?", (result.revision_id,)
        ).fetchone()
    finally:
        conn.close()
    assert (event["action"], event["from_status"], event["to_status"]) == (
        "EDITED",
        "PENDING_REVIEW",
        "IN_REVIEW",
    )
    assert (event["actor"], event["revision_id"]) == ("დავით ფარღალავა", result.revision_id)
    assert (revision["origin"], revision["author"], revision["note"]) == (
        "EDIT",
        "დავით ფარღალავა",
        "ყველა ველი",
    )
    assert revision["content_hash"] == content.content_hash() and revision["revision_no"] == 2


def test_the_correct_answer_can_be_any_of_the_five(env) -> None:
    _p, edits, _d, catalog, _ = env
    for letter in ANSWER_LETTERS:
        detail = current(catalog)
        content = edited(detail, correct=AnswerLetter(letter))
        edits.save(detail.id, detail.lock_version, content)
        assert current(catalog).content.correct.value == letter
    assert current(catalog).revision_no >= 2  # only real changes created revisions


# ------------------------------------------------------------------ Unicode and normalisation


def test_georgian_unicode_round_trips_exactly(env) -> None:
    _p, edits, _d, catalog, _ = env
    before = current(catalog)
    text = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ — „ციტატა“ ≥ 5,5 cm · ΔP × 10⁹/ლ · PaO₂ < 60 mmHg & <b>"
    content = edited(before, stem=text, explanation=f"{text}\n\nმეორე აბზაცი")
    edits.save(before.id, before.lock_version, content)
    after = current(catalog)
    assert after.content.stem == text
    assert after.content.explanation == f"{text}\n\nმეორე აბზაცი"
    assert after.content.stem.encode("utf-8").decode("utf-8") == text


def test_text_is_normalised_like_imported_text(env) -> None:
    _p, edits, _d, catalog, _ = env
    before = current(catalog)
    messy = edited(before, stem="﻿José ა\r\nბ\r\n  ", explanation="  განმარტება\r\n")
    edits.save(before.id, before.lock_version, messy)
    after = current(catalog).content
    assert after.stem == "José ა\nბ" and unicodedata.is_normalized("NFC", after.stem)
    assert after.explanation == "განმარტება" and "\r" not in after.stem


# ------------------------------------------------------------------ unchanged content


def test_saving_unchanged_content_creates_nothing(env) -> None:
    paths, edits, _d, catalog, _ = env
    before = current(catalog)
    state = snapshot(paths)
    result = edits.save(before.id, before.lock_version, before.content)
    assert (
        not result.created and result.revision_no == 1 and result.status_after is ReviewStatus.PENDING_REVIEW
    )
    assert snapshot(paths) == state  # no revision, no event, no status or timestamp change


def test_whitespace_only_difference_is_not_a_change(env) -> None:
    paths, edits, _d, catalog, _ = env
    before = current(catalog)
    state = snapshot(paths)
    sloppy = edited(before, stem=f"  {before.content.stem}  \r\n")
    assert not edits.save(before.id, before.lock_version, sloppy).created
    assert snapshot(paths) == state


def test_reverting_to_the_old_text_is_a_new_revision_not_a_rewrite(env) -> None:
    _p, edits, _d, catalog, _ = env
    first = current(catalog)
    edits.save(first.id, first.lock_version, edited(first, stem="A"))
    second = current(catalog)
    edits.save(second.id, second.lock_version, first.content)  # back to the imported wording
    third = current(catalog)
    assert third.revision_no == 3 and third.content == first.content
    assert [r.revision_no for r in catalog.revisions(first.id)] == [3, 2, 1]


# ------------------------------------------------------------------ revision numbering


def test_revision_numbers_are_contiguous_and_history_is_append_only(env) -> None:
    paths, edits, _d, catalog, _ = env
    detail = current(catalog)
    for n in range(2, 7):
        detail = current(catalog)
        edits.save(detail.id, detail.lock_version, edited(detail, stem=f"ტექსტი {n}"), f"შენიშვნა {n}")
        assert current(catalog).revision_no == n
    history = catalog.revisions(detail.id)
    assert [r.revision_no for r in history] == [6, 5, 4, 3, 2, 1]
    assert [r.is_current for r in history] == [True, False, False, False, False, False]
    assert history[0].note == "შენიშვნა 6" and history[-1].origin == "IMPORT" and history[0].origin == "EDIT"
    assert {r.author for r in history[:-1]} == {"დავით ფარღალავა"} and history[-1].author == "იმპორტი"
    for info in history:  # every old revision still holds its own text
        revision = catalog.revision(info.id)
        assert revision is not None and revision.info.revision_no == info.revision_no
    assert catalog.revision(history[3].id).content.stem == "ტექსტი 3"
    conn = sqlite3.connect(paths.database_file)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE question_revisions SET stem = 'x' WHERE id = ?", (history[0].id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM review_events")
    conn.close()


def test_original_imported_content_is_immutable(env) -> None:
    paths, edits, _d, catalog, _ = env
    detail = current(catalog)
    conn = sqlite3.connect(paths.database_file)
    original = conn.execute(
        "SELECT original_content_json, source_fingerprint FROM questions WHERE id = ?", (detail.id,)
    ).fetchone()
    edits.save(detail.id, detail.lock_version, edited(detail, stem="სრულიად ახალი"))
    assert (
        conn.execute(
            "SELECT original_content_json, source_fingerprint FROM questions WHERE id = ?", (detail.id,)
        ).fetchone()
        == original
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE questions SET original_content_json = '{}' WHERE id = ?", (detail.id,))
    conn.close()


def test_only_the_edited_question_changes(env) -> None:
    paths, edits, _d, catalog, _ = env
    detail = current(catalog)
    other = question_row(paths, "ALI-0004")
    edits.save(detail.id, detail.lock_version, edited(detail, stem="x"))
    assert question_row(paths, "ALI-0004") == other


# ------------------------------------------------------------------ status rules


def test_status_follows_the_state_machine(env) -> None:
    paths, edits, _d, catalog, _ = env
    # PENDING_REVIEW -> IN_REVIEW on the first saved edit; IN_REVIEW stays IN_REVIEW
    d = current(catalog)
    assert edits.save(d.id, d.lock_version, edited(d, stem="1")).status_after is ReviewStatus.IN_REVIEW
    d = current(catalog)
    assert d.status is ReviewStatus.IN_REVIEW
    assert edits.save(d.id, d.lock_version, edited(d, stem="2")).status_after is ReviewStatus.IN_REVIEW
    # REJECTED stays REJECTED (only an explicit reopen, Phase 5, leaves it)
    set_status(paths, "ALI-0005", ReviewStatus.REJECTED)
    r = current(catalog, "ALI-0005")
    result = edits.save(r.id, r.lock_version, edited(r, stem="gadasaxedia"))
    assert result.status_before is ReviewStatus.REJECTED and result.status_after is ReviewStatus.REJECTED
    assert current(catalog, "ALI-0005").status is ReviewStatus.REJECTED


def test_editing_an_approved_question_revokes_the_approval_but_keeps_the_approved_revision(env) -> None:
    paths, edits, _d, catalog, _ = env
    approved_no = approve(paths, "ALI-0006")
    detail = current(catalog, "ALI-0006")
    assert detail.status is ReviewStatus.APPROVED and detail.approved_at is not None
    row = question_row(paths, "ALI-0006")
    assert row["approved_revision_id"] == row["current_revision_id"]

    result = edits.save(detail.id, detail.lock_version, edited(detail, stem="დამტკიცებულის შესწორება"))
    assert result.approval_revoked and result.revoked_revision_no == approved_no == 1
    assert (result.status_before, result.status_after) == (ReviewStatus.APPROVED, ReviewStatus.IN_REVIEW)
    after = question_row(paths, "ALI-0006")
    assert (
        after["status"] == "IN_REVIEW"
        and after["approved_revision_id"] is None
        and after["approved_at"] is None
    )
    assert after["current_revision_id"] != row["current_revision_id"]

    history = catalog.revisions(detail.id)
    approved = next(r for r in history if r.revision_no == 1)
    assert approved.was_approved  # the revision that had been approved is still there, still marked
    assert catalog.revision(approved.id).content == detail.content  # ... with its exact text
    newest = history[0]
    assert (newest.from_status, newest.to_status) == (ReviewStatus.APPROVED, ReviewStatus.IN_REVIEW)
    assert newest.event_note == "approval of revision 1 revoked"


def test_typing_in_a_draft_never_changes_an_approval(env) -> None:
    paths, _e, drafts, catalog, _ = env
    approve(paths, "ALI-0006")
    detail = current(catalog, "ALI-0006")
    before = snapshot(paths)
    for n in range(5):
        drafts.save_draft(
            detail.id,
            detail.revision_id,
            detail.lock_version,
            edited(detail, stem=f"ჩანაწერი {n}"),
            "დავით ფარღალავა",
        )
    after = snapshot(paths)
    for table in ("questions", "question_revisions", "review_events"):
        assert after[table] == before[table]  # approval, status, lock version, timestamps: all untouched
    assert current(catalog, "ALI-0006").status is ReviewStatus.APPROVED


# ------------------------------------------------------------------ validation and reviewer


@pytest.mark.parametrize(
    ("changes", "field", "kind"),
    [
        ({"stem": "   \n"}, EditField.STEM, ProblemKind.EMPTY),
        ({"options": ("ა", "", "გ", "დ", "ე")}, EditField.OPTION_B, ProblemKind.EMPTY),
        ({"explanation": ""}, EditField.EXPLANATION, ProblemKind.EMPTY),
        ({"stem": "ა\x00"}, EditField.STEM, ProblemKind.CONTROL_CHARACTERS),
        ({"stem": "ა" * 20_001}, EditField.STEM, ProblemKind.TOO_LONG),
    ],
)
def test_invalid_content_is_refused_and_nothing_changes(env, changes: dict, field, kind) -> None:
    paths, edits, _d, catalog, _ = env
    detail = current(catalog)
    state = snapshot(paths)
    with pytest.raises(ValidationFailed) as info:
        edits.save(detail.id, detail.lock_version, edited(detail, **changes))
    assert [(p.field, p.kind) for p in info.value.problems] == [(field, kind)]
    assert snapshot(paths) == state


def test_a_reviewer_name_is_required(env, app_paths: AppPaths) -> None:
    paths, _e, _d, catalog, _ = env
    anonymous = EditService(paths, ReviewerProfile(SettingsStore(paths.data_dir / "other-settings.json")))
    detail = current(catalog)
    state = snapshot(paths)
    with pytest.raises(ReviewerRequired):
        anonymous.save(detail.id, detail.lock_version, edited(detail, stem="x"))
    assert snapshot(paths) == state


def test_a_missing_question_is_reported(env) -> None:
    _p, edits, _d, catalog, _ = env
    with pytest.raises(EditTargetMissing):
        edits.save(999_999, 0, edited(current(catalog), stem="x"))


# ------------------------------------------------------------------ concurrent modification


def test_a_stale_editor_cannot_overwrite_newer_content(env) -> None:
    paths, edits, _d, catalog, profile = env
    mine = current(catalog)  # the editor loads the question at lock version 0
    other_window = EditService(paths, profile)  # a second window / instance saves first
    other_window.save(mine.id, mine.lock_version, edited(mine, stem="ტექსტი სხვა ფანჯრიდან"))
    state = snapshot(paths)

    with pytest.raises(EditConflict) as info:
        edits.save(mine.id, mine.lock_version, edited(mine, stem="ჩემი ვერსია"))
    assert (info.value.expected_lock_version, info.value.current_lock_version) == (0, 1)
    assert info.value.current_revision_no == 2
    assert snapshot(paths) == state  # nothing was written
    assert current(catalog).content.stem == "ტექსტი სხვა ფანჯრიდან"


def test_conflict_keeps_my_draft_and_a_knowing_rebase_is_recorded(env) -> None:
    paths, edits, drafts, catalog, profile = env
    mine = current(catalog)
    my_text = edited(mine, stem="ჩემი ვერსია")
    drafts.save_draft(mine.id, mine.revision_id, mine.lock_version, my_text, "დავით ფარღალავა")
    EditService(paths, profile).save(mine.id, mine.lock_version, edited(mine, stem="სხვისი ვერსია"))

    with pytest.raises(EditConflict) as info:
        edits.save(mine.id, mine.lock_version, my_text)
    assert drafts.load_draft(mine.id) is not None  # the reviewer's text is still safe

    latest = current(catalog)  # the reviewer chooses "save mine on top of the newer revision"
    result = edits.save(latest.id, latest.lock_version, my_text, rebased_from_revision_no=1)
    assert result.created and result.revision_no == 3 and result.draft_cleared
    assert current(catalog).content.stem == "ჩემი ვერსია"
    assert (
        catalog.revision(catalog.revisions(mine.id)[1].id).content.stem == "სხვისი ვერსია"
    )  # still in history
    conn = sqlite3.connect(paths.database_file)
    note = conn.execute("SELECT note FROM review_events ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    assert "edited from revision 1" in note and info.value.current_revision_no == 2


def test_two_threads_saving_from_the_same_base_one_wins_one_conflicts(env) -> None:
    paths, _e, _d, catalog, profile = env
    base = current(catalog)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker(text: str) -> None:
        service = EditService(paths, profile)
        barrier.wait()
        try:
            service.save(base.id, base.lock_version, edited(base, stem=text))
            outcomes.append("saved")
        except EditConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=worker, args=(f"ხრეად {i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["conflict", "saved"]
    assert current(catalog).revision_no == 2 and current(catalog).lock_version == 1


# ------------------------------------------------------------------ drafts and the save


def test_the_draft_is_cleared_with_the_commit_when_it_holds_the_saved_text(env) -> None:
    _p, edits, drafts, catalog, _ = env
    d = current(catalog)
    content = edited(d, stem="შენახული ტექსტი")
    drafts.save_draft(d.id, d.revision_id, d.lock_version, content, "დავით ფარღალავა")
    result = edits.save(d.id, d.lock_version, content)
    assert result.draft_cleared and drafts.load_draft(d.id) is None and drafts.count() == 0


def test_a_different_draft_is_never_deleted_as_a_side_effect(env) -> None:
    _p, edits, drafts, catalog, _ = env
    d = current(catalog)
    drafts.save_draft(
        d.id, d.revision_id, d.lock_version, edited(d, stem="უფრო ახალი ტექსტი"), "დავით ფარღალავა"
    )
    result = edits.save(d.id, d.lock_version, edited(d, stem="სხვა შენახული"))
    assert result.created and not result.draft_cleared
    assert drafts.load_draft(d.id).content.stem == "უფრო ახალი ტექსტი"


# ------------------------------------------------------------------ transactions and failures


def test_a_failure_late_in_the_save_rolls_everything_back(env) -> None:
    paths, edits, drafts, catalog, _ = env
    d = current(catalog)
    content = edited(d, stem="დაუმთავრებელი")
    drafts.save_draft(d.id, d.revision_id, d.lock_version, content, "დავით ფარღალავა")
    # The review event is the LAST insert of the transaction: by then the revision has been
    # appended and the question updated, so this proves they are all undone together.
    conn = sqlite3.connect(paths.database_file)
    conn.execute(
        "CREATE TRIGGER sabotage BEFORE INSERT ON review_events WHEN NEW.action = 'EDITED' "
        "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    conn.commit()
    conn.close()
    before = snapshot(paths)
    with pytest.raises(StorageFailure, match="simulated failure"):
        edits.save(d.id, d.lock_version, content)
    assert snapshot(paths) == before  # no revision, no pointer move, no status change, no event
    assert drafts.load_draft(d.id) is not None  # ... and the draft was not deleted either
    assert current(catalog).revision_no == 1 and current(catalog).lock_version == 0


def test_failure_rolls_back_the_approval_revocation_too(env) -> None:
    paths, edits, _d, catalog, _ = env
    approve(paths, "ALI-0006")
    detail = current(catalog, "ALI-0006")
    conn = sqlite3.connect(paths.database_file)
    conn.execute(
        "CREATE TRIGGER sabotage BEFORE INSERT ON review_events WHEN NEW.action = 'EDITED' "
        "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    conn.commit()
    conn.close()
    with pytest.raises(StorageFailure):
        edits.save(detail.id, detail.lock_version, edited(detail, stem="x"))
    row = question_row(paths, "ALI-0006")
    assert row["status"] == "APPROVED" and row["approved_revision_id"] == row["current_revision_id"]


def _limit_database_size(monkeypatch, *modules) -> None:
    """Make every connection of these modules behave as if the disk were full."""
    real_open = open_database

    def full_disk(path):
        conn = real_open(path)
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        conn.execute(f"PRAGMA max_page_count = {pages}")  # not one page more may be allocated
        return conn

    for module in modules:
        monkeypatch.setattr(module, "open_database", full_disk)


def test_disk_full_during_a_save_is_reported_and_changes_nothing(env, monkeypatch) -> None:
    paths, edits, _d, catalog, _ = env
    d = current(catalog)
    big = edited(d, stem="ა" * 15_000, explanation="ბ" * 15_000)  # needs new pages
    before = snapshot(paths)
    _limit_database_size(monkeypatch, edit_service)
    with pytest.raises(StorageFailure) as info:
        edits.save(d.id, d.lock_version, big)
    assert info.value.problem is StorageProblem.DISK_FULL
    assert snapshot(paths) == before
    monkeypatch.undo()
    assert edits.save(d.id, d.lock_version, big).created  # once space is back, the same save works
    assert sqlite3.connect(paths.database_file).execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_disk_full_during_a_draft_write_keeps_the_previous_draft(env, monkeypatch) -> None:
    paths, _e, drafts, catalog, _ = env
    d = current(catalog)
    drafts.save_draft(
        d.id, d.revision_id, d.lock_version, edited(d, stem="ბოლო მყარი ვერსია"), "დავით ფარღალავა"
    )
    _limit_database_size(monkeypatch, draft_service)
    with pytest.raises(StorageFailure) as info:
        drafts.save_draft(
            d.id, d.revision_id, d.lock_version, edited(d, stem="ა" * 40_000), "დავით ფარღალავა"
        )
    assert info.value.problem is StorageProblem.DISK_FULL
    assert drafts.load_draft(d.id).content.stem == "ბოლო მყარი ვერსია"  # the confirmed draft is intact


def test_a_locked_database_is_reported_as_such(env, monkeypatch) -> None:
    paths, edits, drafts, catalog, _ = env
    d = current(catalog)
    real_open = open_database

    def impatient(path):
        conn = real_open(path)
        conn.execute("PRAGMA busy_timeout = 50")
        return conn

    monkeypatch.setattr(edit_service, "open_database", impatient)
    monkeypatch.setattr(draft_service, "open_database", impatient)
    blocker = sqlite3.connect(paths.database_file, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")  # another program holds the write lock
    try:
        with pytest.raises(StorageFailure) as save_error:
            edits.save(d.id, d.lock_version, edited(d, stem="x"))
        with pytest.raises(StorageFailure) as draft_error:
            drafts.save_draft(d.id, d.revision_id, d.lock_version, edited(d, stem="x"), "დავით ფარღალავა")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert (
        save_error.value.problem is StorageProblem.LOCKED
        and draft_error.value.problem is StorageProblem.LOCKED
    )
    assert edits.save(d.id, d.lock_version, edited(d, stem="x")).created  # and it works again afterwards


# ------------------------------------------------------------------ drafts themselves


def test_draft_round_trip_replacement_and_counters(env) -> None:
    _p, _e, drafts, catalog, _ = env
    d = current(catalog)
    first = drafts.save_draft(
        d.id, d.revision_id, d.lock_version, edited(d, stem="პირველი "), "რამაზი აფხაძე", "შენიშვნა"
    )
    assert first.write_count == 1 and first.saved_at.endswith("+00:00")
    second = drafts.save_draft(
        d.id,
        d.revision_id,
        d.lock_version,
        edited(d, stem="მეორე", correct=AnswerLetter.E),
        "დავით ფარღალავა",
    )
    assert second.write_count == 2
    stored = drafts.load_draft(d.id)
    assert stored is not None and stored.content.stem == "მეორე" and stored.content.correct is AnswerLetter.E
    assert (stored.reviewer_name, stored.note) == ("დავით ფარღალავა", None)
    assert (stored.base_revision_id, stored.base_lock_version) == (d.revision_id, d.lock_version)
    assert drafts.count() == 1 and drafts.draft_question_ids() == {d.id}


def test_a_draft_keeps_what_was_typed_including_unfinished_text(env) -> None:
    _p, _e, drafts, catalog, _ = env
    d = current(catalog)
    typed = edited(
        d, stem="ტექსტი, რომელიც ჯერ არ დასრულებულა ", options=("", "ბ", "გ", "დ", "ე"), explanation=""
    )
    drafts.save_draft(d.id, d.revision_id, d.lock_version, typed, "დავით ფარღალავა")
    stored = drafts.load_draft(d.id).content
    assert (
        stored.stem.endswith(" ") and stored.options[0] == "" and stored.explanation == ""
    )  # not trimmed, not rejected


def test_a_draft_needs_a_reviewer_and_a_matching_base_revision(env) -> None:
    _p, _e, drafts, catalog, _ = env
    d, other = current(catalog), current(catalog, "ALI-0004")
    with pytest.raises(ReviewerRequired):
        drafts.save_draft(d.id, d.revision_id, d.lock_version, d.content, "   ")
    with pytest.raises(StorageFailure, match="same question"):
        drafts.save_draft(d.id, other.revision_id, d.lock_version, d.content, "დავით ფარღალავა")
    assert drafts.count() == 0


def test_discarding_archives_the_full_text_and_is_append_only(env) -> None:
    paths, _e, drafts, catalog, _ = env
    d = current(catalog)
    drafts.save_draft(
        d.id, d.revision_id, d.lock_version, edited(d, stem="გადასაგდები ტექსტი"), "დავით ფარღალავა"
    )
    assert drafts.discard_draft(d.id, "დავით ფარღალავა", DiscardReason.DISCARDED)
    assert drafts.load_draft(d.id) is None and not drafts.discard_draft(d.id, "დავით ფარღალავა")
    conn = sqlite3.connect(paths.database_file)
    row = conn.execute("SELECT stem, reason, discarded_by FROM draft_discards").fetchone()
    assert row == ("გადასაგდები ტექსტი", "DISCARDED", "დავით ფარღალავა")  # recoverable from the database
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM draft_discards")
    conn.close()


def test_a_draft_identical_to_the_saved_text_is_dropped_but_a_different_one_is_kept(env) -> None:
    _p, _e, drafts, catalog, _ = env
    d = current(catalog)
    drafts.save_draft(d.id, d.revision_id, d.lock_version, d.content, "დავით ფარღალავა")
    assert drafts.drop_if_identical(d.id, d.content) and drafts.count() == 0
    drafts.save_draft(d.id, d.revision_id, d.lock_version, edited(d, stem="სხვა"), "დავით ფარღალავა")
    assert not drafts.drop_if_identical(d.id, d.content) and drafts.count() == 1


def test_draft_writes_never_touch_saved_history(env) -> None:
    paths, _e, drafts, catalog, _ = env
    d = current(catalog)
    before = snapshot(paths)
    drafts.save_draft(d.id, d.revision_id, d.lock_version, edited(d, stem="ა"), "დავით ფარღალავა")
    drafts.save_draft(d.id, d.revision_id, d.lock_version, edited(d, stem="ბ"), "დავით ფარღალავა")
    drafts.discard_draft(d.id, "დავით ფარღალავა")
    assert snapshot(paths) == before  # questions, revisions, events, batches, metadata: byte-for-byte equal


def test_listing_drafts_reports_which_are_outdated(env) -> None:
    paths, edits, drafts, catalog, _ = env
    a, b = current(catalog, "ALI-0003"), current(catalog, "ALI-0004")
    drafts.save_draft(a.id, a.revision_id, a.lock_version, edited(a, stem="a"), "დავით ფარღალავა")
    drafts.save_draft(b.id, b.revision_id, b.lock_version, edited(b, stem="b"), "რამაზი აფხაძე")
    edits.save(b.id, b.lock_version, edited(b, stem="ახალი შენახული"))  # b moves on to revision 2
    rows = {r.uid: r for r in drafts.list_drafts()}
    assert set(rows) == {"ALI-0003", "ALI-0004"}
    assert not rows["ALI-0003"].outdated and rows["ALI-0004"].outdated
    assert (rows["ALI-0004"].base_revision_no, rows["ALI-0004"].current_revision_no) == (1, 2)
    assert rows["ALI-0004"].reviewer_name == "რამაზი აფხაძე" and rows["ALI-0003"].subject_code == "ALI"


def test_concurrent_draft_writes_are_serialised(env) -> None:
    _p, _e, drafts, catalog, _ = env
    d = current(catalog)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(25):
                drafts.save_draft(
                    d.id, d.revision_id, d.lock_version, edited(d, stem=f"{n}-{i}"), "დავით ფარღალავა"
                )
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    stored = drafts.load_draft(d.id)
    assert stored is not None and stored.write_count == 100  # every write committed, none interleaved
    assert drafts.count() == 1


# ------------------------------------------------------------------ search stays correct


def test_a_saved_edit_is_found_by_search_and_the_old_text_is_not(env) -> None:
    from vascqbank_desktop.domain.catalog import CatalogFilter

    _p, edits, _d, catalog, _ = env
    d = current(catalog)
    edits.save(d.id, d.lock_version, edited(d, stem="უნიკალური ფორმულირება ანევრიზმაზე"))
    assert [r.uid for r in catalog.rows(catalog.query(CatalogFilter(text="ანევრიზმაზე")).ids)] == [UID]
    assert catalog.query(CatalogFilter(text="სინთეზური სატესტო კითხვა ALI №3")).ids == ()


def test_drafts_and_saves_are_synced_to_disk_at_every_commit(env, monkeypatch) -> None:
    """``synchronous=FULL`` (2) on the editing connections: a power cut must not take the last
    commits with it. The shared default stays ``NORMAL`` (1) for everything else."""
    paths, edits, drafts, catalog, _ = env
    levels: dict[str, int] = {}

    def level(conn) -> int:
        return conn.execute("PRAGMA synchronous").fetchone()[0]

    real_upsert, real_append = draft_service.repo.upsert_draft, edit_service.repo.append_edit

    def spy_upsert(conn, *args, **kwargs):
        levels["draft"] = level(conn)
        return real_upsert(conn, *args, **kwargs)

    def spy_append(conn, **kwargs):
        levels["save"] = level(conn)
        return real_append(conn, **kwargs)

    monkeypatch.setattr(draft_service.repo, "upsert_draft", spy_upsert)
    monkeypatch.setattr(edit_service.repo, "append_edit", spy_append)
    d = current(catalog)
    drafts.save_draft(d.id, d.revision_id, d.lock_version, edited(d, stem="ა"), "დავით ფარღალავა")
    edits.save(d.id, d.lock_version, edited(d, stem="ბ"))
    assert levels == {"draft": 2, "save": 2}
    assert level(open_database(paths.database_file)) == 1
