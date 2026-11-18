"""Build a seed database from paired-TXT source files — with the EXISTING import pipeline.

Developer-side only (``tools/build_seed.py``); the application never builds seeds. Steps:

1. **Discover and dry-run** the source folder with :func:`import_service.dry_run` against a fresh,
   empty database in a temporary folder (the same parser, validator and conflict rules as a
   manual import — there is no second parser).
2. **Gates** (every seed): no blocking validation error, no invalid item, no duplicate ID, no
   conflict, at least one question. **Production** additionally: only paired-TXT sources, exactly
   the 12 subjects, each at its target count, 10,000 questions in total.
3. **Import** with :func:`import_service.commit_import` — every question ``PENDING_REVIEW``, one
   ``IMPORT`` revision, its original content recorded immutably.
4. **Compact** into a single self-contained file (``VACUUM INTO``, rollback-journal mode, read-only
   file permissions) and **inspect** it read-only: application id, schema, ``integrity_check``,
   ``foreign_key_check``, counts, and *no reviewer work*.
5. **Manifest** ``seed-manifest.json`` (format in ARCHITECTURE.md §24.3) and a final
   :func:`seed_service.verify_seed` with exactly the checks the application applies at first launch.
6. **Publish** the folder by renaming it into place; an existing artifact is never overwritten.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from vascqbank_desktop import __version__
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.db.migrations import LATEST_VERSION, read_metadata, utc_now_iso
from vascqbank_desktop.domain.question import SourceKind
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.services import import_service
from vascqbank_desktop.services.seed_service import (
    DATABASE_NAME,
    MANIFEST_FORMAT,
    MANIFEST_NAME,
    SeedKind,
    VerifiedSeed,
    inspect_database,
    production_gate_failures,
    sha256_file,
    verify_seed,
    work_problems,
)
from vascqbank_desktop.services.storage import initialize_storage


class SeedBuildFailed(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems[:10]))


@dataclass(frozen=True, slots=True)
class SeedBuildResult:
    directory: Path
    seed: VerifiedSeed
    timings: dict[str, float] = field(default_factory=dict)


def _gate_problems(report: import_service.DryRunReport, kind: SeedKind) -> list[str]:
    problems: list[str] = []
    errors = [i for i in report.all_issues() if i.is_error]
    for issue in errors[:50]:
        where = f"{issue.question_id or ''} {issue.location() or issue.file or ''}".strip()
        problems.append(f"{issue.code.value} {where}: {issue.message}")
    if len(errors) > 50:
        problems.append(f"… {len(errors) - 50} more blocking errors")
    counts = report.counts
    for label, value in (
        ("invalid questions", counts.invalid),
        ("duplicate question IDs in the sources", counts.duplicates_in_selection),
        ("conflicts", counts.conflicts),
        ("questions already present", counts.identical_existing),
    ):
        if value:
            problems.append(f"{value} {label}")
    if counts.eligible == 0:
        problems.append("no question to import")
    if kind is SeedKind.PRODUCTION:
        kinds = {s.kind for s in report.sources}
        if kinds - {SourceKind.PAIRED_TXT}:
            problems.append("a production seed is built from the 12 paired-TXT subject files only")
        subjects: dict[str, int] = {}
        for question in report.eligible:
            subjects[question.qid.subject_code] = subjects.get(question.qid.subject_code, 0) + 1
        problems += production_gate_failures(subjects)
    return problems


def build_seed(source: Path, output: Path, *, kind: SeedKind, seed_version: str) -> SeedBuildResult:
    """Build and verify a seed from *source*; publish it as the new folder *output*."""
    if output.exists() and any(output.iterdir()):
        raise SeedBuildFailed([f"{output} already exists and is not empty; a seed is never overwritten"])
    if not seed_version.strip():
        raise SeedBuildFailed(["a seed version is required"])
    timings: dict[str, float] = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".seed-build-", dir=output.parent) as tmp:
        work = AppPaths(data_dir=Path(tmp) / "work")
        initialize_storage(work)

        started = time.perf_counter()
        report = import_service.dry_run(work, [source])
        timings["dry_run"] = time.perf_counter() - started
        problems = _gate_problems(report, kind)
        if problems:
            raise SeedBuildFailed(problems)

        started = time.perf_counter()
        result = import_service.commit_import(work, report)
        timings["import"] = time.perf_counter() - started
        if result.invalid or len(result.created) != report.counts.eligible:
            raise SeedBuildFailed([f"the import created {len(result.created)} of {report.counts.eligible}"])

        started = time.perf_counter()
        staging = Path(tmp) / "seed"
        staging.mkdir()
        database = staging / DATABASE_NAME
        conn = open_database(work.database_file)
        try:
            seed_id = read_metadata(conn)["database_id"]
            conn.execute("VACUUM INTO ?", (str(database),))
        finally:
            conn.close()
        compact = sqlite3.connect(database, isolation_level=None)
        try:
            compact.execute("PRAGMA journal_mode = DELETE")  # one self-contained file, no -wal
        finally:
            compact.close()
        timings["compact"] = time.perf_counter() - started

        started = time.perf_counter()
        facts = inspect_database(database)
        expected = {
            s.code: n
            for s in SUBJECTS
            if (n := sum(1 for q in report.eligible if q.qid.subject_code == s.code))
        }
        checks = []
        if facts["integrity_check"] != "ok" or facts["foreign_key_check"] != "ok":
            checks.append(f"integrity: {facts['integrity_check']}; {facts['foreign_key_check']}")
        if facts["schema_version"] != LATEST_VERSION:
            checks.append(f"schema {facts['schema_version']}, expected {LATEST_VERSION}")
        if facts.get("subjects") != expected:
            checks.append("question counts after import differ from the dry run")
        checks += work_problems(facts)
        if checks:
            raise SeedBuildFailed(checks)

        os.chmod(database, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # the artifact is read-only
        manifest = {
            "manifest_format": MANIFEST_FORMAT,
            "kind": kind.value,
            "seed_version": seed_version,
            "seed_database_id": seed_id,
            "built_at": utc_now_iso(),
            "app_version": __version__,
            "schema_version": facts["schema_version"],
            "question_count": facts["questions"],
            "subjects": {s.code: expected[s.code] for s in SUBJECTS if s.code in expected},
            "source_files": [
                {"name": f.name, "sha256": f.sha256, "size": f.size}
                for f in sorted(report.source_files(), key=lambda f: f.name)
            ],
            "database": {
                "file": DATABASE_NAME,
                "sha256": sha256_file(database),
                "size": database.stat().st_size,
            },
            "verification": {
                "integrity_check": facts["integrity_check"],
                "foreign_key_check": facts["foreign_key_check"],
                "all_pending_review": facts["not_pending"] == 0,
                "one_import_revision_per_question": facts["revisions"] == facts["questions"],
                "approvals": facts["approved"],
                "drafts": facts["drafts"],
                "export_runs": facts["export_runs"],
                "warnings": report.counts.warnings,
            },
            "production_gates": {
                "passed": not production_gate_failures(expected),
                "failures": production_gate_failures(expected),
            },
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        verified = verify_seed(staging, require_production=kind is SeedKind.PRODUCTION)
        timings["verify"] = time.perf_counter() - started

        if output.exists():
            output.rmdir()  # empty (checked above)
        os.rename(staging, output)
    return SeedBuildResult(output, VerifiedSeed(output, verified.manifest, verified.manifest_sha256), timings)
