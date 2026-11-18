"""Create a synthetic working folder WITH AN OLDER RELEASE of the program (Phase 9b upgrade tests).

Run as ``python upgrade_driver.py <data_dir> <sources_dir> <dump.json>`` with ``PYTHONPATH`` pointing
at the ``src`` folder of an earlier release (extracted from git by ``test_upgrade_history.py``). It
uses only that release's own public services, and only the ones it has: storage (Phase 1), import
(2), reviewer name, session and manual backups (3), drafts and saved revisions (4), approve /
reject / reopen / restore-revision (5), export (6). It then dumps every row of every table.

Synthetic Georgian filler text only; everything happens in temporary folders.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

REVIEWER = "ძველი ვერსიის რეცენზენტი"


def has(module: str) -> bool:
    try:
        importlib.import_module(module)
    except ImportError:
        return False
    return True


def question(conn: sqlite3.Connection, uid: str) -> tuple[int, int, int]:
    row = conn.execute(
        "SELECT id, current_revision_id, lock_version FROM questions WHERE question_uid = ?", (uid,)
    ).fetchone()
    return int(row[0]), int(row[1]), int(row[2])


def content_of(conn: sqlite3.Connection, revision_id: int):  # noqa: ANN201 - type of the old release
    from vascqbank_desktop.domain.answers import AnswerLetter
    from vascqbank_desktop.domain.question import QuestionContent

    r = conn.execute(
        "SELECT stem, option_a, option_b, option_c, option_d, option_e, correct_letter, explanation "
        "FROM question_revisions WHERE id = ?",
        (revision_id,),
    ).fetchone()
    return QuestionContent(r[0], tuple(r[1:6]), AnswerLetter(r[6]), r[7])


def dump(database: Path) -> dict[str, list[list]]:
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


def main() -> None:
    data_dir, sources, out = (Path(a) for a in sys.argv[1:4])
    from vascqbank_desktop import __version__
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.services.storage import initialize_storage

    paths = AppPaths(data_dir=data_dir)
    initialize_storage(paths)
    done: list[str] = ["storage"]

    if has("vascqbank_desktop.services.import_service"):
        from vascqbank_desktop.services import import_service

        report = import_service.dry_run(paths, [sources])
        import_service.commit_import(paths, report)
        done.append("import")

    profile = None
    if has("vascqbank_desktop.services.settings_service"):
        from vascqbank_desktop.services.settings_service import ReviewerProfile, SavedSession, SettingsStore

        store = SettingsStore(paths.settings_file)
        profile = ReviewerProfile(store)
        profile.set_name(REVIEWER)
        store.set_session(SavedSession(subject_code="ALI", status=None, question_uid="ALI-0003"))
        done.append("settings")

    if has("vascqbank_desktop.services.backup_service"):
        from vascqbank_desktop.services.backup_service import create_manual_backup

        create_manual_backup(paths)
        done.append("backup-before-work")

    from vascqbank_desktop.db.connection import open_database

    if has("vascqbank_desktop.services.edit_service") and profile is not None:
        from vascqbank_desktop.services.draft_service import DraftService
        from vascqbank_desktop.services.edit_service import EditService

        conn = open_database(paths.database_file)
        try:
            qid, rev, lock = question(conn, "ALI-0002")
            base = content_of(conn, rev)
        finally:
            conn.close()
        edited = type(base)(
            base.stem + " — შესწორებული ძველ ვერსიაში", base.options, base.correct, base.explanation
        )
        EditService(paths, profile).save(qid, lock, edited, "ძველი რედაქცია")
        conn = open_database(paths.database_file)
        try:
            qid3, rev3, lock3 = question(conn, "ALI-0003")
            base3 = content_of(conn, rev3)
        finally:
            conn.close()
        draft = type(base3)(
            base3.stem + " — შეუნახავი მონახაზი", base3.options, base3.correct, base3.explanation
        )
        DraftService(paths).save_draft(qid3, rev3, lock3, draft, REVIEWER)
        done += ["revision", "draft"]

    if has("vascqbank_desktop.services.review_service") and profile is not None:
        from vascqbank_desktop.services.review_service import ReviewService

        review = ReviewService(paths, profile)
        conn = open_database(paths.database_file)
        try:
            ids = {uid: question(conn, uid) for uid in ("ALI-0001", "ALI-0002", "ALI-0004", "ALI-0005")}
            first_rev_2 = conn.execute(
                "SELECT id FROM question_revisions WHERE question_id = ? AND revision_no = 1",
                (ids["ALI-0002"][0],),
            ).fetchone()[0]
        finally:
            conn.close()
        review.approve(ids["ALI-0001"][0], ids["ALI-0001"][2])
        review.reject(ids["ALI-0004"][0], ids["ALI-0004"][2], "სინთეზური უარყოფის მიზეზი")
        approved = review.approve(ids["ALI-0005"][0], ids["ALI-0005"][2])
        review.reopen(ids["ALI-0005"][0], approved.lock_version, "ხელახლა გახსნა")
        restored = review.restore_revision(ids["ALI-0002"][0], ids["ALI-0002"][2], first_rev_2)
        review.approve(ids["ALI-0002"][0], restored.lock_version)
        done += ["approve", "reject", "reopen", "restore-revision"]

    if has("vascqbank_desktop.services.export_service"):
        from vascqbank_desktop.importexport.docx_export import DocxEdition
        from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService

        ExportService(paths).run(
            ExportRequest(
                subjects=("ALI",),
                formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
                destination=data_dir / "old-exports",
                editions=(DocxEdition.REVIEW,),
            )
        )
        done.append("export")

    if has("vascqbank_desktop.services.backup_service"):
        create_manual_backup(paths)
        done.append("backup-after-work")

    out.write_text(
        json.dumps(
            {"version": __version__, "done": done, "tables": dump(paths.database_file)}, ensure_ascii=False
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
