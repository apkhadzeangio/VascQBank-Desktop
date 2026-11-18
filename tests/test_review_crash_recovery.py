"""Approval, rejection, reopening and restore interrupted by SIGKILL.

Isolated child processes work on TEMPORARY databases of synthetic questions and are killed at
an exact SQL statement of the transaction (SQLite's trace hook). The parent then reopens the
database: it must hold exactly the last committed state — never a half-applied decision — and
the same action must work afterwards.
"""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
import textwrap

import pytest
from bank import approve, events_of, make_question, question_row, revisions_of, seed, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the scenarios kill the child with SIGKILL")

UID = "ALI-0003"
REVIEWER = "დავით ფარღალავა"

PRELUDE = textwrap.dedent(
    """
    import os, signal, sys
    from pathlib import Path
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.db import connection
    from vascqbank_desktop.services import review_service
    from vascqbank_desktop.services.catalog_service import CatalogService
    from vascqbank_desktop.services.review_service import ReviewService
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

    review_service.open_database = arm
    service = ReviewService(paths, ReviewerProfile(SettingsStore(paths.settings_file)))
    detail = CatalogService(paths).detail_by_uid("ALI-0003")
    revisions = CatalogService(paths).revisions(detail.id)
    """
)

SCRIPTS = {
    "APPROVE": 'service.approve(detail.id, detail.lock_version)\nprint("DONE", flush=True)',
    "REJECT": 'service.reject(detail.id, detail.lock_version, "მიზეზი")\nprint("DONE", flush=True)',
    "REOPEN": 'service.reopen(detail.id, detail.lock_version)\nprint("DONE", flush=True)',
    "START_REVIEW": 'service.start_review(detail.id, detail.lock_version)\nprint("DONE", flush=True)',
    "RESTORE": "service.restore_revision(detail.id, detail.lock_version, revisions[-1].id)\n"
    'print("DONE", flush=True)',
}

KILL_POINTS = {
    "APPROVE": ["UPDATE questions SET status", "INSERT INTO review_events", "COMMIT"],
    "REJECT": ["UPDATE questions SET status", "INSERT INTO review_events", "COMMIT"],
    "REOPEN": ["UPDATE questions SET status", "INSERT INTO review_events", "COMMIT"],
    "START_REVIEW": ["UPDATE questions SET status", "COMMIT"],
    "RESTORE": [
        "INSERT INTO question_revisions",
        "UPDATE questions SET current_revision_id",
        "INSERT INTO review_events",
        "COMMIT",
    ],
}


def run_child(paths: AppPaths, action: str, kill_at: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", PRELUDE + SCRIPTS[action], str(paths.data_dir), kill_at],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


@pytest.fixture
def bank(app_paths: AppPaths) -> AppPaths:
    seed(app_paths, [make_question("ALI", n) for n in range(1, 8)])
    ReviewerProfile(SettingsStore(app_paths.settings_file)).set_name(REVIEWER)
    return app_paths


def prepare(paths: AppPaths, action: str) -> None:
    if action == "REOPEN":
        approve(paths, UID)
    if action == "RESTORE":
        detail = CatalogService(paths).detail_by_uid(UID)
        from vascqbank_desktop.domain.question import QuestionContent

        EditService(paths, ReviewerProfile(SettingsStore(paths.settings_file))).save(
            detail.id,
            detail.lock_version,
            QuestionContent("მეორე რედაქცია", detail.content.options, detail.content.correct, "e"),
        )


def integrity_ok(paths: AppPaths) -> bool:
    conn = sqlite3.connect(paths.database_file)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return ok and conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("action", "kill_at"),
    [(action, point) for action, points in KILL_POINTS.items() for point in points],
)
def test_a_kill_at_any_point_leaves_the_database_exactly_as_it_was(
    bank: AppPaths, action: str, kill_at: str
) -> None:
    prepare(bank, action)
    before = snapshot(bank)
    result = run_child(bank, action, kill_at)
    assert result.returncode == -signal.SIGKILL and "DONE" not in result.stdout, result.stderr
    assert integrity_ok(bank)
    assert snapshot(bank) == before  # status, pointers, lock version, revisions, events: all or nothing

    again = run_child(bank, action)  # the interrupted decision can simply be made again
    assert again.returncode == 0 and "DONE" in again.stdout, again.stderr
    assert integrity_ok(bank)
    assert snapshot(bank) != before


def test_the_uninterrupted_approval_commits_everything(bank: AppPaths) -> None:
    """Control: the child script works, so the kill tests above are meaningful."""
    result = run_child(bank, "APPROVE")
    assert result.returncode == 0 and "DONE" in result.stdout, result.stderr
    row = question_row(bank, UID)
    assert row["status"] == ReviewStatus.APPROVED.value and row["approved_revision_id"] is not None
    assert row["lock_version"] == 1
    assert [e["action"] for e in events_of(bank, UID)] == ["IMPORTED", "APPROVE"]


def test_a_killed_restore_leaves_no_half_revision(bank: AppPaths) -> None:
    prepare(bank, "RESTORE")
    run_child(bank, "RESTORE", "UPDATE questions SET current_revision_id")  # revision inserted, pointer not
    assert [r["revision_no"] for r in revisions_of(bank, UID)] == [1, 2]  # the new revision rolled back
    service = ReviewService(bank, ReviewerProfile(SettingsStore(bank.settings_file)))
    detail = CatalogService(bank).detail_by_uid(UID)
    result = service.restore_revision(
        detail.id, detail.lock_version, CatalogService(bank).revisions(detail.id)[-1].id
    )
    assert result.revision_no == 3 and [r["revision_no"] for r in revisions_of(bank, UID)] == [1, 2, 3]
