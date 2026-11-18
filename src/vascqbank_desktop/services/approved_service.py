"""The read-side contract between review (Phase 5) and export (Phase 6).

The exporter must obtain its questions **only** through this service:

* :meth:`ApprovedQuestionsService.snapshot` — every approved question (or one subject's), in
  export order, read in a single consistent database snapshot together with a fingerprint the
  export run can record;
* each :class:`~vascqbank_desktop.db.approved_repository.ApprovedQuestion` carries the
  immutable question ID, the *approved revision's* id and number, and its exact text, correct
  answer and explanation.

Never exported, by construction of the queries: unsaved drafts, pending / in-review / rejected
questions, revisions other than ``approved_revision_id`` (a later edit or restore revokes the
approval first, so a superseded revision can never be approved *and* exported). The rule itself
is :func:`vascqbank_desktop.domain.workflow.is_exportable`.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import approved_repository as repo
from vascqbank_desktop.db.approved_repository import ApprovedQuestion, ContractViolation
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.db.migrations import utc_now_iso

__all__ = ["ApprovedQuestion", "ApprovedQuestionsService", "ApprovedSnapshot", "ContractViolation"]


@dataclass(frozen=True, slots=True)
class ApprovedSnapshot:
    """A consistent view of the approved questions at one moment."""

    taken_at: str
    subject_code: str | None  # None = every subject
    questions: tuple[ApprovedQuestion, ...]
    fingerprint: str  # SHA-256 over "uid|revision_id|content_hash" lines, in export order

    def __len__(self) -> int:
        return len(self.questions)

    @property
    def uids(self) -> tuple[str, ...]:
        return tuple(q.uid for q in self.questions)


def fingerprint_of(questions: tuple[ApprovedQuestion, ...]) -> str:
    digest = hashlib.sha256()
    for q in questions:
        digest.update(f"{q.uid}|{q.revision_id}|{q.content_hash}\n".encode())
    return digest.hexdigest()


class ApprovedQuestionsService:
    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        conn = open_database(self._paths.database_file)
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")  # one read snapshot for everything inside the block
            try:
                yield conn
            finally:
                conn.execute("ROLLBACK")
        finally:
            conn.close()

    def snapshot(self, subject_code: str | None = None) -> ApprovedSnapshot:
        """All approved questions (optionally of one subject) as of one consistent read."""
        with self._reader() as conn:
            questions = tuple(repo.list_approved(conn, subject_code))
        return ApprovedSnapshot(utc_now_iso(), subject_code, questions, fingerprint_of(questions))

    def get(self, uid: str) -> ApprovedQuestion | None:
        """The approved revision of one question, or ``None`` if it is not (currently) approved."""
        with self._reader() as conn:
            return repo.get_approved(conn, uid)

    def approved_ids(self, subject_code: str | None = None) -> list[int]:
        with self._reader() as conn:
            return repo.approved_ids(conn, subject_code)

    def count(self, subject_code: str | None = None) -> int:
        with self._reader() as conn:
            counts = repo.count_by_subject(conn)
        return counts.get(subject_code, 0) if subject_code else sum(counts.values())

    def counts_by_subject(self) -> dict[str, int]:
        with self._reader() as conn:
            return repo.count_by_subject(conn)

    def verify(self) -> list[ContractViolation]:
        """Run before exporting: an empty list means every approved question is safe to export."""
        with self._reader() as conn:
            return repo.contract_violations(conn)
