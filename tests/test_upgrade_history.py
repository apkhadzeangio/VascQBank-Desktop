"""Phase 9b — a working folder created by EVERY earlier release opens correctly in this version.

For each release commit the source tree is extracted from git into a temporary folder and
``tests/upgrade_driver.py`` runs *that* release's own services in a child process: import, reviewer
name and session, backups, drafts, saved revisions, approve / reject / reopen / restore-revision and
export — whatever the release had. The folder is then opened with the current code. Asserted:

* start-up succeeds; an older schema is migrated behind a verified pre-migration backup that holds
  exactly the old rows;
* every row of every old table is preserved exactly (questions, revisions, drafts, decisions,
  history, import batches, export runs, metadata); ``schema_migrations`` only grows;
* the migrated schema is exactly the reference schema (no stray object, nothing missing);
* every old backup still verifies and can be restored;
* settings, reviewer name and saved session load; new work is possible afterwards.

CI clones with full history (``fetch-depth: 0``); where the history is missing (a shallow clone) the
test is skipped with that reason. Synthetic data only.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from synthetic import write_pair

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.db.errors import SchemaTooNewError
from vascqbank_desktop.db.migrations import LATEST_VERSION
from vascqbank_desktop.db.schema_check import schema_differences
from vascqbank_desktop.services.export_publication import reconcile
from vascqbank_desktop.services.restore_service import inspect_backup, restore_backup
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.seed_service import sha256_file
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore
from vascqbank_desktop.services.startup import Outcome, prepare_storage

REPO = Path(__file__).resolve().parent.parent
DRIVER = Path(__file__).resolve().parent / "upgrade_driver.py"
REVIEWER = "ძველი ვერსიის რეცენზენტი"

# (release commit, version, schema version it writes)
RELEASES = [
    ("097011d", "0.1.0", 1),  # Phase 1 — storage foundation only
    ("e32f60d", "0.2.0", 2),  # Phase 2 — questions and import
    ("83bc299", "0.3.0", 2),  # Phase 3 — settings, session, manual backups
    ("edcf989", "0.4.0", 3),  # Phase 4 — drafts and saved revisions
    ("10753fe", "0.5.0", 3),  # Phase 5 — approve / reject / reopen / restore-revision
    ("cf40a83", "0.6.0", 3),  # Phase 6 — export
    ("babb513", "0.7.0", 3),  # Phase 7 — seed, export publication journal
    ("5163d2a", "0.8.0", 3),  # Phase 8 — restore, reviewer workspace
    ("35016c2", "0.9.0", 3),  # Phase 9 — reliability release
]


def _extract_release(commit: str, target: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not installed: earlier releases cannot be extracted")
    probe = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True
    )
    if probe.returncode != 0:
        pytest.skip(f"release commit {commit} is not in this clone (shallow history)")
    archive = subprocess.run(
        ["git", "-C", str(REPO), "archive", "--format=tar", commit, "src"], capture_output=True, check=True
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(target, filter="data")
    return target / "src"


def _dump(database: Path) -> dict[str, list[list]]:
    conn = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {t: [list(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY 1')] for t in tables}
    finally:
        conn.close()


def _run_old_release(src: Path, data: Path, sources: Path, out: Path) -> dict:
    code = (
        "import sys, runpy; sys.path.insert(0, sys.argv[1]); "
        "sys.argv = [sys.argv[2], *sys.argv[3:]]; runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    child = subprocess.run(
        [sys.executable, "-I", "-c", code, str(src), str(DRIVER), str(data), str(sources), str(out)],
        capture_output=True, text=True, timeout=300, cwd=str(out.parent),
    )  # fmt: skip
    assert child.returncode == 0, child.stderr[-3000:]
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def old_folders(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, dict]]:
    """One folder per release, created once by that release (copied by each test before use)."""
    root = tmp_path_factory.mktemp("releases")
    sources = root / "სინთეზური წყარო"
    write_pair(sources, "ALI", range(1, 9))
    write_pair(sources, "VENOUS", range(1, 4))
    made: dict[str, tuple[Path, dict]] = {}
    for commit, _version, _schema in RELEASES:
        src = _extract_release(commit, root / f"src-{commit}")
        data = root / f"მონაცემები {commit}"
        made[commit] = (data, _run_old_release(src, data, sources, root / f"{commit}.json"))
    return made


def _fresh_copy(old: Path, tmp_path: Path) -> AppPaths:
    target = tmp_path / "ახალი ვერსია"
    shutil.copytree(old, target)
    return AppPaths(data_dir=target)


@pytest.mark.parametrize(("commit", "version", "schema"), RELEASES, ids=[r[1] for r in RELEASES])
def test_a_folder_from_an_earlier_release_opens_with_everything_preserved(
    old_folders, tmp_path: Path, commit: str, version: str, schema: int
) -> None:
    old_data, created = old_folders[commit]
    assert created["version"] == version
    paths = _fresh_copy(old_data, tmp_path)
    old_rows = created["tables"]
    old_backups = backup_db.list_backups(paths.backups_dir)

    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.READY and result.status is not None
    status = result.status
    assert status.schema_version == LATEST_VERSION and status.integrity_ok
    assert status.applied_migrations == tuple(range(schema + 1, LATEST_VERSION + 1))

    if schema < LATEST_VERSION:  # migrated behind a verified backup that holds the old rows exactly
        assert status.pre_migration_backup is not None
        assert backup_db.check_backup_file(status.pre_migration_backup).ok
        assert _dump(status.pre_migration_backup) == old_rows
    else:
        assert status.pre_migration_backup is None

    new_rows = _dump(paths.database_file)
    for table, rows in old_rows.items():
        if table == "schema_migrations":
            assert new_rows[table][: len(rows)] == rows  # the audit trail only grows
        else:
            assert new_rows[table] == rows, table  # every question, revision, draft, decision, …
    conn = sqlite3.connect(paths.database_file)
    try:
        assert schema_differences(conn, LATEST_VERSION, limit=0) == []
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()

    for backup in backup_db.list_backups(paths.backups_dir):  # old backups and the new one
        assert backup_db.check_backup_file(backup).ok, backup.name
        inspect_backup(backup)

    if "settings" in created["done"]:
        settings = SettingsStore(paths.settings_file).load()
        assert settings.reviewer_name == REVIEWER
        assert settings.session.question_uid == "ALI-0003" and settings.session.subject_code == "ALI"
    if "export" in created["done"]:
        runs_before = len(new_rows["export_runs"])
        reconcile(paths)  # an export published by the old release is neither lost nor recorded twice
        assert runs_before == 2  # one row per format (DOCX, TXT) of the single old export
        assert len(_dump(paths.database_file)["export_runs"]) == runs_before

    # new work on top of the upgraded data
    if "import" in created["done"]:
        profile = ReviewerProfile(SettingsStore(paths.settings_file))
        profile.set_name("ახალი ვერსიის რეცენზენტი")
        conn = sqlite3.connect(paths.database_file)
        try:
            qid, lock = conn.execute(
                "SELECT id, lock_version FROM questions WHERE question_uid = 'VENOUS-0001'"
            ).fetchone()
        finally:
            conn.close()
        assert ReviewService(paths, profile).approve(qid, lock).changed

    # an old backup made after the old release's work restores and migrates
    if old_backups:
        newest_old = old_backups[-1]
        summary = inspect_backup(newest_old)
        restored = restore_backup(paths, newest_old, expected_sha256=summary.sha256)
        assert restored.status.schema_version == LATEST_VERSION
        after = _dump(paths.database_file)
        expected = _dump(newest_old)
        for table in ("questions", "question_revisions", "review_events"):
            if table in expected:
                assert after[table] == expected[table]


def test_a_database_from_a_newer_version_is_refused_untouched(old_folders, tmp_path: Path) -> None:
    old_data, _created = old_folders[RELEASES[-1][0]]
    paths = _fresh_copy(old_data, tmp_path)
    conn = sqlite3.connect(paths.database_file)
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")
    conn.close()
    before = sha256_file(paths.database_file)
    backups_before = sorted(p.name for p in paths.backups_dir.iterdir())
    with pytest.raises(SchemaTooNewError):
        prepare_storage(paths, None)
    assert sha256_file(paths.database_file) == before
    assert sorted(p.name for p in paths.backups_dir.iterdir()) == backups_before
