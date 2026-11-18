"""Read-only comparison of a proposed source with the working database (synthetic data only)."""

from __future__ import annotations

from pathlib import Path

import pytest
from bank import approve, simulate_edit, snapshot
from seedbank import small_seed
from synthetic import question_block, write_pair

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services import import_service
from vascqbank_desktop.services.seed_compare import Difference, compare_seed, compare_sources
from vascqbank_desktop.services.seed_service import SeedInvalid, sha256_file
from vascqbank_desktop.services.storage import initialize_storage


@pytest.fixture
def working(app_paths: AppPaths, tmp_path: Path) -> AppPaths:
    """ALI 1–10 and VENOUS 1–4 imported; ALI-0002 approved, ALI-0003 edited."""
    source = tmp_path / "first"
    write_pair(source, "ALI", range(1, 11))
    write_pair(source, "VENOUS", range(1, 5))
    initialize_storage(app_paths)
    import_service.commit_import(app_paths, import_service.dry_run(app_paths, [source]))
    approve(app_paths, "ALI-0002")
    simulate_edit(app_paths, "ALI-0003", stem="რეცენზენტის შესწორებული ტექსტი")
    return app_paths


def proposal(tmp_path: Path) -> Path:
    """ALI 1–12 (1 and 2 changed in the source), VENOUS 1–2: new, identical, conflicting, missing."""
    folder = tmp_path / "proposed"
    changed = {1, 2}
    blocks = [
        question_block("ALI", n).replace("ხელოვნურ", "შეცვლილ ხელოვნურ")
        if n in changed
        else question_block("ALI", n)
        for n in range(1, 13)
    ]
    write_pair(folder, "ALI", range(1, 13), questions_text="\n\n".join(blocks) + "\n")
    write_pair(folder, "VENOUS", range(1, 3))
    return folder


def test_txt_sources_are_classified_without_changing_anything(working: AppPaths, tmp_path: Path) -> None:
    before = snapshot(working)
    database_hash = sha256_file(working.database_file)
    report = compare_sources(working, [proposal(tmp_path)])
    assert report.new == ("ALI-0011", "ALI-0012")
    assert report.missing == ("VENOUS-0003", "VENOUS-0004")
    assert {c.uid for c in report.conflicts} == {"ALI-0001", "ALI-0002"}
    assert "ALI-0003" in report.identical  # the SOURCE is unchanged; the reviewer's edit is irrelevant here
    assert len(report.identical) == 8 + 2  # ALI 3–10 and VENOUS 1–2
    reviewed = {c.uid: c for c in report.conflicts}
    assert reviewed["ALI-0002"].working_status is ReviewStatus.APPROVED and reviewed["ALI-0002"].reviewed
    assert not reviewed["ALI-0001"].reviewed
    assert report.counts == {
        Difference.NEW: 2,
        Difference.IDENTICAL: 10,
        Difference.CONFLICT: 2,
        Difference.MISSING: 2,
    }
    assert snapshot(working) == before and sha256_file(working.database_file) == database_hash


def test_invalid_and_duplicate_items_are_counted_not_classified(working: AppPaths, tmp_path: Path) -> None:
    folder = tmp_path / "broken"
    text = "\n\n".join(
        [question_block("ALI", 1), question_block("ALI", 1), "15. მხოლოდ კითხვა, პასუხების გარეშე"]
    )
    write_pair(folder, "ALI", (1, 15), questions_text=text + "\n")
    report = compare_sources(working, [folder])
    assert report.duplicates == 1 and report.invalid >= 1
    assert "ALI-0001" not in report.identical and "ALI-0001" not in report.new


def test_a_seed_folder_is_verified_and_compared_read_only(working: AppPaths, tmp_path: Path) -> None:
    seed = small_seed(tmp_path / "seeds")  # ALI 1–20, VENOUS 1,2,3,5,8,13 (synthetic)
    before = snapshot(working)
    report = compare_seed(working, seed)
    assert report.source_label.startswith("DEVELOPMENT")
    assert report.new == tuple(f"ALI-{n:04d}" for n in range(11, 21)) + (
        "VENOUS-0005",
        "VENOUS-0008",
        "VENOUS-0013",
    )
    assert report.missing == ("VENOUS-0004",)
    assert report.conflicts == () and len(report.identical) == 13
    assert snapshot(working) == before


def test_a_damaged_seed_is_refused_before_any_comparison(working: AppPaths, tmp_path: Path) -> None:
    seed = small_seed(tmp_path / "seeds")
    (seed / "seed-manifest.json").chmod(0o644)
    (seed / "seed-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SeedInvalid):
        compare_seed(working, seed)


def test_an_empty_working_database_sees_everything_as_new(app_paths: AppPaths, tmp_path: Path) -> None:
    initialize_storage(app_paths)
    report = compare_sources(app_paths, [proposal(tmp_path)])
    assert len(report.new) == 14 and report.missing == () and report.conflicts == ()


def test_the_comparison_window_shows_the_classes_and_changes_nothing(
    qtbot, styled_qapp, working: AppPaths, tmp_path: Path
) -> None:
    from vascqbank_desktop.ui import strings
    from vascqbank_desktop.ui.compare_dialog import CompareDialog

    before = snapshot(working)
    dialog = CompareDialog(working)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.compare(proposal(tmp_path))
    qtbot.waitUntil(lambda: not dialog.is_busy(), timeout=10_000)
    summary = dialog.summary_label.text()
    assert f"{strings.CMP_KINDS['NEW']}: 2" in summary and f"{strings.CMP_KINDS['CONFLICT']}: 2" in summary
    assert strings.CMP_REVIEWED.format(count="1") in summary  # ALI-0002 is approved
    assert "ALI-0011, ALI-0012" in dialog.details.toPlainText()
    seed = small_seed(tmp_path / "seeds")
    dialog.compare(seed)
    qtbot.waitUntil(lambda: not dialog.is_busy(), timeout=10_000)
    assert "DEVELOPMENT" in dialog.summary_label.text()
    assert snapshot(working) == before
    dialog.reject()
