"""Local reviewer profile and working-session state.

Stored as one small JSON file, ``settings.json``, in the data directory — a
stable per-user location outside the source repository (see
:class:`vascqbank_desktop.config.paths.AppPaths`). Because it sits beside the
database, installations with different ``--data-dir`` keep separate profiles.

Reliability:

* **Atomic writes** — data goes to a temporary file in the same directory, is
  flushed to disk and then renamed over the old file, so a crash or power cut
  leaves either the complete old file or the complete new one.
* **Loading never fails** — a missing file gives defaults; an unreadable or
  corrupt file is moved aside (``settings.json.corrupt-<time>``) and defaults
  are used, so a damaged settings file can never stop the application.
* **Nothing is trusted** — every value is validated on load (known subject,
  known status, well-formed question ID, bounded sizes); invalid parts fall
  back to their defaults individually.
* Unknown keys written by a newer version are preserved on save.

The reviewer name is *not* written to the database here. Future edit and
approval services read it through :class:`ReviewerProfile` and stamp it on
new revisions and events only; existing history is append-only and unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vascqbank_desktop.domain.question_id import QuestionId
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.reviewer import (
    MAX_RECENT_REVIEWERS,
    InvalidReviewerName,
    normalize_reviewer_name,
    remember_reviewer,
)
from vascqbank_desktop.domain.subjects import is_known_subject
from vascqbank_desktop.importexport.text_io import write_utf8_text_atomic

log = logging.getLogger(__name__)

SETTINGS_VERSION = 1
MAX_GEOMETRY_CHARS = 16_384  # base64 of a Qt geometry blob is well under 1 KB
_REPLACE_RETRIES = 3


@dataclass(frozen=True, slots=True)
class SavedSession:
    """Where the reviewer stopped working."""

    subject_code: str | None = None
    status: ReviewStatus | None = None
    question_uid: str | None = None


@dataclass(frozen=True, slots=True)
class AppSettings:
    reviewer_name: str = ""
    recent_reviewers: tuple[str, ...] = ()
    session: SavedSession = field(default_factory=SavedSession)
    window_geometry: str | None = None  # base64 of QWidget.saveGeometry()
    # Top-level keys this version does not know, kept verbatim for forward compatibility.
    extra: dict[str, Any] = field(default_factory=dict, compare=False)


class ReviewerNotSetError(RuntimeError):
    """An action that must be attributed to a reviewer was attempted without a name."""


# --------------------------------------------------------------------------
# (de)serialisation
# --------------------------------------------------------------------------
def _clean_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return normalize_reviewer_name(value)
    except InvalidReviewerName:
        return ""


def _clean_session(value: object) -> SavedSession:
    if not isinstance(value, dict):
        return SavedSession()
    subject = value.get("subject")
    subject_code = subject if isinstance(subject, str) and is_known_subject(subject) else None
    status: ReviewStatus | None = None
    raw_status = value.get("status")
    if isinstance(raw_status, str):
        try:
            status = ReviewStatus(raw_status)
        except ValueError:
            status = None
    uid: str | None = None
    raw_uid = value.get("question_uid")
    if isinstance(raw_uid, str):
        try:
            uid = str(QuestionId.parse(raw_uid))
        except ValueError:
            uid = None
    return SavedSession(subject_code, status, uid)


def _clean_geometry(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= MAX_GEOMETRY_CHARS and value.isascii():
        return value
    return None


def parse_settings(data: object) -> AppSettings:
    if not isinstance(data, dict):
        return AppSettings()
    recent_raw = data.get("recent_reviewers")
    recent: list[str] = []
    if isinstance(recent_raw, list):
        for item in recent_raw:
            name = _clean_name(item)
            if name and name not in recent:
                recent.append(name)
    known = {"version", "reviewer_name", "recent_reviewers", "session", "window"}
    window = data.get("window")
    geometry = _clean_geometry(window.get("geometry")) if isinstance(window, dict) else None
    return AppSettings(
        reviewer_name=_clean_name(data.get("reviewer_name")),
        recent_reviewers=tuple(recent[:MAX_RECENT_REVIEWERS]),
        session=_clean_session(data.get("session")),
        window_geometry=geometry,
        extra={k: v for k, v in data.items() if k not in known},
    )


def serialize_settings(settings: AppSettings) -> str:
    session = settings.session
    payload: dict[str, Any] = {
        **settings.extra,
        "version": SETTINGS_VERSION,
        "reviewer_name": settings.reviewer_name,
        "recent_reviewers": list(settings.recent_reviewers),
        "session": {
            "subject": session.subject_code,
            "status": session.status.value if session.status else None,
            "question_uid": session.question_uid,
            "saved_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        },
        "window": {"geometry": settings.window_geometry},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------
class SettingsStore:
    """Reads and atomically writes ``settings.json``. Safe to call from any thread."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AppSettings:
        """Current settings; never raises."""
        with self._lock:
            try:
                raw = self._path.read_bytes()
            except FileNotFoundError:
                return AppSettings()
            except OSError as exc:
                log.warning("Settings file unreadable (%s); using defaults", exc)
                return AppSettings()
            try:
                return parse_settings(json.loads(raw.decode("utf-8")))
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                self._quarantine(exc)
                return AppSettings()

    def save(self, settings: AppSettings) -> None:
        """Atomically replace the settings file. Raises ``OSError`` if it cannot be written."""
        text = serialize_settings(settings)
        with self._lock:
            for attempt in range(1, _REPLACE_RETRIES + 1):
                try:
                    write_utf8_text_atomic(self._path, text)
                    return
                except PermissionError:
                    # Windows: an antivirus scanner or indexer can hold the target for a moment.
                    if attempt == _REPLACE_RETRIES:
                        raise
                    time.sleep(0.05 * attempt)

    def _quarantine(self, reason: object) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        aside = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        try:
            os.replace(self._path, aside)
        except OSError:
            aside = self._path
        log.warning("Settings file was corrupt (%s); kept as %s and using defaults", reason, aside)

    # -- targeted read-modify-write helpers -----------------------------------
    def update(self, **changes: Any) -> AppSettings:  # noqa: ANN401 - field names checked by replace()
        with self._lock:
            updated = replace(self.load(), **changes)
            self.save(updated)
            return updated

    def set_reviewer_name(self, raw_name: str) -> str:
        """Validate, store and remember a reviewer name; returns the canonical name.

        Raises :class:`InvalidReviewerName` without changing anything if the
        name is unusable.
        """
        name = normalize_reviewer_name(raw_name)
        with self._lock:
            current = self.load()
            self.save(
                replace(
                    current,
                    reviewer_name=name,
                    recent_reviewers=remember_reviewer(current.recent_reviewers, name),
                )
            )
        return name

    def set_session(self, session: SavedSession) -> None:
        with self._lock:
            self.save(replace(self.load(), session=session))

    def set_window_geometry(self, geometry: str | None) -> None:
        with self._lock:
            self.save(replace(self.load(), window_geometry=_clean_geometry(geometry)))


class ReviewerProfile:
    """The reviewer chosen on this computer, as future edit/approval services see it."""

    def __init__(self, store: SettingsStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        """The saved name, or ``""`` when none has been chosen yet."""
        return self._store.load().reviewer_name

    def require_name(self) -> str:
        """The name to attribute new work to; raises :class:`ReviewerNotSetError` if unset."""
        name = self.name
        if not name:
            raise ReviewerNotSetError("no reviewer name has been chosen on this computer")
        return name

    def set_name(self, raw_name: str) -> str:
        return self._store.set_reviewer_name(raw_name)

    def recent_names(self) -> tuple[str, ...]:
        return self._store.load().recent_reviewers
