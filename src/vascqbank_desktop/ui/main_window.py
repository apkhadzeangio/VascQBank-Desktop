"""Main application window."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from vascqbank_desktop import __version__
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import Subject
from vascqbank_desktop.services.catalog_service import CatalogService, SearchIndexStatus
from vascqbank_desktop.services.question_stats import BankCounts, bank_counts, summarize
from vascqbank_desktop.services.session_service import restore_context
from vascqbank_desktop.services.settings_service import ReviewerProfile, SavedSession, SettingsStore
from vascqbank_desktop.services.storage import StorageStatus
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.backup_view import BackupView
from vascqbank_desktop.ui.catalog_view import CatalogView
from vascqbank_desktop.ui.import_dialog import ImportDialog
from vascqbank_desktop.ui.reviewer_view import ReviewerView
from vascqbank_desktop.ui.sidebar import (
    PAGE_BACKUPS,
    PAGE_CATALOG,
    PAGE_HOME,
    PAGE_REVIEWER,
    Sidebar,
)
from vascqbank_desktop.ui.welcome_view import WelcomeView
from vascqbank_desktop.ui.window_state import encode_geometry, restore_geometry
from vascqbank_desktop.ui.workers import BackgroundJob

log = logging.getLogger(__name__)

MIN_WIDTH = 1080
MIN_HEIGHT = 680
DEFAULT_WIDTH = 1240
DEFAULT_HEIGHT = 800
SESSION_SAVE_DELAY_MS = 500


class MainWindow(QMainWindow):
    def __init__(
        self,
        status: StorageStatus,
        subjects: Sequence[Subject],
        app_icon: QIcon | None = None,
        settings_store: SettingsStore | None = None,
    ) -> None:
        super().__init__()
        self._status = status
        self._paths = AppPaths(data_dir=status.data_dir)
        self._subjects = {s.code: s for s in subjects}
        self._counts: BankCounts | None = None
        self.settings_store = settings_store or SettingsStore(self._paths.settings_file)
        self.reviewer_profile = ReviewerProfile(self.settings_store)
        self.catalog_service = CatalogService(self._paths)
        self.index_status: SearchIndexStatus | None = None
        self._warmup_job: BackgroundJob | None = None
        self._warmup_again = False
        self._pending_session: SavedSession | None = None

        self.setWindowTitle(strings.WINDOW_TITLE)
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar(subjects, app_icon=app_icon)
        self.pages = QStackedWidget()
        self._page_index: dict[str, int] = {}
        self.welcome_view = WelcomeView(status)
        self.catalog_view = CatalogView(self.catalog_service, subjects)
        self.backup_view = BackupView(self._paths)
        self.reviewer_view = ReviewerView(self.reviewer_profile)
        self._add_page(PAGE_HOME, self.welcome_view)
        self._add_page(PAGE_CATALOG, self.catalog_view)
        self._add_page(PAGE_BACKUPS, self.backup_view)
        self._add_page(PAGE_REVIEWER, self.reviewer_view)

        root.addWidget(self.sidebar)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        self._session_timer = QTimer(self)
        self._session_timer.setSingleShot(True)
        self._session_timer.setInterval(SESSION_SAVE_DELAY_MS)
        self._session_timer.timeout.connect(self._flush_session)

        self.sidebar.pageRequested.connect(self.show_page)
        self.sidebar.subjectRequested.connect(self.open_subject)
        self.welcome_view.importRequested.connect(self.open_import_dialog)
        self.welcome_view.resumeRequested.connect(lambda: self.show_page(PAGE_CATALOG))
        self.catalog_view.importRequested.connect(self.open_import_dialog)
        self.catalog_view.contextChanged.connect(self._on_context_changed)
        self.catalog_view.questionOpened.connect(self.welcome_view.set_resume)
        self.reviewer_view.reviewerChanged.connect(self._on_reviewer_changed)
        self._build_menus()
        self._build_status_bar()

        restore_geometry_ok = restore_geometry(self, self.settings_store.load().window_geometry)
        if not restore_geometry_ok:
            self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.refresh_counts()
        self._restore_session()
        self._start_index_warmup()

    # -- data ------------------------------------------------------------
    def refresh_counts(self) -> BankCounts:
        counts = bank_counts(self._paths)
        self._counts = counts
        self.sidebar.set_counts({code: s.total for code, s in counts.subjects.items()})
        with_questions = sum(1 for s in counts.subjects.values() if s.total)
        self.welcome_view.set_bank_counts(counts.total, with_questions)
        self.catalog_view.set_counts(counts)
        self._update_progress(self.catalog_view.current_filter.subject_code)
        self.bank_label.setText(strings.STATUS_BANK.format(total=counts.total))
        return counts

    def _update_progress(self, subject_code: str | None) -> None:
        if self._counts is None:
            return
        subject = self._subjects.get(subject_code) if subject_code else None
        self.welcome_view.set_progress(
            summarize(self._counts, None),
            summarize(self._counts, subject_code) if subject else None,
            subject.name_ka if subject else None,
        )

    def create_import_dialog(self) -> ImportDialog:
        dialog = ImportDialog(self._paths, self)
        dialog.importFinished.connect(lambda _result: self._after_import())
        return dialog

    def open_import_dialog(self) -> None:
        dialog = self.create_import_dialog()
        dialog.exec()
        dialog.deleteLater()

    def _after_import(self) -> None:
        self.refresh_counts()
        self.catalog_view.refresh()
        self._start_index_warmup()

    # -- pages -----------------------------------------------------------
    def _add_page(self, key: str, widget: QWidget) -> None:
        self._page_index[key] = self.pages.addWidget(widget)

    def show_page(self, key: str) -> None:
        index = self._page_index.get(key)
        if index is None:
            return
        self.pages.setCurrentIndex(index)
        self.sidebar.select_page(key)
        if key == PAGE_BACKUPS:
            self.backup_view.refresh()
        elif key == PAGE_REVIEWER:
            self.reviewer_view.refresh()

    def open_subject(self, code: str | None) -> None:
        """Browse one subject (or all subjects when *code* is ``None``)."""
        self.catalog_view.set_subject(code)
        self.show_page(PAGE_CATALOG)

    # -- working session -----------------------------------------------------
    def _restore_session(self) -> None:
        """Return to where the reviewer stopped; read-only, never changes a status."""
        saved = self.settings_store.load().session
        if self._counts is None or self._counts.total == 0:
            return
        context = restore_context(self.catalog_service, saved)
        self.catalog_view.restore(context)
        if context.question_id is not None:
            self.show_page(PAGE_CATALOG)

    def _on_context_changed(self, subject: object, status: object, uid: object) -> None:
        subject_code = subject if isinstance(subject, str) else None
        self.sidebar.set_selected_subject(subject_code)
        self._update_progress(subject_code)
        self._pending_session = SavedSession(
            subject_code,
            status if isinstance(status, ReviewStatus) else None,
            uid if isinstance(uid, str) else None,
        )
        self._session_timer.start()  # coalesces bursts, e.g. holding the arrow key in the list

    def _flush_session(self) -> None:
        session, self._pending_session = self._pending_session, None
        self._session_timer.stop()
        if session is None:
            return
        try:
            self.settings_store.set_session(session)
        except OSError as exc:
            log.warning("Could not save the working session: %s", exc)

    # -- reviewer ----------------------------------------------------------------
    def _on_reviewer_changed(self, name: str) -> None:
        self.reviewer_label.setText(strings.STATUS_REVIEWER.format(name=name))

    # -- search index warm-up --------------------------------------------------------
    def _start_index_warmup(self) -> None:
        """Build/refresh the full-text index in the background; searches work (via LIKE) meanwhile."""
        if self._counts is None or self._counts.total == 0 or not self.catalog_service.fts_available:
            return
        if self._warmup_job is not None:
            self._warmup_again = True
            return
        job = BackgroundJob(self.catalog_service.ensure_search_index, self)
        job.succeeded.connect(lambda result: self._on_warmup_done(job, result))
        job.failed.connect(lambda error: self._on_warmup_failed(job, error))
        self._warmup_job = job
        job.start()

    def _on_warmup_done(self, job: BackgroundJob, result: object) -> None:
        self._warmup_job = None
        job.deleteLater()
        if isinstance(result, SearchIndexStatus):
            self.index_status = result
        if self._warmup_again:
            self._warmup_again = False
            self._start_index_warmup()

    def _on_warmup_failed(self, job: BackgroundJob, error: object) -> None:
        self._warmup_job = None
        job.deleteLater()
        log.warning("Search index warm-up failed: %s", error)

    def wait_for_background_work(self, timeout_ms: int = 30_000) -> None:
        """Block until the index warm-up has finished (tests and orderly shutdown)."""
        job = self._warmup_job
        if job is not None:
            job.stop_and_wait(timeout_ms)
            self._warmup_job = None

    # -- chrome ----------------------------------------------------------
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu(strings.MENU_FILE)

        self.action_import = QAction(strings.ACTION_IMPORT, self)
        self.action_import.setShortcut(QKeySequence("Ctrl+I"))
        self.action_import.triggered.connect(self.open_import_dialog)
        file_menu.addAction(self.action_import)

        self.action_open_data_dir = QAction(strings.ACTION_OPEN_DATA_DIR, self)
        self.action_open_data_dir.triggered.connect(self._open_data_dir)
        file_menu.addAction(self.action_open_data_dir)
        file_menu.addSeparator()

        self.action_quit = QAction(strings.ACTION_QUIT, self)
        # StandardKey.Quit is empty on Windows, so set Ctrl+Q explicitly.
        self.action_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.action_quit.triggered.connect(self.close)
        file_menu.addAction(self.action_quit)

        questions_menu = self.menuBar().addMenu(strings.MENU_QUESTIONS)
        self.action_search = self._catalog_action(
            questions_menu, strings.ACTION_SEARCH, "Ctrl+F", self.catalog_view.focus_search
        )
        self.action_goto = self._catalog_action(
            questions_menu, strings.ACTION_GOTO, "Ctrl+G", self.catalog_view.focus_goto
        )
        questions_menu.addSeparator()
        self.action_previous = self._catalog_action(
            questions_menu, strings.ACTION_PREVIOUS, "Alt+Left", self.catalog_view.previous_question
        )
        self.action_next = self._catalog_action(
            questions_menu, strings.ACTION_NEXT, "Alt+Right", self.catalog_view.next_question
        )

        help_menu = self.menuBar().addMenu(strings.MENU_HELP)
        self.action_about = QAction(strings.ACTION_ABOUT, self)
        self.action_about.triggered.connect(self._show_about)
        help_menu.addAction(self.action_about)

    def _catalog_action(self, menu, title: str, shortcut: str, handler) -> QAction:  # noqa: ANN001
        action = QAction(title, self)
        action.setShortcut(QKeySequence(shortcut))

        def run() -> None:
            self.show_page(PAGE_CATALOG)
            handler()

        action.triggered.connect(run)
        menu.addAction(action)
        return action

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(True)
        bar.addWidget(QLabel(strings.STATUS_OFFLINE))
        self.bank_label = QLabel()
        bar.addWidget(self.bank_label)
        name = self.reviewer_profile.name
        self.reviewer_label = QLabel(
            strings.STATUS_REVIEWER.format(name=name) if name else strings.STATUS_REVIEWER_NONE
        )
        bar.addPermanentWidget(self.reviewer_label)
        bar.addPermanentWidget(
            QLabel(strings.STATUS_SCHEMA.format(version=self._status.schema_version))
        )
        bar.addPermanentWidget(QLabel(strings.STATUS_VERSION.format(version=__version__)))

    # -- actions ---------------------------------------------------------
    def _open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._status.data_dir)))

    def about_text(self) -> str:
        return strings.ABOUT_TEXT.format(
            title=strings.WINDOW_TITLE,
            version=__version__,
            data_dir=self._status.data_dir,
            schema=self._status.schema_version,
            sqlite=self._status.sqlite_version,
        )

    def _show_about(self) -> None:
        QMessageBox.about(self, strings.ABOUT_TITLE, self.about_text())

    # -- shutdown --------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._flush_session()
        try:
            self.settings_store.set_window_geometry(encode_geometry(self))
        except OSError as exc:
            log.warning("Could not save the window geometry: %s", exc)
        self.catalog_view.shutdown()
        self.backup_view.shutdown()
        self.wait_for_background_work()
        super().closeEvent(event)
