"""Settings store: reviewer name, session state, atomic writes, corruption, historical events."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from bank import make_question, seed, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.reviewer import InvalidReviewerName
from vascqbank_desktop.services import settings_service
from vascqbank_desktop.services.settings_service import (
    AppSettings,
    ReviewerNotSetError,
    ReviewerProfile,
    SavedSession,
    SettingsStore,
    parse_settings,
)


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "მონაცემები" / "settings.json")


def test_settings_live_in_the_data_directory_beside_the_database(app_paths: AppPaths) -> None:
    assert app_paths.settings_file == app_paths.data_dir / "settings.json"
    assert app_paths.search_index_file.parent == app_paths.database_file.parent


def test_defaults_when_there_is_no_file(store: SettingsStore) -> None:
    settings = store.load()
    assert settings == AppSettings()
    assert settings.reviewer_name == "" and settings.session == SavedSession()


def test_reviewer_name_round_trips_in_georgian(store: SettingsStore) -> None:
    assert store.set_reviewer_name("  რამაზი   აფხაძე ") == "რამაზი აფხაძე"
    reloaded = SettingsStore(store.path).load()  # a fresh store = a new program start
    assert reloaded.reviewer_name == "რამაზი აფხაძე"
    raw = store.path.read_text(encoding="utf-8")
    assert "რამაზი აფხაძე" in raw  # stored as readable UTF-8, not \u escapes
    assert json.loads(raw)["version"] == settings_service.SETTINGS_VERSION


def test_invalid_name_is_rejected_and_nothing_changes(store: SettingsStore) -> None:
    store.set_reviewer_name("დავით ფარღალავა")
    for bad in ("", "   ", "x" * 200, "a\u0000b"):
        with pytest.raises(InvalidReviewerName):
            store.set_reviewer_name(bad)
    assert store.load().reviewer_name == "დავით ფარღალავა"


def test_recent_names_are_remembered_most_recent_first(store: SettingsStore) -> None:
    for name in ("რამაზი აფხაძე", "დავით ფარღალავა", "Custom Name", "რამაზი აფხაძე"):
        store.set_reviewer_name(name)
    settings = store.load()
    assert settings.reviewer_name == "რამაზი აფხაძე"
    assert settings.recent_reviewers == ("რამაზი აფხაძე", "Custom Name", "დავით ფარღალავა")


def test_session_state_round_trips(store: SettingsStore) -> None:
    session = SavedSession("PAD_CLTI", ReviewStatus.IN_REVIEW, "PAD_CLTI-0042")
    store.set_session(session)
    assert SettingsStore(store.path).load().session == session
    store.set_session(SavedSession())  # "all subjects, no filter, nothing open"
    assert store.load().session == SavedSession()


def test_saving_one_part_keeps_the_others(store: SettingsStore) -> None:
    store.set_reviewer_name("დავით ფარღალავა")
    store.set_session(SavedSession("ALI", None, "ALI-0007"))
    store.set_window_geometry("QUJDRA==")
    store.set_session(SavedSession("VENOUS", ReviewStatus.APPROVED, None))
    loaded = store.load()
    assert loaded.reviewer_name == "დავით ფარღალავა"
    assert loaded.window_geometry == "QUJDRA=="
    assert loaded.session == SavedSession("VENOUS", ReviewStatus.APPROVED, None)


def test_unknown_keys_from_a_newer_version_survive_a_save(store: SettingsStore) -> None:
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps({"version": 9, "reviewer_name": "ა ბ", "future_feature": {"x": [1, 2, 3]}}),
        encoding="utf-8",
    )
    store.set_session(SavedSession("ALI", None, None))
    assert json.loads(store.path.read_text(encoding="utf-8"))["future_feature"] == {"x": [1, 2, 3]}


@pytest.mark.parametrize(
    "payload",
    [
        {"session": {"subject": "NOT_A_SUBJECT", "status": "BOGUS", "question_uid": "not an id"}},
        {"session": "nonsense"},
        {"session": {"subject": 7, "status": 7, "question_uid": 7}},
        {"session": {"question_uid": "ALI-00001"}},  # non-canonical id
        {"reviewer_name": 123, "recent_reviewers": "x", "window": 5},
        {"reviewer_name": "a\u0000b", "recent_reviewers": ["​", "ok name", "ok name", 3]},
        [1, 2, 3],
        None,
    ],
)
def test_invalid_values_fall_back_to_defaults_individually(payload) -> None:
    settings = parse_settings(payload)
    assert settings.session == SavedSession()
    assert settings.reviewer_name == ""
    assert settings.window_geometry is None
    assert set(settings.recent_reviewers) <= {"ok name"}


def test_valid_parts_survive_next_to_invalid_ones() -> None:
    settings = parse_settings(
        {
            "reviewer_name": "დავით ფარღალავა",
            "session": {"subject": "ALI", "status": "NOPE", "question_uid": "ALI-0042"},
        }
    )
    assert settings.reviewer_name == "დავით ფარღალავა"
    assert settings.session == SavedSession("ALI", None, "ALI-0042")


def test_oversized_or_non_ascii_geometry_is_ignored() -> None:
    assert parse_settings({"window": {"geometry": "A" * 100_000}}).window_geometry is None
    assert parse_settings({"window": {"geometry": "ა"}}).window_geometry is None
    assert parse_settings({"window": {"geometry": "QUJD"}}).window_geometry == "QUJD"


@pytest.mark.parametrize("content", [b"{ not json", b"\xff\xfe\x00garbage", b"", b"[" * 50_000])
def test_corrupt_file_is_moved_aside_and_defaults_are_used(store: SettingsStore, content: bytes) -> None:
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(content)
    assert store.load() == AppSettings()  # never raises
    quarantined = list(store.path.parent.glob("settings.json.corrupt-*"))
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == content  # evidence kept
    assert not store.path.exists()
    store.set_reviewer_name("ა ბ")  # and the application can carry on writing
    assert store.load().reviewer_name == "ა ბ"


def test_a_failed_write_leaves_the_previous_file_intact(
    store: SettingsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_reviewer_name("პირველი სახელი")
    before = store.path.read_bytes()

    def crash(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)  # the atomic rename is the commit point
    with pytest.raises(OSError, match="disk full"):
        store.set_reviewer_name("მეორე სახელი")
    monkeypatch.undo()
    assert store.path.read_bytes() == before
    assert store.load().reviewer_name == "პირველი სახელი"
    assert [p.name for p in store.path.parent.iterdir() if p.suffix == ".tmp"] == []  # no litter


def test_writes_go_through_a_temporary_file_in_the_same_directory(
    store: SettingsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    store.set_reviewer_name("ა ბ")
    ((src, dst),) = seen
    assert Path(src).parent == Path(dst).parent == store.path.parent and Path(dst) == store.path


def test_concurrent_writers_never_produce_a_torn_file(store: SettingsStore) -> None:
    names = [f"სახელი {i}" for i in range(40)]
    errors: list[BaseException] = []

    def worker(chunk: list[str]) -> None:
        try:
            for name in chunk:
                store.set_reviewer_name(name)
                assert store.load().reviewer_name  # always a complete, parseable file
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(names[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert store.load().reviewer_name in names
    assert not list(store.path.parent.glob("settings.json.corrupt-*"))


def test_reviewer_profile_for_future_services(store: SettingsStore) -> None:
    profile = ReviewerProfile(store)
    assert profile.name == ""
    with pytest.raises(ReviewerNotSetError):
        profile.require_name()  # an edit/approval cannot be attributed to nobody
    assert profile.set_name(" დავით  ფარღალავა ") == "დავით ფარღალავა"
    assert profile.require_name() == "დავით ფარღალავა"
    assert profile.recent_names() == ("დავით ფარღალავა",)


def test_changing_the_reviewer_name_never_touches_history(app_paths: AppPaths, store: SettingsStore) -> None:
    seed(app_paths, [make_question("ALI", n) for n in range(1, 6)])
    before = snapshot(app_paths)
    profile = ReviewerProfile(store)
    profile.set_name("რამაზი აფხაძე")
    profile.set_name("დავით ფარღალავა")
    assert snapshot(app_paths) == before  # revisions, review events, import batches: all unchanged
    raw = sqlite3.connect(app_paths.database_file)
    try:
        assert raw.execute("SELECT DISTINCT author FROM question_revisions").fetchall() == [("იმპორტი",)]
        assert raw.execute("SELECT DISTINCT actor FROM review_events").fetchall() == [("იმპორტი",)]
    finally:
        raw.close()
