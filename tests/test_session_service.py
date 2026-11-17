"""Session restoration and its fallbacks, using the real catalog service on a seeded database."""

from __future__ import annotations

import pytest
from bank import make_question, seed, set_status, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.session_service import ContextNotice, restore_context
from vascqbank_desktop.services.settings_service import SavedSession


@pytest.fixture
def catalog(app_paths: AppPaths) -> CatalogService:
    seed(
        app_paths,
        [make_question("ALI", n) for n in range(1, 11)]
        + [make_question("VENOUS", n) for n in range(1, 6)]
        + [make_question("PAD_CLTI", n) for n in range(1, 4)],
    )
    return CatalogService(app_paths)


def uid_of(catalog: CatalogService, question_id: int | None) -> str | None:
    return None if question_id is None else catalog.locate_id(question_id).uid


def test_a_valid_session_is_restored_exactly(catalog: CatalogService) -> None:
    ctx = restore_context(catalog, SavedSession("ALI", None, "ALI-0007"))
    assert (ctx.subject_code, ctx.status, uid_of(catalog, ctx.question_id)) == ("ALI", None, "ALI-0007")
    assert ctx.notice is ContextNotice.NONE


def test_filter_and_question_are_restored_together(catalog: CatalogService, app_paths: AppPaths) -> None:
    set_status(app_paths, "VENOUS-0003", ReviewStatus.IN_REVIEW)
    ctx = restore_context(catalog, SavedSession("VENOUS", ReviewStatus.IN_REVIEW, "VENOUS-0003"))
    assert (ctx.subject_code, ctx.status, uid_of(catalog, ctx.question_id)) == (
        "VENOUS",
        ReviewStatus.IN_REVIEW,
        "VENOUS-0003",
    )
    assert ctx.notice is ContextNotice.NONE


def test_no_saved_question_means_nothing_is_selected(catalog: CatalogService) -> None:
    ctx = restore_context(catalog, SavedSession())
    assert ctx.question_id is None and ctx.subject_code is None and ctx.status is None
    only_subject = restore_context(catalog, SavedSession("ALI", ReviewStatus.PENDING_REVIEW, None))
    assert only_subject.question_id is None and only_subject.subject_code == "ALI"


def test_question_that_no_longer_matches_the_filter_falls_back_to_the_next_matching_one(
    catalog: CatalogService, app_paths: AppPaths
) -> None:
    # The reviewer stopped at ALI-0004 while filtering "in review"; meanwhile it moved on.
    for uid in ("ALI-0002", "ALI-0004", "ALI-0008"):
        set_status(app_paths, uid, ReviewStatus.IN_REVIEW)
    set_status(app_paths, "ALI-0004", ReviewStatus.APPROVED)
    ctx = restore_context(catalog, SavedSession("ALI", ReviewStatus.IN_REVIEW, "ALI-0004"))
    assert uid_of(catalog, ctx.question_id) == "ALI-0008"  # the next one that is still in review
    assert (ctx.subject_code, ctx.status) == ("ALI", ReviewStatus.IN_REVIEW)  # the filter is kept
    assert ctx.notice is ContextNotice.QUESTION_FILTERED and ctx.requested_uid == "ALI-0004"


def test_fallback_goes_backwards_when_nothing_follows(catalog: CatalogService, app_paths: AppPaths) -> None:
    for uid in ("ALI-0002", "ALI-0006"):
        set_status(app_paths, uid, ReviewStatus.IN_REVIEW)
    ctx = restore_context(catalog, SavedSession("ALI", ReviewStatus.IN_REVIEW, "ALI-0010"))
    assert uid_of(catalog, ctx.question_id) == "ALI-0006"
    assert ctx.notice is ContextNotice.QUESTION_FILTERED


def test_fallback_when_the_filter_matches_nothing_clears_the_filter(catalog: CatalogService) -> None:
    ctx = restore_context(catalog, SavedSession("ALI", ReviewStatus.REJECTED, "ALI-0005"))
    assert ctx.status is None  # nothing is rejected, so the status filter is dropped …
    assert (ctx.subject_code, uid_of(catalog, ctx.question_id)) == (
        "ALI",
        "ALI-0005",
    )  # … and the question shows
    assert ctx.notice is ContextNotice.FILTER_RESET


def test_question_from_another_subject_than_the_saved_filter(catalog: CatalogService) -> None:
    ctx = restore_context(catalog, SavedSession("VENOUS", None, "ALI-0005"))
    assert ctx.subject_code == "VENOUS" and uid_of(catalog, ctx.question_id) == "VENOUS-0001"
    assert ctx.notice is ContextNotice.QUESTION_FILTERED


def test_missing_question_shows_the_first_question_of_the_filter(catalog: CatalogService) -> None:
    ctx = restore_context(catalog, SavedSession("VENOUS", None, "VENOUS-0999"))
    assert uid_of(catalog, ctx.question_id) == "VENOUS-0001" and ctx.subject_code == "VENOUS"
    assert ctx.notice is ContextNotice.QUESTION_MISSING and ctx.requested_uid == "VENOUS-0999"
    everywhere = restore_context(catalog, SavedSession(None, None, "ALI-0999"))
    assert uid_of(catalog, everywhere.question_id) == "PAD_CLTI-0001"  # first in the app's subject order


def test_missing_question_with_an_empty_status_filter_relaxes_it(catalog: CatalogService) -> None:
    ctx = restore_context(catalog, SavedSession("ALI", ReviewStatus.APPROVED, "ALI-0999"))
    assert ctx.status is None and uid_of(catalog, ctx.question_id) == "ALI-0001"
    assert ctx.notice is ContextNotice.QUESTION_MISSING


def test_subject_without_questions_is_reset(catalog: CatalogService) -> None:
    ctx = restore_context(catalog, SavedSession("MESENTERIC_RENAL", None, "ALI-0003"))
    assert ctx.subject_code is None and uid_of(catalog, ctx.question_id) == "ALI-0003"
    assert ctx.notice is ContextNotice.SUBJECT_RESET


def test_empty_database_restores_to_nothing(app_paths: AppPaths) -> None:
    seed(app_paths, [])
    ctx = restore_context(CatalogService(app_paths), SavedSession("ALI", ReviewStatus.APPROVED, "ALI-0001"))
    assert ctx.question_id is None and ctx.subject_code is None


def test_restoring_never_modifies_the_database(catalog: CatalogService, app_paths: AppPaths) -> None:
    before = snapshot(app_paths)
    for saved in (
        SavedSession("ALI", None, "ALI-0007"),
        SavedSession("ALI", ReviewStatus.APPROVED, "ALI-0007"),
        SavedSession("ALI", None, "ALI-9999"),
        SavedSession("VENOUS", ReviewStatus.REJECTED, "ALI-0001"),
    ):
        restore_context(catalog, saved)
    assert snapshot(app_paths) == before
