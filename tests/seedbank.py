"""Synthetic seeds for the Phase 7 tests (artificial filler text only, never real questions)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from synthetic import question_block, write_full_bank, write_pair

from vascqbank_desktop.services.seed_builder import build_seed
from vascqbank_desktop.services.seed_service import MANIFEST_NAME, SeedKind, sha256_file

SMALL = {"ALI": range(1, 21), "VENOUS": (1, 2, 3, 5, 8, 13)}


def small_sources(folder: Path) -> Path:
    for code, numbers in SMALL.items():
        write_pair(folder, code, numbers)
    return folder


def small_seed(root: Path, version: str = "dev-1") -> Path:
    """A DEVELOPMENT seed with 26 synthetic questions in two subjects."""
    return build_seed(small_sources(root / f"src-{version}"), root / f"seed-{version}",
                      kind=SeedKind.DEVELOPMENT, seed_version=version).directory  # fmt: skip


def full_sources(folder: Path) -> Path:
    write_full_bank(folder)
    return folder


def rewrite_manifest(seed: Path, **changes) -> None:
    """Edit a manifest field (tests of tampering)."""
    path = seed / MANIFEST_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def reseal_database(seed: Path) -> None:
    """After a deliberate edit of the seed database, make its manifest hash match again — so the
    content checks (not the checksum) are what must catch the edit."""
    database = seed / "vascqbank-seed.sqlite3"
    path = seed / MANIFEST_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["database"]["sha256"] = sha256_file(database)
    data["database"]["size"] = database.stat().st_size
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def writable(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def duplicate_numbers_text(code: str) -> str:
    return "\n\n".join(question_block(code, n) for n in (1, 2, 2, 3)) + "\n"
