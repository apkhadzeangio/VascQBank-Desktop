"""Phase 9b — Hypothesis fuzzing of the readers Phase 9 did not reach.

Backup file names (reason, kind, time), the refined-master parser, import discovery over arbitrary
file names, the schema comparison over arbitrary SQLite schemas, and oversized settings files.
Deterministic and database-free (``derandomize=True``, ``database=None``). Synthetic text only.
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from vascqbank_desktop.db import backup as backup_db  # noqa: E402
from vascqbank_desktop.db.schema_check import schema_differences  # noqa: E402
from vascqbank_desktop.importexport.discovery import discover  # noqa: E402
from vascqbank_desktop.importexport.refined_master import parse_master  # noqa: E402
from vascqbank_desktop.services import backup_service  # noqa: E402
from vascqbank_desktop.services.settings_service import (  # noqa: E402
    MAX_SETTINGS_BYTES,
    AppSettings,
    SettingsStore,
)

FUZZ = settings(
    derandomize=True,
    deadline=None,
    max_examples=250,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    database=None,
)
NAME_ALPHABET = "vascqbank-0123456789abcdefghijklmnopqrstuvwxyz._-ა ს‮​VASCQBANK"
GEORGIAN = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"


# ----------------------------------------------------------------------------------- backup names
@FUZZ
@given(st.one_of(st.text(alphabet=NAME_ALPHABET, max_size=80), st.text(max_size=80)))
def test_backup_name_readers_are_total(name: str) -> None:
    path = Path("backups") / (name.replace("/", "_").replace("\x00", "_") or "x")
    reason = backup_db.backup_reason(path)
    assert reason is None or (reason and set(reason) <= set("abcdefghijklmnopqrstuvwxyz0123456789-"))
    backup_service._kind(reason or "")  # never raises
    created = backup_service._created_from_name(path.name)
    assert created is None or isinstance(created, str)


@FUZZ
@given(st.text(alphabet="vascqbank-0123456789-preimotgraulnx.sql3", min_size=10, max_size=70))
def test_only_exact_automatic_names_are_ever_pruneable(name: str) -> None:
    pruneable = backup_db._is_pruneable(Path(name), backup_db.AUTOMATIC_REASONS + (backup_db.DAILY_REASON,))
    if pruneable:
        reason = backup_db.backup_reason(Path(name))
        assert reason in ("pre-import", "pre-migration", "automatic") or (
            reason is not None and reason.startswith("pre-migration-v")
        )


# ----------------------------------------------------------------------------------- master file
master_text = st.text(
    alphabet=GEORGIAN + "abcxyzQIDSTATUSANSWERCORRECT0123456789:=#-*.,;()[]\t \r\n\x00﻿ა́",
    max_size=1500,
)


@FUZZ
@given(master_text)
def test_master_parser_never_raises(text: str) -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "REFINED_MASTER_QBANK.txt"
        path.write_text(text, encoding="utf-8", newline="")
        result = parse_master(path)
    for item in result.items:
        if item.is_valid:
            assert item.question is not None and item.qid is not None


@FUZZ
@given(st.binary(max_size=800))
def test_master_parser_on_arbitrary_bytes(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "REFINED_MASTER_QBANK.txt"
        path.write_bytes(data)
        parse_master(path)


# ----------------------------------------------------------------------------------- discovery
file_name = st.text(
    alphabet="ALIVENOUSCAROTID_questionsexplanationsREFINEDMASTERQBANK.txt-ა ‮", min_size=1, max_size=40
).filter(lambda n: n not in (".", "..") and "/" not in n)


@FUZZ
@given(st.lists(file_name, max_size=8, unique=True))
def test_discovery_of_any_file_names_never_raises(names: list[str]) -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for name in names:
            try:
                (root / name).write_text("1. x\nა) y\n", encoding="utf-8")
            except OSError:  # a name this file system refuses
                continue
        result = discover([root])
        for source in result.sources:
            source.parse()


# ----------------------------------------------------------------------------------- schema comparison
schema_statement = st.sampled_from(
    [
        "CREATE TABLE t{n} (x TEXT)",
        "CREATE TABLE t{n} (x INTEGER PRIMARY KEY, y TEXT CHECK (length(y) < 9)) STRICT",
        "CREATE INDEX i{n} ON base (v)",
        "CREATE VIEW v{n} AS SELECT v FROM base",
        "CREATE TRIGGER g{n} AFTER INSERT ON base BEGIN SELECT 1; END",
        "CREATE TABLE app_metadata{n} (key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID",
    ]
)


@FUZZ
@given(st.lists(schema_statement, max_size=6), st.integers(min_value=-3, max_value=6))
def test_schema_comparison_is_total_and_flags_every_foreign_object(
    statements: list[str], version: int
) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE base (v TEXT)")
        for n, statement in enumerate(statements):
            conn.execute(statement.format(n=n))
        problems = schema_differences(conn, version, limit=0)
        assert problems  # never "identical": 'base' alone is not our schema
    finally:
        conn.close()


# ----------------------------------------------------------------------------------- settings size
def test_an_oversized_settings_file_is_set_aside_not_parsed(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    filler = "x" * 1000
    payload = (
        '{"reviewer_name": "ნინო", "giant": ['
        + ",".join(f'"{filler}"' for _ in range(MAX_SETTINGS_BYTES // 900))
        + "]}"
    )
    path.write_text(payload, encoding="utf-8")
    assert path.stat().st_size > MAX_SETTINGS_BYTES
    store = SettingsStore(path)
    started = time.perf_counter()
    assert store.load() == AppSettings()
    assert time.perf_counter() - started < 2
    assert not path.exists() and list(tmp_path.glob("settings.json.corrupt-*"))  # kept aside, not deleted
    store.set_reviewer_name("ნინო")
    assert path.stat().st_size < 10_000  # the giant unknown key is not carried forward
