"""tools/build_seed.py and its service: the existing import pipeline, gates, manifest, artefact."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from seedbank import duplicate_numbers_text, full_sources, small_seed, small_sources
from synthetic import write_pair

from vascqbank_desktop import __version__
from vascqbank_desktop.config.constants import SQLITE_APPLICATION_ID
from vascqbank_desktop.db.migrations import LATEST_VERSION
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.services.seed_builder import SeedBuildFailed, build_seed
from vascqbank_desktop.services.seed_service import (
    MANIFEST_NAME,
    SeedInvalid,
    SeedKind,
    SeedProblem,
    sha256_file,
    verify_seed,
)

ROOT = Path(__file__).resolve().parent.parent


def test_a_development_seed_is_built_through_the_import_pipeline(tmp_path: Path) -> None:
    seed = small_seed(tmp_path)
    manifest = json.loads((seed / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["manifest_format"] == 1 and manifest["kind"] == "DEVELOPMENT"
    assert manifest["seed_version"] == "dev-1" and manifest["app_version"] == __version__
    assert manifest["schema_version"] == LATEST_VERSION and manifest["question_count"] == 26
    assert manifest["subjects"] == {"ALI": 20, "VENOUS": 6}
    assert manifest["verification"] == {
        "integrity_check": "ok",
        "foreign_key_check": "ok",
        "all_pending_review": True,
        "one_import_revision_per_question": True,
        "approvals": 0,
        "drafts": 0,
        "export_runs": 0,
        "warnings": manifest["verification"]["warnings"],
    }
    assert manifest["production_gates"]["passed"] is False
    sources = {f["name"]: f["sha256"] for f in manifest["source_files"]}
    assert sources == {
        p.name: sha256_file(p) for p in (tmp_path / "src-dev-1").iterdir()
    }  # every source file is fingerprinted
    database = seed / manifest["database"]["file"]
    assert sha256_file(database) == manifest["database"]["sha256"]
    assert not (database.stat().st_mode & 0o222)  # the artefact is read-only
    assert sorted(p.name for p in seed.iterdir()) == [
        "seed-manifest.json",
        "vascqbank-seed.sqlite3",
    ]  # no -wal
    conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == SQLITE_APPLICATION_ID
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        rows = conn.execute("SELECT status, approved_revision_id, lock_version FROM questions").fetchall()
        assert len(rows) == 26 and set(rows) == {("PENDING_REVIEW", None, 0)}
        assert conn.execute("SELECT count(*), max(revision_no) FROM question_revisions").fetchone() == (26, 1)
        assert conn.execute("SELECT DISTINCT action FROM review_events").fetchall() == [("IMPORTED",)]
        assert (
            conn.execute("SELECT count(*) FROM questions WHERE original_content_json IS NULL").fetchone()[0]
            == 0
        )
        meta = dict(conn.execute("SELECT key, value FROM app_metadata").fetchall())
        assert meta["database_id"] == manifest["seed_database_id"]
        assert not [k for k in meta if k.startswith("seed_")]  # provenance belongs to installations only
    finally:
        conn.close()
    assert verify_seed(seed).manifest.question_count == 26


def test_a_partial_bank_is_never_a_production_seed(tmp_path: Path) -> None:
    with pytest.raises(SeedBuildFailed) as info:
        build_seed(
            small_sources(tmp_path / "src"), tmp_path / "out", kind=SeedKind.PRODUCTION, seed_version="1"
        )
    assert any("missing subjects" in p for p in info.value.problems)
    assert any("26 questions in total, expected 10000" in p for p in info.value.problems)
    assert not (tmp_path / "out").exists()
    with pytest.raises(SeedInvalid) as refused:
        verify_seed(small_seed(tmp_path), require_production=True)
    assert refused.value.problem is SeedProblem.NOT_PRODUCTION


def test_the_complete_synthetic_bank_passes_every_production_gate(tmp_path: Path) -> None:
    result = build_seed(full_sources(tmp_path / "src"), tmp_path / "seed", kind=SeedKind.PRODUCTION,
                        seed_version="synthetic-full")  # fmt: skip
    manifest = result.seed.manifest
    assert manifest.kind is SeedKind.PRODUCTION and manifest.question_count == 10_000
    assert manifest.subjects == {s.code: s.target_count for s in SUBJECTS}
    assert manifest.raw["production_gates"] == {"passed": True, "failures": []}
    assert verify_seed(result.directory, require_production=True).manifest.question_count == 10_000


def test_one_question_short_fails_the_exact_target_gate(tmp_path: Path) -> None:
    source = full_sources(tmp_path / "src")
    write_pair(source, "ALI", range(1, 900))  # 899 of 900
    with pytest.raises(SeedBuildFailed) as info:
        build_seed(source, tmp_path / "seed", kind=SeedKind.PRODUCTION, seed_version="1")
    assert "ALI: 899 questions, expected 900" in info.value.problems
    assert "9999 questions in total, expected 10000" in info.value.problems


def test_a_missing_subject_file_blocks_every_seed(tmp_path: Path) -> None:
    source = small_sources(tmp_path / "src")
    (source / "VENOUS_explanations.txt").unlink()
    with pytest.raises(SeedBuildFailed) as info:
        build_seed(source, tmp_path / "seed", kind=SeedKind.DEVELOPMENT, seed_version="1")
    assert any("MISSING_EXPLANATIONS_FILE" in p for p in info.value.problems)


def test_duplicate_ids_block_every_seed(tmp_path: Path) -> None:
    source = small_sources(tmp_path / "src")
    write_pair(source, "ALI", (1, 2, 3), questions_text=duplicate_numbers_text("ALI"))
    with pytest.raises(SeedBuildFailed) as info:
        build_seed(source, tmp_path / "seed", kind=SeedKind.DEVELOPMENT, seed_version="1")
    assert any("DUPLICATE_NUMBER" in p for p in info.value.problems)


def test_an_invalid_source_file_blocks_every_seed(tmp_path: Path) -> None:
    source = small_sources(tmp_path / "src")
    (source / "ALI_questions.txt").write_bytes(b"1. \xff\xfe broken\n")
    with pytest.raises(SeedBuildFailed) as info:
        build_seed(source, tmp_path / "seed", kind=SeedKind.DEVELOPMENT, seed_version="1")
    assert any("FILE_NOT_UTF8" in p for p in info.value.problems)


def test_an_existing_artefact_is_never_overwritten(tmp_path: Path) -> None:
    seed = small_seed(tmp_path)
    before = sha256_file(seed / "vascqbank-seed.sqlite3")
    with pytest.raises(SeedBuildFailed, match="never overwritten"):
        build_seed(small_sources(tmp_path / "again"), seed, kind=SeedKind.DEVELOPMENT, seed_version="2")
    assert sha256_file(seed / "vascqbank-seed.sqlite3") == before


def test_the_command_line_tools(tmp_path: Path) -> None:
    source = small_sources(tmp_path / "src")
    built = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_seed.py"), str(source), str(tmp_path / "seed"),
         "--version", "dev-9"],
        capture_output=True, text=True, encoding="utf-8",
    )  # fmt: skip
    assert built.returncode == 0 and "DEVELOPMENT seed dev-9: 26 questions" in built.stdout, built.stderr
    refused = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_seed.py"), str(source), str(tmp_path / "prod"),
         "--version", "1", "--production"],
        capture_output=True, text=True, encoding="utf-8",
    )  # fmt: skip
    assert refused.returncode == 1 and "SEED NOT BUILT (PRODUCTION)" in refused.stderr
    target = tmp_path / "package"
    tool = str(ROOT / "tools" / "prepare_package_seed.py")
    no = subprocess.run([sys.executable, tool, str(tmp_path / "seed"), "--target", str(target)],
                        capture_output=True, text=True, encoding="utf-8")  # fmt: skip
    assert no.returncode == 1 and "NOT_PRODUCTION" in no.stderr and not target.exists()
    yes = subprocess.run(
        [sys.executable, tool, str(tmp_path / "seed"), "--target", str(target), "--allow-development-seed"],
        capture_output=True, text=True, encoding="utf-8",
    )  # fmt: skip
    assert yes.returncode == 0 and verify_seed(target).manifest.seed_version == "dev-9"
    again = subprocess.run(
        [sys.executable, tool, str(tmp_path / "seed"), "--target", str(target), "--allow-development-seed"],
        capture_output=True, text=True, encoding="utf-8",
    )  # fmt: skip
    assert again.returncode == 1  # an existing packaged seed is never overwritten


def test_the_repository_carries_no_seed() -> None:
    folder = ROOT / "src" / "vascqbank_desktop" / "resources" / "seed"
    tracked = subprocess.run(
        ["git", "ls-files", str(folder)], capture_output=True, text=True, cwd=ROOT
    ).stdout.split()
    assert [Path(p).name for p in tracked] in ([], ["README.md"])
    ignored = subprocess.run(["git", "check-ignore", "-q", str(folder / "vascqbank-seed.sqlite3")], cwd=ROOT)
    assert ignored.returncode == 0  # a seed placed there for packaging cannot be committed by accident
