"""Phase 9b, campaign B — every write point of every data operation fails once.

Each operation is first run once to *record* its write points in order (file opens for writing,
writes, ``fsync``, ``replace``/``rename``/``link``/``unlink``, ``mkdir``, every writing SQLite statement
and the backup API; see ``tests/faults.py``). Then, on a fresh copy of the same data folder, it is run
again once per point with exactly that point failing — ``ENOSPC``, ``EACCES``, ``EIO`` or ``EROFS``
for file calls, ``SQLITE_FULL``, ``SQLITE_IOERR``, ``SQLITE_BUSY`` (beyond the busy timeout) or
``SQLITE_READONLY`` for SQLite. In the CI-sized run the error kinds rotate over the points; with
``VASCQBANK_FAULTS_FULL=1`` every point is failed with every kind.

After every fault, asserted:

* the working database passes ``integrity_check`` and ``foreign_key_check``;
* the state is **exactly the previous one** (every row, every file name, the settings) **or the
  operation's complete effect** — never anything in between;
* a raised error has a Georgian message (the same mapping the window uses); an operation that raised
  nothing must have completed;
* no temporary file is left behind — or, where the injected fault was the removal of that very file,
  the next start-up removes it;
* with the fault removed, the operation succeeds.

Synthetic data in temporary folders only.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bank import approve, seed_counts
from faults import FILE_FAULTS, SQL_FAULTS, Point, error_for, fail_once, record
from seedbank import small_seed
from synthetic import write_pair

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.importexport.docx_export import DocxEdition
from vascqbank_desktop.services import export_publication, import_service
from vascqbank_desktop.services.auto_backup import AutoBackupOutcome, run_automatic_backup
from vascqbank_desktop.services.backup_portability import import_external_backup, save_backup_copy
from vascqbank_desktop.services.backup_service import create_manual_backup
from vascqbank_desktop.services.draft_service import DraftService
from vascqbank_desktop.services.edit_errors import StorageFailure
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService
from vascqbank_desktop.services.restore_service import RestoreRefused, inspect_backup, restore_backup
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.seed_service import SeedInvalid, install_seed, verify_seed
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore
from vascqbank_desktop.services.startup import Outcome, prepare_storage

pytest.importorskip("PySide6")
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.backup_view import copy_problem_text  # noqa: E402
from vascqbank_desktop.ui.export_view import describe_error as export_error_text  # noqa: E402
from vascqbank_desktop.ui.question_editor_view import storage_text  # noqa: E402

FULL = os.environ.get("VASCQBANK_FAULTS_FULL") == "1"
REVIEWER = "ტესტის რეცენზენტი"
GEORGIAN = re.compile("[ა-ჿ]")
TEMP_NAMES = re.compile(
    r"(\.partial$|\.tmp$|^\.restore-|^\.seed-install-|^\.vqb-export-|^\.vascqbank-|\.json\.tmp$)"
)


# ----------------------------------------------------------------------------------- helpers
def _db_rows(paths: AppPaths) -> dict[str, list[tuple]] | None:
    if not paths.database_file.exists():
        return None
    conn = sqlite3.connect(paths.database_file)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1"
            )
        ]
        return {t: [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY 1')] for t in tables}
    finally:
        conn.close()


def _files(root: Path) -> list[str]:
    ignored = ("logs", "search")
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file()
        and not p.name.endswith(("-wal", "-shm"))
        and not any(part in ignored for part in p.relative_to(root).parts[:1])
        and "search-index" not in p.name
    )


def _state(ctx: Ctx) -> tuple:
    settings = (
        ctx.paths.settings_file.read_text(encoding="utf-8") if ctx.paths.settings_file.exists() else None
    )
    extra = _files(ctx.outside) if ctx.outside.exists() else []
    return _db_rows(ctx.paths), _files(ctx.paths.data_dir), settings, extra


def _same_or_only_safety_backups(ctx: Ctx, before: tuple, after: tuple) -> bool:
    """Equal states — or the only difference is complete, *verified* safety backups (a failed import
    keeps its pre-import backup, a failed restore its pre-restore backup: intended, never a half state)."""
    if after == before:
        return True
    rows_b, files_b, settings_b, extra_b = before
    rows_a, files_a, settings_a, extra_a = after
    if (rows_a, settings_a, extra_a) != (rows_b, settings_b, extra_b):
        return False
    added = set(files_a) - set(files_b)
    if set(files_b) - set(files_a):
        return False
    for name in added:
        path = ctx.paths.data_dir / name
        if name.endswith(".json") and (ctx.paths.data_dir / name[: -len(".json")]).exists():
            continue
        if not (name.startswith("backups/") and backup_db.check_backup_file(path).ok):
            return False
    return True


def _leftovers(ctx: Ctx) -> list[Path]:
    found = []
    for root in (ctx.paths.data_dir, ctx.outside):
        if root.exists():
            found += [p for p in root.rglob("*") if TEMP_NAMES.search(p.name) and "logs" not in p.parts]
    return found


def _database_sound(paths: AppPaths) -> None:
    if not paths.database_file.exists():
        return
    conn = sqlite3.connect(paths.database_file)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def _next_start(ctx: Ctx) -> None:
    """What the program does at its next start: restore/seed/export recovery and stale cleanup."""
    if ctx.paths.database_file.exists() or (ctx.paths.data_dir / "journal").exists():
        result = prepare_storage(ctx.paths, None)
        assert result.outcome in (Outcome.READY, Outcome.RECOVERY_NEEDED), result
        export_publication.reconcile(ctx.paths)
    later = (datetime.now(UTC) + timedelta(hours=2)).timestamp()
    backup_db.remove_stale_leftovers(ctx.paths.backups_dir, now=later)


def _question(paths: AppPaths, uid: str) -> tuple[int, int, int, QuestionContent]:
    conn = sqlite3.connect(paths.database_file)
    try:
        qid, rev, lock = conn.execute(
            "SELECT id, current_revision_id, lock_version FROM questions WHERE question_uid = ?", (uid,)
        ).fetchone()
        r = conn.execute(
            "SELECT stem, option_a, option_b, option_c, option_d, option_e, correct_letter, explanation "
            "FROM question_revisions WHERE id = ?",
            (rev,),
        ).fetchone()
    finally:
        conn.close()
    return qid, rev, lock, QuestionContent(r[0], tuple(r[1:6]), AnswerLetter(r[6]), r[7])


def _one(paths: AppPaths, sql: str, *args: object) -> object:
    conn = sqlite3.connect(paths.database_file)
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _profile(paths: AppPaths) -> ReviewerProfile:
    return ReviewerProfile(SettingsStore(paths.settings_file))


def _review_message(exc: BaseException) -> str:
    if isinstance(exc, StorageFailure):
        return storage_text(exc)
    return ""


def _restore_message(exc: BaseException) -> str:
    if isinstance(exc, RestoreRefused):
        return strings.RS_FAILED.format(reason=strings.RS_PROBLEMS[exc.problem.value])
    return ""


# ----------------------------------------------------------------------------------- scenarios
@dataclass
class Ctx:
    paths: AppPaths
    outside: Path  # a folder outside the data folder (USB stick, external backup, export target)


@dataclass(frozen=True)
class Scenario:
    name: str
    run: Callable[[Ctx], object]
    completed: Callable[[Ctx], bool]
    message: Callable[[BaseException], str]
    prepare: Callable[[Ctx], None] = lambda ctx: None
    needs_database: bool = True


NEW_STEM = "შესწორებული ფორმულირება — ხელოვნური ტექსტი"


def _draft_run(ctx: Ctx) -> None:
    qid, rev, lock, content = _question(ctx.paths, "ALI-0002")
    changed = QuestionContent(NEW_STEM, content.options, content.correct, content.explanation)
    DraftService(ctx.paths).save_draft(qid, rev, lock, changed, REVIEWER)


def _revision_run(ctx: Ctx) -> None:
    qid, _rev, lock, content = _question(ctx.paths, "ALI-0002")
    changed = QuestionContent(NEW_STEM, content.options, content.correct, content.explanation)
    EditService(ctx.paths, _profile(ctx.paths)).save(qid, lock, changed)


def _review(action: str, uid: str) -> Callable[[Ctx], object]:
    def run(ctx: Ctx) -> object:
        qid, _rev, lock, _content = _question(ctx.paths, uid)
        service = ReviewService(ctx.paths, _profile(ctx.paths))
        if action == "approve":
            return service.approve(qid, lock)
        if action == "reject":
            return service.reject(qid, lock, "ხელოვნური უარყოფის მიზეზი")
        return service.reopen(qid, lock)

    return run


def _restore_revision_run(ctx: Ctx) -> object:
    qid, _rev, lock, _content = _question(ctx.paths, "ALI-0005")
    first = _one(
        ctx.paths, "SELECT id FROM question_revisions WHERE question_id = ? AND revision_no = 1", qid
    )
    return ReviewService(ctx.paths, _profile(ctx.paths)).restore_revision(qid, lock, int(first))


def _import_run(ctx: Ctx) -> object:
    report = import_service.dry_run(ctx.paths, [ctx.outside / "sources"])
    return import_service.commit_import(ctx.paths, report)


def _backups(paths: AppPaths, reason: str) -> list[Path]:
    return [p for p in backup_db.list_backups(paths.backups_dir) if backup_db.backup_reason(p) == reason]


def _verified_new(paths: AppPaths, reason: str, before: int) -> bool:
    found = _backups(paths, reason)
    return len(found) == before + 1 and all(backup_db.check_backup_file(p).ok for p in found)


def _restore_run(ctx: Ctx) -> object:
    backup = _backups(ctx.paths, "manual")[0]
    return restore_backup(ctx.paths, backup, expected_sha256=inspect_backup(backup).sha256)


def _restored(ctx: Ctx) -> bool:
    return (
        _one(ctx.paths, "SELECT count(*) FROM questions") == 9
        and not (ctx.paths.data_dir / "journal" / "restore.json").exists()
    )


def _export_run(ctx: Ctx) -> object:
    return ExportService(ctx.paths).run(
        ExportRequest(
            subjects=("ALI",),
            formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
            destination=ctx.outside / "exports",
            editions=(DocxEdition.REVIEW,),
        )
    )


def _exported(ctx: Ctx) -> bool:
    published = [p for p in (ctx.outside / "exports").glob("VascQBank_Export_*") if p.is_dir()]
    runs = _one(ctx.paths, "SELECT count(*) FROM export_runs")
    return len(published) == 1 and runs == 2 and (published[0] / "export-manifest.json").exists()


def _seed_run(ctx: Ctx) -> object:
    return install_seed(ctx.paths, verify_seed(ctx.outside / "seed-dev-1"))


def _settings_run(ctx: Ctx) -> object:
    return SettingsStore(ctx.paths.settings_file).set_reviewer_name("ახალი რეცენზენტი")


SCENARIOS = [
    Scenario(
        "draft-save",
        _draft_run,
        lambda c: (
            _one(
                c.paths,
                "SELECT d.stem FROM drafts d JOIN questions q ON q.id = d.question_id "
                "WHERE q.question_uid = 'ALI-0002'",
            )
            == NEW_STEM
        ),  # fmt: skip
        _review_message,
    ),
    Scenario(
        "revision-save",
        _revision_run,
        lambda c: _question(c.paths, "ALI-0002")[3].stem == NEW_STEM,
        _review_message,
    ),
    Scenario(
        "approve",
        _review("approve", "ALI-0003"),
        lambda c: _one(c.paths, "SELECT status FROM questions WHERE question_uid='ALI-0003'") == "APPROVED",
        _review_message,
    ),
    Scenario(
        "reject",
        _review("reject", "ALI-0003"),
        lambda c: _one(c.paths, "SELECT status FROM questions WHERE question_uid='ALI-0003'") == "REJECTED",
        _review_message,
    ),
    Scenario(
        "reopen",
        _review("reopen", "ALI-0001"),
        lambda c: _one(c.paths, "SELECT status FROM questions WHERE question_uid='ALI-0001'") == "IN_REVIEW",
        _review_message,
    ),
    Scenario(
        "restore-revision",
        _restore_revision_run,
        lambda c: (
            _one(
                c.paths,
                "SELECT count(*) FROM question_revisions r JOIN questions q ON q.id = r.question_id "
                "WHERE q.question_uid = 'ALI-0005'",
            )
            == 3
        ),
        _review_message,
    ),
    Scenario(
        "import-commit",
        _import_run,
        lambda c: _one(c.paths, "SELECT count(*) FROM questions") == 12,
        lambda exc: strings.IMP_ERROR_TEXT,  # the import dialog shows this for every failure
    ),
    Scenario(
        "backup-create",
        lambda c: create_manual_backup(c.paths),
        lambda c: _verified_new(c.paths, "manual", 1),
        lambda exc: strings.BK_CREATE_FAILED.format(detail=exc),
    ),
    Scenario(
        "automatic-backup",
        lambda c: _automatic(c),
        lambda c: _verified_new(c.paths, "automatic", 0),
        lambda exc: "",  # never raises: failures are shown in the status bar
        prepare=lambda c: approve(c.paths, "ALI-0006"),  # changed since the last backup
    ),
    Scenario(
        "backup-save-copy",
        lambda c: save_backup_copy(_backups(c.paths, "manual")[0], c.outside / "usb"),
        lambda c: (
            len(list((c.outside / "usb").glob("vascqbank-*.sqlite3"))) == 1
            and backup_db.check_backup_file(next((c.outside / "usb").glob("vascqbank-*.sqlite3"))).ok
        ),
        copy_problem_text,
        prepare=lambda c: (c.outside / "usb").mkdir(parents=True, exist_ok=True),
    ),
    Scenario(
        "backup-add-external",
        lambda c: import_external_backup(c.paths, next((c.outside / "external").glob("*.sqlite3"))),
        lambda c: _verified_new(c.paths, "imported", 0),
        copy_problem_text,
    ),
    Scenario("restore", _restore_run, _restored, _restore_message),
    Scenario("export", _export_run, _exported, export_error_text),
    Scenario(
        "seed-install",
        _seed_run,
        lambda c: c.paths.database_file.exists() and _one(c.paths, "SELECT count(*) FROM questions") == 26,
        lambda exc: strings.SEED_INVALID_TEXT if isinstance(exc, SeedInvalid) else strings.STARTUP_ERROR_TEXT,
        needs_database=False,
    ),
    Scenario(
        "settings-save",
        _settings_run,
        lambda c: SettingsStore(c.paths.settings_file).load().reviewer_name == "ახალი რეცენზენტი",
        lambda exc: strings.ED_STORAGE_IO if isinstance(exc, OSError) else "",
    ),
]


def _automatic(ctx: Ctx) -> object:
    result = run_automatic_backup(ctx.paths, now=datetime.now(UTC) + timedelta(days=2))
    if result.outcome is AutoBackupOutcome.FAILED:
        raise RuntimeError(result.detail)  # surfaced as the status-bar message
    return result


# ----------------------------------------------------------------------------------- templates
@pytest.fixture(scope="module")
def templates(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("fault-templates")
    with_db = root / "with-db"
    paths = AppPaths(data_dir=with_db / "მონაცემები")
    seed_counts(paths, {"ALI": 6, "VENOUS": 3})
    _profile(paths).set_name(REVIEWER)
    approve(paths, "ALI-0001")
    qid, _rev, lock, content = _question(paths, "ALI-0005")
    EditService(paths, _profile(paths)).save(
        qid,
        lock,
        QuestionContent(content.stem + " (რედაქცია)", content.options, content.correct, content.explanation),
    )
    create_manual_backup(paths)
    outside = with_db / "outside"
    write_pair(outside / "sources", "ALI", range(7, 10))
    other = AppPaths(data_dir=root / "other-install")
    seed_counts(other, {"VENOUS": 4})
    external = create_manual_backup(other).path
    (outside / "external").mkdir(parents=True)
    shutil.copy2(external, outside / "external" / external.name)
    shutil.copy2(
        external.with_name(external.name + ".json"), outside / "external" / (external.name + ".json")
    )

    empty = root / "empty"
    (empty / "მონაცემები").mkdir(parents=True)
    small_seed(empty / "outside")
    return {"with-db": with_db, "empty": empty}


def _fresh(template: Path, target: Path) -> Ctx:
    shutil.copytree(template, target)
    return Ctx(AppPaths(data_dir=target / "მონაცემები"), target / "outside")


def _faults_for(point: Point) -> list[tuple[str, str]]:
    if FULL:
        return [(f, s) for f, s in zip(FILE_FAULTS, SQL_FAULTS, strict=True)]
    files, sqls = list(FILE_FAULTS), list(SQL_FAULTS)
    return [(files[point.index % len(files)], sqls[point.index % len(sqls)])]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_every_write_point_fails_safely(scenario: Scenario, templates, tmp_path: Path) -> None:
    template = templates["with-db" if scenario.needs_database else "empty"]
    probe = _fresh(template, tmp_path / "probe")
    scenario.prepare(probe)
    points = record(lambda: scenario.run(probe))
    assert points, "the operation wrote nothing?"
    assert scenario.completed(probe), "the operation does not complete without faults"

    checked = 0
    problems: list[str] = []
    for point in points:
        for file_fault, sql_fault in _faults_for(point):
            ctx = _fresh(template, tmp_path / f"case-{point.index}-{file_fault}")
            scenario.prepare(ctx)
            before = _state(ctx)
            exc, fired = fail_once(
                lambda c=ctx: scenario.run(c),
                point.index,
                lambda p, f=file_fault, s=sql_fault: error_for(p, f, s),
            )
            label = (
                f"{scenario.name} @{point.index} {point.op} {point.detail[-50:]!r} ({file_fault}/{sql_fault})"
            )
            if fired is None:  # the operation took a different path this time (e.g. time-dependent)
                continue
            checked += 1
            try:
                _database_sound(ctx.paths)
                if exc is None:
                    assert scenario.completed(ctx), "reported success but did not complete"
                elif not isinstance(exc, (OSError, sqlite3.Error, Exception)):
                    raise AssertionError(f"unexpected {exc!r}")
                if exc is not None and scenario.name != "automatic-backup":
                    text = scenario.message(exc)
                    assert GEORGIAN.search(text or ""), f"no Georgian message for {exc!r}"
                done = scenario.completed(ctx)
                if not done:
                    after = _state(ctx)
                    if not _same_or_only_safety_backups(ctx, before, after):
                        _next_start(ctx)
                        after = _state(ctx)
                    assert _same_or_only_safety_backups(ctx, before, after), f"half state after {exc!r}"
                leftovers = _leftovers(ctx)
                if leftovers and fired.op in ("unlink", "remove", "rename", "replace"):
                    _next_start(ctx)
                    leftovers = _leftovers(ctx)
                assert not leftovers, f"left behind: {[p.name for p in leftovers]} after {exc!r}"
                if not scenario.completed(ctx):
                    scenario.run(ctx)  # the fault is gone: the same operation now succeeds
                    assert scenario.completed(ctx), "retry did not complete"
                _database_sound(ctx.paths)
            except AssertionError as failure:
                problems.append(f"{label}: {failure}")
            shutil.rmtree(tmp_path / f"case-{point.index}-{file_fault}", ignore_errors=True)
    print(f"\n[faults] {scenario.name}: {len(points)} write points, {checked} faults injected")
    assert not problems, "\n".join(problems[:20]) + f"\n({len(problems)} problem(s) in {checked} faults)"
    assert checked >= len(points) // 2
