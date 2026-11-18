"""The bundled seed database: manifest, strict verification and safe installation.

A *seed* is a read-only SQLite file built by ``tools/build_seed.py`` through the normal import
pipeline, plus ``seed-manifest.json`` describing it. This module

* :func:`verify_seed` — trusts nothing: the manifest is parsed against its documented format, the
  database file's size and SHA-256 must match, and the database itself is opened **read-only**
  (``mode=ro&immutable=1``, so not even its header is written) and checked: our application id,
  schema version, ``integrity_check``, ``foreign_key_check``, the per-subject counts, and that it holds
  no reviewer work (every question pending, one revision each, no approvals, no drafts, no exports);
* :func:`install_seed` — copies the verified seed to a temporary file next to the working database,
  verifies the copy, gives it a fresh ``database_id`` and records its provenance in ``app_metadata``,
  then publishes it with a **no-overwrite** link/rename. It refuses to run if a working database
  already exists. An interruption at any point leaves either no database (the temporary file is
  removed on the next start) or the complete one;
* :func:`locate_seed` — where a seed comes from (command line, environment, or package data).

The bundled seed itself is never modified.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop import __version__
from vascqbank_desktop.config.constants import SQLITE_APPLICATION_ID
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.migrations import LATEST_VERSION, utc_now_iso
from vascqbank_desktop.db.schema_check import schema_differences
from vascqbank_desktop.domain.subjects import SUBJECTS, TOTAL_TARGET_QUESTIONS

log = logging.getLogger(__name__)

MANIFEST_FORMAT = 1
MANIFEST_NAME = "seed-manifest.json"
MAX_MANIFEST_BYTES = 4 << 20  # a real seed manifest is a few KB
DATABASE_NAME = "vascqbank-seed.sqlite3"
SEED_DIR_ENV = "VASCQBANK_SEED_DIR"
INSTALL_PREFIX = ".seed-install-"
INSTALL_SUFFIX = ".partial"

# app_metadata keys written into a working database installed from a seed
META_SEED_DATABASE_ID = "seed_database_id"
META_SEED_VERSION = "seed_version"
META_SEED_KIND = "seed_kind"
META_SEED_INSTALLED_AT = "seed_installed_at"
META_SEED_MANIFEST_HASH = "seed_manifest_hash"
SEED_META_KEYS = (
    META_SEED_DATABASE_ID,
    META_SEED_VERSION,
    META_SEED_KIND,
    META_SEED_INSTALLED_AT,
    META_SEED_MANIFEST_HASH,
)


class SeedKind(StrEnum):
    PRODUCTION = "PRODUCTION"  # the real, complete bank — every acceptance gate passed
    DEVELOPMENT = "DEVELOPMENT"  # synthetic or partial test data; always shown as such


class SeedProblem(StrEnum):
    MANIFEST_MISSING = "MANIFEST_MISSING"
    MANIFEST_INVALID = "MANIFEST_INVALID"
    DATABASE_MISSING = "DATABASE_MISSING"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    NOT_OUR_DATABASE = "NOT_OUR_DATABASE"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    INTEGRITY = "INTEGRITY"
    CONTENT_MISMATCH = "CONTENT_MISMATCH"
    CONTAINS_WORK = "CONTAINS_WORK"
    NOT_PRODUCTION = "NOT_PRODUCTION"


class SeedInvalid(Exception):
    def __init__(self, problem: SeedProblem, detail: str) -> None:
        self.problem = problem
        self.detail = detail
        super().__init__(f"{problem.value}: {detail}")


class DatabaseAlreadyExists(Exception):
    """A working database exists; a seed is never installed over it."""


@dataclass(frozen=True, slots=True)
class SeedManifest:
    kind: SeedKind
    seed_version: str
    seed_database_id: str
    built_at: str
    app_version: str
    schema_version: int
    question_count: int
    subjects: dict[str, int]
    database_file: str
    database_sha256: str
    database_size: int
    raw: dict

    @property
    def is_production(self) -> bool:
        return self.kind is SeedKind.PRODUCTION


@dataclass(frozen=True, slots=True)
class VerifiedSeed:
    directory: Path
    manifest: SeedManifest
    manifest_sha256: str

    @property
    def database_path(self) -> Path:
        return self.directory / self.manifest.database_file


@dataclass(frozen=True, slots=True)
class SeedInstallResult:
    database_id: str  # the new working database's own id
    seed_database_id: str
    seed_version: str
    seed_kind: SeedKind
    duration_s: float


def _checkpoint(stage: str) -> None:
    """Named points of the installation; tests replace this to interrupt it exactly there."""


# ----------------------------------------------------------------------------------- hashing
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production_gate_failures(subjects: dict[str, int]) -> list[str]:
    """Why these per-subject counts are not a complete production bank (empty = they are)."""
    failures: list[str] = []
    expected = {s.code: s.target_count for s in SUBJECTS}
    missing = sorted(set(expected) - set(subjects))
    extra = sorted(set(subjects) - set(expected))
    if missing:
        failures.append(f"missing subjects: {', '.join(missing)}")
    if extra:
        failures.append(f"unknown subjects: {', '.join(extra)}")
    for code, target in expected.items():
        if code in subjects and subjects[code] != target:
            failures.append(f"{code}: {subjects[code]} questions, expected {target}")
    total = sum(subjects.values())
    if total != TOTAL_TARGET_QUESTIONS:
        failures.append(f"{total} questions in total, expected {TOTAL_TARGET_QUESTIONS}")
    return failures


# ----------------------------------------------------------------------------------- manifest
def parse_manifest(data: bytes) -> SeedManifest:
    """Parse and type-check ``seed-manifest.json`` (format 1); raises :class:`SeedInvalid`."""
    if len(data) > MAX_MANIFEST_BYTES:
        raise SeedInvalid(SeedProblem.MANIFEST_INVALID, "the manifest is implausibly large")
    try:
        raw = json.loads(data.decode("utf-8"))
    # Phase 9 (fuzzing): an over-long integer raises a plain ValueError (not JSONDecodeError), deep
    # nesting a RecursionError — both must be a controlled "invalid manifest", never a crash.
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SeedInvalid(SeedProblem.MANIFEST_INVALID, f"not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("manifest_format") != MANIFEST_FORMAT:
        raise SeedInvalid(SeedProblem.MANIFEST_INVALID, f"manifest_format must be {MANIFEST_FORMAT}")
    try:
        database = raw["database"]
        subjects = raw["subjects"]
        manifest = SeedManifest(
            kind=SeedKind(raw["kind"]),
            seed_version=str(raw["seed_version"]),
            seed_database_id=str(raw["seed_database_id"]),
            built_at=str(raw["built_at"]),
            app_version=str(raw["app_version"]),
            schema_version=int(raw["schema_version"]),
            question_count=int(raw["question_count"]),
            subjects={str(k): int(v) for k, v in subjects.items()},
            database_file=str(database["file"]),
            database_sha256=str(database["sha256"]),
            database_size=int(database["size"]),
            raw=raw,
        )
    # OverflowError: int(float("inf")) from a JSON number such as 1e999 (found by fuzzing, Phase 9)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise SeedInvalid(SeedProblem.MANIFEST_INVALID, f"missing or malformed field: {exc}") from exc
    if not manifest.seed_version.strip() or len(manifest.database_sha256) != 64:
        raise SeedInvalid(SeedProblem.MANIFEST_INVALID, "seed_version or database sha256 malformed")
    if (
        "/" in manifest.database_file
        or "\\" in manifest.database_file
        or manifest.database_file.startswith(".")
    ):
        raise SeedInvalid(SeedProblem.MANIFEST_INVALID, "the database file must be a plain file name")
    if sum(manifest.subjects.values()) != manifest.question_count:
        raise SeedInvalid(SeedProblem.MANIFEST_INVALID, "subject counts do not add up to question_count")
    return manifest


# ----------------------------------------------------------------------------------- verification
def _read_only(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, isolation_level=None)


def inspect_database(path: Path) -> dict:
    """Facts about a seed database, read without writing a single byte to it."""
    conn = _read_only(path)
    try:
        app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
        facts: dict = {
            "application_id": app_id,
            "schema_version": version,
            "integrity_check": "ok" if integrity == ["ok"] else "; ".join(integrity[:5]),
            "foreign_key_check": "ok" if not fk else f"{len(fk)} violation(s)",
            "tables": sorted(tables),
            # Phase 9b: anything our migrations would not have created (or a missing object)
            "schema_differences": schema_differences(conn, version),
        }
        if "questions" not in tables:
            return facts
        meta = dict(conn.execute("SELECT key, value FROM app_metadata").fetchall())
        facts["database_id"] = meta.get("database_id", "")
        facts["seed_meta_keys"] = sorted(k for k in meta if k in SEED_META_KEYS)
        facts["subjects"] = {
            code: n
            for code, n in conn.execute("SELECT subject_code, count(*) FROM questions GROUP BY subject_code")
        }
        q = conn.execute(
            "SELECT count(*), sum(status <> 'PENDING_REVIEW'), sum(approved_revision_id IS NOT NULL "
            "OR approved_at IS NOT NULL), sum(lock_version <> 0) FROM questions"
        ).fetchone()
        facts["questions"] = int(q[0])
        facts["not_pending"] = int(q[1] or 0)
        facts["approved"] = int(q[2] or 0)
        facts["locked_changed"] = int(q[3] or 0)
        r = conn.execute(
            "SELECT count(*), sum(revision_no <> 1), sum(origin <> 'IMPORT') FROM question_revisions"
        ).fetchone()
        facts["revisions"] = int(r[0])
        facts["revisions_not_initial"] = int(r[1] or 0)
        facts["revisions_not_import"] = int(r[2] or 0)
        facts["events_not_import"] = int(
            conn.execute("SELECT count(*) FROM review_events WHERE action <> 'IMPORTED'").fetchone()[0]
        )
        facts["export_runs"] = int(conn.execute("SELECT count(*) FROM export_runs").fetchone()[0])
        for table in ("drafts", "draft_discards"):
            facts[table] = (
                int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) if table in tables else 0
            )
        return facts
    finally:
        conn.close()


def work_problems(facts: dict) -> list[str]:
    """Reviewer work found in a database that must be a pristine seed."""
    found = []
    for key, label in (
        ("not_pending", "questions not PENDING_REVIEW"),
        ("approved", "approved questions"),
        ("locked_changed", "questions changed after import"),
        ("revisions_not_initial", "revisions beyond the first"),
        ("revisions_not_import", "revisions not from the import"),
        ("events_not_import", "review events other than IMPORTED"),
        ("export_runs", "export records"),
        ("drafts", "drafts"),
        ("draft_discards", "discarded drafts"),
    ):
        if facts.get(key):
            found.append(f"{facts[key]} {label}")
    if facts.get("revisions") != facts.get("questions"):
        found.append(f"{facts.get('revisions')} revisions for {facts.get('questions')} questions")
    if facts.get("seed_meta_keys"):
        found.append("installation metadata present (a working copy, not a seed)")
    return found


def verify_seed(directory: Path, *, require_production: bool = False) -> VerifiedSeed:
    """Strictly verify a seed directory; raises :class:`SeedInvalid` with the first problem."""
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SeedInvalid(SeedProblem.MANIFEST_MISSING, str(manifest_path))
    data = manifest_path.read_bytes()
    manifest = parse_manifest(data)
    database = directory / manifest.database_file
    if not database.is_file():
        raise SeedInvalid(SeedProblem.DATABASE_MISSING, str(database))
    if database.stat().st_size != manifest.database_size or sha256_file(database) != manifest.database_sha256:
        raise SeedInvalid(SeedProblem.CHECKSUM_MISMATCH, f"{database.name} does not match its manifest")
    try:
        facts = inspect_database(database)
    except sqlite3.Error as exc:
        raise SeedInvalid(SeedProblem.INTEGRITY, f"cannot be read: {exc}") from exc
    if facts["application_id"] != SQLITE_APPLICATION_ID:
        raise SeedInvalid(SeedProblem.NOT_OUR_DATABASE, f"application_id {facts['application_id']:#010x}")
    if (
        facts["schema_version"] != manifest.schema_version
        or not 3 <= manifest.schema_version <= LATEST_VERSION
    ):
        raise SeedInvalid(
            SeedProblem.SCHEMA_MISMATCH,
            f"schema {facts['schema_version']} (manifest {manifest.schema_version}, "
            f"supported ≤ {LATEST_VERSION})",
        )
    if facts["schema_differences"]:
        raise SeedInvalid(SeedProblem.SCHEMA_MISMATCH, "; ".join(facts["schema_differences"]))
    if facts["integrity_check"] != "ok" or facts["foreign_key_check"] != "ok":
        raise SeedInvalid(SeedProblem.INTEGRITY, f"{facts['integrity_check']}; {facts['foreign_key_check']}")
    if facts.get("database_id") != manifest.seed_database_id:
        raise SeedInvalid(SeedProblem.CONTENT_MISMATCH, "database id differs from the manifest")
    if facts.get("subjects") != manifest.subjects or facts.get("questions") != manifest.question_count:
        raise SeedInvalid(SeedProblem.CONTENT_MISMATCH, "question counts differ from the manifest")
    work = work_problems(facts)
    if work:
        raise SeedInvalid(SeedProblem.CONTAINS_WORK, "; ".join(work))
    if manifest.is_production:
        failures = production_gate_failures(manifest.subjects)
        if failures:
            raise SeedInvalid(
                SeedProblem.CONTENT_MISMATCH, "PRODUCTION seed fails its gates: " + "; ".join(failures)
            )
    elif require_production:
        raise SeedInvalid(SeedProblem.NOT_PRODUCTION, f"{manifest.kind.value} seed {manifest.seed_version}")
    return VerifiedSeed(directory, manifest, hashlib.sha256(data).hexdigest())


# ----------------------------------------------------------------------------------- locating
def bundled_seed_directory() -> Path | None:
    """The seed shipped inside the package (``vascqbank_desktop/resources/seed``), if any."""
    from vascqbank_desktop import resources

    try:
        folder = resources.resource_path("seed")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None
    return folder if (folder / MANIFEST_NAME).is_file() else None


def locate_seed(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """``--seed-dir`` beats ``VASCQBANK_SEED_DIR`` beats the bundled package data."""
    if explicit:
        return Path(explicit)
    env = os.environ.get(SEED_DIR_ENV)
    if env:
        return Path(env)
    return bundled_seed_directory()


# ----------------------------------------------------------------------------------- installation
def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def cleanup_interrupted_installs(paths: AppPaths) -> list[Path]:
    """Remove temporary files of an interrupted installation (never a published database)."""
    removed: list[Path] = []
    if not paths.database_dir.is_dir():
        return removed
    for candidate in paths.database_dir.iterdir():
        if (
            candidate.name.startswith(INSTALL_PREFIX)
            and INSTALL_SUFFIX in candidate.name
            and candidate.is_file()
        ):
            candidate.unlink()
            removed.append(candidate)
    return removed


def _publish_no_overwrite(temp: Path, final: Path) -> None:
    """Make *temp* appear as *final* only if *final* does not exist (never replaces a database)."""
    try:
        os.link(temp, final)  # atomic, fails with FileExistsError if final exists
    except FileExistsError:
        raise
    except (OSError, NotImplementedError, AttributeError):
        if final.exists():  # file systems without hard links: rename refuses to replace on Windows
            raise FileExistsError(str(final)) from None
        os.rename(temp, final)
        return
    temp.unlink()


def install_seed(paths: AppPaths, seed: VerifiedSeed) -> SeedInstallResult:
    """Create the working database from a verified seed (see the module docstring)."""
    started = time.perf_counter()
    paths.ensure_directories()
    final = paths.database_file
    if final.exists():
        raise DatabaseAlreadyExists(str(final))
    temp = paths.database_dir / f"{INSTALL_PREFIX}{uuid.uuid4().hex}{INSTALL_SUFFIX}"
    manifest = seed.manifest
    try:
        digest = hashlib.sha256()
        with seed.database_path.open("rb") as source, temp.open("xb") as target:
            for chunk in iter(lambda: source.read(1 << 20), b""):
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        _checkpoint("copied")
        if digest.hexdigest() != manifest.database_sha256:
            raise SeedInvalid(SeedProblem.CHECKSUM_MISMATCH, "the copy differs from the verified seed")
        new_id = str(uuid.uuid4())
        installed_at = utc_now_iso()
        conn = sqlite3.connect(temp, isolation_level=None)
        try:
            if int(conn.execute("PRAGMA application_id").fetchone()[0]) != SQLITE_APPLICATION_ID:
                raise SeedInvalid(SeedProblem.NOT_OUR_DATABASE, "copied file")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE app_metadata SET value = ? WHERE key = 'database_id'", (new_id,))
            conn.executemany(
                "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                [
                    (META_SEED_DATABASE_ID, manifest.seed_database_id),
                    (META_SEED_VERSION, manifest.seed_version),
                    (META_SEED_KIND, manifest.kind.value),
                    (META_SEED_INSTALLED_AT, installed_at),
                    (META_SEED_MANIFEST_HASH, seed.manifest_sha256),
                    ("installed_by_app_version", __version__),
                ],
            )
            conn.execute("COMMIT")
            _checkpoint("provenance")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise SeedInvalid(SeedProblem.INTEGRITY, "the installed copy fails quick_check")
        finally:
            conn.close()
        # "rb+", not "rb": on Windows fsync (FlushFileBuffers) needs a handle opened for writing.
        with temp.open("rb+") as handle:
            os.fsync(handle.fileno())
        _checkpoint("before-publish")
        _publish_no_overwrite(temp, final)
        _fsync_dir(paths.database_dir)
        _checkpoint("published")
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    # the derived search index belongs to whatever database was there before: rebuild it
    for derived in (
        paths.search_index_file,
        *(Path(f"{paths.search_index_file}{s}") for s in ("-wal", "-shm")),
    ):
        derived.unlink(missing_ok=True)
    log.info(
        "Installed %s seed %s (%s questions) as database %s",
        manifest.kind.value, manifest.seed_version, manifest.question_count, new_id,
    )  # fmt: skip
    return SeedInstallResult(new_id, manifest.seed_database_id, manifest.seed_version, manifest.kind,
                             time.perf_counter() - started)  # fmt: skip


def copy_seed(seed: VerifiedSeed, target: Path) -> Path:
    """Copy a verified seed (manifest + database) into *target* (for packaging), refusing to overwrite."""
    target.mkdir(parents=True, exist_ok=True)
    for name in (MANIFEST_NAME, seed.manifest.database_file):
        if (target / name).exists():
            raise FileExistsError(str(target / name))
    shutil.copy2(seed.database_path, target / seed.manifest.database_file)
    shutil.copy2(seed.directory / MANIFEST_NAME, target / MANIFEST_NAME)
    return target
