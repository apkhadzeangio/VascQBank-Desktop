"""SQLite connection policy.

Every connection in the application is opened through :func:`open_database`
so that the same safety settings always apply. A file that belongs to another
program is rejected *before* any of the persistent settings below are applied.

* ``isolation_level=None`` — Python's implicit transactions are disabled; the
  code opens explicit ``BEGIN IMMEDIATE`` transactions where it writes.
* ``foreign_keys=ON`` — referential integrity is enforced (off by default in SQLite).
* ``journal_mode=WAL`` — readers never block the writer, and an interrupted
  write cannot corrupt committed data.
* ``synchronous=NORMAL`` — the recommended durability level for WAL; the editing services
  raise it to ``FULL`` for their own connections (:func:`make_durable`).
* ``busy_timeout`` — brief lock contention waits instead of failing.

SQLite stores text as UTF-8 (``PRAGMA encoding``), which is set before the
first table is created and cannot change afterwards.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from vascqbank_desktop.db.errors import DatabaseError
from vascqbank_desktop.db.identity import ensure_owned_or_blank

BUSY_TIMEOUT_MS = 5000
# STRICT tables (3.37) and the sqlite_schema alias (3.33) are relied upon.
MIN_SQLITE_VERSION: tuple[int, int, int] = (3, 37, 0)


def ensure_supported_sqlite(version_info: tuple[int, int, int] | None = None) -> None:
    found = sqlite3.sqlite_version_info if version_info is None else version_info
    if tuple(found) < MIN_SQLITE_VERSION:
        need = ".".join(map(str, MIN_SQLITE_VERSION))
        have = ".".join(map(str, found))
        raise DatabaseError(f"SQLite {need} or newer is required; this Python has {have}.")


def open_database(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open (creating if necessary) the SQLite database at *path*."""
    ensure_supported_sqlite()
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    try:
        conn.row_factory = sqlite3.Row
        # Connection-scoped settings first: they never touch the file.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        # Refuse other programs' databases before anything persistent happens.
        ensure_owned_or_blank(conn)
        conn.execute("PRAGMA encoding = 'UTF-8'")  # only effective on a blank file
        conn.execute("PRAGMA journal_mode = WAL")  # persistent: stored in the header
        conn.execute("PRAGMA synchronous = NORMAL")
    except BaseException:
        conn.close()
        raise
    return conn


def make_durable(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Make this connection's commits survive a power cut (``synchronous=FULL``).

    The default ``NORMAL`` level of WAL mode keeps a committed transaction through a crash of
    the *program*, but the most recent commits may be lost if the *computer* loses power before
    the log reaches the disk. Reviewer work (drafts and saved revisions) is worth the extra
    ``fsync`` per commit, so the editing services call this on their own connections. The
    setting is per connection, never stored in the file.
    """
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def quick_check(conn: sqlite3.Connection) -> bool:
    """Run SQLite's fast structural integrity check."""
    row = conn.execute("PRAGMA quick_check").fetchone()
    return row is not None and row[0] == "ok"


def has_fts5_trigram(conn: sqlite3.Connection) -> bool:
    """Whether this SQLite build supports FTS5 with the trigram tokenizer.

    Planned full-text search depends on it (see ARCHITECTURE.md, search); the
    check uses a TEMP table so the database file is not modified.
    """
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._fts_probe USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE temp._fts_probe")
    except sqlite3.OperationalError:
        return False
    return True
