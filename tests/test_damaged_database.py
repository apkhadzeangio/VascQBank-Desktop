"""Phase 9b — a damaged or emptied WORKING database: never silently replaced, always recoverable.

Before Phase 9b a zero-byte working database (a crash on a FAT medium, an over-eager cleaner, a
full disk) was opened as a *blank* database and silently became an empty bank, although backups
of the real work existed; a corrupted one stopped the start with "could not open" and no way to
reach the backups from the program. Now start-up reports :attr:`Outcome.DATABASE_DAMAGED`, nothing
is changed, and the reviewer may (explicitly) set the file aside into ``displaced/`` — kept
byte-for-byte — and restore a verified backup. Synthetic data in temporary folders only.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from bank import approve, seed_counts
from seedbank import small_seed

from vascqbank_desktop.app import main as app_main
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.services import restore_service
from vascqbank_desktop.services.backup_service import create_manual_backup
from vascqbank_desktop.services.restore_service import displaced_dir, set_aside_damaged_database
from vascqbank_desktop.services.seed_service import sha256_file
from vascqbank_desktop.services.startup import DamageKind, EvidenceKind, Outcome, prepare_storage


def _questions(paths: AppPaths) -> list[tuple]:
    conn = sqlite3.connect(paths.database_file)
    try:
        return conn.execute("SELECT question_uid, status, lock_version FROM questions ORDER BY 1").fetchall()
    finally:
        conn.close()


@pytest.fixture
def worked(app_paths: AppPaths) -> tuple[AppPaths, Path, list[tuple]]:
    """A bank with work and a verified backup of it."""
    seed_counts(app_paths, {"ALI": 120, "VENOUS": 40})
    approve(app_paths, "ALI-0001")
    backup = create_manual_backup(app_paths).path
    return app_paths, backup, _questions(app_paths)


def _truncate(paths: AppPaths) -> None:
    size = paths.database_file.stat().st_size
    with paths.database_file.open("r+b") as handle:
        handle.truncate(size // 2)


def _bitflip(paths: AppPaths) -> None:
    data = bytearray(paths.database_file.read_bytes())
    for i in range(4096 * 3, len(data), 997):
        data[i] ^= 0x5A
    paths.database_file.write_bytes(bytes(data))


def _bad_magic(paths: AppPaths) -> None:
    data = bytearray(paths.database_file.read_bytes())
    data[0:16] = b"Garbage format\0\0"
    paths.database_file.write_bytes(bytes(data))


def _broken_tree(paths: AppPaths) -> None:
    """The file still opens, but ``quick_check`` fails (a b-tree pointer beyond the end of the file)."""
    conn = sqlite3.connect(paths.database_file)
    try:
        root = conn.execute(
            "SELECT rootpage FROM sqlite_schema WHERE name = 'question_revisions'"
        ).fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
    finally:
        conn.close()
    data = bytearray(paths.database_file.read_bytes())
    offset = (root - 1) * page_size
    assert data[offset] == 5  # an interior table page: its right-most child pointer follows
    data[offset + 8 : offset + 12] = (pages + 50).to_bytes(4, "big")
    paths.database_file.write_bytes(bytes(data))


CORRUPTIONS = {"truncated": _truncate, "bit-flips": _bitflip, "header-destroyed": _bad_magic}


def _files(paths: AppPaths) -> dict[str, str]:
    return {
        str(p.relative_to(paths.data_dir)): sha256_file(p)
        for p in sorted(paths.data_dir.rglob("*"))
        if p.is_file() and p.parent.name != "logs"
    }


# ----------------------------------------------------------------------------------- start-up
def test_a_zero_byte_database_with_backups_is_not_silently_replaced(worked) -> None:
    paths, _backup, _rows = worked
    paths.database_file.write_bytes(b"")
    before = _files(paths)
    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.DATABASE_DAMAGED and result.damage is DamageKind.EMPTY
    assert any(e.kind is EvidenceKind.BACKUPS for e in result.evidence)
    assert _files(paths) == before  # not initialised, not moved, nothing created
    # only an explicit "new database" decision makes it a new, empty bank (as before Phase 9b)
    result = prepare_storage(paths, None, allow_new_database=True)
    assert result.outcome is Outcome.READY and result.status is not None and result.status.created_new


def test_a_zero_byte_leftover_without_any_evidence_does_not_block_the_seed(
    tmp_path: Path, app_paths: AppPaths
) -> None:
    """A first launch that died before writing anything leaves an empty file; the seed still installs."""
    app_paths.ensure_directories()
    app_paths.database_file.write_bytes(b"")
    result = prepare_storage(app_paths, small_seed(tmp_path))
    assert result.outcome is Outcome.READY and result.seed_install is not None
    assert len(_questions(app_paths)) == 26


@pytest.mark.parametrize("how", sorted(CORRUPTIONS))
def test_a_corrupted_database_is_reported_and_left_untouched(worked, how: str) -> None:
    paths, _backup, _rows = worked
    CORRUPTIONS[how](paths)
    before = _files(paths)
    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.DATABASE_DAMAGED and result.damage is DamageKind.CORRUPT
    assert result.damage_detail and result.status is None
    assert _files(paths) == before


def test_a_database_that_opens_but_fails_its_check_is_reported(worked) -> None:
    paths, _backup, _rows = worked
    _broken_tree(paths)
    before = sha256_file(paths.database_file)
    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.DATABASE_DAMAGED and result.damage is DamageKind.CHECK_FAILED
    assert result.status is not None and not result.status.integrity_ok
    assert sha256_file(paths.database_file) == before
    # "open anyway" (e.g. to export approved work first) stays possible, explicitly
    result = prepare_storage(paths, None, accept_damaged=True)
    assert result.outcome is Outcome.READY and result.status is not None and not result.status.integrity_ok


def test_a_directory_in_place_of_the_database_is_an_error_not_damage(app_paths: AppPaths) -> None:
    """'Cannot open' may be transient (antivirus, permissions): never treated as damage, never moved."""
    app_paths.ensure_directories()
    app_paths.database_file.mkdir()
    (app_paths.database_file / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(sqlite3.OperationalError):
        prepare_storage(app_paths, None)
    assert (app_paths.database_file / "keep.txt").read_text(encoding="utf-8") == "x"


# ----------------------------------------------------------------------------------- set aside + restore
@pytest.mark.parametrize("how", ["truncated", "zero-bytes", "broken-tree"])
def test_setting_aside_keeps_every_byte_and_leads_to_the_normal_restore(worked, how: str) -> None:
    paths, backup, rows = worked
    damage = {
        "truncated": _truncate,
        "zero-bytes": lambda p: p.database_file.write_bytes(b""),
        "broken-tree": _broken_tree,
    }
    damage[how](paths)
    Path(f"{paths.database_file}-wal").write_bytes(os.urandom(1000))  # a stale side file goes along
    damaged_sha = sha256_file(paths.database_file)
    moved = set_aside_damaged_database(paths)
    assert not paths.database_file.exists() and not Path(f"{paths.database_file}-wal").exists()
    kept = [p for p in moved if p.name.endswith("-damaged.sqlite3")]
    assert len(kept) == 1 and kept[0].parent == displaced_dir(paths) and sha256_file(kept[0]) == damaged_sha
    assert any(p.name.endswith("-damaged.sqlite3-wal") for p in moved)

    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.RECOVERY_NEEDED
    assert {e.kind for e in result.evidence} >= {EvidenceKind.BACKUPS, EvidenceKind.DISPLACED}
    restore_service.restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    assert _questions(paths) == rows
    assert sha256_file(kept[0]) == damaged_sha  # still there after the restore


def test_set_aside_refuses_while_a_restore_journal_exists(worked) -> None:
    paths, *_ = worked
    _truncate(paths)
    restore_service.journal_path(paths).parent.mkdir(parents=True, exist_ok=True)
    restore_service.journal_path(paths).write_text("{}", encoding="utf-8")
    with pytest.raises(restore_service.RestoreRefused):
        set_aside_damaged_database(paths)
    assert paths.database_file.exists()


# ----------------------------------------------------------------------------------- the program
def test_the_program_offers_restore_for_a_corrupted_database(qtbot, worked, monkeypatch) -> None:
    paths, backup, rows = worked
    _bitflip(paths)
    damaged_sha = sha256_file(paths.database_file)
    asked: list[DamageKind] = []
    monkeypatch.setattr(
        app_main, "ask_damaged", lambda result, data_dir, _ms=0: asked.append(result.damage) or "restore"
    )

    def restore_like_the_reviewer(p: AppPaths, _ms: int = 0) -> bool:
        restore_service.restore_backup(p, backup, expected_sha256=sha256_file(backup))
        return True

    monkeypatch.setattr(app_main, "ask_restore", restore_like_the_reviewer)
    result = app_main.startup(paths, None)
    assert not isinstance(result, int) and result.outcome is Outcome.READY
    assert asked == [DamageKind.CORRUPT] and _questions(paths) == rows
    assert [sha256_file(p) for p in displaced_dir(paths).glob("*-damaged.sqlite3")] == [damaged_sha]


def test_quitting_at_the_damage_message_changes_nothing(qtbot, worked, monkeypatch) -> None:
    paths, *_ = worked
    _truncate(paths)
    before = _files(paths)
    monkeypatch.setattr(app_main, "ask_damaged", lambda result, data_dir, _ms=0: "exit")
    assert app_main.startup(paths, None) == app_main.EXIT_DATABASE_DAMAGED
    assert _files(paths) == before
    assert app_main.run_self_check(paths) == app_main.EXIT_DATABASE_DAMAGED
    assert _files(paths) == before


def test_open_anyway_is_offered_only_when_the_file_opens(qtbot, worked, monkeypatch) -> None:
    paths, *_ = worked
    _broken_tree(paths)
    monkeypatch.setattr(app_main, "ask_damaged", lambda result, data_dir, _ms=0: "continue")
    result = app_main.startup(paths, None)
    assert not isinstance(result, int) and result.outcome is Outcome.READY
    assert result.status is not None and not result.status.integrity_ok


def test_the_real_damage_message_is_georgian_and_defaults_to_quit(qtbot, worked) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    paths, *_ = worked
    _truncate(paths)
    result = prepare_storage(paths, None)
    seen: list[tuple[str, list[str]]] = []

    def inspect() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                seen.append((widget.text(), [b.text() for b in widget.buttons()]))
                widget.reject()

    from PySide6.QtCore import QTimer

    QTimer.singleShot(200, inspect)
    assert app_main.ask_damaged(result, paths.data_dir, 5000) == "exit"
    from vascqbank_desktop.ui import strings

    assert seen and strings.DAMAGED_TEXT_CORRUPT.split("\n")[0] in seen[0][0]
    assert strings.DAMAGED_RESTORE in seen[0][1] and strings.DAMAGED_CONTINUE not in seen[0][1]


def test_copying_the_damaged_file_elsewhere_first_is_possible(worked, tmp_path: Path) -> None:
    """Nothing locks or hides the damaged file: a person can always take a copy before deciding."""
    paths, *_ = worked
    _truncate(paths)
    assert prepare_storage(paths, None).outcome is Outcome.DATABASE_DAMAGED
    shutil.copy2(paths.database_file, tmp_path / "copy.sqlite3")
    assert sha256_file(tmp_path / "copy.sqlite3") == sha256_file(paths.database_file)
