"""Qt style sheet (QSS) for a calm, professional light theme.

Object names (``setObjectName``) are the styling hooks; widgets never embed
colours directly, so the palette lives in one place.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

ACCENT = "#7A1C36"  # burgundy — matches the application icon
ACCENT_SOFT = "#F6E9ED"
SIDEBAR_BG = "#1E2632"
SIDEBAR_TEXT = "#E6EAF0"
SIDEBAR_MUTED = "#8C97A8"
SIDEBAR_SELECTED = "#2C3747"
CONTENT_BG = "#F5F6F8"
CARD_BG = "#FFFFFF"
BORDER = "#E1E5EA"
TEXT = "#1C232D"
TEXT_MUTED = "#5E6A7A"
SUCCESS = "#2E7D4F"
DANGER = "#B3261E"

STYLESHEET = f"""
QMainWindow, QWidget#ContentArea {{
    background: {CONTENT_BG};
}}
QWidget {{
    color: {TEXT};
}}

/* ---- Sidebar ---------------------------------------------------- */
QWidget#Sidebar {{
    background: {SIDEBAR_BG};
}}
QWidget#Sidebar QLabel {{
    color: {SIDEBAR_TEXT};
    background: transparent;
}}
QLabel#BrandTitle {{
    font-size: 17pt;
    font-weight: 700;
}}
QLabel#BrandSubtitle {{
    color: {SIDEBAR_MUTED};
}}
QWidget#Sidebar QLabel#SectionLabel {{
    color: {SIDEBAR_MUTED};
    font-size: 8.5pt;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding-top: 6px;
}}
QWidget#Sidebar QLabel#SidebarHint {{
    color: {SIDEBAR_MUTED};
    font-size: 8.5pt;
}}
QListWidget#NavList {{
    background: transparent;
    border: none;
    outline: none;
}}
QScrollArea#SubjectScroll, QWidget#SubjectPlaceholders {{
    background: transparent;
    border: none;
}}
QWidget#Sidebar QLabel#SubjectItem {{
    color: {SIDEBAR_MUTED};
    padding: 4px 10px;
}}
QScrollArea#SubjectScroll QScrollBar:vertical {{
    background: transparent;
    width: 8px;
}}
QScrollArea#SubjectScroll QScrollBar::handle:vertical {{
    background: #3A4556;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollArea#SubjectScroll QScrollBar::add-line:vertical,
QScrollArea#SubjectScroll QScrollBar::sub-line:vertical {{
    height: 0;
}}
QListWidget#NavList::item {{
    color: {SIDEBAR_TEXT};
    padding: 8px 10px;
    border-radius: 6px;
    margin: 1px 0;
}}
QListWidget#NavList::item:selected {{
    background: {SIDEBAR_SELECTED};
    color: #FFFFFF;
    border-left: 3px solid {ACCENT};
}}
QFrame#SidebarDivider {{
    background: #2E3846;
    max-height: 1px;
    border: none;
}}

/* ---- Content ---------------------------------------------------- */
QLabel#PageHeading {{
    font-size: 19pt;
    font-weight: 700;
}}
QLabel#PageTagline {{
    color: {TEXT_MUTED};
    font-size: 11pt;
}}
QFrame#Card {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#EmptyStateCard {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-left: 4px solid {ACCENT};
    border-radius: 10px;
}}
QLabel#CardTitle {{
    font-size: 12.5pt;
    font-weight: 600;
}}
QLabel#CardBody {{
    color: {TEXT_MUTED};
}}
QLabel#FieldName {{
    color: {TEXT_MUTED};
}}
QLabel#FieldValue {{
    font-family: "Noto Sans", "Noto Sans Georgian", "Segoe UI", "DejaVu Sans";
}}
QLabel#StatusOk {{
    color: {SUCCESS};
    font-weight: 600;
}}
QLabel#StatusBad {{
    color: {DANGER};
    font-weight: 600;
}}
QLabel#RoadmapItem {{
    color: {TEXT_MUTED};
}}
QStatusBar {{
    background: {CARD_BG};
    border-top: 1px solid {BORDER};
    color: {TEXT_MUTED};
}}
QStatusBar QLabel {{
    color: {TEXT_MUTED};
    padding: 0 8px;
}}
QMenuBar {{
    background: {CARD_BG};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item:selected, QMenu::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QToolTip {{
    background: {SIDEBAR_BG};
    color: {SIDEBAR_TEXT};
    border: none;
    padding: 6px;
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")  # identical widget metrics on Windows and Linux
    app.setStyleSheet(STYLESHEET)
