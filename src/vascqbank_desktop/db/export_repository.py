"""Append-only record of published exports (``export_runs``) and the publication step itself.

A filesystem rename and a SQLite ``COMMIT`` are two separate systems; **no code can make them one
atomic step**. :func:`record_and_publish` therefore only orders them so that every interruption is
recoverable, and the service writes a durable *intent journal* before calling it
(:mod:`vascqbank_desktop.services.export_publication`):

1. ``BEGIN IMMEDIATE``; the rows are inserted (not yet visible to anyone);
2. the verified staging folder is renamed to the final name (never over an existing folder) and the
   parent directory is synced;
3. ``COMMIT``. If the commit itself fails, the folder is renamed back — the export failed.

A crash between 2 and 3 leaves a published folder whose rows were rolled back by SQLite; the
journal lets :func:`~vascqbank_desktop.services.export_publication.reconcile` verify that folder's
files against its manifest and record it exactly once (:func:`record_once`). Rows are identified by
the export id in ``scope_json`` so no export is ever recorded twice.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExportRunRow:
    format: str  # DOCX / TXT
    scope: dict  # must contain "export_id"
    question_count: int
    files: list[dict]
    manifest: dict
    app_version: str
    created_at: str


def _checkpoint(stage: str) -> None:
    """Named points of publication; tests replace this to interrupt it exactly there."""


def runs_for_export(conn: sqlite3.Connection, export_id: str) -> list[int]:
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM export_runs WHERE json_extract(scope_json, '$.export_id') = ? ORDER BY id",
            (export_id,),
        )
    ]


def _insert(conn: sqlite3.Connection, rows: Sequence[ExportRunRow]) -> list[int]:
    return [
        int(
            conn.execute(
                "INSERT INTO export_runs (format, scope_json, question_count, files_json, manifest_json, "
                "app_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row.format,
                    json.dumps(row.scope, ensure_ascii=False, sort_keys=True),
                    row.question_count,
                    json.dumps(row.files, ensure_ascii=False),
                    json.dumps(row.manifest, ensure_ascii=False, sort_keys=True),
                    row.app_version,
                    row.created_at,
                ),
            ).lastrowid
            or 0
        )
        for row in rows
    ]


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record_and_publish(
    conn: sqlite3.Connection,
    rows: Sequence[ExportRunRow],
    staging: Path,
    final: Path,
    *,
    export_id: str | None = None,
) -> list[int]:
    """Insert the rows, rename *staging* to *final*, commit (see the module docstring)."""
    conn.execute("BEGIN IMMEDIATE")
    renamed = False
    try:
        if export_id is not None and runs_for_export(conn, export_id):
            raise FileExistsError(f"export {export_id} is already recorded")
        ids = _insert(conn, rows)
        _checkpoint("inserted")
        if final.exists():
            raise FileExistsError(f"{final} already exists; an earlier export is never overwritten")
        os.rename(staging, final)
        renamed = True
        _fsync_dir(final.parent)
        _checkpoint("renamed")
    except BaseException:
        try:
            if renamed:
                os.rename(final, staging)  # the rows will not be committed, so neither is the folder
        finally:
            conn.execute("ROLLBACK")
        raise
    try:
        conn.execute("COMMIT")
    except BaseException:
        try:
            os.rename(final, staging)  # not recorded, so not published either
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        raise
    _checkpoint("committed")
    return ids


def record_once(
    conn: sqlite3.Connection, rows: Sequence[ExportRunRow], export_id: str
) -> tuple[list[int], bool]:
    """Record an already published export unless it is recorded; returns (ids, newly_inserted)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = runs_for_export(conn, export_id)
        if existing:
            conn.execute("ROLLBACK")
            return existing, False
        ids = _insert(conn, rows)
        _checkpoint("reconcile-inserted")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return ids, True


def list_runs(conn: sqlite3.Connection) -> list[dict]:
    return [
        {
            "id": r[0],
            "format": r[1],
            "scope": json.loads(r[2]),
            "question_count": r[3],
            "files": json.loads(r[4]),
            "app_version": r[5],
            "created_at": r[6],
        }
        for r in conn.execute(
            "SELECT id, format, scope_json, question_count, files_json, app_version, created_at "
            "FROM export_runs ORDER BY id"
        )
    ]
