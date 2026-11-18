"""Phase 9 end-to-end acceptance scenario: the whole data lifecycle on synthetic questions.

One isolated data folder goes through the 24 steps of the Phase 9 specification — empty
installation, reviewer profile, partial imports, autosaved draft, restart, draft recovery, commit,
approval, rejection, revision restore, DOCX/TXT export, verified backup, further edits, an
explicitly confirmed restore, displaced-database recovery, a second export, TXT re-import into a
fresh database and a final restart with session persistence.

"Restart" means what it means for the real program: every service object is dropped and storage is
prepared again from the files on disk (``prepare_storage``), exactly as ``app.main`` does.

Persistence semantics checked separately (they are deliberately different):

* an autosaved **draft** survives a restart but is never authoritative (not approvable, not exported);
* a saved **revision** is authoritative and append-only;
* an **approved revision** is the exact revision an APPROVE event is bound to;
* an **export** is a snapshot of approved revisions, verified by its manifest hashes;
* a **backup** is a verified copy of the whole database at one moment;
* a **restore** replaces the working database by that copy — everything written after the backup is
  rolled back *on purpose* (the reviewer confirmed it), and the replaced database stays recoverable
  in ``displaced/`` and in the pre-restore safety backup. The rolled-back rows are enumerated
  exactly below; this is the expected effect of an explicit restore, not data loss.

All question texts are artificial; no medical content is used.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from synthetic import correct_letter, write_pair

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.backup import check_backup_file, read_manifest
from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.catalog import CatalogFilter
from vascqbank_desktop.domain.question import QuestionContent, SourceKind
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.importexport.docx_export import DocxEdition
from vascqbank_desktop.services import backup_service, import_service, restore_service
from vascqbank_desktop.services.approved_service import ApprovedQuestionsService
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.draft_service import DraftService
from vascqbank_desktop.services.edit_errors import UnresolvedDraft
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.export_publication import MANIFEST_NAME, verify_published
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService
from vascqbank_desktop.services.restore_service import RestoreRefused
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.session_service import ContextNotice, restore_context
from vascqbank_desktop.services.settings_service import ReviewerProfile, SavedSession, SettingsStore
from vascqbank_desktop.services.startup import Outcome, prepare_storage

docx = pytest.importorskip("docx")

REVIEWER = "სინთეზური რეცენზენტი"
ALI_NUMBERS = (1, 2, 3, 4, 5, 6, 8, 13, 21)  # a partial subject with gaps
VENOUS_NUMBERS = (2, 7, 10)
EVAR_NUMBERS = (1, 2, 3)  # imported only after the backup: rolled back by the restore
MARKER = "ბოლოდროინდელიცვლილება"  # appears only in a post-backup edit


# ------------------------------------------------------------------------------------ helpers
def restart(paths: AppPaths) -> Services:
    """What a new program start does: resolve leftovers, open storage, build fresh services."""
    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.READY, result
    assert result.status is not None and result.status.integrity_ok
    return Services(paths)


class Services:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.settings = SettingsStore(paths.settings_file)
        self.profile = ReviewerProfile(self.settings)
        self.catalog = CatalogService(paths)
        self.drafts = DraftService(paths)
        self.edits = EditService(paths, self.profile)
        self.review = ReviewService(paths, self.profile)
        self.approved = ApprovedQuestionsService(paths)

    def detail(self, uid: str):  # noqa: ANN201 - QuestionDetail
        detail = self.catalog.detail_by_uid(uid)
        assert detail is not None, uid
        return detail

    def edit(self, uid: str, **changes: object) -> None:
        d = self.detail(uid)
        c = d.content
        self.edits.save(
            d.id,
            d.lock_version,
            QuestionContent(
                changes.get("stem", c.stem),  # type: ignore[arg-type]
                changes.get("options", c.options),  # type: ignore[arg-type]
                changes.get("correct", c.correct),  # type: ignore[arg-type]
                changes.get("explanation", c.explanation),  # type: ignore[arg-type]
            ),
        )

    def approve(self, uid: str) -> None:
        d = self.detail(uid)
        self.review.approve(d.id, d.lock_version)


def import_folder(paths: AppPaths, folder: Path) -> tuple[str, ...]:
    report = import_service.dry_run(paths, [folder])
    assert not [i for i in report.all_issues() if i.is_error]
    result = import_service.commit_import(paths, report)
    assert result.invalid == 0 and result.skipped_conflicts == 0
    assert result.backup.path.is_file()  # every import is preceded by a verified backup
    return result.created


def check_integrity(database: Path) -> None:
    conn = sqlite3.connect(database)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def state(database: Path) -> dict[str, frozenset[tuple]]:
    """The authoritative content of a database as comparable row sets (internal ids included)."""
    conn = sqlite3.connect(database)
    try:
        return {
            "questions": frozenset(
                conn.execute(
                    "SELECT q.question_uid, q.status, q.lock_version, cur.revision_no, cur.content_hash,"
                    " app.revision_no, q.approved_at, q.source_kind, q.source_file, q.source_line,"
                    " q.import_batch_id, q.source_fingerprint, q.original_content_json"
                    " FROM questions q JOIN question_revisions cur ON cur.id = q.current_revision_id"
                    " LEFT JOIN question_revisions app ON app.id = q.approved_revision_id"
                )
            ),
            "revisions": frozenset(
                conn.execute(
                    "SELECT r.id, q.question_uid, r.revision_no, r.origin, r.content_hash, r.author,"
                    " src.revision_no FROM question_revisions r JOIN questions q ON q.id = r.question_id"
                    " LEFT JOIN question_revisions src ON src.id = r.restored_from_revision_id"
                )
            ),
            "events": frozenset(
                conn.execute(
                    "SELECT e.id, q.question_uid, e.action, e.from_status, e.to_status, r.revision_no,"
                    " e.actor FROM review_events e JOIN questions q ON q.id = e.question_id"
                    " LEFT JOIN question_revisions r ON r.id = e.revision_id"
                )
            ),
            "drafts": frozenset(
                conn.execute(
                    "SELECT q.question_uid, r.revision_no, d.stem, d.correct_letter FROM drafts d"
                    " JOIN questions q ON q.id = d.question_id"
                    " JOIN question_revisions r ON r.id = d.base_revision_id"
                )
            ),
            "batches": frozenset(conn.execute("SELECT id, source_summary FROM import_batches")),
            "export_runs": frozenset(
                conn.execute("SELECT id, format, question_count, created_at FROM export_runs")
            ),
        }
    finally:
        conn.close()


def uids_of(rows: frozenset[tuple]) -> set[str]:
    return {row[1] if isinstance(row[0], int) else row[0] for row in rows}


def export(services: Services, destination: Path):  # noqa: ANN201 - ExportResult
    return ExportService(services.paths).run(
        ExportRequest(
            subjects=("ALI", "VENOUS"),
            formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
            destination=destination,
            editions=(DocxEdition.EXAM, DocxEdition.ANSWER_KEY),
            per_subject_docx=False,
            combined_docx=True,
        )
    )


def verify_export(services: Services, result) -> dict:  # noqa: ANN001
    """Step 15: every artifact checked against the manifest and against the database."""
    manifest_file = result.folder / MANIFEST_NAME
    manifest_sha = next(f.sha256 for f in result.files if f.kind == "MANIFEST")
    assert verify_published(result.folder, manifest_sha) == []  # every file: size + SHA-256
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    snapshot = services.approved.snapshot()
    expected = [q for q in snapshot.questions if q.subject_code in ("ALI", "VENOUS")]
    assert [
        (q["uid"], q["approved_revision_id"], q["revision_no"], q["content_hash"])
        for q in manifest["questions"]
    ] == [(q.uid, q.revision_id, q.revision_no, q.content_hash) for q in expected]
    for q in expected:  # the exported hash is the hash of the approved text itself
        assert q.content.content_hash() == q.content_hash
    assert manifest["snapshot"]["fingerprint"] == result.fingerprint == snapshot.fingerprint

    exam = docx.Document(str(result.folder / "DOCX" / "VascQBank_All_Approved_exam.docx"))
    key = docx.Document(str(result.folder / "DOCX" / "VascQBank_All_Approved_answer_key.docx"))
    exam_ids = [p.text.split(". ", 1)[1] for p in exam.paragraphs if p.style.name == "VQB Question ID"]
    assert exam_ids == [q.uid for q in expected]
    exam_text = "\n".join(p.text for p in exam.paragraphs)
    for q in expected:
        for line in q.content.stem.splitlines():
            assert line in exam_text
    for paragraph in key.paragraphs:
        if paragraph.style.name == "VQB Key Entry":
            uid, rest = paragraph.text.split(". ", 1)[1].split(" — ", 1)
            assert rest.startswith(f"{services.approved.get(uid).content.correct.value})")
    txt_files = sorted(p.name for p in (result.folder / "TXT").iterdir())
    assert txt_files == sorted(
        f"{c}_{k}.txt" for c in ("ALI", "VENOUS") for k in ("questions", "explanations")
    )
    return manifest


# -------------------------------------------------------------------------------- the scenario
def test_phase9_end_to_end_acceptance(tmp_path: Path) -> None:  # noqa: PLR0915 - one long story
    paths = AppPaths(data_dir=tmp_path / "მონაცემები")

    # 1. empty installation (no seed, no earlier traces) --------------------------------------
    s = restart(paths)
    assert s.catalog.count() == 0 and s.approved.count() == 0 and s.drafts.count() == 0
    check_integrity(paths.database_file)

    # 2. reviewer profile -------------------------------------------------------------------
    assert s.profile.set_name(f"  {REVIEWER} ") == REVIEWER

    # 3. several partial subject datasets, imported separately --------------------------------
    write_pair(tmp_path / "წყარო" / "ALI", "ALI", ALI_NUMBERS)
    write_pair(tmp_path / "წყარო" / "VENOUS", "VENOUS", VENOUS_NUMBERS)
    created = import_folder(paths, tmp_path / "წყარო" / "ALI")
    created += import_folder(paths, tmp_path / "წყარო" / "VENOUS")
    assert sorted(created) == sorted(
        [f"ALI-{n:04d}" for n in ALI_NUMBERS] + [f"VENOUS-{n:04d}" for n in VENOUS_NUMBERS]
    )
    imported = state(paths.database_file)
    original_json = {row[0]: row[12] for row in imported["questions"]}  # immutable from now on
    provenance = {row[0]: row[7:12] for row in imported["questions"]}
    assert provenance["ALI-0002"][:2] == (SourceKind.PAIRED_TXT.value, "ALI_questions.txt")
    assert provenance["VENOUS-0007"][:2] == (SourceKind.PAIRED_TXT.value, "VENOUS_questions.txt")

    # 4.–6. open a question, change its text and correct answer, autosave -----------------------
    a = s.detail("ALI-0002")
    assert a.revision_no == 1 and a.status is ReviewStatus.PENDING_REVIEW
    new_correct = next(x for x in AnswerLetter if x.value != correct_letter(2))
    edited = QuestionContent(
        a.content.stem + "\nდამატებითი სინთეზური წინადადება: ABI = 0.45, ≥ 70 %.",
        a.content.options,
        new_correct,
        a.content.explanation,
    )
    s.drafts.save_draft(a.id, a.revision_id, a.lock_version, edited, s.profile.require_name())
    # draft isolation: the authoritative revision is untouched and the draft cannot be approved
    assert s.detail("ALI-0002").content == a.content and s.detail("ALI-0002").revision_no == 1
    with pytest.raises(UnresolvedDraft):
        s.review.approve(a.id, a.lock_version)
    assert s.approved.count() == 0

    # 7.–8. close, reopen, recover the draft ---------------------------------------------------
    del s
    s = restart(paths)
    assert s.profile.name == REVIEWER  # the profile survived the restart
    [row] = s.drafts.list_drafts()
    assert (row.uid, row.base_revision_no, row.current_revision_no) == ("ALI-0002", 1, 1)
    recovered = s.drafts.load_draft(a.id)
    assert recovered is not None and recovered.content == edited and recovered.reviewer_name == REVIEWER

    # 9. commit the revision (the draft holding exactly this text disappears with the commit) ---
    saved = s.edits.save(a.id, recovered.base_lock_version, recovered.content)
    assert saved.revision_no == 2 and saved.draft_cleared and s.drafts.count() == 0

    # 10. approve it ----------------------------------------------------------------------------
    s.approve("ALI-0002")
    a2 = s.detail("ALI-0002")
    assert a2.status is ReviewStatus.APPROVED and a2.content == edited
    assert s.approved.get("ALI-0002").revision_no == 2

    # 11. reject another ------------------------------------------------------------------------
    b = s.detail("VENOUS-0007")
    s.review.reject(b.id, b.lock_version, "სინთეზური უარყოფის მიზეზი")
    assert s.detail("VENOUS-0007").status is ReviewStatus.REJECTED and s.approved.get("VENOUS-0007") is None

    # 12.–13. restore an older revision of a third question, approve the restored revision -----
    original_c = s.detail("ALI-0003").content
    s.edit("ALI-0003", stem="სინთეზური შუალედური ვერსია, რომელიც მოგვიანებით უკან დაბრუნდება?")
    c = s.detail("ALI-0003")
    assert c.revision_no == 2
    first = next(r for r in s.catalog.revisions(c.id) if r.revision_no == 1)
    restored = s.review.restore_revision(c.id, c.lock_version, first.id)
    assert restored.changed and restored.revision_no == 3
    s.approve("ALI-0003")
    c3 = s.detail("ALI-0003")
    assert c3.content == original_c and c3.status is ReviewStatus.APPROVED
    infos = {r.revision_no: r for r in s.catalog.revisions(c3.id)}
    assert [infos[n].origin for n in (1, 2, 3)] == ["IMPORT", "EDIT", "RESTORE"]
    assert infos[3].restored_from_revision_no == 1 and infos[3].was_approved
    assert s.approved.get("ALI-0003").content_hash == original_c.content_hash()

    s.approve("ALI-0008")
    s.approve("VENOUS-0010")
    approved_uids = ["ALI-0002", "ALI-0003", "ALI-0008", "VENOUS-0010"]
    assert list(s.approved.snapshot().uids) == approved_uids

    # 14.–15. export approved questions to DOCX and TXT, verify every artifact ----------------
    first_export = export(s, tmp_path / "ექსპორტი")
    manifest1 = verify_export(s, first_export)
    assert [q["uid"] for q in manifest1["questions"]] == approved_uids  # draft/pending/rejected excluded
    txt = (first_export.folder / "TXT" / "VENOUS_questions.txt").read_text(encoding="utf-8")
    assert "VENOUS №7" not in txt and "VENOUS №10" in txt

    # 16. verified backup -----------------------------------------------------------------------
    entry = backup_service.create_manual_backup(s.paths)
    assert backup_service.verify_entry(entry).status is backup_service.VerificationStatus.VERIFIED
    assert read_manifest(entry.path)["sha256"]
    at_backup = state(paths.database_file)

    # 17. further legitimate work after the backup ------------------------------------------------
    s.edit("ALI-0004", stem=f"სინთეზური ცვლილება ბექაპის შემდეგ: {MARKER}?")
    s.approve("ALI-0005")
    d8 = s.detail("ALI-0008")
    s.review.reopen(d8.id, d8.lock_version, "სინთეზური ხელახალი განხილვა")
    d6 = s.detail("ALI-0006")
    c6 = d6.content
    draft6 = QuestionContent(c6.stem + " (დრაფტი)", c6.options, c6.correct, c6.explanation)
    s.drafts.save_draft(d6.id, d6.revision_id, d6.lock_version, draft6, REVIEWER)
    write_pair(tmp_path / "წყარო" / "EVAR", "AAA_EVAR", EVAR_NUMBERS)
    import_folder(paths, tmp_path / "წყარო" / "EVAR")
    s.settings.set_session(SavedSession("AAA_EVAR", None, "AAA_EVAR-0002"))
    assert s.catalog.query(CatalogFilter(text=MARKER)).ids  # searchable before the restore
    before_restore = state(paths.database_file)
    check_integrity(paths.database_file)

    # append-only: nothing that existed at the backup was removed by ordinary work
    for table in ("revisions", "events", "batches", "export_runs"):
        assert at_backup[table] <= before_restore[table], table
    for uid, json_text in original_json.items():
        assert {r[0]: r[12] for r in before_restore["questions"]}[uid] == json_text

    # 18. restore the previous backup — only after explicit confirmation ------------------------
    summary = restore_service.inspect_backup(entry.path)  # what the confirmation dialog shows
    assert summary.questions == len(ALI_NUMBERS) + len(VENOUS_NUMBERS) and summary.drafts == 0
    assert summary.count(ReviewStatus.APPROVED) == 4 and summary.count(ReviewStatus.REJECTED) == 1
    with pytest.raises(RestoreRefused):  # a confirmation for different bytes is refused, nothing changes
        restore_service.restore_backup(paths, entry.path, expected_sha256="0" * 64)
    assert state(paths.database_file) == before_restore
    del s
    result = restore_service.restore_backup(paths, entry.path, expected_sha256=summary.sha256)

    # 19. restored history and statuses equal the backup exactly; the rollback is enumerated -----
    s = restart(paths)
    check_integrity(paths.database_file)
    after_restore = state(paths.database_file)
    assert after_restore == at_backup
    assert result.status.restored_from == entry.path.name
    rolled_back = {table: before_restore[table] - at_backup[table] for table in before_restore}
    # exactly the post-backup work, nothing else (intended effect of the confirmed restore):
    assert uids_of(rolled_back["revisions"]) == {
        "ALI-0004",
        "AAA_EVAR-0001",
        "AAA_EVAR-0002",
        "AAA_EVAR-0003",
    }
    assert uids_of(rolled_back["events"]) == {
        "ALI-0004",  # EDITED
        "ALI-0005",  # APPROVE
        "ALI-0008",  # REOPEN
        "AAA_EVAR-0001",  # IMPORTED ×3
        "AAA_EVAR-0002",
        "AAA_EVAR-0003",
    }
    assert {e[2] for e in rolled_back["events"] if e[1].startswith("ALI")} == {"EDITED", "APPROVE", "REOPEN"}
    assert uids_of(rolled_back["drafts"]) == {"ALI-0006"}
    assert len(rolled_back["batches"]) == 1 and not rolled_back["export_runs"]
    changed_rows = {row[0] for row in before_restore["questions"] - at_backup["questions"]}
    assert changed_rows == {
        "ALI-0004",
        "ALI-0005",
        "ALI-0008",
        "AAA_EVAR-0001",
        "AAA_EVAR-0002",
        "AAA_EVAR-0003",
    }
    # everything not touched after the backup is bit-for-bit what it was before the restore
    untouched = {row for row in before_restore["questions"] if row[0] not in changed_rows}
    assert untouched <= after_restore["questions"]
    assert s.detail("ALI-0002").revision_no == 2 and s.detail("ALI-0003").revision_no == 3
    assert s.detail("ALI-0008").status is ReviewStatus.APPROVED  # the reopen was rolled back
    assert not s.catalog.query(CatalogFilter(text=MARKER)).ids  # the search index was rebuilt
    assert s.profile.name == REVIEWER  # settings live outside the database: untouched
    context = restore_context(s.catalog, s.settings.load().session)
    assert context.notice is ContextNotice.QUESTION_MISSING and context.question_id is not None

    # 20. the displaced database and the safety backup still hold the rolled-back work ----------
    assert not restore_service.journal_path(paths).exists()
    assert restore_service.stray_temporary_files(paths) == []
    [displaced_db] = [p for p in result.displaced if p.suffix == ".sqlite3"]
    check_integrity(displaced_db)
    assert state(displaced_db) == before_restore  # recoverable bit-for-bit
    assert result.safety_backup is not None and check_backup_file(result.safety_backup).ok
    safety = restore_service.inspect_backup(result.safety_backup)
    assert safety.questions == len(ALI_NUMBERS) + len(VENOUS_NUMBERS) + len(EVAR_NUMBERS)
    assert safety.drafts == 1

    # 21. export again from the restored state ----------------------------------------------------
    second_export = export(s, tmp_path / "ექსპორტი")
    manifest2 = verify_export(s, second_export)
    assert second_export.folder != first_export.folder
    assert manifest2["snapshot"]["fingerprint"] == manifest1["snapshot"]["fingerprint"]
    assert (
        verify_published(  # the earlier export is untouched
            first_export.folder, next(f.sha256 for f in first_export.files if f.kind == "MANIFEST")
        )
        == []
    )

    # 22.–23. re-import the TXT output into a new database; compare every eligible field --------
    fresh = AppPaths(data_dir=tmp_path / "ახალი მონაცემები")
    restart(fresh)
    back_uids = import_folder(fresh, second_export.folder / "TXT")
    assert sorted(back_uids) == sorted(approved_uids)
    fresh_catalog = CatalogService(fresh)
    for q in manifest2["questions"]:
        mine = s.approved.get(q["uid"])
        theirs = fresh_catalog.detail_by_uid(q["uid"])
        assert theirs is not None
        assert (theirs.subject_code, theirs.source_number) == (q["subject"], q["source_number"])
        assert theirs.content == mine.content  # stem, five options, correct answer, explanation
        assert theirs.content.content_hash() == q["content_hash"]
        assert theirs.status is ReviewStatus.PENDING_REVIEW and theirs.revision_no == 1  # a new bank
        assert theirs.source_file == f"{q['subject']}_questions.txt"
    check_integrity(fresh.database_file)

    # 24. restart again and verify session persistence -------------------------------------------
    s.settings.set_session(SavedSession("ALI", ReviewStatus.APPROVED, "ALI-0003"))
    target = s.detail("ALI-0003").id
    del s
    s = restart(paths)
    saved_session = s.settings.load().session
    assert saved_session == SavedSession("ALI", ReviewStatus.APPROVED, "ALI-0003")
    context = restore_context(s.catalog, saved_session)
    assert (context.question_id, context.notice) == (target, ContextNotice.NONE)
    final = state(paths.database_file)
    new_runs = final["export_runs"] - at_backup["export_runs"]
    assert len(new_runs) == 2  # the second export (DOCX + TXT) is the only change since the restore
    assert {**final, "export_runs": at_backup["export_runs"]} == at_backup
    for uid, json_text in original_json.items():  # imported originals never changed
        assert {r[0]: r[12] for r in final["questions"]}[uid] == json_text
    check_integrity(paths.database_file)
