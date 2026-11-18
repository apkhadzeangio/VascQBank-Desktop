"""Phase 9 restore matrix: the same restore contract for every kind of database a reviewer may have.

For each case — empty database, partial bank, bank with saved drafts, bank with approved and rejected
questions, full synthetic 10,000-question bank — the test backs up, does more legitimate work,
restores the backup after explicit confirmation and then proves:

* the restored database equals the backup state exactly (rows, revisions, events, drafts);
* the replaced database is preserved bit-for-bit in ``displaced/`` and in the safety backup, and
  restoring that safety backup brings the later work back (the previous valid database stays
  recoverable);
* integrity and foreign keys are intact, the program starts normally afterwards.

The later work being rolled back is the intended effect of an explicit restore. Synthetic data only.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from bank import approve, make_question, seed, seed_counts, set_status
from test_phase9_acceptance import check_integrity, state

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.services import backup_service, restore_service
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.draft_service import DraftService
from vascqbank_desktop.services.startup import Outcome, prepare_storage
from vascqbank_desktop.services.storage import initialize_storage

REVIEWER = "სინთეზური რეცენზენტი"


def _drafts(paths: AppPaths, uids: list[str]) -> None:
    catalog, drafts = CatalogService(paths), DraftService(paths)
    for uid in uids:
        d = catalog.detail_by_uid(uid)
        assert d is not None
        c = d.content
        changed = type(c)(c.stem + " — სინთეზური დრაფტი", c.options, c.correct, c.explanation)
        drafts.save_draft(d.id, d.revision_id, d.lock_version, changed, REVIEWER)


def build_empty(paths: AppPaths) -> None:
    initialize_storage(paths)


def build_partial(paths: AppPaths) -> None:
    seed(paths, [make_question("ALI", n) for n in (1, 2, 5, 9, 14)] + [make_question("VENOUS", 3)])


def build_with_drafts(paths: AppPaths) -> None:
    build_partial(paths)
    _drafts(paths, ["ALI-0002", "ALI-0009", "VENOUS-0003"])


def build_with_approved(paths: AppPaths) -> None:
    build_partial(paths)
    approve(paths, "ALI-0001")
    approve(paths, "ALI-0014")
    set_status(paths, "ALI-0005", ReviewStatus.REJECTED)


def build_full(paths: AppPaths) -> None:
    seed_counts(paths, {s.code: s.target_count for s in SUBJECTS})
    approve(paths, "ALI-0001")
    _drafts(paths, ["ALI-0002"])


def later_work(paths: AppPaths) -> None:
    """Legitimate work after the backup: a new import batch and a draft on it."""
    seed(paths, [make_question("PAD_CLTI", n) for n in (1, 2, 3)])
    _drafts(paths, ["PAD_CLTI-0002"])


CASES: dict[str, Callable[[AppPaths], None]] = {
    "empty": build_empty,
    "partial": build_partial,
    "drafts": build_with_drafts,
    "approved": build_with_approved,
    "full-synthetic-10000": build_full,
}


@pytest.mark.parametrize(
    "case",
    [pytest.param(name, marks=pytest.mark.perf) if name.startswith("full") else name for name in CASES],
)
def test_restore_matrix(app_paths: AppPaths, case: str) -> None:
    CASES[case](app_paths)
    entry = backup_service.create_manual_backup(app_paths)
    at_backup = state(app_paths.database_file)
    summary = restore_service.inspect_backup(entry.path)
    assert summary.questions == len(at_backup["questions"])
    assert summary.drafts == len(at_backup["drafts"])

    later_work(app_paths)
    before = state(app_paths.database_file)
    assert before != at_backup

    result = restore_service.restore_backup(app_paths, entry.path, expected_sha256=summary.sha256)
    started = prepare_storage(app_paths, None)
    assert started.outcome is Outcome.READY and started.restore_recovery is None
    check_integrity(app_paths.database_file)
    assert state(app_paths.database_file) == at_backup
    assert DraftService(app_paths).count() == len(at_backup["drafts"])  # saved drafts come back too

    [displaced] = [p for p in result.displaced if p.suffix == ".sqlite3"]
    check_integrity(displaced)
    assert state(displaced) == before
    assert result.safety_backup is not None

    # the previous valid database is recoverable through the normal, confirmed restore path
    safety = restore_service.inspect_backup(result.safety_backup)
    restore_service.restore_backup(app_paths, result.safety_backup, expected_sha256=safety.sha256)
    assert prepare_storage(app_paths, None).outcome is Outcome.READY
    check_integrity(app_paths.database_file)
    assert state(app_paths.database_file) == before
    assert entry.path.is_file() and result.safety_backup.is_file()  # restoring never deletes backups
