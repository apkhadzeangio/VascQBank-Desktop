"""Main application window."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from vascqbank_desktop import __version__
from vascqbank_desktop.domain.subjects import Subject
from vascqbank_desktop.services.storage import StorageStatus
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.sidebar import PAGE_HOME, Sidebar
from vascqbank_desktop.ui.welcome_view import WelcomeView

MIN_WIDTH = 1080
MIN_HEIGHT = 680


class MainWindow(QMainWindow):
    def __init__(
        self,
        status: StorageStatus,
        subjects: Sequence[Subject],
        app_icon: QIcon | None = None,
    ) -> None:
        super().__init__()
        self._status = status
        self.setWindowTitle(strings.WINDOW_TITLE)
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.resize(1240, 800)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar(subjects, app_icon=app_icon)
        self.pages = QStackedWidget()
        self._page_index: dict[str, int] = {}
        self.welcome_view = WelcomeView(status)
        self._add_page(PAGE_HOME, self.welcome_view)

        root.addWidget(self.sidebar)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        self.sidebar.pageRequested.connect(self.show_page)
        self._build_menus()
        self._build_status_bar()

    # -- pages -----------------------------------------------------------
    def _add_page(self, key: str, widget: QWidget) -> None:
        self._page_index[key] = self.pages.addWidget(widget)

    def show_page(self, key: str) -> None:
        index = self._page_index.get(key)
        if index is not None:
            self.pages.setCurrentIndex(index)

    # -- chrome ----------------------------------------------------------
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu(strings.MENU_FILE)

        self.action_open_data_dir = QAction(strings.ACTION_OPEN_DATA_DIR, self)
        self.action_open_data_dir.triggered.connect(self._open_data_dir)
        file_menu.addAction(self.action_open_data_dir)
        file_menu.addSeparator()

        self.action_quit = QAction(strings.ACTION_QUIT, self)
        # StandardKey.Quit is empty on Windows, so set Ctrl+Q explicitly.
        self.action_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.action_quit.triggered.connect(self.close)
        file_menu.addAction(self.action_quit)

        help_menu = self.menuBar().addMenu(strings.MENU_HELP)
        self.action_about = QAction(strings.ACTION_ABOUT, self)
        self.action_about.triggered.connect(self._show_about)
        help_menu.addAction(self.action_about)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(True)
        bar.addWidget(QLabel(strings.STATUS_OFFLINE))
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
