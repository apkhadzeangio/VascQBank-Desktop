"""Abrupt termination (SIGKILL) of isolated child processes working on TEMPORARY databases.

No real user data is ever involved: every child opens a throw-away database created by the
test in its own temporary directory, and only that child is killed. The kill is injected at a
precise point with SQLite's statement trace hook, then the parent reopens the database and
checks that it holds exactly the last *committed* state — never a half-written one.
"""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
import textwrap

import pytest
from bank import make_question, question_row, seed, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.draft_service import DraftService

UID = "ALI-0003"
GEORGIAN = "კაროტიდული სტენოზი ≥ 70% — „ციტატა“ ΔP × 10⁹/ლ"

# Shared by the child scripts: kill this process when SQLite is about to run a statement
# containing KILL_AT (the statements of a transaction are traced as they start).
PRELUDE = textwrap.dedent(
    """
    import os, signal, sys
    from pathlib import Path
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.db import connection
    from vascqbank_desktop.domain.answers import AnswerLetter
    from vascqbank_desktop.domain.question import QuestionContent
    from vascqbank_desktop.services import draft_service, edit_service
    from vascqbank_desktop.services.catalog_service import CatalogService
    from vascqbank_desktop.services.draft_service import DraftService
    from vascqbank_desktop.services.edit_service import EditService
    from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

    paths = AppPaths(data_dir=Path(sys.argv[1]))
    kill_at = sys.argv[2] if len(sys.argv) > 2 else ""
    real_open = connection.open_database

    def arm(path):
        conn = real_open(path)
        if kill_at:
            def trace(statement):
                if kill_at in statement:
                    os.kill(os.getpid(), signal.SIGKILL)
            conn.set_trace_callback(trace)
        return conn

    draft_service.open_database = arm
    edit_service.open_database = arm
    detail = CatalogService(paths).detail_by_uid("ALI-0003")
    """
)


def run_child(script: str, paths: AppPaths, kill_at: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", PRELUDE + textwrap.dedent(script), str(paths.data_dir), kill_at],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def killed(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode == -signal.SIGKILL


@pytest.fixture
def bank(app_paths: AppPaths) -> AppPaths:
    seed(app_paths, [make_question("ALI", n) for n in range(1, 8)])
    return app_paths


def integrity_ok(paths: AppPaths) -> bool:
    conn = sqlite3.connect(paths.database_file)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


# ------------------------------------------------------------------ drafts


def test_a_confirmed_draft_survives_the_process_being_killed(bank: AppPaths) -> None:
    result = run_child(
        f"""
        DraftService(paths).save_draft(detail.id, detail.revision_id, detail.lock_version,
            QuestionContent({GEORGIAN!r}, detail.content.options, AnswerLetter.D, "განმარტება"),
            "დავით ფარღალავა")
        print("CONFIRMED", flush=True)          # printed only AFTER save_draft returned (= committed)
        os.kill(os.getpid(), signal.SIGKILL)    # abrupt end: no cleanup, no flush, no close
        """,
        bank,
    )
    assert killed(result) and "CONFIRMED" in result.stdout
    assert integrity_ok(bank)
    draft = DraftService(bank).load_draft(CatalogService(bank).detail_by_uid(UID).id)
    assert draft is not None
    assert draft.content.stem == GEORGIAN and draft.content.correct is AnswerLetter.D
    assert draft.reviewer_name == "დავით ფარღალავა" and draft.base_lock_version == 0


@pytest.mark.parametrize("kill_at", ["INSERT INTO drafts", "SELECT id, question_id", "COMMIT"])
def test_a_kill_inside_a_draft_write_leaves_the_previous_confirmed_draft(
    bank: AppPaths, kill_at: str
) -> None:
    detail = CatalogService(bank).detail_by_uid(UID)
    drafts = DraftService(bank)
    old = QuestionContent("ბოლო დადასტურებული ვერსია", detail.content.options, detail.content.correct, "e")
    drafts.save_draft(detail.id, detail.revision_id, detail.lock_version, old, "დავით ფარღალავა")

    result = run_child(
        """
        DraftService(paths).save_draft(detail.id, detail.revision_id, detail.lock_version,
            QuestionContent("ახალი, ჯერ არადადასტურებული ტექსტი", detail.content.options,
                            detail.content.correct, "e"),
            "დავით ფარღალავა")
        print("CONFIRMED", flush=True)
        """,
        bank,
        kill_at,
    )
    assert killed(result) and "CONFIRMED" not in result.stdout  # it never claimed the write succeeded
    assert integrity_ok(bank)
    stored = drafts.load_draft(detail.id)
    assert stored is not None and stored.content.stem == "ბოლო დადასტურებული ვერსია"
    assert stored.write_count == 1


def test_a_kill_during_the_first_ever_draft_write_leaves_no_draft(bank: AppPaths) -> None:
    result = run_child(
        """
        DraftService(paths).save_draft(detail.id, detail.revision_id, detail.lock_version,
            detail.content, "დავით ფარღალავა")
        """,
        bank,
        "COMMIT",
    )
    assert killed(result) and integrity_ok(bank)
    assert DraftService(bank).count() == 0  # a half-written draft must not exist


# ------------------------------------------------------------------ the authoritative save


SAVE_SCRIPT = """
EditService(paths, ReviewerProfile(SettingsStore(paths.settings_file))).save(
    detail.id, detail.lock_version,
    QuestionContent("შენახული ტექსტი", detail.content.options, AnswerLetter.A, "ახალი განმარტება"))
print("SAVED", flush=True)
"""


@pytest.mark.parametrize(
    "kill_at",
    [
        "INSERT INTO question_revisions",
        "UPDATE questions SET current_revision_id",
        "INSERT INTO review_events",
        "DELETE FROM drafts",
        "COMMIT",
    ],
)
def test_a_kill_at_any_point_of_a_save_leaves_the_database_exactly_as_it_was(
    bank: AppPaths, kill_at: str
) -> None:
    from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

    ReviewerProfile(SettingsStore(bank.settings_file)).set_name("დავით ფარღალავა")
    detail = CatalogService(bank).detail_by_uid(UID)
    DraftService(bank).save_draft(
        detail.id,
        detail.revision_id,
        detail.lock_version,
        QuestionContent("შენახული ტექსტი", detail.content.options, AnswerLetter.A, "ახალი განმარტება"),
        "დავით ფარღალავა",
    )
    before = snapshot(bank)

    result = run_child(SAVE_SCRIPT, bank, kill_at)
    assert killed(result) and "SAVED" not in result.stdout
    assert integrity_ok(bank)
    after = snapshot(bank)
    assert after == before  # no revision, no pointer move, no status change, no event — all or nothing
    row = question_row(bank, UID)
    assert (row["status"], row["lock_version"], row["revision_count"]) == ("PENDING_REVIEW", 0, 1)
    assert DraftService(bank).load_draft(detail.id) is not None  # the draft is still there to be recovered


def test_the_same_save_without_a_kill_commits_everything(bank: AppPaths) -> None:
    """Control: the child script itself works, so the kill tests above are meaningful."""
    from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

    ReviewerProfile(SettingsStore(bank.settings_file)).set_name("დავით ფარღალავა")
    detail = CatalogService(bank).detail_by_uid(UID)
    DraftService(bank).save_draft(
        detail.id,
        detail.revision_id,
        detail.lock_version,
        QuestionContent("შენახული ტექსტი", detail.content.options, AnswerLetter.A, "ახალი განმარტება"),
        "დავით ფარღალავა",
    )
    result = run_child(SAVE_SCRIPT, bank, "")
    assert result.returncode == 0 and "SAVED" in result.stdout, result.stderr
    row = question_row(bank, UID)
    assert (row["status"], row["lock_version"], row["revision_count"]) == ("IN_REVIEW", 1, 2)
    assert DraftService(bank).count() == 0 and integrity_ok(bank)


def test_a_database_killed_mid_save_can_be_edited_again(bank: AppPaths) -> None:
    from vascqbank_desktop.services.edit_service import EditService
    from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

    profile = ReviewerProfile(SettingsStore(bank.settings_file))
    profile.set_name("დავით ფარღალავა")
    run_child(SAVE_SCRIPT, bank, "INSERT INTO review_events")
    detail = CatalogService(bank).detail_by_uid(UID)
    result = EditService(bank, profile).save(
        detail.id,
        detail.lock_version,
        QuestionContent("ხელახლა", detail.content.options, detail.content.correct, "e"),
    )
    assert result.created and result.revision_no == 2 and integrity_ok(bank)


# ------------------------------------------------------------------ discarding a draft


@pytest.mark.parametrize("kill_at", ["INSERT INTO draft_discards", "DELETE FROM drafts", "COMMIT"])
def test_a_kill_during_a_discard_never_loses_the_text(bank: AppPaths, kill_at: str) -> None:
    """Archiving and deleting are one transaction: either the draft is still there and nothing
    was archived, or (after the commit) the archive holds the text. Never neither."""
    detail = CatalogService(bank).detail_by_uid(UID)
    drafts = DraftService(bank)
    content = QuestionContent(
        "ტექსტი, რომლის გაუქმებაც შეიძლება", detail.content.options, AnswerLetter.B, "e"
    )
    drafts.save_draft(detail.id, detail.revision_id, detail.lock_version, content, "დავით ფარღალავა")
    before = snapshot(bank)

    result = run_child(
        """
        DraftService(paths).discard_draft(detail.id, "დავით ფარღალავა")
        print("DISCARDED", flush=True)
        """,
        bank,
        kill_at,
    )
    assert killed(result) and "DISCARDED" not in result.stdout
    assert integrity_ok(bank)
    assert snapshot(bank) == before  # the authoritative tables are untouched by any draft action
    stored = drafts.load_draft(detail.id)
    assert stored is not None and stored.content.stem == "ტექსტი, რომლის გაუქმებაც შეიძლება"
    conn = sqlite3.connect(bank.database_file)
    try:
        assert conn.execute("SELECT count(*) FROM draft_discards").fetchone()[0] == 0  # nothing half-archived
    finally:
        conn.close()
    # and the discard itself still works afterwards
    assert drafts.discard_draft(detail.id, "დავით ფარღალავა")
    assert drafts.load_draft(detail.id) is None
    conn = sqlite3.connect(bank.database_file)
    try:
        row = conn.execute("SELECT stem, reason FROM draft_discards").fetchone()
    finally:
        conn.close()
    assert row == ("ტექსტი, რომლის გაუქმებაც შეიძლება", "DISCARDED")
