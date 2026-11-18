"""Append-only record of *successful* exports (``export_runs``).

:func:`record_and_publish` is the only writer. It inserts the rows **and** moves the finished,
verified export folder into place inside one transaction: if the move fails the rows are rolled
back; if the commit fails the folder is moved back. An export that did not complete is therefore
never recorded, and a recorded export always has its files.
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
    scope: dict
    question_count: int
    files: list[dict]
    manifest: dict
    app_version: str
    created_at: str


def record_and_publish(
    conn: sqlite3.Connection, rows: Sequence[ExportRunRow], staging: Path, final: Path
) -> list[int]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = [
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
        if final.exists():
            raise FileExistsError(f"{final} already exists; an earlier export is never overwritten")
        os.rename(staging, final)
    except BaseException:
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
    return ids


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
