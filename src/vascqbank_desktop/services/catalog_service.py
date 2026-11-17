"""Browsing, searching and opening questions (read-only).

Every call opens its own short-lived connection with ``PRAGMA query_only``
(0.3 ms to open), so it is safe from any thread and *cannot* modify the
database: opening a question never changes a status, timestamp or revision.

Search uses the derived FTS5 trigram index when the SQLite build supports it
and the index is an exact image of the database; otherwise — no FTS5,
a term shorter than three characters, a stale or damaged index — it falls
back to a LIKE substring scan (≈20 ms for 10,000 questions). Both give the
same results; :attr:`ResultSet.mode` tells which one answered.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import catalog_repository as repo
from vascqbank_desktop.db import search_index
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.catalog import (
    CatalogFilter,
    QuestionDetail,
    QuestionLocation,
    QuestionSummary,
    ResultSet,
    RevisionDetail,
    RevisionInfo,
    SearchMode,
)
from vascqbank_desktop.domain.question_id import parse_reference
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.search import fts_match_expression, fts_supports

log = logging.getLogger(__name__)

# A difference this small (an edit or two) is synchronised inside the search that
# notices it; a bigger one (first build, a new import) is left to the warm-up job
# and that search uses the LIKE scan.
INLINE_SYNC_LIMIT = 300


class QuestionNotFound(LookupError):
    def __init__(self, uid: str) -> None:
        self.uid = uid
        super().__init__(f"question {uid} is not in the database")


@dataclass(frozen=True, slots=True)
class SearchIndexStatus:
    available: bool  # FTS5 trigram is usable on this computer
    rebuilt: bool = False
    indexed: int = 0
    duration_s: float = 0.0
    message: str = ""


class CatalogService:
    def __init__(self, paths: AppPaths, *, use_fts: bool = True) -> None:
        self._paths = paths
        self._use_fts = use_fts

    # -- connections -----------------------------------------------------------
    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        conn = open_database(self._paths.database_file)
        try:
            conn.execute("PRAGMA query_only = ON")
            yield conn
        finally:
            conn.close()

    @property
    def fts_available(self) -> bool:
        return self._use_fts and search_index.fts5_trigram_supported()

    # -- listing ---------------------------------------------------------------
    def query(self, flt: CatalogFilter) -> ResultSet:
        """Ordered ids matching *flt* (ids only; use :meth:`rows` for visible pages)."""
        terms = flt.terms
        with self._reader() as conn:
            if not terms:
                ids = repo.list_ids(conn, flt.subject_code, flt.status, flt.sort)
                return ResultSet(tuple(ids), (), SearchMode.BROWSE)
            ids_fts = self._fts_ids(conn, flt, terms) if self.fts_available and fts_supports(terms) else None
            if ids_fts is not None:
                return ResultSet(tuple(ids_fts), terms, SearchMode.FTS)
            ids = repo.search_ids_like(conn, terms, flt.subject_code, flt.status, flt.sort)
            return ResultSet(tuple(ids), terms, SearchMode.LIKE)

    def _fts_ids(
        self, conn: sqlite3.Connection, flt: CatalogFilter, terms: tuple[str, ...]
    ) -> list[int] | None:
        """Ids from the index, or ``None`` if it cannot answer right now."""
        path = self._paths.search_index_file
        schema = repo.INDEX_SCHEMA
        try:
            expected = search_index.main_fingerprint(conn)
            for attempt in (1, 2):
                if path.is_file():
                    search_index.attach_index(conn, path, schema)
                    try:
                        if search_index.attached_index_is_fresh(conn, schema, expected):
                            return repo.search_ids_fts(
                                conn, fts_match_expression(terms), flt.subject_code, flt.status, flt.sort
                            )
                    finally:
                        conn.execute(f"DETACH DATABASE {schema}")
                if attempt == 2:
                    return None
                result = search_index.sync_index(conn, path, max_changes=INLINE_SYNC_LIMIT)
                if result.pending:
                    return None
        except (sqlite3.Error, search_index.SearchIndexUnavailable) as exc:
            log.warning("Full-text index unavailable (%s); using LIKE search", exc)
        return None

    def rows(self, ids: Sequence[int], terms: tuple[str, ...] = ()) -> list[QuestionSummary]:
        """Summaries for one page of ids, fetched with a single query."""
        if not ids:
            return []
        with self._reader() as conn:
            return repo.fetch_summaries(conn, ids, terms)

    def count(self, subject_code: str | None = None, status: ReviewStatus | None = None) -> int:
        with self._reader() as conn:
            return repo.count_matching(conn, subject_code, status)

    def first_id(self, subject_code: str | None, status: ReviewStatus | None) -> int | None:
        with self._reader() as conn:
            ids = repo.list_ids(conn, subject_code, status)
        return ids[0] if ids else None

    # -- single questions --------------------------------------------------------
    def detail(self, question_id: int) -> QuestionDetail | None:
        with self._reader() as conn:
            return repo.get_detail(conn, question_id)

    def detail_by_uid(self, uid: str) -> QuestionDetail | None:
        with self._reader() as conn:
            return repo.get_detail_by_uid(conn, uid)

    def revisions(self, question_id: int) -> list[RevisionInfo]:
        """The revision history of one question, newest first (metadata only)."""
        with self._reader() as conn:
            return repo.list_revisions(conn, question_id)

    def revision(self, revision_id: int) -> RevisionDetail | None:
        """One revision with its full text."""
        with self._reader() as conn:
            return repo.get_revision(conn, revision_id)

    def locate(self, uid: str) -> QuestionLocation | None:
        with self._reader() as conn:
            return repo.find_location(conn, uid)

    def locate_id(self, question_id: int) -> QuestionLocation | None:
        with self._reader() as conn:
            return repo.find_location_by_id(conn, question_id)

    def resolve_reference(self, text: str, default_subject: str | None = None) -> QuestionLocation:
        """Find the question a reviewer typed (ID such as ``ALI-0042`` or a number).

        Raises :class:`~vascqbank_desktop.domain.question_id.InvalidReference` for unusable text and
        :class:`QuestionNotFound` for a well-formed ID that is not in the database.
        """
        qid = parse_reference(text, default_subject)
        location = self.locate(str(qid))
        if location is None:
            raise QuestionNotFound(str(qid))
        return location

    def nearest_id(self, uid: str, subject_code: str | None, status: ReviewStatus | None) -> int | None:
        with self._reader() as conn:
            return repo.nearest_id(conn, uid, subject_code, status)

    # -- search index ----------------------------------------------------------------
    def ensure_search_index(self) -> SearchIndexStatus:
        """Build or refresh the full-text index (run on a background thread)."""
        if not self.fts_available:
            return SearchIndexStatus(False, message="FTS5 trigram is not available; LIKE search is used")
        try:
            with self._reader() as conn:
                result = search_index.sync_index(conn, self._paths.search_index_file)
        except (sqlite3.Error, search_index.SearchIndexUnavailable) as exc:
            log.warning("Could not build the full-text index (%s); LIKE search is used", exc)
            return SearchIndexStatus(False, message=str(exc))
        if result.changed:
            log.info(
                "Search index %s: %s documents in %.2fs",
                "built" if result.rebuilt else "updated",
                result.indexed,
                result.duration_s,
            )
        return SearchIndexStatus(True, result.rebuilt, result.indexed, result.duration_s)
