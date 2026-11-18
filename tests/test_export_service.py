"""The export engine: snapshot, plan, staging, verification, manifest, publication, failures."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from bank import snapshot
from exportbank import APPROVED, NOT_APPROVED, REVIEWER, build

from vascqbank_desktop import __version__
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import export_repository
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.importexport.docx_export import DocxEdition
from vascqbank_desktop.importexport.docx_verify import DocxReport
from vascqbank_desktop.importexport.txt_export import TxtProblem
from vascqbank_desktop.services import export_service
from vascqbank_desktop.services.approved_service import ApprovedQuestionsService
from vascqbank_desktop.services.draft_service import DraftService
from vascqbank_desktop.services.edit_errors import StorageProblem
from vascqbank_desktop.services.export_service import (
    ExportBlocked,
    ExportCancelled,
    ExportEmpty,
    ExportFormat,
    ExportPhase,
    ExportRequest,
    ExportService,
    ExportStorageError,
    ExportVerificationFailed,
)

BOTH = frozenset({ExportFormat.DOCX, ExportFormat.TXT})


@pytest.fixture
def bank(app_paths: AppPaths):
    return build(app_paths)


def request(dest: Path, **changes) -> ExportRequest:
    values = {
        "subjects": ("ALI", "VENOUS"),
        "formats": BOTH,
        "destination": dest,
        "editions": (DocxEdition.REVIEW,),
        "per_subject_docx": True,
        "combined_docx": False,
    }
    values.update(changes)
    return ExportRequest(**values)


def runs(paths: AppPaths) -> list[dict]:
    conn = open_database(paths.database_file)
    try:
        return export_repository.list_runs(conn)
    finally:
        conn.close()


def leftovers(dest: Path) -> list[str]:
    return sorted(p.name for p in dest.iterdir()) if dest.exists() else []


# ------------------------------------------------------------------ plan


def test_the_plan_counts_approved_and_excluded_and_lists_the_files(bank, tmp_path: Path) -> None:
    plan = ExportService(bank.paths).plan(request(tmp_path / "out", combined_docx=True))
    assert plan.approved == 6 and [q.uid for q in plan.snapshot.questions] == list(APPROVED)
    assert {s.code: (s.total, s.approved, s.excluded) for s in plan.subjects} == {
        "ALI": (8, 4, 4),
        "VENOUS": (3, 2, 1),
    }
    assert plan.excluded == len(NOT_APPROVED) and not plan.blocked and not plan.is_empty
    assert plan.files == (
        "DOCX/ALI_review.docx",
        "DOCX/VENOUS_review.docx",
        "DOCX/VascQBank_All_Approved_review.docx",
        "TXT/ALI_questions.txt",
        "TXT/ALI_explanations.txt",
        "TXT/VENOUS_questions.txt",
        "TXT/VENOUS_explanations.txt",
        "export-manifest.json",
    )


def test_one_subject_and_a_subject_without_approvals(bank, tmp_path: Path) -> None:
    service = ExportService(bank.paths)
    assert service.plan(request(tmp_path, subjects=("VENOUS",))).approved == 2
    empty = service.plan(request(tmp_path, subjects=("PAD_CLTI",)))
    assert empty.is_empty and empty.files == ()
    with pytest.raises(ExportEmpty):
        service.run(request(tmp_path / "out", subjects=("PAD_CLTI",)))
    assert leftovers(tmp_path / "out") == [] and runs(bank.paths) == []  # no empty document


# ------------------------------------------------------------------ a complete export


def test_a_complete_export_is_verified_published_and_recorded(bank, tmp_path: Path) -> None:
    dest = tmp_path / "exports"
    phases: list[ExportPhase] = []
    before = snapshot(bank.paths)
    drafts_before = DraftService(bank.paths).count()
    result = ExportService(bank.paths).run(
        request(dest, combined_docx=True, editions=tuple(DocxEdition)),
        progress=lambda phase, done, total, detail: phases.append(phase),
    )
    assert leftovers(dest) == [result.folder.name] and result.folder.name.startswith("VascQBank_Export_")
    assert set(phases) >= {ExportPhase.SNAPSHOT, ExportPhase.TXT, ExportPhase.DOCX, ExportPhase.VERIFY,
                           ExportPhase.MANIFEST, ExportPhase.PUBLISH, ExportPhase.DONE}  # fmt: skip
    assert phases[-1] is ExportPhase.DONE and result.approved == 6
    on_disk = sorted(
        str(p.relative_to(result.folder)).replace("\\", "/") for p in result.folder.rglob("*") if p.is_file()
    )
    assert on_disk == sorted(f.path for f in result.files)
    assert len([f for f in result.files if f.kind == "DOCX"]) == 9  # 3 editions × (2 subjects + combined)
    # only the export record changed in the database
    assert snapshot(bank.paths) == before and DraftService(bank.paths).count() == drafts_before
    recorded = runs(bank.paths)
    assert [r["format"] for r in recorded] == ["DOCX", "TXT"] and recorded[0]["question_count"] == 6
    assert recorded[0]["scope"]["export_id"] == result.export_id
    assert {f["path"] for f in recorded[1]["files"]} == {
        "TXT/ALI_questions.txt", "TXT/ALI_explanations.txt", "TXT/VENOUS_questions.txt",
        "TXT/VENOUS_explanations.txt", "export-manifest.json",
    }  # fmt: skip


def test_the_manifest_is_complete_and_its_hashes_are_true(bank, tmp_path: Path) -> None:
    result = ExportService(bank.paths).run(request(tmp_path))
    manifest = json.loads((result.folder / "export-manifest.json").read_text(encoding="utf-8"))
    contract = ApprovedQuestionsService(bank.paths).snapshot()
    assert manifest["export_id"] == result.export_id and manifest["app_version"] == __version__
    conn = sqlite3.connect(bank.paths.database_file)
    database_id = conn.execute("SELECT value FROM app_metadata WHERE key = 'database_id'").fetchone()[0]
    conn.close()
    assert manifest["database_id"] == database_id
    assert manifest["snapshot"]["fingerprint"] == contract.fingerprint == result.fingerprint
    assert [q["uid"] for q in manifest["questions"]] == list(APPROVED)
    expected = {q.uid: (q.revision_id, q.content_hash) for q in contract.questions}
    assert {
        q["uid"]: (q["approved_revision_id"], q["content_hash"]) for q in manifest["questions"]
    } == expected
    assert manifest["counts"] == {
        "approved_exported": 6,
        "excluded_not_approved": 5,
        "by_subject": {"ALI": {"total": 8, "approved": 4}, "VENOUS": {"total": 3, "approved": 2}},
    }
    assert manifest["selection"]["subjects"] == ["ALI", "VENOUS"]
    for entry in manifest["files"]:
        data = (result.folder / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"] and len(data) == entry["size"]
    manifest_entry = next(f for f in result.files if f.kind == "MANIFEST")
    assert (
        manifest_entry.sha256
        == hashlib.sha256((result.folder / "export-manifest.json").read_bytes()).hexdigest()
    )
    assert REVIEWER not in json.dumps(manifest["selection"])
    modes = {(result.folder / f.path).stat().st_mode & 0o777 for f in result.files}
    assert len(modes) == 1  # TXT, manifest and Word files share the folder's ordinary permissions


def test_txt_files_follow_the_source_convention(bank, tmp_path: Path) -> None:
    result = ExportService(bank.paths).run(request(tmp_path, formats=frozenset({ExportFormat.TXT})))
    raw = (result.folder / "TXT" / "ALI_questions.txt").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    text = raw.decode("utf-8")
    assert [line.split(".")[0] for line in text.split("\n") if line[:1].isdigit()] == ["1", "3", "8", "21"]
    assert "3. სინთეზური შემთხვევა, ორ ხაზად:\nEVAR-ის შემდეგ" in text
    assert "ა) ენდოვასკულური მკურნალობა (EVAR)\n*ბ) ღია რეკონსტრუქცია\nდისტალური ანასტომოზით\nგ)" in text
    explanations = (result.folder / "TXT" / "ALI_explanations.txt").read_text(encoding="utf-8")
    assert "3. ბ) სინთეზური განმარტება.\nმეორე ხაზი: ხსნადობა < 5 % და p < 0.05." in explanations
    assert "#" not in text and "**" not in text  # no Markdown
    assert not list((result.folder).glob("DOCX"))  # only what was selected


# ------------------------------------------------------------------ consistency and blocking


def test_an_edit_committed_during_the_export_cannot_reach_it(bank, tmp_path: Path, monkeypatch) -> None:
    approved_before = bank.detail("ALI-0008").content
    real = export_service.build_docx
    calls: list[int] = []

    def build_while_someone_edits(*args, **kwargs):
        if not calls:  # another window saves a new revision of an exported question meanwhile
            bank.edit("ALI-0008", stem="მოგვიანებით შეცვლილი, დაუმტკიცებელი ტექსტი")
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(export_service, "build_docx", build_while_someone_edits)
    result = ExportService(bank.paths).run(request(tmp_path))
    text = (result.folder / "TXT" / "ALI_questions.txt").read_text(encoding="utf-8")
    assert approved_before.stem in text and "მოგვიანებით შეცვლილი" not in text
    manifest = json.loads((result.folder / "export-manifest.json").read_text(encoding="utf-8"))
    assert "ALI-0008" in [q["uid"] for q in manifest["questions"]]
    now = ApprovedQuestionsService(bank.paths).snapshot()
    assert "ALI-0008" not in now.uids and now.fingerprint != manifest["snapshot"]["fingerprint"]


def test_text_the_txt_format_cannot_carry_blocks_txt_but_not_word(bank, tmp_path: Path) -> None:
    bank.edit("VENOUS-0007", stem="პირველი აბზაცი\n\nმეორე აბზაცი")
    bank.approve("VENOUS-0007")
    service = ExportService(bank.paths)
    plan = service.plan(request(tmp_path))
    assert plan.blocked and [(i.uid, i.problem) for i in plan.txt_issues] == [
        ("VENOUS-0007", TxtProblem.BLANK_LINE)
    ]
    with pytest.raises(ExportBlocked) as info:
        service.run(request(tmp_path / "out"))
    assert info.value.txt_issues and leftovers(tmp_path / "out") == [] and runs(bank.paths) == []
    result = service.run(request(tmp_path / "out", formats=frozenset({ExportFormat.DOCX})))
    assert result.approved == 6  # Word carries the paragraph break exactly (verified)


def test_a_contract_violation_blocks_the_export(bank, tmp_path: Path) -> None:
    from bank import set_status

    from vascqbank_desktop.domain.review_status import ReviewStatus

    set_status(bank.paths, "ALI-0002", ReviewStatus.APPROVED)  # approved without an APPROVE event
    with pytest.raises(ExportBlocked) as info:
        ExportService(bank.paths).run(request(tmp_path / "out"))
    assert [v.uid for v in info.value.violations] == ["ALI-0002"]
    assert leftovers(tmp_path / "out") == []


# ------------------------------------------------------------------ failures leave nothing behind


def test_a_failed_verification_publishes_nothing(bank, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        export_service, "verify_docx", lambda *a, **k: DocxReport(0, 0, ("simulated damage",))
    )
    with pytest.raises(ExportVerificationFailed) as info:
        ExportService(bank.paths).run(request(tmp_path / "out"))
    assert "simulated damage" in info.value.problems[0]
    assert leftovers(tmp_path / "out") == [] and runs(bank.paths) == []


def test_a_txt_that_does_not_read_back_identically_fails(bank, tmp_path: Path, monkeypatch) -> None:
    real = export_service.render_explanations
    monkeypatch.setattr(
        export_service, "render_explanations", lambda qs: real(qs).replace("განმარტება", "განმარტ", 1)
    )
    with pytest.raises(ExportVerificationFailed) as info:
        ExportService(bank.paths).run(request(tmp_path / "out", formats=frozenset({ExportFormat.TXT})))
    assert any("does not read back identically" in p for p in info.value.problems)
    assert leftovers(tmp_path / "out") == []


def test_disk_full_while_writing_keeps_earlier_exports_intact(bank, tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "out"
    first = ExportService(bank.paths).run(request(dest))
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.folder.rglob("*") if p.is_file()}
    real = export_service.write_utf8_text_atomic
    count = [0]

    def full(path, text, **kwargs):
        count[0] += 1
        if count[0] == 3:
            raise OSError(28, "No space left on device")
        return real(path, text, **kwargs)

    monkeypatch.setattr(export_service, "write_utf8_text_atomic", full)
    with pytest.raises(ExportStorageError) as info:
        ExportService(bank.paths).run(request(dest))
    assert info.value.failure.problem is StorageProblem.DISK_FULL
    assert leftovers(dest) == [first.folder.name]  # no new folder, no staging left
    assert {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.folder.rglob("*") if p.is_file()
    } == hashes
    assert len(runs(bank.paths)) == 2  # only the first export's two records


def test_an_unwritable_destination_is_reported(bank, tmp_path: Path, monkeypatch) -> None:
    real = Path.mkdir
    locked = tmp_path / "locked"

    def denied(self, *args, **kwargs):
        if self == locked or locked in self.parents:  # only the chosen destination is read-only
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(ExportStorageError) as info:
        ExportService(bank.paths).run(request(tmp_path / "locked"))
    assert info.value.failure.problem is StorageProblem.READ_ONLY
    monkeypatch.undo()
    assert runs(bank.paths) == []


def test_cancelling_publishes_nothing(bank, tmp_path: Path) -> None:
    cancel = threading.Event()

    def progress(phase, done, total, detail):
        if phase is ExportPhase.DOCX:
            cancel.set()

    with pytest.raises(ExportCancelled):
        ExportService(bank.paths).run(request(tmp_path / "out"), progress=progress, cancel=cancel)
    assert leftovers(tmp_path / "out") == [] and runs(bank.paths) == []


def test_a_failed_rename_rolls_the_record_back(bank, tmp_path: Path, monkeypatch) -> None:
    def failing(src, dst):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(export_repository.os, "rename", failing)
    with pytest.raises(ExportStorageError):
        ExportService(bank.paths).run(request(tmp_path / "out"))
    monkeypatch.undo()
    assert runs(bank.paths) == [] and leftovers(tmp_path / "out") == []


def test_a_failed_commit_unpublishes_the_folder(tmp_path: Path, app_paths: AppPaths) -> None:
    build(app_paths)
    staging, final = tmp_path / ".vqb-export-x.partial", tmp_path / "VascQBank_Export_x"
    staging.mkdir()
    (staging / "f.txt").write_text("x", encoding="utf-8")

    class CommitFails:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            if sql == "COMMIT":
                raise sqlite3.OperationalError("disk I/O error")
            return self.conn.execute(sql, *args)

        @property
        def in_transaction(self):
            return self.conn.in_transaction

    conn = open_database(app_paths.database_file)
    row = export_repository.ExportRunRow("TXT", {}, 1, [], {}, "0", "t")
    with pytest.raises(sqlite3.OperationalError):
        export_repository.record_and_publish(CommitFails(conn), [row], staging, final)  # type: ignore[arg-type]
    conn.close()
    assert staging.is_dir() and not final.exists() and runs(app_paths) == []


def test_an_existing_folder_is_never_overwritten(bank, tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "out"
    dest.mkdir()

    class FixedUuid:
        hex = "abcdef0123456789"

    monkeypatch.setattr(export_service.uuid, "uuid4", lambda: FixedUuid())
    first = ExportService(bank.paths).run(request(dest))
    marker = first.folder / "export-manifest.json"
    before = marker.read_bytes()
    monkeypatch.setattr(
        export_service, "datetime", type("Frozen", (), {"now": staticmethod(lambda tz=None: _frozen(first))})
    )
    with pytest.raises((ExportStorageError, FileExistsError)):
        ExportService(bank.paths).run(request(dest))
    assert marker.read_bytes() == before and len(runs(bank.paths)) == 2


def _frozen(first):
    from datetime import UTC, datetime

    stamp = first.export_id.rsplit("-", 1)[0]
    return datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def test_only_our_own_abandoned_staging_folders_are_cleaned(bank, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    ours = dest / ".vqb-export-old.partial"
    ours.mkdir(parents=True)
    (ours / ".vqb-staging").write_text("old", encoding="utf-8")
    lookalike = dest / ".vqb-export-mine.partial"  # no marker: not created by an export
    lookalike.mkdir()
    (lookalike / "keep.txt").write_text("user file", encoding="utf-8")
    (dest / "notes.docx").write_bytes(b"user document")
    ExportService(bank.paths).run(request(dest))
    assert not ours.exists() and (lookalike / "keep.txt").exists() and (dest / "notes.docx").exists()


def test_nothing_selected_is_refused(bank, tmp_path: Path) -> None:
    service = ExportService(bank.paths)
    with pytest.raises(ExportEmpty):
        service.run(request(tmp_path, formats=frozenset()))
    with pytest.raises(ExportEmpty):
        service.run(request(tmp_path, formats=frozenset({ExportFormat.DOCX}), editions=()))
    with pytest.raises(ExportEmpty):
        service.run(
            request(
                tmp_path, formats=frozenset({ExportFormat.DOCX}), per_subject_docx=False, combined_docx=False
            )
        )


def test_draft_text_is_never_exported(bank, tmp_path: Path) -> None:
    d = bank.detail("ALI-0001")
    DraftService(bank.paths).save_draft(
        d.id,
        d.revision_id,
        d.lock_version,
        QuestionContent("მონახაზის ტექსტი", d.content.options, d.content.correct, "e"),
        REVIEWER,
    )
    result = ExportService(bank.paths).run(request(tmp_path, editions=tuple(DocxEdition)))
    for path in result.folder.rglob("*"):
        if path.suffix == ".txt" or path.suffix == ".json":
            assert "მონახაზის ტექსტი" not in path.read_text(encoding="utf-8")
        if path.suffix == ".docx":
            import zipfile

            with zipfile.ZipFile(path) as archive:
                assert "მონახაზის ტექსტი" not in archive.read("word/document.xml").decode()
