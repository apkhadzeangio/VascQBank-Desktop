"""Export publication killed (SIGKILL) at every step, and the reconciliation that repairs it.

A filesystem rename and a SQLite COMMIT are not jointly atomic; these tests prove the documented
guarantee instead: after any interruption and the next reconciliation, a published, verified folder
has exactly one record, an unpublished staging folder is gone, nothing valid is deleted, and nothing
is recorded twice. Child processes work on temporary folders only.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from exportbank import build

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import export_repository
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.services import export_publication
from vascqbank_desktop.services.export_publication import journal_dir, reconcile
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL")

CHILD = textwrap.dedent(
    """
    import os, signal, sys
    from pathlib import Path
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.db import export_repository
    from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService

    stage = sys.argv[3]
    def checkpoint(name):
        if name == stage:
            os.kill(os.getpid(), signal.SIGKILL)
    export_repository._checkpoint = checkpoint
    ExportService(AppPaths(data_dir=Path(sys.argv[1]))).run(ExportRequest(
        subjects=("ALI", "VENOUS"), formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
        destination=Path(sys.argv[2])))
    print("SURVIVED", flush=True)
    """
)


def kill_export_at(paths: AppPaths, dest: Path, stage: str) -> None:
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(paths.data_dir), str(dest), stage],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert child.returncode == -signal.SIGKILL and "SURVIVED" not in child.stdout, child.stderr


def runs(paths: AppPaths) -> list[dict]:
    conn = open_database(paths.database_file)
    try:
        return export_repository.list_runs(conn)
    finally:
        conn.close()


def folders(dest: Path) -> tuple[list[str], list[str]]:
    names = sorted(p.name for p in dest.iterdir()) if dest.exists() else []
    return [n for n in names if n.startswith("VascQBank_Export_")], [
        n for n in names if n.startswith(".vqb-")
    ]


def journals(paths: AppPaths) -> list[str]:
    folder = journal_dir(paths)
    return sorted(p.name for p in folder.glob("export-*.json")) if folder.is_dir() else []


@pytest.fixture
def bank(app_paths: AppPaths) -> AppPaths:
    build(app_paths)
    return app_paths


@pytest.mark.parametrize("stage", ["journal-written", "inserted"])
def test_killed_before_the_rename_nothing_is_published_or_recorded(
    bank: AppPaths, tmp_path: Path, stage: str
) -> None:
    dest = tmp_path / "out"
    kill_export_at(bank, dest, stage)
    assert folders(dest)[0] == [] and runs(bank) == [] and journals(bank)
    report = reconcile(bank)
    assert len(report.discarded) == 1 and not report.recorded
    assert folders(dest) == ([], []) and journals(bank) == [] and runs(bank) == []


def test_killed_after_the_rename_before_the_commit_the_folder_is_verified_and_recorded(
    bank: AppPaths, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    kill_export_at(bank, dest, "renamed")
    published, staging = folders(dest)
    assert len(published) == 1 and staging == [] and runs(bank) == []  # SQLite rolled the rows back
    report = reconcile(bank)
    assert len(report.recorded) == 1 and report.recorded[0] in published[0]
    recorded = runs(bank)
    assert [r["format"] for r in recorded] == ["DOCX", "TXT"]
    assert {r["scope"]["export_id"] for r in recorded} == {report.recorded[0]}
    assert journals(bank) == [] and not (dest / published[0] / ".vqb-staging").exists()
    assert reconcile(bank).changed is False and len(runs(bank)) == 2  # idempotent: never twice


@pytest.mark.parametrize("stage", ["committed", "journal-removed"])
def test_killed_after_the_commit_nothing_is_recorded_twice(
    bank: AppPaths, tmp_path: Path, stage: str
) -> None:
    dest = tmp_path / "out"
    kill_export_at(bank, dest, stage)
    assert len(folders(dest)[0]) == 1 and len(runs(bank)) == 2
    report = reconcile(bank)
    assert not report.recorded and len(runs(bank)) == 2 and journals(bank) == []


def test_a_crash_during_reconciliation_is_itself_recovered(bank: AppPaths, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    kill_export_at(bank, dest, "renamed")
    child = subprocess.run(
        [
            sys.executable, "-c",
            textwrap.dedent(
                """
                import os, signal, sys
                from pathlib import Path
                from vascqbank_desktop.config.paths import AppPaths
                from vascqbank_desktop.db import export_repository
                from vascqbank_desktop.services.export_publication import reconcile
                def checkpoint(name):
                    if name == "reconcile-inserted":
                        os.kill(os.getpid(), signal.SIGKILL)
                export_repository._checkpoint = checkpoint
                reconcile(AppPaths(data_dir=Path(sys.argv[1])))
                """
            ),
            str(bank.data_dir),
        ],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert child.returncode == -signal.SIGKILL
    assert runs(bank) == [] and journals(bank)  # the insert was rolled back, the intent kept
    assert len(reconcile(bank).recorded) == 1 and len(runs(bank)) == 2


def test_a_published_folder_that_was_changed_is_kept_but_never_recorded(
    bank: AppPaths, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    kill_export_at(bank, dest, "renamed")
    folder = dest / folders(dest)[0][0]
    (folder / "TXT" / "ALI_questions.txt").write_text("changed by someone\n", encoding="utf-8")
    report = reconcile(bank)
    assert len(report.unverifiable) == 1 and runs(bank) == []
    assert (
        folder.is_dir() and (folder / "TXT" / "ALI_questions.txt").exists()
    )  # a user's folder is never deleted
    assert journals(bank) == [] and list((journal_dir(bank) / "unverified").iterdir())


def test_the_next_export_reconciles_first_and_then_works(bank: AppPaths, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    kill_export_at(bank, dest, "renamed")
    result = ExportService(bank).run(
        ExportRequest(subjects=("ALI",), formats=frozenset({ExportFormat.TXT}), destination=dest)
    )
    recorded = runs(bank)
    assert len(recorded) == 3 and recorded[-1]["scope"]["export_id"] == result.export_id
    assert len(folders(dest)[0]) == 2 and journals(bank) == []


def test_the_main_window_reconciles_at_start_up(bank: AppPaths, tmp_path: Path, qtbot, styled_qapp) -> None:
    from vascqbank_desktop.domain.subjects import SUBJECTS
    from vascqbank_desktop.services.storage import initialize_storage
    from vascqbank_desktop.ui.main_window import MainWindow

    dest = tmp_path / "out"
    kill_export_at(bank, dest, "renamed")
    window = MainWindow(initialize_storage(bank), SUBJECTS)
    qtbot.addWidget(window)
    assert len(runs(bank)) == 2 and journals(bank) == []


def test_an_ordinary_failure_after_the_rename_unpublishes_and_forgets_the_intent(
    bank: AppPaths, tmp_path: Path, monkeypatch
) -> None:
    calls = []

    def fail_commit(stage: str) -> None:
        calls.append(stage)
        if stage == "renamed":
            raise OSError(5, "simulated I/O error before the commit")

    monkeypatch.setattr(export_repository, "_checkpoint", fail_commit)
    with pytest.raises(Exception):  # noqa: B017 - the service maps it to an export error
        ExportService(bank).run(
            ExportRequest(
                subjects=("ALI",), formats=frozenset({ExportFormat.TXT}), destination=tmp_path / "out"
            )
        )
    assert folders(tmp_path / "out") == ([], []) and runs(bank) == [] and journals(bank) == []


def test_journal_entries_hold_no_question_text(bank: AppPaths, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    kill_export_at(bank, dest, "inserted")
    (entry,) = journal_dir(bank).glob("export-*.json")
    data = json.loads(entry.read_text(encoding="utf-8"))
    assert set(data) == {"journal_format", "export_id", "staging", "final", "manifest_sha256", "rows"}
    assert "manifest" not in data["rows"][0] and "სინთეზური" not in entry.read_text(encoding="utf-8")
    assert data["journal_format"] == export_publication.JOURNAL_FORMAT
