"""Autosaved drafts: crash-safe working copies, separate from saved revisions.

A draft is written in one SQLite transaction; :meth:`DraftService.save_draft` returns only
**after** that transaction has committed, and raises :class:`StorageFailure` (with nothing
changed) otherwise, so callers can never claim "saved" too early. Writes from this process are
serialised by a lock (SQLite additionally serialises writers across processes with
``BEGIN IMMEDIATE``). Drafts never alter ``questions``, ``question_revisions`` or
``review_events``: typing cannot change a status, a lock version or any saved text.

A draft is only removed by (a) the commit of an explicit save that stored exactly its text
(``EditService``), (b) the reviewer discarding it — the full text is first copied to the
append-only ``draft_discards`` table in the same transaction — or (c) a draft that is
identical to the saved revision, which carries nothing to lose.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import draft_repository as repo
from vascqbank_desktop.db.connection import make_durable, open_database
from vascqbank_desktop.db.draft_repository import DraftListRow, DraftRecord
from vascqbank_desktop.db.migrations import utc_now_iso
from vascqbank_desktop.domain.editing import MAX_NOTE_LENGTH, normalize_note
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.services.edit_errors import STORAGE_ERRORS, ReviewerRequired, StorageFailure
from vascqbank_desktop.validation.text import normalize_text

log = logging.getLogger(__name__)

__all__ = ["DiscardReason", "DraftListRow", "DraftRecord", "DraftSaved", "DraftService"]


class DiscardReason(StrEnum):
    DISCARDED = "DISCARDED"  # thrown away from the recovery list or bar
    REVERTED = "REVERTED"  # editing cancelled and the changes given up


@dataclass(frozen=True, slots=True)
class DraftSaved:
    """What the database confirmed: this is the committed state of the draft."""

    question_id: int
    saved_at: str
    write_count: int


def _draft_text(text: str) -> str:
    # NFC and LF only: the draft keeps exactly what was typed (including a trailing space that
    # is simply the word being typed); trimming happens when a revision is saved.
    return normalize_text(text)


class DraftService:
    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._write_lock = threading.Lock()  # one draft write at a time in this process

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = make_durable(open_database(self._paths.database_file))  # per call: never shared
        try:
            yield conn
        finally:
            conn.close()

    # -- writes ----------------------------------------------------------------
    def save_draft(
        self,
        question_id: int,
        base_revision_id: int,
        base_lock_version: int,
        content: QuestionContent,
        reviewer_name: str,
        note: str | None = None,
    ) -> DraftSaved:
        """Persist the draft (insert or replace). Returns only after the commit."""
        name = " ".join(reviewer_name.split())
        if not name:
            raise ReviewerRequired("a draft is attributed to a reviewer")
        cleaned = QuestionContent(
            stem=_draft_text(content.stem),
            options=tuple(_draft_text(o) for o in content.options),
            correct=content.correct,
            explanation=_draft_text(content.explanation),
        )
        cleaned_note = normalize_note(note)
        if cleaned_note is not None:
            cleaned_note = cleaned_note[:MAX_NOTE_LENGTH]
        with self._write_lock:
            try:
                with self._connect() as conn:
                    record = repo.upsert_draft(
                        conn,
                        question_id=question_id,
                        base_revision_id=base_revision_id,
                        base_lock_version=base_lock_version,
                        content=cleaned,
                        note=cleaned_note,
                        reviewer_name=name,
                        now=utc_now_iso(),
                    )
            except STORAGE_ERRORS as exc:
                log.warning("Draft write failed for question %s: %s", question_id, exc)
                raise StorageFailure.from_exception(exc) from exc
        return DraftSaved(question_id, record.saved_at, record.write_count)

    def discard_draft(
        self, question_id: int, discarded_by: str, reason: DiscardReason = DiscardReason.DISCARDED
    ) -> bool:
        """Intentionally throw a draft away; its text is archived in ``draft_discards``."""
        name = " ".join(discarded_by.split())
        if not name:
            raise ReviewerRequired("discarding a draft is attributed to a reviewer")
        with self._write_lock:
            try:
                with self._connect() as conn:
                    return repo.delete_draft(
                        conn, question_id, archive_reason=reason.value, discarded_by=name, now=utc_now_iso()
                    )
            except STORAGE_ERRORS as exc:
                raise StorageFailure.from_exception(exc) from exc

    def drop_if_identical(self, question_id: int, saved_content: QuestionContent) -> bool:
        """Delete the draft when it equals *saved_content* (nothing to lose); keep it otherwise."""
        with self._write_lock:
            try:
                with self._connect() as conn:
                    return repo.delete_draft_if_identical(conn, question_id, saved_content)
            except STORAGE_ERRORS as exc:
                raise StorageFailure.from_exception(exc) from exc

    # -- reads ---------------------------------------------------------------------
    def load_draft(self, question_id: int) -> DraftRecord | None:
        with self._connect() as conn:
            return repo.get_draft(conn, question_id)

    def list_drafts(self) -> list[DraftListRow]:
        with self._connect() as conn:
            return repo.list_drafts(conn)

    def draft_info(self, question_id: int) -> DraftListRow | None:
        """Metadata of one question's draft (who, when, how outdated); ``None`` if it has none."""
        with self._connect() as conn:
            rows = repo.list_drafts(conn, question_id)
        return rows[0] if rows else None

    def draft_question_ids(self) -> set[int]:
        with self._connect() as conn:
            return repo.draft_question_ids(conn)

    def count(self) -> int:
        with self._connect() as conn:
            return repo.count_drafts(conn)
