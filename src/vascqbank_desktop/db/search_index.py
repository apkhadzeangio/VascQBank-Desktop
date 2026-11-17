"""Derived full-text index (SQLite FTS5 with the trigram tokenizer).

The index is *derived, disposable data*: it can be deleted at any time and is
rebuilt from the authoritative database. It deliberately lives in its own file
(``database/search-index.sqlite3``) rather than inside ``vascqbank.sqlite3``:

* the authoritative database, backups and a bundled seed database never depend
  on an optional SQLite extension — on a build without FTS5/trigram the
  application still opens, edits and backs up everything and simply searches
  with the LIKE fallback;
* backups and their verification do not carry or count a derived structure;
* a damaged or stale index costs a rebuild (≈0.3 s per 10,000 questions), never data.

One row per question, ``rowid = questions.id``, holding the question's
*current* revision text (:func:`vascqbank_desktop.domain.search.document_text`).
``doc_state`` records which revision each row was built from, and ``meta``
the database it belongs to. Freshness is a cheap comparison of
``(database_id, question count, sum of current revision ids)``: revisions are
append-only and the current revision only ever moves forward, so any edit
strictly increases the sum. Synchronisation itself is exact — it compares
every question's current revision id with the indexed one.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from vascqbank_desktop.db.errors import DatabaseError
from vascqbank_desktop.domain.search import document_text

log = logging.getLogger(__name__)

INDEX_FORMAT = "1"
TOKENIZER = "trigram"
BUSY_TIMEOUT_MS = 5000
BUILD_CHUNK = 1000
SIDE_FILE_SUFFIXES = ("", "-wal", "-shm", "-journal")


class SearchIndexUnavailable(DatabaseError):
    """The index cannot be used (no FTS5 trigram support, locked or damaged beyond repair)."""


class IndexState(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"  # exists, but behind the database
    MISSING = "MISSING"
    UNUSABLE = "UNUSABLE"


@dataclass(frozen=True, slots=True)
class Fingerprint:
    database_id: str
    question_count: int
    revision_sum: int


@dataclass(frozen=True, slots=True)
class SyncResult:
    rebuilt: bool  # everything was (re)indexed, not just a difference
    indexed: int  # documents written
    removed: int  # documents dropped
    pending: int  # differences left undone because they exceeded max_changes
    duration_s: float

    @property
    def changed(self) -> bool:
        return bool(self.indexed or self.removed)


@lru_cache(maxsize=1)
def fts5_trigram_supported() -> bool:
    """Whether this SQLite build can create an FTS5 table with the trigram tokenizer.

    A property of the linked SQLite library, so probed once in a throw-away
    in-memory database (the application database is never touched).
    """
    try:
        probe = sqlite3.connect(":memory:")
        try:
            probe.execute("CREATE VIRTUAL TABLE probe USING fts5(x, tokenize='trigram')")
        finally:
            probe.close()
    except sqlite3.Error:
        return False
    return True


# --------------------------------------------------------------------------
# fingerprints
# --------------------------------------------------------------------------
def main_fingerprint(main: sqlite3.Connection) -> Fingerprint:
    row = main.execute("SELECT value FROM app_metadata WHERE key = 'database_id'").fetchone()
    count, total = main.execute(
        "SELECT count(*), COALESCE(sum(current_revision_id), 0) FROM questions"
    ).fetchone()
    return Fingerprint(row[0] if row else "", int(count), int(total))


def _index_fingerprint(index: sqlite3.Connection, schema: str = "main") -> Fingerprint | None:
    """The fingerprint the index was built for, or ``None`` if it is not a complete index."""
    try:
        meta = dict(index.execute(f"SELECT key, value FROM {schema}.meta").fetchall())
        if meta.get("format") != INDEX_FORMAT or meta.get("tokenizer") != TOKENIZER:
            return None
        count, total = index.execute(
            f"SELECT count(*), COALESCE(sum(revision_id), 0) FROM {schema}.doc_state"
        ).fetchone()
    except sqlite3.Error:
        return None
    return Fingerprint(meta.get("database_id", ""), int(count), int(total))


def attached_index_is_fresh(conn: sqlite3.Connection, schema: str, expected: Fingerprint) -> bool:
    """Is the index attached to *conn* as *schema* an exact, complete image of *expected*?"""
    return _index_fingerprint(conn, schema) == expected


def index_state(index_path: Path, expected: Fingerprint) -> IndexState:
    if not index_path.is_file():
        return IndexState.MISSING
    try:
        index = sqlite3.connect(index_path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    except sqlite3.Error:
        return IndexState.UNUSABLE
    try:
        index.execute("PRAGMA query_only = ON")
        found = _index_fingerprint(index)
    except sqlite3.Error:
        return IndexState.UNUSABLE
    finally:
        index.close()
    if found is None:
        return IndexState.UNUSABLE
    return IndexState.FRESH if found == expected else IndexState.STALE


# --------------------------------------------------------------------------
# attaching for queries
# --------------------------------------------------------------------------
def attach_index(conn: sqlite3.Connection, index_path: Path, schema: str) -> None:
    """Attach an *existing* index file; never creates one (ATTACH would silently do so)."""
    if not index_path.is_file():
        raise SearchIndexUnavailable(f"search index does not exist: {index_path}")
    conn.execute(f"ATTACH DATABASE ? AS {schema}", (str(index_path),))


# --------------------------------------------------------------------------
# building / synchronising
# --------------------------------------------------------------------------
_SCHEMA_STATEMENTS = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(body, tokenize = '{TOKENIZER}')",
    "CREATE TABLE IF NOT EXISTS doc_state ("
    " question_id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL) STRICT",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT, WITHOUT ROWID",
)

_DOCUMENT_ROWS_SQL = (
    "SELECT q.id, q.current_revision_id, q.question_uid, r.stem,"
    " r.option_a, r.option_b, r.option_c, r.option_d, r.option_e, r.explanation"
    " FROM questions q JOIN question_revisions r ON r.id = q.current_revision_id"
)


def delete_index_files(index_path: Path) -> None:
    """Remove the index and its side files (derived data: nothing of value is lost)."""
    for suffix in SIDE_FILE_SUFFIXES:
        Path(str(index_path) + suffix).unlink(missing_ok=True)


def _open_index_for_writing(index_path: Path) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = sqlite3.connect(index_path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    try:
        index.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        index.execute("PRAGMA journal_mode = WAL")
        index.execute("PRAGMA synchronous = NORMAL")
        for statement in _SCHEMA_STATEMENTS:
            index.execute(statement)
    except BaseException:
        index.close()
        raise
    return index


def _document(row: sqlite3.Row) -> tuple[int, int, str]:
    return int(row[0]), int(row[1]), document_text(row[2], row[3], tuple(row[4:9]), row[9])


def _sync_once(
    main: sqlite3.Connection, index_path: Path, max_changes: int | None
) -> SyncResult:
    started = time.perf_counter()
    expected = main_fingerprint(main)
    index = _open_index_for_writing(index_path)
    try:
        stored = _index_fingerprint(index)
        reset = stored is None or stored.database_id != expected.database_id
        indexed_revisions: dict[int, int] = (
            {}
            if reset
            else {int(q): int(r) for q, r in index.execute("SELECT question_id, revision_id FROM doc_state")}
        )
        current = {int(q): int(r) for q, r in main.execute("SELECT id, current_revision_id FROM questions")}
        to_write = [q for q, rev in current.items() if indexed_revisions.get(q) != rev]
        to_remove = [q for q in indexed_revisions if q not in current]
        pending = len(to_write) + len(to_remove)
        if max_changes is not None and pending > max_changes:
            return SyncResult(False, 0, 0, pending, time.perf_counter() - started)

        index.execute("BEGIN IMMEDIATE")
        try:
            if reset:
                index.execute("DELETE FROM docs")
                index.execute("DELETE FROM doc_state")
                index.execute("DELETE FROM meta")
            for question_id in to_remove:
                index.execute("DELETE FROM docs WHERE rowid = ?", (question_id,))
                index.execute("DELETE FROM doc_state WHERE question_id = ?", (question_id,))
            if to_write:
                _write_documents(main, index, to_write, replace_existing=not reset)
            index.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                [
                    ("format", INDEX_FORMAT),
                    ("tokenizer", TOKENIZER),
                    ("database_id", expected.database_id),
                ],
            )
            index.execute("COMMIT")
        except BaseException:
            if index.in_transaction:
                index.execute("ROLLBACK")
            raise
    finally:
        index.close()
    return SyncResult(reset, len(to_write), len(to_remove), 0, time.perf_counter() - started)


def _write_documents(
    main: sqlite3.Connection, index: sqlite3.Connection, question_ids: list[int], *, replace_existing: bool
) -> None:
    for start in range(0, len(question_ids), BUILD_CHUNK):
        chunk = question_ids[start : start + BUILD_CHUNK]
        marks = ",".join("?" * len(chunk))
        rows = [_document(r) for r in main.execute(f"{_DOCUMENT_ROWS_SQL} WHERE q.id IN ({marks})", chunk)]
        if replace_existing:
            index.executemany("DELETE FROM docs WHERE rowid = ?", [(qid,) for qid, _, _ in rows])
        index.executemany(
            "INSERT INTO docs (rowid, body) VALUES (?, ?)", [(qid, body) for qid, _, body in rows]
        )
        index.executemany(
            "INSERT INTO doc_state (question_id, revision_id) VALUES (?, ?) "
            "ON CONFLICT (question_id) DO UPDATE SET revision_id = excluded.revision_id",
            [(qid, revision) for qid, revision, _ in rows],
        )


def sync_index(
    main: sqlite3.Connection, index_path: Path, *, max_changes: int | None = None
) -> SyncResult:
    """Make the index an exact image of the database's current revisions.

    Only differences are written (a full build when the index is new, belongs
    to another database or has another format). With *max_changes*, a larger
    difference is left alone and reported in ``pending`` — callers use that to
    avoid a long rebuild in the middle of a search.

    Raises :class:`SearchIndexUnavailable` when FTS5 trigram support is missing
    or the index stays unusable; a damaged index file is deleted and rebuilt once.
    """
    if not fts5_trigram_supported():
        raise SearchIndexUnavailable("this SQLite build has no FTS5 trigram tokenizer")
    for attempt in (1, 2):
        try:
            return _sync_once(main, index_path, max_changes)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                raise SearchIndexUnavailable(f"search index is busy: {exc}") from exc
            if "no such module" in message or "no such tokenizer" in message:
                raise SearchIndexUnavailable(str(exc)) from exc
            if attempt == 2:
                raise SearchIndexUnavailable(str(exc)) from exc
            log.warning("Search index unusable (%s); rebuilding from scratch", exc)
            delete_index_files(index_path)
        except sqlite3.DatabaseError as exc:
            if attempt == 2:
                raise SearchIndexUnavailable(str(exc)) from exc
            log.warning("Search index damaged (%s); rebuilding from scratch", exc)
            delete_index_files(index_path)
    raise SearchIndexUnavailable("search index could not be built")  # pragma: no cover
