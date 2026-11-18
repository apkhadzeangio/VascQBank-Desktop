"""Phase 9: "save backup as…", external backups and the daily automatic backup.

Synthetic questions only; every file lives in pytest's temporary folders.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bank import approve, seed_counts, simulate_edit

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.services import auto_backup, backup_portability, backup_service, restore_service
from vascqbank_desktop.services.auto_backup import AutoBackupOutcome
from vascqbank_desktop.services.backup_portability import BackupCopyError, CopyProblem
from vascqbank_desktop.services.backup_service import BackupKind
from vascqbank_desktop.services.storage import initialize_storage

POSIX_ROOT = sys.platform != "win32" and hasattr(os, "geteuid") and os.geteuid() == 0


def _bank(paths: AppPaths, counts: dict[str, int] | None = None) -> None:
    seed_counts(paths, counts or {"ALI": 4, "VENOUS": 3})


def _manual(paths: AppPaths) -> Path:
    return backup_service.create_manual_backup(paths).path


def _uids(paths: AppPaths) -> list[str]:
    conn = sqlite3.connect(paths.database_file)
    try:
        return [r[0] for r in conn.execute("SELECT question_uid FROM questions ORDER BY id")]
    finally:
        conn.close()


def _tree(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


# ------------------------------------------------------------------------------------ save copy
def test_save_copy_writes_identical_verified_backup_and_manifest(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    before = _tree(app_paths.backups_dir)
    usb = tmp_path / "USB დისკი"
    usb.mkdir()

    result = backup_portability.save_backup_copy(source, usb)

    assert result.backup == usb / source.name
    assert result.backup.read_bytes() == source.read_bytes()
    assert result.manifest.read_bytes() == (source.with_name(source.name + ".json")).read_bytes()
    assert backup_db.check_backup_file(result.backup).ok
    assert sorted(p.name for p in usb.iterdir()) == sorted([source.name, source.name + ".json"])
    assert _tree(app_paths.backups_dir) == before  # the original is never touched


def test_save_copy_never_overwrites(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    usb = tmp_path / "usb"
    usb.mkdir()
    (usb / source.name).write_bytes(b"reviewer's other file")

    with pytest.raises(BackupCopyError) as caught:
        backup_portability.save_backup_copy(source, usb)

    assert caught.value.problem is CopyProblem.DESTINATION_EXISTS
    assert (usb / source.name).read_bytes() == b"reviewer's other file"
    assert sorted(p.name for p in usb.iterdir()) == [source.name]  # no temp file left


def test_save_copy_refuses_existing_manifest_too(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    usb = tmp_path / "usb"
    usb.mkdir()
    (usb / (source.name + ".json")).write_text("{}", encoding="utf-8")
    with pytest.raises(BackupCopyError) as caught:
        backup_portability.save_backup_copy(source, usb)
    assert caught.value.problem is CopyProblem.DESTINATION_EXISTS
    assert (usb / (source.name + ".json")).read_text(encoding="utf-8") == "{}"


def test_save_copy_refuses_missing_destination(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    with pytest.raises(BackupCopyError) as caught:
        backup_portability.save_backup_copy(source, tmp_path / "unplugged")
    assert caught.value.problem is CopyProblem.DESTINATION_MISSING


def test_save_copy_refuses_damaged_source(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    data = bytearray(source.read_bytes())
    data[len(data) // 2] ^= 0xFF
    source.write_bytes(bytes(data))
    usb = tmp_path / "usb"
    usb.mkdir()
    with pytest.raises(BackupCopyError) as caught:
        backup_portability.save_backup_copy(source, usb)
    assert caught.value.problem is CopyProblem.SOURCE_INVALID
    assert list(usb.iterdir()) == []


def test_save_copy_refuses_backup_without_manifest(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    source.with_name(source.name + ".json").unlink()
    usb = tmp_path / "usb"
    usb.mkdir()
    with pytest.raises(BackupCopyError) as caught:
        backup_portability.save_backup_copy(source, usb)
    assert caught.value.problem is CopyProblem.SOURCE_INVALID


@pytest.mark.skipif(sys.platform == "win32" or POSIX_ROOT, reason="POSIX permission bits (not as root)")
def test_save_copy_to_read_only_folder_reports_not_writable(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    usb = tmp_path / "read-only"
    usb.mkdir()
    usb.chmod(0o555)
    try:
        with pytest.raises(BackupCopyError) as caught:
            backup_portability.save_backup_copy(source, usb)
        assert caught.value.problem is CopyProblem.NOT_WRITABLE
        assert list(usb.iterdir()) == []
    finally:
        usb.chmod(0o755)


def test_save_copy_failure_after_first_file_removes_only_what_it_wrote(
    app_paths: AppPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    usb = tmp_path / "usb"
    usb.mkdir()
    (usb / "unrelated.txt").write_text("keep", encoding="utf-8")
    real = backup_portability._publish_no_overwrite
    calls = {"n": 0}

    def flaky(temp: Path, final: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # the manifest: as if the drive were pulled out
            raise OSError(5, "Input/output error")
        real(temp, final)

    monkeypatch.setattr(backup_portability, "_publish_no_overwrite", flaky)
    with pytest.raises(BackupCopyError) as caught:
        backup_portability.save_backup_copy(source, usb)
    assert caught.value.problem is CopyProblem.WRITE_FAILED
    assert sorted(p.name for p in usb.iterdir()) == ["unrelated.txt"]
    assert backup_db.check_backup_file(source).ok


def test_save_copy_detects_corruption_on_the_medium(
    app_paths: AppPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    usb = tmp_path / "usb"
    usb.mkdir()
    real = backup_portability._sha256_file
    monkeypatch.setattr(backup_portability, "_sha256_file", lambda p: ("0" * 64, real(p)[1]))
    with pytest.raises(BackupCopyError) as caught:
        backup_portability.save_backup_copy(source, usb)
    assert caught.value.problem is CopyProblem.VERIFY_FAILED
    assert list(usb.iterdir()) == []


def test_publish_without_hard_links_still_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAT/exFAT have no hard links: the rename fallback must refuse an existing name too."""

    def no_links(*_args: object) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    temp = tmp_path / ".x.partial"
    final = tmp_path / "x.sqlite3"
    temp.write_bytes(b"new")
    final.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        backup_portability._publish_no_overwrite(temp, final)
    assert final.read_bytes() == b"old"
    final.unlink()
    backup_portability._publish_no_overwrite(temp, final)
    assert final.read_bytes() == b"new" and not temp.exists()


# ------------------------------------------------------------------------------- external import
def test_import_external_backup_then_restore_it(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    uids = _uids(app_paths)
    approve(app_paths, uids[0])
    source = _manual(app_paths)
    usb = tmp_path / "usb"
    usb.mkdir()
    saved = backup_portability.save_backup_copy(source, usb)
    source_bytes = source.read_bytes()
    # The reviewer loses the local backups folder (e.g. a new computer) …
    shutil.rmtree(app_paths.backups_dir)
    simulate_edit(app_paths, uids[1], stem="სინთეზური ცვლილება ასლის შემდეგ")
    external_before = _tree(usb)

    imported = backup_portability.import_external_backup(app_paths, saved.backup)

    assert imported.backup.parent == app_paths.backups_dir
    assert backup_db.backup_reason(imported.backup) == "imported"
    assert imported.backup.read_bytes() == source_bytes
    manifest = json.loads(imported.manifest.read_text(encoding="utf-8"))
    assert manifest["imported_from"] == source.name and manifest["original_reason"] == "manual"
    assert manifest["path"] == imported.backup.name
    assert str(usb) not in imported.manifest.read_text(encoding="utf-8")  # no external path stored
    assert _tree(usb) == external_before  # the external copy is only read
    entries = backup_service.list_backup_entries(app_paths)
    assert [e.kind for e in entries] == [BackupKind.IMPORTED]

    summary = restore_service.inspect_backup(imported.backup)
    result = restore_service.restore_backup(app_paths, imported.backup, expected_sha256=summary.sha256)
    assert result.status.integrity_ok
    conn = sqlite3.connect(app_paths.database_file)
    try:
        revisions = conn.execute("SELECT count(*) FROM question_revisions").fetchone()[0]
        approved = conn.execute("SELECT count(*) FROM questions WHERE status='APPROVED'").fetchone()[0]
    finally:
        conn.close()
    assert revisions == len(uids)  # the edit made after the backup is intentionally rolled back
    assert approved == 1


def test_import_identical_backup_twice_returns_existing(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    usb = tmp_path / "usb"
    usb.mkdir()
    saved = backup_portability.save_backup_copy(source, usb)
    first = backup_portability.import_external_backup(app_paths, saved.backup)
    assert first.already_present  # the same bytes were already in the folder (the original)
    assert first.backup == source
    assert len(backup_db.list_backups(app_paths.backups_dir)) == 1


def test_import_refuses_damaged_or_foreign_files(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    usb = tmp_path / "usb"
    usb.mkdir()
    foreign = usb / "vascqbank-20260101-000000-000000-manual.sqlite3"
    sqlite3.connect(foreign).close()
    foreign.with_name(foreign.name + ".json").write_text(
        json.dumps({"size": foreign.stat().st_size, "sha256": "0" * 64}), encoding="utf-8"
    )
    with pytest.raises(BackupCopyError) as caught:
        backup_portability.import_external_backup(app_paths, foreign)
    assert caught.value.problem is CopyProblem.SOURCE_INVALID
    no_manifest = usb / "other.sqlite3"
    no_manifest.write_bytes(b"SQLite format 3\x00")
    with pytest.raises(BackupCopyError):
        backup_portability.import_external_backup(app_paths, no_manifest)
    assert backup_db.list_backups(app_paths.backups_dir) == []


def test_imported_backups_are_never_pruned(app_paths: AppPaths, tmp_path: Path) -> None:
    _bank(app_paths)
    source = _manual(app_paths)
    usb = tmp_path / "usb"
    usb.mkdir()
    saved = backup_portability.save_backup_copy(source, usb)
    source.unlink()
    source.with_name(source.name + ".json").unlink()
    imported = backup_portability.import_external_backup(app_paths, saved.backup)
    for reasons in (backup_db.AUTOMATIC_REASONS, (backup_db.DAILY_REASON,)):
        assert backup_db.prune_automatic_backups(app_paths.backups_dir, 0, reasons=reasons) == []
    assert imported.backup.exists()


# ------------------------------------------------------------------------------ automatic backup
def _age_all_backups(paths: AppPaths, hours: float) -> None:
    """Pretend every backup was made *hours* ago (manifest created_at only; bytes unchanged)."""
    for path in backup_db.list_backups(paths.backups_dir):
        manifest_path = path.with_name(path.name + ".json")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        moment = datetime.fromisoformat(data["created_at"]) - timedelta(hours=hours)
        data["created_at"] = moment.isoformat()
        manifest_path.write_text(json.dumps(data), encoding="utf-8")


def test_auto_backup_skips_empty_installation(app_paths: AppPaths) -> None:
    assert auto_backup.run_automatic_backup(app_paths).outcome is AutoBackupOutcome.EMPTY_DATABASE
    initialize_storage(app_paths)
    assert auto_backup.run_automatic_backup(app_paths).outcome is AutoBackupOutcome.EMPTY_DATABASE
    assert backup_db.list_backups(app_paths.backups_dir) == []


def test_auto_backup_first_then_recent_then_unchanged_then_changed(app_paths: AppPaths) -> None:
    _bank(app_paths)
    first = auto_backup.run_automatic_backup(app_paths)
    assert first.outcome is AutoBackupOutcome.CREATED
    assert first.path is not None and backup_db.backup_reason(first.path) == "automatic"
    assert backup_db.check_backup_file(first.path).ok

    # Next launch the same day: nothing new.
    again = auto_backup.run_automatic_backup(app_paths)
    assert again.outcome is AutoBackupOutcome.RECENT_BACKUP and again.path == first.path

    # Two days later, nothing was changed: still nothing new.
    later = datetime.now(UTC) + timedelta(days=2)
    unchanged = auto_backup.run_automatic_backup(app_paths, now=later)
    assert unchanged.outcome is AutoBackupOutcome.UNCHANGED

    # A reviewer edit → one new backup.
    simulate_edit(app_paths, _uids(app_paths)[0], stem="სინთეზური რედაქცია")
    changed = auto_backup.run_automatic_backup(app_paths, now=later)
    assert changed.outcome is AutoBackupOutcome.CREATED
    assert len(backup_db.list_backups(app_paths.backups_dir)) == 2


def test_auto_backup_detects_draft_only_changes(app_paths: AppPaths) -> None:
    from vascqbank_desktop.domain.question import AnswerLetter, QuestionContent
    from vascqbank_desktop.services.draft_service import DraftService

    _bank(app_paths)
    assert auto_backup.run_automatic_backup(app_paths).outcome is AutoBackupOutcome.CREATED
    later = datetime.now(UTC) + timedelta(days=2)
    assert auto_backup.run_automatic_backup(app_paths, now=later).outcome is AutoBackupOutcome.UNCHANGED
    conn = open_database(app_paths.database_file)
    try:
        row = conn.execute(
            "SELECT q.id, q.lock_version, q.current_revision_id, r.stem, r.option_a, r.option_b, r.option_c, "
            "r.option_d, r.option_e, r.correct_letter, r.explanation FROM questions q "
            "JOIN question_revisions r ON r.id = q.current_revision_id ORDER BY q.id LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    content = QuestionContent(
        stem=row["stem"] + " (სინთეზური მონახაზი)",
        options=tuple(row[f"option_{x}"] for x in "abcde"),
        correct=AnswerLetter(row["correct_letter"]),
        explanation=row["explanation"],
    )
    DraftService(app_paths).save_draft(
        row["id"], row["current_revision_id"], row["lock_version"], content, "სატესტო რეცენზენტი"
    )
    assert auto_backup.run_automatic_backup(app_paths, now=later).outcome is AutoBackupOutcome.CREATED


def test_auto_backup_counts_a_recent_manual_backup(app_paths: AppPaths) -> None:
    _bank(app_paths)
    manual = _manual(app_paths)
    result = auto_backup.run_automatic_backup(app_paths)
    assert result.outcome is AutoBackupOutcome.RECENT_BACKUP and result.path == manual


def test_auto_backup_ignores_a_damaged_newest_backup(app_paths: AppPaths) -> None:
    _bank(app_paths)
    manual = _manual(app_paths)
    manual.write_bytes(b"damaged")
    result = auto_backup.run_automatic_backup(app_paths)
    assert result.outcome is AutoBackupOutcome.CREATED
    assert manual.read_bytes() == b"damaged"  # evidence is never deleted


def test_auto_backup_retention_prunes_only_automatic(app_paths: AppPaths) -> None:
    _bank(app_paths)
    keep = {_manual(app_paths)}
    conn = open_database(app_paths.database_file)
    try:
        keep.add(backup_db.create_backup(conn, app_paths.backups_dir, "pre-restore").path)
        keep.add(backup_db.create_backup(conn, app_paths.backups_dir, "pre-import").path)
    finally:
        conn.close()
    uids = _uids(app_paths)
    created: list[Path] = []
    for day in range(backup_db.KEEP_DAILY + 3):
        _age_all_backups(app_paths, 25)
        simulate_edit(app_paths, uids[day % len(uids)], stem=f"სინთეზური რედაქცია {day}")
        result = auto_backup.run_automatic_backup(app_paths)
        assert result.outcome is AutoBackupOutcome.CREATED, result
        created.append(result.path)  # type: ignore[arg-type]
    remaining = set(backup_db.list_backups(app_paths.backups_dir))
    automatic = sorted(p for p in remaining if backup_db.backup_reason(p) == "automatic")
    assert len(automatic) == backup_db.KEEP_DAILY
    assert automatic == sorted(created[-backup_db.KEEP_DAILY :])
    assert keep <= remaining  # manual, pre-restore and pre-import untouched


def test_auto_backup_failure_is_reported_not_raised(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bank(app_paths)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise backup_db.BackupError("disk full (simulated)")

    monkeypatch.setattr(backup_db, "create_backup", broken)
    result = auto_backup.run_automatic_backup(app_paths)
    assert result.outcome is AutoBackupOutcome.FAILED and "simulated" in result.detail


def test_backup_snapshot_is_consistent_while_another_connection_writes(app_paths: AppPaths) -> None:
    """The start-up backup runs while the reviewer may already be autosaving."""
    _bank(app_paths, {"ALI": 50})
    uids = _uids(app_paths)
    reader = open_database(app_paths.database_file)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM questions").fetchone()  # snapshot starts here
        simulate_edit(app_paths, uids[0], stem="სინთეზური ცვლილება ასლის დროს")  # another connection
        info = backup_db.create_backup(reader, app_paths.backups_dir, "automatic")
        assert reader.in_transaction  # the caller's transaction is left as it was
        reader.execute("COMMIT")
    finally:
        reader.close()
    assert info.table_counts["question_revisions"] == 50  # the snapshot, not half of the edit
    assert backup_db.check_backup_file(info.path).ok


# ------------------------------------------------------------------------------------- GUI wiring
@pytest.mark.gui
def test_backup_view_save_copy_and_add_external(
    app_paths: AppPaths, tmp_path: Path, qtbot, styled_qapp
) -> None:
    from vascqbank_desktop.ui import strings
    from vascqbank_desktop.ui.backup_view import BackupView

    _bank(app_paths)
    source = _manual(app_paths)
    view = BackupView(app_paths)
    qtbot.addWidget(view)
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=10_000)
    assert view.select_backup(source.name)
    usb = tmp_path / "usb"
    usb.mkdir()
    assert view.save_copy(usb)
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=10_000)
    assert (usb / source.name).exists()
    assert view.status_label.text().startswith(strings.BK_COPY_SAVED.split("{")[0])

    assert view.save_copy(usb)  # a second time: refused, nothing overwritten
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=10_000)
    assert strings.BK_COPY_PROBLEMS["DESTINATION_EXISTS"] in view.status_label.text()

    source.unlink()
    source.with_name(source.name + ".json").unlink()
    view.refresh()
    assert view.add_external(usb / source.name)
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=10_000)
    assert [e.kind for e in view.entries] == [BackupKind.IMPORTED]
    assert view.selected_entry is not None and view.selected_entry.kind is BackupKind.IMPORTED
    view.shutdown()


@pytest.mark.gui
def test_main_window_runs_the_automatic_backup_once(
    app_paths: AppPaths, qtbot, styled_qapp, monkeypatch
) -> None:
    from vascqbank_desktop.domain.subjects import SUBJECTS
    from vascqbank_desktop.services.storage import initialize_storage as init
    from vascqbank_desktop.ui import main_window as main_window_module
    from vascqbank_desktop.ui.main_window import MainWindow

    _bank(app_paths)
    status = init(app_paths)
    monkeypatch.setattr(main_window_module, "AUTO_BACKUP_DELAY_MS", 10)
    window = MainWindow(status, SUBJECTS, auto_backup=True)
    qtbot.addWidget(window)
    results: list[object] = []
    window.backup_view.automaticBackupFinished.connect(results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=15_000)
    assert results[0].outcome is AutoBackupOutcome.CREATED  # type: ignore[attr-defined]
    assert [backup_db.backup_reason(p) for p in backup_db.list_backups(app_paths.backups_dir)] == [
        "automatic"
    ]
    window.close()

    second = MainWindow(init(app_paths), SUBJECTS, auto_backup=True)
    qtbot.addWidget(second)
    later: list[object] = []
    second.backup_view.automaticBackupFinished.connect(later.append)
    qtbot.waitUntil(lambda: bool(later), timeout=15_000)
    assert later[0].outcome is AutoBackupOutcome.RECENT_BACKUP  # type: ignore[attr-defined]
    assert len(backup_db.list_backups(app_paths.backups_dir)) == 1
    second.close()
