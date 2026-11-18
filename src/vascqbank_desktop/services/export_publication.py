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
import re
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


MAX_JOURNAL_BYTES = 4 << 20  # rows without the manifest body: a few KB in practice
MAX_MANIFEST_BYTES = 256 << 20  # ≈ 3 MB for 10,000 questions; far larger is not ours
FOLDER_PREFIX = "VascQBank_Export_"
STAGING_SUFFIX = ".partial"
_EXPORT_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROW_KEYS = {"format", "scope", "question_count", "files", "app_version", "created_at"}


class UntrustedJournal(ValueError):
    """A journal or manifest that does not have exactly the shape this program writes."""


def _read_json(path: Path, limit: int) -> object:
    if path.is_symlink():
        raise UntrustedJournal(f"{path.name} is a symbolic link")
    if path.stat().st_size > limit:
        raise UntrustedJournal(f"{path.name} is implausibly large")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except RecursionError as exc:
        raise UntrustedJournal(f"{path.name} is nested too deeply") from exc


def _safe_relative(folder: Path, relative: object) -> Path:
    """*relative* as a regular file inside *folder* — never outside it, never through a link."""
    if not isinstance(relative, str) or not relative or len(relative) > 512 or "\x00" in relative:
        raise UntrustedJournal("a manifest path is not a string")
    parts = relative.replace("\\", "/").split("/")
    if relative.startswith(("/", "\\")) or ":" in relative or any(p in ("", ".", "..") for p in parts):
        raise UntrustedJournal(f"manifest path {relative!r} leaves the export folder")
    path = folder.joinpath(*parts)
    current = folder
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise UntrustedJournal(f"manifest path {relative!r} goes through a symbolic link")
    return path


def verify_published(folder: Path, manifest_sha256: str) -> list[str]:
    """Problems with a published export folder (empty list = every file is exactly as recorded).

    The manifest inside the folder is only trusted after its SHA-256 equals the journal's, and even
    then every file path is confined to the folder (Phase 9).
    """
    manifest_file = folder / MANIFEST_NAME
    if folder.is_symlink() or not manifest_file.is_file() or manifest_file.is_symlink():
        return ["the manifest is missing"]
    if _sha256(manifest_file) != manifest_sha256:
        return ["the manifest differs from the one that was published"]
    try:
        manifest = _read_json(manifest_file, MAX_MANIFEST_BYTES)
        entries = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(entries, list):
            return ["the manifest has no file list"]
        problems = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                return ["a manifest file entry is malformed"]
            relative, size, digest = entry.get("path"), entry.get("size"), entry.get("sha256")
            if not isinstance(size, int) or isinstance(size, bool) or not isinstance(digest, str):
                return ["a manifest file entry is malformed"]
            path = _safe_relative(folder, relative)
            if str(relative) in seen:
                return [f"{relative} is listed twice"]
            seen.add(str(relative))
            if not path.is_file():
                problems.append(f"{relative} is missing")
            elif path.stat().st_size != size or _sha256(path) != digest:
                problems.append(f"{relative} differs from the manifest")
        return problems
    except (UntrustedJournal, OSError, ValueError) as exc:
        return [f"the manifest cannot be trusted: {exc}"]


def parse_journal(entry: object, journal_file: Path) -> tuple[str, Path, Path, str, list[dict]]:
    """Validate an intent written by :func:`write_journal`; raises :class:`UntrustedJournal`.

    Returns ``(export_id, final, staging, manifest_sha256, rows)``. Staging and final folders must be
    the two sibling names this program derives from the export id, and the export id must be the one
    in the journal's own file name — so a damaged or crafted journal can never point the recovery at
    another folder.
    """
    if not isinstance(entry, dict) or entry.get("journal_format") != JOURNAL_FORMAT:
        raise UntrustedJournal("not a version-1 export journal")
    export_id = entry.get("export_id")
    if not isinstance(export_id, str) or not _EXPORT_ID.match(export_id):
        raise UntrustedJournal("malformed export id")
    if journal_file.name != f"export-{export_id}.json":
        raise UntrustedJournal("the export id differs from the journal's file name")
    raw_final, raw_staging = entry.get("final"), entry.get("staging")
    if (
        not isinstance(raw_final, str)
        or not isinstance(raw_staging, str)
        or "\x00" in raw_final + raw_staging
    ):
        raise UntrustedJournal("folder paths are not strings")
    final, staging = Path(raw_final), Path(raw_staging)
    if not final.is_absolute() or not staging.is_absolute():
        raise UntrustedJournal("folder paths are not absolute")
    if (
        final.name != f"{FOLDER_PREFIX}{export_id}"
        or staging.name != f"{STAGING_PREFIX}{export_id}{STAGING_SUFFIX}"
    ):
        raise UntrustedJournal("folder names do not belong to this export")
    if final.parent != staging.parent or ".." in final.parts:
        raise UntrustedJournal("staging and final folders are not siblings")
    digest = entry.get("manifest_sha256")
    if not isinstance(digest, str) or not _SHA256.match(digest):
        raise UntrustedJournal("malformed manifest SHA-256")
    rows = entry.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        raise UntrustedJournal("malformed rows")
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise UntrustedJournal("malformed row")
        scope = row["scope"]
        if not isinstance(scope, dict) or scope.get("export_id") != export_id:
            raise UntrustedJournal("a row belongs to another export")
        count = row["question_count"]
        if row["format"] not in ("DOCX", "TXT") or not isinstance(count, int) or isinstance(count, bool):
            raise UntrustedJournal("malformed row values")
        if count < 0 or not isinstance(row["files"], list):
            raise UntrustedJournal("malformed row values")
        if not isinstance(row["app_version"], str) or not isinstance(row["created_at"], str):
            raise UntrustedJournal("malformed row values")
    return export_id, final, staging, digest, rows


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


def _discard_staging(staging: Path, manifest_sha256: str) -> bool:
    """Remove an unpublished staging folder — only when it is provably the one this journal staged
    (a real directory, not a link, whose manifest has the journal's SHA-256)."""
    if not staging.exists():
        return True
    manifest = staging / MANIFEST_NAME
    if (
        staging.is_symlink()
        or not staging.is_dir()
        or manifest.is_symlink()
        or not manifest.is_file()
        or _sha256(manifest) != manifest_sha256
    ):
        return False
    shutil.rmtree(staging)
    return True


def reconcile(paths: AppPaths) -> ReconcileReport:
    report = ReconcileReport()
    folder = journal_dir(paths)
    if not folder.is_dir():
        return report
    for leftover in folder.glob("export-*.json.tmp"):
        leftover.unlink(missing_ok=True)  # an intent that was never completed (nothing followed it)
    for path in sorted(folder.glob("export-*.json")):
        try:
            export_id, final, staging, manifest_sha256, raw_rows = parse_journal(
                _read_json(path, MAX_JOURNAL_BYTES), path
            )
        except (OSError, ValueError) as exc:  # UntrustedJournal is a ValueError
            log.error("Unreadable or untrusted export journal %s (%s); set aside", path, exc)
            _set_aside(path, "unreadable")
            report.unverifiable.append(path.name)
            continue
        conn = make_durable(open_database(paths.database_file))
        try:
            if export_repository.runs_for_export(conn, export_id):
                report.already_recorded.append(export_id)
            elif final.is_dir() and not final.is_symlink():
                problems = verify_published(final, manifest_sha256)
                if problems:
                    log.error("Published export %s cannot be verified: %s", final, "; ".join(problems))
                    _set_aside(path, "unverified")
                    report.unverifiable.append(export_id)
                    continue
                manifest = _read_json(final / MANIFEST_NAME, MAX_MANIFEST_BYTES)
                rows = [export_repository.ExportRunRow(manifest=manifest, **row) for row in raw_rows]
                export_repository.record_once(conn, rows, export_id)
                marker = final / ".vqb-staging"
                if marker.is_file() and not marker.is_symlink():
                    marker.unlink()
                report.recorded.append(export_id)
                log.warning("Recorded export %s that was published just before an interruption", export_id)
            elif final.exists() or final.is_symlink():
                log.error("Export target %s is not a plain folder; nothing recorded", final)
                _set_aside(path, "unverified")
                report.unverifiable.append(export_id)
                continue
            else:
                if not _discard_staging(staging, manifest_sha256):
                    log.error("Staging folder %s is not provably ours; left untouched", staging)
                    _set_aside(path, "unverified")
                    report.unverifiable.append(export_id)
                    continue
                report.discarded.append(export_id)
        finally:
            conn.close()
        remove_journal(path)
    return report
