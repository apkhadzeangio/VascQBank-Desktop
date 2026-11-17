"""Saving an edited question as a new revision (the authoritative write).

``EditService.save`` validates, requires a reviewer name, and then performs the whole change
in one SQLite transaction (``db/edit_repository.append_edit``): append the revision, move
``current_revision_id``, apply the status rule, bump ``lock_version``, append the review event
and remove the draft that held exactly this text — all or nothing. It returns only after the
commit; every failure leaves the database as it was.

What it deliberately does **not** do: approve, reject or reopen (Phase 5), restore an older
revision, or touch anything but the one question.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import edit_repository as repo
from vascqbank_desktop.db.connection import make_durable, open_database
from vascqbank_desktop.db.migrations import utc_now_iso
from vascqbank_desktop.domain.editing import normalize_content, normalize_note, validate_content
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.edit_errors import (
    STORAGE_ERRORS,
    EditConflict,
    EditTargetMissing,
    ReviewerRequired,
    StorageFailure,
    ValidationFailed,
)
from vascqbank_desktop.services.settings_service import ReviewerNotSetError, ReviewerProfile

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SaveResult:
    created: bool  # False: the content equals the current revision, nothing was written
    question_id: int
    revision_id: int
    revision_no: int
    lock_version: int
    status_before: ReviewStatus
    status_after: ReviewStatus
    approval_revoked: bool
    revoked_revision_no: int | None
    modified_at: str
    draft_cleared: bool
    author: str


class EditService:
    def __init__(self, paths: AppPaths, profile: ReviewerProfile) -> None:
        self._paths = paths
        self._profile = profile
        self._write_lock = threading.Lock()

    def save(
        self,
        question_id: int,
        expected_lock_version: int,
        content: QuestionContent,
        note: str | None = None,
        *,
        rebased_from_revision_no: int | None = None,
    ) -> SaveResult:
        """Save *content* as the next revision of *question_id*.

        *expected_lock_version* is the one the editor loaded; if the question was saved since,
        :class:`EditConflict` is raised and nothing changes. *rebased_from_revision_no*
        records, in the review event, that the reviewer knowingly saved a text that began as an
        older revision on top of a newer one (the newer revision stays in the history).
        """
        try:
            author = self._profile.require_name()
        except ReviewerNotSetError as exc:
            raise ReviewerRequired(str(exc)) from exc
        problems = validate_content(content, note)
        if problems:
            raise ValidationFailed(problems)
        normalized = normalize_content(content)
        cleaned_note = normalize_note(note)

        event_note = (
            f"saved over a newer revision; edited from revision {rebased_from_revision_no}"
            if rebased_from_revision_no
            else None
        )
        with self._write_lock:
            try:
                # a connection per call (never shared), synced to disk at every commit
                conn = make_durable(open_database(self._paths.database_file))
                try:
                    outcome = repo.append_edit(
                        conn,
                        question_id=question_id,
                        expected_lock_version=expected_lock_version,
                        content=normalized,
                        author=author,
                        note=cleaned_note,
                        event_note=event_note,
                        now=utc_now_iso(),
                    )
                finally:
                    conn.close()
            except repo.LockConflict as exc:
                raise EditConflict(
                    question_id,
                    exc.expected_lock_version,
                    exc.current_lock_version,
                    exc.current_revision_no,
                    exc.current_status,
                ) from exc
            except repo.QuestionMissing as exc:
                raise EditTargetMissing(question_id) from exc
            except STORAGE_ERRORS as exc:
                log.warning("Saving question %s failed: %s", question_id, exc)
                raise StorageFailure.from_exception(exc) from exc
        return SaveResult(
            created=outcome.created,
            question_id=outcome.question_id,
            revision_id=outcome.revision_id,
            revision_no=outcome.revision_no,
            lock_version=outcome.lock_version,
            status_before=outcome.status_before,
            status_after=outcome.status_after,
            approval_revoked=outcome.approval_revoked,
            revoked_revision_no=outcome.revoked_revision_no,
            modified_at=outcome.modified_at,
            draft_cleared=outcome.draft_cleared,
            author=author,
        )
