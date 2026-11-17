"""In-process GUI smoke tests (pytest-qt).

They build the real main window on the offscreen Qt platform (see conftest).
They prove the widget tree, wiring and fonts are correct; they do not replace a
visual check on a real Windows or Linux desktop.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QIcon  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel  # noqa: E402

from vascqbank_desktop import __version__, resources  # noqa: E402
from vascqbank_desktop.config.constants import APP_NAME_KA  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.fonts import (  # noqa: E402
    GEORGIAN_FAMILY,
    LATIN_FAMILY,
    build_ui_font,
    register_bundled_fonts,
)
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.theme import apply_theme  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def styled_app():
    app = QApplication.instance() or QApplication([])
    families = register_bundled_fonts()
    app.setFont(build_ui_font())
    apply_theme(app)
    return app, families


@pytest.fixture
def window(qtbot, styled_app, app_paths):
    status = initialize_storage(app_paths)
    icon = QIcon(str(resources.resource_path("icons", "app.svg")))
    win = MainWindow(status, SUBJECTS, app_icon=icon)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    return win


def test_bundled_fonts_cover_georgian_and_latin(styled_app) -> None:
    _app, families = styled_app
    assert GEORGIAN_FAMILY in families
    assert LATIN_FAMILY in families
    assert GEORGIAN_FAMILY in QFontDatabase.families()
    georgian = QFontMetrics(QFont(GEORGIAN_FAMILY))
    latin = QFontMetrics(QFont(LATIN_FAMILY))
    assert all(georgian.inFontUcs4(ord(c)) for c in "აბგდე")
    assert all(latin.inFontUcs4(ord(c)) for c in "EVAR09*„“×")
    assert QApplication.font().families()[:2] == [LATIN_FAMILY, GEORGIAN_FAMILY]


def test_icon_resource_loads(styled_app) -> None:
    assert not QIcon(str(resources.resource_path("icons", "app.svg"))).isNull()


def test_window_title_is_georgian(window) -> None:
    assert APP_NAME_KA in window.windowTitle()
    assert window.isVisible()


def test_sidebar_navigation_has_working_home_page(window) -> None:
    nav = window.sidebar.nav_list
    assert nav.count() == 1
    assert nav.currentItem().text() == strings.NAV_HOME
    assert window.pages.currentWidget() is window.welcome_view


def test_twelve_subject_placeholders_are_not_interactive(window) -> None:
    labels = window.sidebar.subject_labels
    assert [lbl.property("subjectCode") for lbl in labels] == [s.code for s in SUBJECTS]
    assert [lbl.text() for lbl in labels] == [s.name_ka for s in SUBJECTS]
    for label, subject in zip(labels, SUBJECTS, strict=True):
        assert isinstance(label, QLabel)
        assert subject.code in label.toolTip()
        flags = label.textInteractionFlags()
        assert not flags & Qt.TextInteractionFlag.TextSelectableByMouse
        assert not flags & Qt.TextInteractionFlag.TextEditable
        assert not label.openExternalLinks()
    assert window.sidebar.subject_area.findChildren(QAbstractButton) == []


def test_no_visible_push_buttons_pretend_to_work(window) -> None:
    visible_buttons = [b for b in window.findChildren(QAbstractButton) if b.isVisible()]
    assert visible_buttons == []


def test_welcome_empty_state_and_real_storage_facts(window, app_paths) -> None:
    view = window.welcome_view
    assert view.empty_title.text() == strings.EMPTY_TITLE
    assert view.storage_path_label.text() == str(app_paths.database_file)
    status_texts = [w.text() for w in window.statusBar().findChildren(QLabel)]
    assert "სქემა v1" in status_texts
    assert f"ვერსია {__version__}" in status_texts


def test_menus_have_real_actions(window, app_paths) -> None:
    assert not window.action_quit.shortcut().isEmpty()
    about = window.about_text()
    assert __version__ in about and str(app_paths.data_dir) in about
    window.action_quit.trigger()
    assert not window.isVisible()


def test_minimum_size_is_respected(window) -> None:
    window.resize(400, 300)
    assert window.width() >= window.minimumWidth() >= 1000
