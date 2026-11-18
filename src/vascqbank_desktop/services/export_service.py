"""Export of approved questions to Word (.docx) and paired TXT — the project's final deliverable.

Workflow of :meth:`ExportService.run` (every step must succeed before the next starts):

1. **Snapshot.** One consistent read through
   :meth:`~vascqbank_desktop.services.approved_service.ApprovedQuestionsService.snapshot` — the
   only source of question text — after :meth:`~ApprovedQuestionsService.verify` reports no
   contract violation. Edits committed while the export runs cannot reach it.
2. **Plan checks.** Nothing approved in the selection → :class:`ExportEmpty` (no empty document);
   a text the TXT format cannot carry unchanged → :class:`ExportBlocked` naming the questions.
3. **Staging.** Files are written into ``.vqb-export-<id>.partial`` next to the destination.
4. **Verification.** Every .docx is reopened and compared with the snapshot
   (:func:`~vascqbank_desktop.importexport.docx_verify.verify_docx`); every TXT pair is parsed back
   with the real importer and compared field by field.
5. **Manifest** (``export-manifest.json``): export id, version, time, database id, selection,
   editions, question IDs, approved revision ids, content hashes, snapshot fingerprint, and every
   output file with its SHA-256.
6. **Publication.** A durable intent journal is written, then ``export_runs`` rows are inserted,
   the staging folder is renamed to ``VascQBank_Export_<id>`` and the transaction commits
   (:mod:`~vascqbank_desktop.db.export_repository`). Rename and commit cannot be one atomic step;
   an interruption between them is completed or undone by :func:`reconcile` on the next start or
   export (:mod:`~vascqbank_desktop.services.export_publication`). An existing folder is never
   overwritten; a failed export leaves no folder and no row; earlier exports are untouched.

Nothing else in the database is written. Cancellation is honoured between questions and files
until publication starts.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop import __version__
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import export_repository
from vascqbank_desktop.db.connection import make_durable, open_database
from vascqbank_desktop.db.migrations import read_metadata, utc_now_iso
from vascqbank_desktop.domain.subjects import SUBJECTS, get_subject
from vascqbank_desktop.importexport.docx_export import (
    DEFAULT_FONT,
    DocxEdition,
    DocxMeta,
    SubjectBlock,
    build_docx,
)
from vascqbank_desktop.importexport.docx_verify import verify_docx
from vascqbank_desktop.importexport.paired_txt import parse_pair
from vascqbank_desktop.importexport.text_io import write_utf8_text_atomic
from vascqbank_desktop.importexport.txt_export import (
    TxtIssue,
    explanations_filename,
    questions_filename,
    render_explanations,
    render_questions,
    txt_problems,
)
from vascqbank_desktop.services import export_publication
from vascqbank_desktop.services.approved_service import (
    ApprovedQuestion,
    ApprovedQuestionsService,
    ApprovedSnapshot,
    ContractViolation,
    fingerprint_of,
)
from vascqbank_desktop.services.edit_errors import StorageFailure, StorageProblem
from vascqbank_desktop.services.question_stats import bank_counts
from vascqbank_desktop.services.seed_service import SEED_META_KEYS
from vascqbank_desktop.validation.issues import has_errors

log = logging.getLogger(__name__)

STAGING_PREFIX = ".vqb-export-"
STAGING_SUFFIX = ".partial"
STAGING_MARKER = ".vqb-staging"  # proves a staging folder is ours before it is ever removed
FOLDER_PREFIX = "VascQBank_Export_"
MANIFEST_NAME = "export-manifest.json"
COMBINED_STEM = "VascQBank_All_Approved"


class ExportFormat(StrEnum):
    DOCX = "DOCX"
    TXT = "TXT"


class ExportPhase(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    TXT = "TXT"
    DOCX = "DOCX"
    VERIFY = "VERIFY"
    MANIFEST = "MANIFEST"
    PUBLISH = "PUBLISH"
    DONE = "DONE"


Progress = Callable[[ExportPhase, int, int, str], None]


@dataclass(frozen=True, slots=True)
class ExportRequest:
    subjects: tuple[str, ...]
    formats: frozenset[ExportFormat]
    destination: Path
    editions: tuple[DocxEdition, ...] = (DocxEdition.REVIEW,)
    per_subject_docx: bool = True
    combined_docx: bool = False
    font: str = DEFAULT_FONT


@dataclass(frozen=True, slots=True)
class SubjectSummary:
    code: str
    name_ka: str
    total: int
    approved: int

    @property
    def excluded(self) -> int:
        return self.total - self.approved


@dataclass(frozen=True, slots=True)
class ExportPlan:
    request: ExportRequest
    snapshot: ApprovedSnapshot  # restricted to the selected subjects
    subjects: tuple[SubjectSummary, ...]
    files: tuple[str, ...]  # relative paths inside the export folder
    txt_issues: tuple[TxtIssue, ...]
    violations: tuple[ContractViolation, ...]

    @property
    def approved(self) -> int:
        return len(self.snapshot)

    @property
    def excluded(self) -> int:
        return sum(s.excluded for s in self.subjects)

    @property
    def is_empty(self) -> bool:
        return self.approved == 0

    @property
    def blocked(self) -> bool:
        return bool(self.violations) or (ExportFormat.TXT in self.request.formats and bool(self.txt_issues))


@dataclass(frozen=True, slots=True)
class ExportedFile:
    path: str  # relative, with forward slashes
    kind: str  # DOCX / TXT / MANIFEST
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExportResult:
    export_id: str
    folder: Path
    files: tuple[ExportedFile, ...]
    approved: int
    fingerprint: str
    run_ids: tuple[int, ...]
    timings: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------- errors (explained in Georgian by the UI)
class ExportError(Exception):
    """The export did not complete; nothing was published or recorded."""


class ExportEmpty(ExportError):
    pass


class ExportBlocked(ExportError):
    def __init__(self, txt_issues: tuple[TxtIssue, ...], violations: tuple[ContractViolation, ...]) -> None:
        self.txt_issues = txt_issues
        self.violations = violations
        super().__init__(f"{len(txt_issues)} TXT problem(s), {len(violations)} contract violation(s)")


class ExportCancelled(ExportError):
    pass


class ExportVerificationFailed(ExportError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems[:5]))


class ExportStorageError(ExportError):
    def __init__(self, failure: StorageFailure) -> None:
        self.failure = failure
        super().__init__(str(failure))


# ---------------------------------------------------------------- helpers
def edition_filename(stem: str, edition: DocxEdition) -> str:
    return f"{stem}_{edition.value}.docx"


def planned_files(request: ExportRequest, codes_with_questions: list[str]) -> tuple[str, ...]:
    files: list[str] = []
    if ExportFormat.DOCX in request.formats:
        for edition in request.editions:
            if request.per_subject_docx:
                files += [f"DOCX/{edition_filename(code, edition)}" for code in codes_with_questions]
            if request.combined_docx and codes_with_questions:
                files.append(f"DOCX/{edition_filename(COMBINED_STEM, edition)}")
    if ExportFormat.TXT in request.formats:
        for code in codes_with_questions:
            files += [f"TXT/{questions_filename(code)}", f"TXT/{explanations_filename(code)}"]
    if files:
        files.append(MANIFEST_NAME)
    return tuple(files)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _storage(exc: OSError) -> StorageFailure:
    failure = StorageFailure.from_exception(exc)
    if isinstance(exc, PermissionError):
        return StorageFailure(StorageProblem.READ_ONLY, str(exc))
    if getattr(exc, "errno", None) == 28:  # ENOSPC
        return StorageFailure(StorageProblem.DISK_FULL, str(exc))
    return failure


def cleanup_stale_staging(destination: Path) -> list[Path]:
    """Remove staging folders left by an interrupted export — only ones carrying our marker."""
    removed: list[Path] = []
    if not destination.is_dir():
        return removed
    for candidate in destination.iterdir():
        if (
            candidate.is_dir()
            and not candidate.is_symlink()
            and candidate.name.startswith(STAGING_PREFIX)
            and candidate.name.endswith(STAGING_SUFFIX)
            and (candidate / STAGING_MARKER).is_file()
        ):
            shutil.rmtree(candidate, ignore_errors=True)
            removed.append(candidate)
    return removed


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------- the service
class ExportService:
    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._approved = ApprovedQuestionsService(paths)

    @property
    def default_destination(self) -> Path:
        return self._paths.exports_dir

    def plan(self, request: ExportRequest) -> ExportPlan:
        """What the export would contain right now (one consistent snapshot)."""
        codes = [s.code for s in SUBJECTS if s.code in set(request.subjects)]
        full = self._approved.snapshot()
        violations = tuple(self._approved.verify())
        selected = tuple(q for q in full.questions if q.subject_code in set(codes))
        snapshot = ApprovedSnapshot(full.taken_at, None, selected, fingerprint_of(selected))
        counts = bank_counts(self._paths)
        approved_by_code: dict[str, int] = {}
        for q in selected:
            approved_by_code[q.subject_code] = approved_by_code.get(q.subject_code, 0) + 1
        subjects = tuple(
            SubjectSummary(
                code,
                get_subject(code).name_ka,
                counts.subjects[code].total if code in counts.subjects else 0,
                approved_by_code.get(code, 0),
            )
            for code in codes
        )
        with_questions = [s.code for s in subjects if s.approved]
        issues = tuple(txt_problems(selected)) if ExportFormat.TXT in request.formats else ()
        return ExportPlan(
            request, snapshot, subjects, planned_files(request, with_questions), issues, violations
        )

    # -- run ---------------------------------------------------------------------------------
    def run(
        self,
        request: ExportRequest,
        *,
        progress: Progress | None = None,
        cancel: threading.Event | None = None,
    ) -> ExportResult:
        report = progress or (lambda *_args: None)
        timings: dict[str, float] = {}
        started = time.perf_counter()
        self.reconcile()  # finish any publication an earlier crash interrupted, before a new one
        report(ExportPhase.SNAPSHOT, 0, 1, "")
        plan = self.plan(request)
        timings["snapshot"] = time.perf_counter() - started
        if not request.formats or (ExportFormat.DOCX in request.formats and not request.editions):
            raise ExportEmpty("nothing to write was selected")
        if plan.is_empty:
            raise ExportEmpty("no approved question in the selection")
        if plan.blocked:
            raise ExportBlocked(plan.txt_issues, plan.violations)
        if ExportFormat.DOCX in request.formats and not (request.per_subject_docx or request.combined_docx):
            raise ExportEmpty("no Word document layout was selected")

        destination = request.destination
        try:
            destination.mkdir(parents=True, exist_ok=True)
            cleanup_stale_staging(destination)
        except OSError as exc:
            raise ExportStorageError(_storage(exc)) from exc
        now = datetime.now(UTC)
        export_id = f"{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        final = destination / f"{FOLDER_PREFIX}{export_id}"
        staging = destination / f"{STAGING_PREFIX}{export_id}{STAGING_SUFFIX}"
        try:
            staging.mkdir()
        except OSError as exc:
            raise ExportStorageError(_storage(exc)) from exc
        try:
            (staging / STAGING_MARKER).write_text(export_id, encoding="utf-8")
        except OSError as exc:
            # Phase 9b: mkdir succeeded, so the folder is ours (and empty but for a partial marker); without
            # a complete marker cleanup_stale_staging would never recognise it, so remove it here
            shutil.rmtree(staging, ignore_errors=True)
            raise ExportStorageError(_storage(exc)) from exc

        def check_cancel() -> None:
            if cancel is not None and cancel.is_set():
                raise _Cancelled

        try:
            blocks = self._blocks(plan)
            written = self._write(plan, blocks, staging, export_id, now, report, check_cancel, timings)
            check_cancel()
            self._verify(plan, blocks, staging, written, report, check_cancel, timings)
            check_cancel()
            files, manifest = self._manifest(plan, staging, written, export_id, now, report, timings)
            check_cancel()
            report(ExportPhase.PUBLISH, 0, 1, final.name)
            run_ids = self._publish(plan, staging, final, files, manifest, export_id)
        except _Cancelled as exc:
            self._discard(staging, export_id, final)
            raise ExportCancelled("the export was cancelled; nothing was published") from exc
        except OSError as exc:
            self._discard(staging, export_id, final)
            raise ExportStorageError(_storage(exc)) from exc
        except BaseException:
            self._discard(staging, export_id, final)
            raise
        timings["total"] = time.perf_counter() - started
        report(ExportPhase.DONE, 1, 1, final.name)
        log.info(
            "Export %s: %s questions, %s files in %.1fs",
            export_id,
            plan.approved,
            len(files),
            timings["total"],
        )
        return ExportResult(
            export_id, final, tuple(files), plan.approved, plan.snapshot.fingerprint, tuple(run_ids), timings
        )

    # -- steps ------------------------------------------------------------------------------------
    @staticmethod
    def _blocks(plan: ExportPlan) -> list[SubjectBlock]:
        by_code: dict[str, list[ApprovedQuestion]] = {}
        for q in plan.snapshot.questions:
            by_code.setdefault(q.subject_code, []).append(q)
        return [
            SubjectBlock(s.code, s.name_ka, tuple(sorted(by_code[s.code], key=lambda q: q.source_number)))
            for s in plan.subjects
            if s.code in by_code
        ]

    def _write(
        self, plan, blocks, staging, export_id, now, report, check_cancel, timings
    ) -> list[tuple[str, str, object]]:  # noqa: ANN001, PLR0913
        """Write every selected file into *staging*; returns (relative path, kind, info) per file."""
        request = plan.request
        written: list[tuple[str, str, object]] = []
        if ExportFormat.TXT in request.formats:
            started = time.perf_counter()
            done = 0
            for block in blocks:
                check_cancel()
                for name, text in (
                    (questions_filename(block.code), render_questions(block.questions)),
                    (explanations_filename(block.code), render_explanations(block.questions)),
                ):
                    write_utf8_text_atomic(staging / "TXT" / name, text)
                    written.append((f"TXT/{name}", "TXT", block))
                done += len(block.questions)
                report(ExportPhase.TXT, done, plan.approved, block.code)
            timings["txt"] = time.perf_counter() - started
        if ExportFormat.DOCX in request.formats:
            started = time.perf_counter()
            meta = DocxMeta(
                export_id=export_id,
                exported_at_local=now.astimezone().strftime("%Y-%m-%d %H:%M"),
                fingerprint=plan.snapshot.fingerprint,
                app_version=__version__,
            )
            documents: list[tuple[str, list[SubjectBlock]]] = []
            for edition in request.editions:
                if request.per_subject_docx:
                    documents += [
                        (f"DOCX/{edition_filename(b.code, edition)}|{edition.value}", [b]) for b in blocks
                    ]
                if request.combined_docx:
                    documents.append(
                        (f"DOCX/{edition_filename(COMBINED_STEM, edition)}|{edition.value}", blocks)
                    )
            total = sum(len(b.questions) for _name, chosen in documents for b in chosen)
            done = 0
            (staging / "DOCX").mkdir(exist_ok=True)
            for key, chosen in documents:
                relative, edition_value = key.split("|")
                edition = DocxEdition(edition_value)
                check_cancel()
                base = done

                def tick(count: int, _base: int = base, _name: str = relative) -> None:
                    if count % 50 == 0:
                        check_cancel()
                        report(ExportPhase.DOCX, _base + count, total, _name)

                document = build_docx(chosen, edition, meta, font=request.font, progress=tick)
                # Phase 9b (D22): rendered in memory, written through a file that is always closed.
                # python-docx's own save left its zip handle open while a write error propagated, so
                # on Windows the staging folder could not be removed after a failed export.
                buffer = io.BytesIO()
                document.save(buffer)
                with (staging / relative).open("wb") as handle:
                    handle.write(buffer.getbuffer())
                done += sum(len(b.questions) for b in chosen)
                report(ExportPhase.DOCX, done, total, relative)
                written.append((relative, "DOCX", (edition, chosen)))
            timings["docx"] = time.perf_counter() - started
        return written

    def _verify(self, plan, blocks, staging, written, report, check_cancel, timings) -> None:  # noqa: ANN001
        started = time.perf_counter()
        problems: list[str] = []
        total = len(written)
        for done, (relative, kind, info) in enumerate(written, start=1):
            check_cancel()
            report(ExportPhase.VERIFY, done - 1, total, relative)
            path = staging / relative
            if kind == "DOCX":
                edition, chosen = info  # type: ignore[misc]
                result = verify_docx(path, chosen, edition, font=plan.request.font)
                problems += [f"{relative}: {p}" for p in result.problems]
            elif relative.endswith("_questions.txt"):
                problems += self._verify_txt(staging, info)  # type: ignore[arg-type]
        report(ExportPhase.VERIFY, total, total, "")
        timings["verify"] = time.perf_counter() - started
        if problems:
            raise ExportVerificationFailed(problems)

    @staticmethod
    def _verify_txt(staging: Path, block: SubjectBlock) -> list[str]:
        q_path = staging / "TXT" / questions_filename(block.code)
        e_path = staging / "TXT" / explanations_filename(block.code)
        problems: list[str] = []
        for path in (q_path, e_path):
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
                problems.append(f"{path.name}: not UTF-8 without BOM with LF line ends")
        result = parse_pair(block.code, q_path, e_path)
        if has_errors(result.issues):
            problems += [f"{q_path.name}: {i.message}" for i in result.issues if i.is_error][:20]
        parsed = {}
        for item in result.items:
            if not item.is_valid or item.question is None:
                problems.append(
                    f"{item.location}: the written text does not read back ({len(item.issues)} problem(s))"
                )
                continue
            parsed[str(item.question.qid)] = item.question.content
        expected = {q.uid: q.content for q in block.questions}
        if set(parsed) != set(expected):
            problems.append(
                f"{block.code}: TXT holds {sorted(set(parsed) - set(expected))[:5]} extra and "
                f"misses {sorted(set(expected) - set(parsed))[:5]}"
            )
        for uid, content in expected.items():
            if uid in parsed and parsed[uid] != content:
                problems.append(f"{uid}: the TXT text does not read back identically")
        return problems

    def _manifest(
        self, plan, staging, written, export_id, now, report, timings
    ) -> tuple[list[ExportedFile], dict]:  # noqa: ANN001
        started = time.perf_counter()
        report(ExportPhase.MANIFEST, 0, 1, MANIFEST_NAME)
        files = [
            ExportedFile(relative, kind, (staging / relative).stat().st_size, _sha256(staging / relative))
            for relative, kind, _info in written
        ]
        request = plan.request
        conn = open_database(self._paths.database_file)
        try:
            conn.execute("PRAGMA query_only = ON")
            metadata = read_metadata(conn)
        finally:
            conn.close()
        database_id = metadata.get("database_id", "")
        seed = (
            {key: metadata.get(key) for key in SEED_META_KEYS}
            if metadata.get("seed_database_id")
            else None  # a database created before Phase 7 or by manual import: no seed
        )
        manifest = {
            "manifest_version": 1,
            "export_id": export_id,
            "application": "VascQBank Review Desktop",
            "app_version": __version__,
            "exported_at": now.replace(microsecond=0).isoformat(),
            "database_id": database_id,
            "seed": seed,
            "snapshot": {"taken_at": plan.snapshot.taken_at, "fingerprint": plan.snapshot.fingerprint},
            "selection": {
                "subjects": [s.code for s in plan.subjects],
                "formats": sorted(f.value for f in request.formats),
                "docx_editions": [e.value for e in request.editions]
                if ExportFormat.DOCX in request.formats
                else [],
                "docx_per_subject": request.per_subject_docx,
                "docx_combined": request.combined_docx,
                "font": request.font,
            },
            "counts": {
                "approved_exported": plan.approved,
                "excluded_not_approved": plan.excluded,
                "by_subject": {s.code: {"total": s.total, "approved": s.approved} for s in plan.subjects},
            },
            "numbering": (
                "TXT keeps the original source numbers (gaps are expected in partial exports); "
                "Word shows a continuous display number followed by the immutable question ID"
            ),
            "questions": [
                {
                    "uid": q.uid,
                    "subject": q.subject_code,
                    "source_number": q.source_number,
                    "approved_revision_id": q.revision_id,
                    "revision_no": q.revision_no,
                    "content_hash": q.content_hash,
                    "approved_at": q.approved_at,
                }
                for q in plan.snapshot.questions
            ],
            "files": [{"path": f.path, "kind": f.kind, "size": f.size, "sha256": f.sha256} for f in files],
        }
        write_utf8_text_atomic(
            staging / MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        )
        manifest_file = staging / MANIFEST_NAME
        files.append(
            ExportedFile(MANIFEST_NAME, "MANIFEST", manifest_file.stat().st_size, _sha256(manifest_file))
        )
        timings["manifest"] = time.perf_counter() - started
        return files, manifest

    def _publish(self, plan, staging, final, files, manifest, export_id) -> list[int]:  # noqa: ANN001
        # The atomic writer creates private (0600) temporary files; deliverables get the ordinary
        # permissions of a file created in this folder (the marker's), like the Word documents.
        mode = (staging / STAGING_MARKER).stat().st_mode & 0o777
        for exported in files:
            os.chmod(staging / exported.path, mode)
        (staging / STAGING_MARKER).unlink()
        request = plan.request
        rows = []
        for fmt in sorted(request.formats):
            chosen = [
                {"path": f.path, "sha256": f.sha256, "size": f.size}
                for f in files
                if f.kind in (fmt.value, "MANIFEST")
            ]
            rows.append(
                export_repository.ExportRunRow(
                    format=fmt.value,
                    scope={
                        "export_id": export_id,
                        "folder": final.name,
                        "subjects": [s.code for s in plan.subjects],
                        "editions": [e.value for e in request.editions] if fmt is ExportFormat.DOCX else [],
                        "fingerprint": plan.snapshot.fingerprint,
                    },
                    question_count=plan.approved,
                    files=chosen,
                    manifest=manifest,
                    app_version=__version__,
                    created_at=utc_now_iso(),
                )
            )
        manifest_sha = next(f.sha256 for f in files if f.kind == "MANIFEST")
        export_publication.write_journal(
            self._paths,
            {
                "export_id": export_id,
                "staging": str(staging),
                "final": str(final),
                "manifest_sha256": manifest_sha,
                "rows": [
                    {
                        "format": r.format,
                        "scope": r.scope,
                        "question_count": r.question_count,
                        "files": r.files,
                        "app_version": r.app_version,
                        "created_at": r.created_at,
                    }
                    for r in rows
                ],
            },
        )
        export_repository._checkpoint("journal-written")
        conn = make_durable(open_database(self._paths.database_file))
        try:
            ids = export_repository.record_and_publish(conn, rows, staging, final, export_id=export_id)
        finally:
            conn.close()
        export_publication.remove_journal(export_publication.journal_path(self._paths, export_id))
        export_repository._checkpoint("journal-removed")
        return ids

    def _discard(self, staging: Path, export_id: str | None = None, final: Path | None = None) -> None:
        """Remove our own unfinished staging folder (never anything else) and its journal entry.

        Phase 9: if the final folder exists (the rename happened, and moving it back failed, or only
        the journal removal failed after the commit), the journal is **kept**: the next
        reconciliation verifies that folder and records it exactly once instead of leaving a
        published export that nothing knows about.
        """
        if staging.name.startswith(STAGING_PREFIX) and staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        if export_id is not None:
            if final is not None and final.exists():
                log.error("Export folder %s exists after a failure; its journal is kept", final)
                return
            export_publication.remove_journal(export_publication.journal_path(self._paths, export_id))

    def reconcile(self) -> export_publication.ReconcileReport:
        """Finish or undo publications interrupted by a crash (idempotent; see export_publication)."""
        return export_publication.reconcile(self._paths)

    def runs(self) -> list[dict]:
        conn = open_database(self._paths.database_file)
        try:
            return export_repository.list_runs(conn)
        finally:
            conn.close()


__all__ = [
    "ExportBlocked",
    "ExportCancelled",
    "ExportEmpty",
    "ExportError",
    "ExportFormat",
    "ExportPhase",
    "ExportPlan",
    "ExportRequest",
    "ExportResult",
    "ExportService",
    "ExportStorageError",
    "ExportVerificationFailed",
    "ExportedFile",
    "SubjectSummary",
]
