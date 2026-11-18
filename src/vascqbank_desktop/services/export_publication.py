"""Crash-consistent publication of an export folder: intent journal and reconciliation.

Before the folder is published, a small JSON *intent* is written durably to
``<data>/journal/export-<id>.json`` (export id, staging and final paths, the manifest's SHA-256,
and the ``export_runs`` rows without the manifest body). After the commit it is removed. If the
program dies anywhere in between, :func:`reconcile` — run at start-up and before every export —
finishes the job idempotently:

* rows for the export id already exist → only the journal is removed;
* the final folder exists → its manifest hash and **every file's size and SHA-256** are verified;
  if all match, the rows are inserted once (from the manifest in the folder) — the export was
  complete and verified before it was renamed; if anything differs, nothing is recorded, the folder
  is left untouched and the journal is moved to ``journal/unverified/`` for a person to look at;
* only the staging folder exists → publication never happened: our staging folder is removed;
* neither exists → the journal is removed.

A valid published export is never deleted, never overwritten, and never recorded twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import export_repository
from vascqbank_desktop.db.connection import make_durable, open_database

log = logging.getLogger(__name__)

JOURNAL_FORMAT = 1
MANIFEST_NAME = "export-manifest.json"
STAGING_PREFIX = ".vqb-export-"


def journal_dir(paths: AppPaths) -> Path:
    return paths.data_dir / "journal"


def journal_path(paths: AppPaths, export_id: str) -> Path:
    return journal_dir(paths) / f"export-{export_id}.json"


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_journal(paths: AppPaths, entry: dict) -> Path:
    """Durably write the intent (temp file, fsync, rename, fsync of the directory)."""
    folder = journal_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    target = journal_path(paths, entry["export_id"])
    temp = target.with_name(target.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump({"journal_format": JOURNAL_FORMAT, **entry}, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    _fsync_dir(folder)
    return target


def remove_journal(path: Path) -> None:
    path.unlink(missing_ok=True)
    if path.parent.is_dir():
        _fsync_dir(path.parent)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_published(folder: Path, manifest_sha256: str) -> list[str]:
    """Problems with a published export folder (empty list = every file is exactly as recorded)."""
    manifest_file = folder / MANIFEST_NAME
    if not manifest_file.is_file():
        return ["the manifest is missing"]
    if _sha256(manifest_file) != manifest_sha256:
        return ["the manifest differs from the one that was published"]
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    problems = []
    for entry in manifest.get("files", []):
        path = folder / entry["path"]
        if not path.is_file():
            problems.append(f"{entry['path']} is missing")
        elif path.stat().st_size != entry["size"] or _sha256(path) != entry["sha256"]:
            problems.append(f"{entry['path']} differs from the manifest")
    return problems


@dataclass(slots=True)
class ReconcileReport:
    recorded: list[str] = field(default_factory=list)  # published folder recorded now
    already_recorded: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)  # never published; staging removed
    unverifiable: list[str] = field(default_factory=list)  # folder kept, not recorded, journal kept aside

    @property
    def changed(self) -> bool:
        return bool(self.recorded or self.discarded or self.unverifiable)


def _set_aside(path: Path, subfolder: str) -> None:
    target = path.parent / subfolder
    target.mkdir(exist_ok=True)
    os.replace(path, target / path.name)


def reconcile(paths: AppPaths) -> ReconcileReport:
    report = ReconcileReport()
    folder = journal_dir(paths)
    if not folder.is_dir():
        return report
    for leftover in folder.glob("export-*.json.tmp"):
        leftover.unlink(missing_ok=True)  # an intent that was never completed (nothing followed it)
    for path in sorted(folder.glob("export-*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            export_id = entry["export_id"]
            final, staging = Path(entry["final"]), Path(entry["staging"])
        except (OSError, ValueError, KeyError) as exc:
            log.error("Unreadable export journal %s (%s); set aside", path, exc)
            _set_aside(path, "unreadable")
            report.unverifiable.append(path.name)
            continue
        conn = make_durable(open_database(paths.database_file))
        try:
            if export_repository.runs_for_export(conn, export_id):
                report.already_recorded.append(export_id)
            elif final.is_dir():
                problems = verify_published(final, entry["manifest_sha256"])
                if problems:
                    log.error("Published export %s cannot be verified: %s", final, "; ".join(problems))
                    _set_aside(path, "unverified")
                    report.unverifiable.append(export_id)
                    continue
                manifest = json.loads((final / MANIFEST_NAME).read_text(encoding="utf-8"))
                rows = [export_repository.ExportRunRow(manifest=manifest, **row) for row in entry["rows"]]
                export_repository.record_once(conn, rows, export_id)
                (final / ".vqb-staging").unlink(missing_ok=True)
                report.recorded.append(export_id)
                log.warning("Recorded export %s that was published just before an interruption", export_id)
            else:
                if staging.is_dir() and staging.name.startswith(STAGING_PREFIX):
                    shutil.rmtree(staging, ignore_errors=True)
                report.discarded.append(export_id)
        finally:
            conn.close()
        remove_journal(path)
    return report
