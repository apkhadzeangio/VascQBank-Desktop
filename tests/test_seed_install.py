"""Seed verification, first-launch installation, start-up decisions, recovery evidence, crashes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from seedbank import reseal_database, rewrite_manifest, small_seed, writable

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.services import backup_service
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.seed_service import (
    INSTALL_PREFIX,
    SEED_META_KEYS,
    DatabaseAlreadyExists,
    SeedInvalid,
    SeedProblem,
    install_seed,
    sha256_file,
    verify_seed,
)
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore
from vascqbank_desktop.services.startup import EvidenceKind, Outcome, prepare_storage
from vascqbank_desktop.services.storage import initialize_storage

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions and SIGKILL")


@pytest.fixture(scope="module")
def seed_template(tmp_path_factory) -> Path:
    return small_seed(tmp_path_factory.mktemp("seed"))


@pytest.fixture
def seed(seed_template: Path, tmp_path: Path) -> Path:
    """A fresh, writable copy for tests that tamper with it."""
    copy = tmp_path / "seed"
    shutil.copytree(seed_template, copy)
    for path in copy.iterdir():
        writable(path)
    return copy


def meta(paths: AppPaths) -> dict[str, str]:
    conn = sqlite3.connect(paths.database_file)
    try:
        return dict(conn.execute("SELECT key, value FROM app_metadata").fetchall())
    finally:
        conn.close()


def fingerprint(folder: Path) -> dict[str, tuple[str, float]]:
    return {p.name: (sha256_file(p), p.stat().st_mtime) for p in folder.iterdir()}


# ------------------------------------------------------------------ verification


def test_verification_never_writes_to_the_seed(seed_template: Path) -> None:
    before = fingerprint(seed_template)
    verify_seed(seed_template)
    verify_seed(seed_template)
    assert fingerprint(seed_template) == before
    assert sorted(p.name for p in seed_template.iterdir()) == ["seed-manifest.json", "vascqbank-seed.sqlite3"]


def test_a_single_changed_byte_fails_the_checksum(seed: Path) -> None:
    database = seed / "vascqbank-seed.sqlite3"
    data = bytearray(database.read_bytes())
    data[-100] ^= 0x01
    database.write_bytes(bytes(data))
    with pytest.raises(SeedInvalid) as info:
        verify_seed(seed)
    assert info.value.problem is SeedProblem.CHECKSUM_MISMATCH


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda s: (s / "seed-manifest.json").unlink(), SeedProblem.MANIFEST_MISSING),
        (
            lambda s: (s / "seed-manifest.json").write_text("{not json", encoding="utf-8"),
            SeedProblem.MANIFEST_INVALID,
        ),
        (lambda s: rewrite_manifest(s, manifest_format=2), SeedProblem.MANIFEST_INVALID),
        (lambda s: rewrite_manifest(s, kind="FINAL"), SeedProblem.MANIFEST_INVALID),
        (lambda s: rewrite_manifest(s, question_count=27), SeedProblem.MANIFEST_INVALID),
        (
            lambda s: rewrite_manifest(s, subjects={"ALI": 21, "VENOUS": 6}, question_count=27),
            SeedProblem.CONTENT_MISMATCH,
        ),
        (lambda s: rewrite_manifest(s, seed_database_id="other"), SeedProblem.CONTENT_MISMATCH),
        (lambda s: rewrite_manifest(s, schema_version=99), SeedProblem.SCHEMA_MISMATCH),
        (lambda s: rewrite_manifest(s, kind="PRODUCTION"), SeedProblem.CONTENT_MISMATCH),
        (lambda s: (s / "vascqbank-seed.sqlite3").unlink(), SeedProblem.DATABASE_MISSING),
    ],
)
def test_a_manifest_is_never_trusted_merely_because_it_exists(seed: Path, change, problem) -> None:
    change(seed)
    with pytest.raises(SeedInvalid) as info:
        verify_seed(seed)
    assert info.value.problem is problem


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE questions SET status = 'IN_REVIEW' WHERE id = 1",
        "INSERT INTO app_metadata (key, value) VALUES ('seed_version', 'x')",
    ],
)
def test_a_seed_with_reviewer_work_is_refused_even_with_a_matching_checksum(seed: Path, sql: str) -> None:
    conn = sqlite3.connect(seed / "vascqbank-seed.sqlite3")
    conn.execute(sql)
    conn.commit()
    conn.close()
    reseal_database(seed)
    with pytest.raises(SeedInvalid) as info:
        verify_seed(seed)
    assert info.value.problem is SeedProblem.CONTAINS_WORK


def test_a_foreign_sqlite_file_is_refused(seed: Path) -> None:
    database = seed / "vascqbank-seed.sqlite3"
    database.unlink()
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE x (y)")
    conn.commit()
    conn.close()
    reseal_database(seed)
    with pytest.raises(SeedInvalid) as info:
        verify_seed(seed)
    assert info.value.problem is SeedProblem.NOT_OUR_DATABASE


# ------------------------------------------------------------------ installation


def test_first_launch_installs_the_seed_with_a_fresh_identity(
    seed_template: Path, app_paths: AppPaths
) -> None:
    before = fingerprint(seed_template)
    result = prepare_storage(app_paths, seed_template)
    assert result.outcome is Outcome.READY and result.seed_install is not None
    status = result.status
    seed = verify_seed(seed_template)
    data = meta(app_paths)
    assert data["database_id"] != seed.manifest.seed_database_id == data["seed_database_id"]
    assert data["database_id"] == status.database_id == result.seed_install.database_id
    assert data["seed_version"] == "dev-1" and data["seed_kind"] == "DEVELOPMENT"
    assert (
        data["seed_manifest_hash"]
        == hashlib.sha256((seed_template / "seed-manifest.json").read_bytes()).hexdigest()
    )
    assert data["seed_installed_at"] and status.from_development_seed and status.seed_version == "dev-1"
    catalog = CatalogService(app_paths)
    assert catalog.count() == 26 and catalog.count(status=None) == 26
    conn = sqlite3.connect(app_paths.database_file)
    assert conn.execute("SELECT DISTINCT status FROM questions").fetchall() == [("PENDING_REVIEW",)]
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"  # a normal working database now
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    assert os.access(app_paths.database_file, os.W_OK)
    assert fingerprint(seed_template) == before  # the bundled seed is untouched
    assert not [p for p in app_paths.database_dir.iterdir() if p.name.startswith(INSTALL_PREFIX)]


def test_two_installations_get_different_database_ids(seed_template: Path, tmp_path: Path) -> None:
    ids = set()
    for name in ("one", "two"):
        paths = AppPaths(data_dir=tmp_path / name)
        ids.add(prepare_storage(paths, seed_template).status.database_id)
    assert len(ids) == 2


def test_later_launches_never_install_again(seed_template: Path, app_paths: AppPaths) -> None:
    first = prepare_storage(app_paths, seed_template)
    stamp = app_paths.database_file.stat().st_ino
    for _ in range(3):
        again = prepare_storage(app_paths, seed_template)
        assert again.outcome is Outcome.READY and again.seed_install is None
        assert again.status.database_id == first.status.database_id
    assert app_paths.database_file.stat().st_ino == stamp  # the same file, never replaced


def test_an_update_with_a_different_seed_never_touches_existing_work(
    seed_template: Path, app_paths: AppPaths, tmp_path: Path
) -> None:
    prepare_storage(app_paths, seed_template)
    profile = ReviewerProfile(SettingsStore(app_paths.settings_file))
    profile.set_name("რამაზ აფხაძე")
    catalog = CatalogService(app_paths)
    d = catalog.detail_by_uid("ALI-0003")
    EditService(app_paths, profile).save(
        d.id, d.lock_version, QuestionContent("რეცენზენტის ტექსტი", d.content.options, d.content.correct, "e")
    )
    d = catalog.detail_by_uid("ALI-0003")
    ReviewService(app_paths, profile).approve(d.id, d.lock_version)
    before = sha256_file(app_paths.database_file)
    newer = small_seed(tmp_path, version="dev-2")  # the "next release" ships another seed
    result = prepare_storage(app_paths, newer)
    assert result.seed_install is None and meta(app_paths)["seed_version"] == "dev-1"
    detail = CatalogService(app_paths).detail_by_uid("ALI-0003")
    assert detail.content.stem == "რეცენზენტის ტექსტი" and detail.status.value == "APPROVED"
    assert before  # (the WAL may have been checkpointed; the content checks above are what matter)


def test_a_database_from_before_phase_7_is_used_as_it_is(seed_template: Path, app_paths: AppPaths) -> None:
    from bank import make_question
    from bank import seed as seed_questions

    seed_questions(app_paths, [make_question("ALI", n) for n in range(1, 4)])
    result = prepare_storage(app_paths, seed_template)
    assert result.outcome is Outcome.READY and result.seed_install is None
    assert result.status.seed_version is None and not result.status.from_development_seed
    assert not [k for k in meta(app_paths) if k in SEED_META_KEYS]
    assert CatalogService(app_paths).count() == 3


def test_without_a_seed_an_empty_database_is_created_as_before(app_paths: AppPaths) -> None:
    result = prepare_storage(app_paths, None)
    assert result.outcome is Outcome.READY and result.status.created_new and result.seed_install is None
    assert CatalogService(app_paths).count() == 0


def test_install_refuses_to_overwrite_a_database(seed_template: Path, app_paths: AppPaths) -> None:
    initialize_storage(app_paths)
    before = sha256_file(app_paths.database_file)
    with pytest.raises(DatabaseAlreadyExists):
        install_seed(app_paths, verify_seed(seed_template))
    assert sha256_file(app_paths.database_file) == before


def test_an_invalid_seed_creates_nothing(seed: Path, app_paths: AppPaths, seed_template: Path) -> None:
    rewrite_manifest(seed, seed_database_id="tampered")
    result = prepare_storage(app_paths, seed)
    assert (
        result.outcome is Outcome.SEED_INVALID and result.seed_problem.problem is SeedProblem.CONTENT_MISMATCH
    )
    assert not app_paths.database_file.exists()  # a corrected application can still install later
    assert prepare_storage(app_paths, seed_template).seed_install is not None


# ------------------------------------------------------------------ a missing database with traces


def _make_evidence(kind: EvidenceKind, paths: AppPaths, seed_template: Path) -> None:
    if kind is EvidenceKind.BACKUPS:
        prepare_storage(paths, seed_template)
        backup_service.create_manual_backup(paths)
        paths.database_file.unlink()
        for suffix in ("-wal", "-shm"):
            Path(f"{paths.database_file}{suffix}").unlink(missing_ok=True)
    elif kind is EvidenceKind.DATABASE_SIDE_FILES:
        paths.database_dir.mkdir(parents=True, exist_ok=True)
        Path(f"{paths.database_file}-wal").write_bytes(b"\0" * 32)
    elif kind is EvidenceKind.SEARCH_INDEX:
        paths.database_dir.mkdir(parents=True, exist_ok=True)
        paths.search_index_file.write_bytes(b"index")
    elif kind is EvidenceKind.EXPORTS:
        (paths.exports_dir / "VascQBank_Export_20261118-000000-abcdef").mkdir(parents=True)
    elif kind is EvidenceKind.DISPLACED:  # a database set aside by a restore (Phase 8)
        displaced = paths.data_dir / "displaced"
        displaced.mkdir(parents=True)
        (displaced / "vascqbank-20261118-000000-000000-displaced.sqlite3").write_bytes(b"db")
    else:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        paths.settings_file.write_text('{"reviewer_name": "x"}', encoding="utf-8")


@pytest.mark.parametrize("kind", list(EvidenceKind))
def test_a_missing_database_with_traces_is_never_silently_replaced(
    kind: EvidenceKind, seed_template: Path, app_paths: AppPaths
) -> None:
    _make_evidence(kind, app_paths, seed_template)
    snapshot = {
        p: sha256_file(p) for p in app_paths.data_dir.rglob("*") if p.is_file() and "logs" not in p.parts
    }
    result = prepare_storage(app_paths, seed_template)
    assert result.outcome is Outcome.RECOVERY_NEEDED and [e.kind for e in result.evidence] == [kind]
    assert not app_paths.database_file.exists()  # nothing created …
    assert {p: sha256_file(p) for p in snapshot} == snapshot  # … and every trace untouched
    with_seed = prepare_storage(app_paths, None)  # the same without a seed
    assert with_seed.outcome is Outcome.RECOVERY_NEEDED and not app_paths.database_file.exists()


def test_an_explicit_decision_creates_a_new_database_and_keeps_the_backups(
    seed_template: Path, app_paths: AppPaths
) -> None:
    _make_evidence(EvidenceKind.BACKUPS, app_paths, seed_template)
    backups = {p: sha256_file(p) for p in app_paths.backups_dir.iterdir()}
    result = prepare_storage(app_paths, seed_template, allow_new_database=True)
    assert result.outcome is Outcome.READY and result.seed_install is not None
    assert {p: sha256_file(p) for p in app_paths.backups_dir.iterdir() if p in backups} == backups


# ------------------------------------------------------------------ interrupted installation (SIGKILL)

CHILD = textwrap.dedent(
    """
    import os, signal, sys
    from pathlib import Path
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.services import seed_service
    from vascqbank_desktop.services.startup import prepare_storage

    stage = sys.argv[3]
    def checkpoint(name):
        if name == stage:
            os.kill(os.getpid(), signal.SIGKILL)
    seed_service._checkpoint = checkpoint
    prepare_storage(AppPaths(data_dir=Path(sys.argv[1])), Path(sys.argv[2]))
    print("SURVIVED", flush=True)
    """
)


@pytest.mark.parametrize("stage", ["copied", "provenance", "before-publish", "published"])
def test_power_loss_during_installation_is_recovered_on_the_next_start(
    stage: str, seed_template: Path, app_paths: AppPaths
) -> None:
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(app_paths.data_dir), str(seed_template), stage],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert child.returncode == -signal.SIGKILL and "SURVIVED" not in child.stdout, child.stderr
    published = app_paths.database_file.exists()
    assert published is (stage == "published")  # never a half-installed database under the real name
    result = prepare_storage(app_paths, seed_template)  # the next start
    assert result.outcome is Outcome.READY
    assert (result.seed_install is not None) is (not published)
    assert not [p for p in app_paths.database_dir.iterdir() if p.name.startswith(INSTALL_PREFIX)]
    conn = sqlite3.connect(app_paths.database_file)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT count(*) FROM questions").fetchone()[0] == 26
        keys = dict(conn.execute("SELECT key, value FROM app_metadata").fetchall())
        assert all(key in keys for key in SEED_META_KEYS)
    finally:
        conn.close()
    assert verify_seed(seed_template)  # the seed itself survived every interruption


# ------------------------------------------------------------------ provenance in exports


def _approve_and_export(paths: AppPaths, dest: Path) -> dict:
    profile = ReviewerProfile(SettingsStore(paths.settings_file))
    profile.set_name("რამაზ აფხაძე")
    d = CatalogService(paths).detail_by_uid("ALI-0001")
    ReviewService(paths, profile).approve(d.id, d.lock_version)
    result = ExportService(paths).run(
        ExportRequest(subjects=("ALI",), formats=frozenset({ExportFormat.TXT}), destination=dest)
    )
    return json.loads((result.folder / "export-manifest.json").read_text(encoding="utf-8"))


def test_exports_carry_the_seed_provenance(seed_template: Path, app_paths: AppPaths, tmp_path: Path) -> None:
    prepare_storage(app_paths, seed_template)
    manifest = _approve_and_export(app_paths, tmp_path / "out")
    data = meta(app_paths)
    assert manifest["database_id"] == data["database_id"]
    assert manifest["seed"] == {key: data[key] for key in SEED_META_KEYS}
    assert manifest["seed"]["seed_database_id"] == verify_seed(seed_template).manifest.seed_database_id


def test_exports_of_a_database_without_a_seed_say_so(app_paths: AppPaths, tmp_path: Path) -> None:
    from bank import make_question
    from bank import seed as seed_questions

    seed_questions(app_paths, [make_question("ALI", n) for n in range(1, 3)])
    assert _approve_and_export(app_paths, tmp_path / "out")["seed"] is None


def test_open_database_on_the_installed_file_keeps_the_provenance(
    seed_template: Path, app_paths: AppPaths
) -> None:
    prepare_storage(app_paths, seed_template)
    conn = open_database(app_paths.database_file)
    conn.close()
    assert meta(app_paths)["seed_version"] == "dev-1"
