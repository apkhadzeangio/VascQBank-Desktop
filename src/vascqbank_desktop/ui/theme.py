"""Qt style sheet (QSS) for a calm, professional light theme.

Object names (``setObjectName``) are the styling hooks; widgets never embed
colours directly, so the palette lives in one place.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from vascqbank_desktop.domain.review_status import ReviewStatus

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
HIGHLIGHT = "#FFE08A"  # search-match background

# (text colour, background) per review status. Status is always also written out
# in words, so colour is never the only signal.
STATUS_COLORS: dict[ReviewStatus, tuple[str, str]] = {
    ReviewStatus.PENDING_REVIEW: ("#7A5B00", "#FFF1CC"),
    ReviewStatus.IN_REVIEW: ("#1B5AA6", "#E1EEFC"),
    ReviewStatus.APPROVED: ("#1F6B40", "#DFF2E6"),
    ReviewStatus.REJECTED: ("#A5271F", "#FADDDA"),
}
# Solid colours for the progress bars.
STATUS_BAR_COLORS: dict[ReviewStatus, str] = {
    ReviewStatus.PENDING_REVIEW: "#E3B341",
    ReviewStatus.IN_REVIEW: "#4A8FE0",
    ReviewStatus.APPROVED: "#3FA066",
    ReviewStatus.REJECTED: "#D9574E",
}


def _status_chip_rules() -> str:
    return "\n".join(
        f'QLabel#StatusChip[status="{status.value}"] {{ color: {fg}; background: {bg}; }}'
        for status, (fg, bg) in STATUS_COLORS.items()
    )

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
QFrame#SubjectRow {{
    background: transparent;
    border-left: 3px solid transparent;
    border-radius: 6px;
}}
QFrame#SubjectRow:enabled:hover {{
    background: #263040;
}}
QFrame#SubjectRow[selected="true"] {{
    background: {SIDEBAR_SELECTED};
    border-left: 3px solid {ACCENT};
}}
QFrame#SubjectRow:focus {{
    border: 1px solid #55657D;
    border-left: 3px solid {ACCENT};
}}
QWidget#Sidebar QLabel#SubjectItem {{
    color: {SIDEBAR_TEXT};
    padding: 4px 10px 0 8px;
}}
QWidget#Sidebar QLabel#SubjectItem:disabled {{
    color: #6F7B8D;
}}
QWidget#Sidebar QLabel#SubjectCount {{
    color: #8C97A8;
    font-size: 8.5pt;
    padding: 0 10px 4px 8px;
}}
QWidget#Sidebar QLabel#SubjectCount:disabled {{
    color: #596576;
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
QLabel#BankTotal {{
    font-size: 13pt;
    font-weight: 600;
}}
QPushButton#PrimaryButton, QPushButton#SecondaryButton {{
    padding: 7px 18px;
    border-radius: 6px;
    font-weight: 600;
}}
QPushButton#PrimaryButton {{
    background: {ACCENT};
    color: #FFFFFF;
    border: 1px solid {ACCENT};
}}
QPushButton#PrimaryButton:hover {{
    background: #8E2443;
}}
QPushButton#PrimaryButton:disabled {{
    background: #C9A3AE;
    border-color: #C9A3AE;
}}
QPushButton#SecondaryButton {{
    background: {CARD_BG};
    color: {ACCENT};
    border: 1px solid {ACCENT};
}}
QPushButton#SecondaryButton:hover {{
    background: {ACCENT_SOFT};
}}
QPushButton#SecondaryButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
QTableWidget {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER};
}}
QHeaderView::section {{
    background: {CONTENT_BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 5px 8px;
    font-weight: 600;
}}
QLabel#SummaryNumber {{
    font-size: 15pt;
    font-weight: 700;
}}
QLabel#SummaryNumberGood {{
    font-size: 15pt;
    font-weight: 700;
    color: {SUCCESS};
}}
QLabel#SummaryNumberBad {{
    font-size: 15pt;
    font-weight: 700;
    color: {DANGER};
}}
QLabel#StepActive {{
    color: {ACCENT};
    font-weight: 700;
}}
QLabel#StepInactive {{
    color: {TEXT_MUTED};
}}
QDialog {{
    background: {CONTENT_BG};
}}

/* ---- Catalog ---------------------------------------------------- */
QLineEdit#SearchBox, QLineEdit#GotoBox {{
    background: {CARD_BG};
    border: 1px solid #C9D0D9;
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {HIGHLIGHT};
    selection-color: {TEXT};
}}
QLineEdit#SearchBox:focus, QLineEdit#GotoBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit#GotoBox[invalid="true"] {{
    border: 1px solid {DANGER};
}}
QListView#QuestionList {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    outline: none;
}}
QFrame#CatalogPanel {{
    background: transparent;
}}
QLabel#ResultInfo {{
    color: {TEXT_MUTED};
}}
QLabel#EmptyListMessage {{
    color: {TEXT_MUTED};
    font-size: 11pt;
}}
QLabel#InfoNotice {{
    background: #FFF7DD;
    border: 1px solid #EBD58D;
    border-radius: 6px;
    padding: 6px 10px;
    color: #5F4A00;
}}
QLabel#ErrorNotice {{
    background: #FDECEA;
    border: 1px solid #F1B8B3;
    border-radius: 6px;
    padding: 6px 10px;
    color: {DANGER};
}}
QLabel#PositionLabel {{
    color: {TEXT_MUTED};
    padding: 0 6px;
}}
QPushButton#NavButton {{
    padding: 6px 14px;
    border-radius: 6px;
    background: {CARD_BG};
    color: {TEXT};
    border: 1px solid #C9D0D9;
    font-weight: 600;
}}
QPushButton#NavButton:hover:enabled {{
    background: {ACCENT_SOFT};
    border-color: {ACCENT};
}}
QPushButton#NavButton:disabled {{
    color: #A3ACB8;
    border-color: {BORDER};
}}

/* ---- Question detail -------------------------------------------- */
QScrollArea#DetailScroll {{
    border: none;
    background: transparent;
}}
QWidget#DetailBody {{
    background: transparent;
}}
QLabel#DetailUid {{
    font-size: 18pt;
    font-weight: 700;
}}
QLabel#DetailSubject {{
    color: {TEXT_MUTED};
    font-size: 10.5pt;
}}
QLabel#SectionTitle {{
    color: {TEXT_MUTED};
    font-size: 9pt;
    font-weight: 700;
    letter-spacing: 0.4px;
}}
QLabel#StemText {{
    font-size: 12pt;
}}
QLabel#ExplanationText {{
    font-size: 11pt;
}}
QLabel#StatusChip {{
    border-radius: 10px;
    padding: 3px 12px;
    font-weight: 600;
    font-size: 9pt;
}}
{_status_chip_rules()}
QLabel#RevisionBadge {{
    color: {TEXT_MUTED};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 9pt;
}}
QFrame#OptionCard {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#OptionCard[correct="true"] {{
    background: #EAF6EE;
    border: 2px solid {SUCCESS};
}}
QLabel#OptionLetter {{
    background: #EDF0F4;
    color: {TEXT};
    border-radius: 15px;
    font-weight: 700;
    font-size: 11pt;
}}
QFrame#OptionCard[correct="true"] QLabel#OptionLetter {{
    background: {SUCCESS};
    color: #FFFFFF;
}}
QLabel#OptionText {{
    font-size: 11.5pt;
}}
QLabel#CorrectTag {{
    color: {SUCCESS};
    font-weight: 700;
    font-size: 9.5pt;
}}
QLabel#ReadOnlyNote {{
    color: {TEXT_MUTED};
    font-size: 9pt;
}}

/* ---- Progress --------------------------------------------------- */
QFrame#ProgressStrip {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel#StripScope {{
    font-weight: 700;
}}
QLabel#StripCount {{
    color: {TEXT_MUTED};
}}
QLabel#StatNumber {{
    font-size: 17pt;
    font-weight: 700;
}}
QLabel#StatCaption {{
    color: {TEXT_MUTED};
    font-size: 9pt;
}}
QLabel#ScopeTitle {{
    font-weight: 700;
    font-size: 11pt;
}}
QLabel#ScopeNote {{
    color: {TEXT_MUTED};
}}

/* ---- Profile ---------------------------------------------------- */
QComboBox#ReviewerCombo {{
    padding: 6px 10px;
    min-width: 320px;
}}
QLabel#CurrentReviewer {{
    font-size: 14pt;
    font-weight: 700;
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
