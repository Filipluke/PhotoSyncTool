"""Shared desktop design tokens and offline typography. See DESIGN.md."""
from importlib import resources
from string import Template

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

TOKENS = {
    'background': '#1c2430', 'surface': '#242e3c', 'raised': '#2d3b4d',
    'foreground': '#edf1f5', 'muted': '#b4bfcd', 'border': '#46576d',
    'accent': '#345776', 'focus': '#91b6db', 'warning': '#e4bb7e',
    'accent_border': '#718fae', 'accent_hover': '#40688c',
    'disabled': '#8290a3', 'disabled_border': '#3c4b60',
    'success': '#b2d1b8', 'danger': '#edb0a7', 'danger_surface': '#66342f',
    'image': '#151c26',
}


def install_typography() -> None:
    app = QApplication.instance()
    if app is None or app.property('plexLoaded'):
        return
    fonts = resources.files('photosync_tool_assets').joinpath('fonts')
    for filename in ('IBMPlexSans.ttf', 'IBMPlexMono-Regular.ttf'):
        font_id = QFontDatabase.addApplicationFontFromData(QByteArray(fonts.joinpath(filename).read_bytes()))
        if font_id < 0:
            raise RuntimeError(f'Cannot load bundled font: {filename}')
    app.setFont(QFont('IBM Plex Sans', 10))
    palette = app.palette()
    for role, token in ((QPalette.ColorRole.Window, 'background'),
                        (QPalette.ColorRole.WindowText, 'foreground'),
                        (QPalette.ColorRole.Base, 'surface'),
                        (QPalette.ColorRole.AlternateBase, 'background'),
                        (QPalette.ColorRole.Text, 'foreground'),
                        (QPalette.ColorRole.Button, 'surface'),
                        (QPalette.ColorRole.ButtonText, 'foreground'),
                        (QPalette.ColorRole.Highlight, 'accent'),
                        (QPalette.ColorRole.HighlightedText, 'foreground'),
                        (QPalette.ColorRole.ToolTipBase, 'surface'),
                        (QPalette.ColorRole.ToolTipText, 'foreground')):
        palette.setColor(role, QColor(TOKENS[token]))
    app.setPalette(palette)
    app.setProperty('plexLoaded', True)


def status_style(active: bool) -> str:
    color = TOKENS['success'] if active else TOKENS['muted']
    return f'color: {color}; background: transparent; padding: 4px 8px;'


def stylesheet() -> str:
    return Template('''
    QWidget { color: $foreground; font-family: "IBM Plex Sans"; font-size: 10pt; }
    QMainWindow, QDialog, QWidget#appRoot, QTabWidget::pane { background: $background; }
    QLabel { background: transparent; }
    QWidget#commandBar, QWidget#sidebar { background: $surface; }
    QWidget#commandBar { border-bottom: 1px solid $border; }
    QWidget#sidebar { border-right: 1px solid $border; }
    QLabel#appTitle { font-size: 14pt; font-weight: 600; }
    QLabel#appSubtitle, QLabel#pageSubtitle, QLabel#metricCaption,
    QLabel#sidebarInfoBody, QLabel#footerText, QLabel#pathLabel { color: $muted; }
    QLabel#pageTitle { font-size: 17pt; font-weight: 600; }
    QLabel#sectionTitle, QLabel#sidebarInfoTitle { font-weight: 600; }
    QLabel#metricValue { font: 20pt "IBM Plex Mono"; }
    QLabel#metricTitle { color: $muted; }
    QLabel#nextActionTitle { font-size: 12pt; font-weight: 600; }
    QLabel#nextActionBody, QLabel#tableEmptyState { color: $muted; }
    QLabel#tableEmptyState { padding: 12px; }
    QWidget#metricCard { border-bottom: 1px solid $border; }
    QWidget#libraryIndexCard { background: $surface; border-left: 3px solid $focus; }
    QWidget#sidebarSeparator { background: $border; }
    QTabWidget::pane { border: none; }
    QScrollArea { border: none; background: $background; }
    QGroupBox { border: none; border-top: 1px solid $border; margin-top: 20px; padding-top: 16px; }
    QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px 0 0; }
    QPushButton { background: $surface; border: 1px solid $border; border-radius: 4px;
                  padding: 6px 10px; min-height: 18px; }
    QPushButton:hover { background: $raised; border-color: $muted; }
    QPushButton:pressed { background: $background; }
    QPushButton:disabled { color: $disabled; border-color: $disabled_border; background: $background; }
    QPushButton#primaryAction { background: $accent; border-color: $accent_border; font-weight: 600; }
    QPushButton#primaryAction:hover { background: $accent_hover; }
    QPushButton#cautionAction { color: $warning; }
    QPushButton#dangerAction { background: $danger_surface; color: $foreground; border-color: $danger; }
    QPushButton#dangerAction:hover { background: #80443b; }
    QPushButton#navButton { text-align: left; background: transparent; border: 1px solid transparent;
                            border-radius: 0; padding: 6px 10px; }
    QPushButton#navButton:hover { background: $raised; }
    QPushButton#navButton:checked { background: $accent; border-left: 3px solid $focus; font-weight: 600; }
    QPushButton:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus,
    QListWidget:focus, QTableWidget:focus { border: 2px solid $focus; }
    QLineEdit, QComboBox, QPlainTextEdit, QListWidget, QTableWidget {
        background: $image; border: 1px solid $border; border-radius: 3px;
        selection-background-color: $accent; selection-color: $foreground;
    }
    QLineEdit, QComboBox { padding: 5px 7px; min-height: 18px; }
    QComboBox QAbstractItemView { background: $surface; selection-background-color: $accent; }
    QPlainTextEdit { font-family: "IBM Plex Mono"; font-size: 9pt; padding: 8px; }
    QTableWidget { alternate-background-color: $surface; border-radius: 0; }
    QTableWidget::item { padding: 4px 7px; }
    QHeaderView::section { background: $raised; color: $muted; border: none;
                           border-bottom: 1px solid $border; padding: 7px; text-align: left; }
    QListWidget::item { padding: 5px; border: 2px solid transparent; }
    QListWidget::item:selected { background: $accent; border: 2px solid $focus; }
    QLabel#imagePreview { background: $image; border: 1px solid $border; }
    QLabel#keepLabel { color: $success; font-weight: 600; }
    QLabel#candidateLabel { color: $warning; font-weight: 600; }
    QSplitter::handle { background: $border; width: 1px; height: 1px; }
    QCheckBox { spacing: 6px; padding: 4px 0; }
    QCheckBox:focus { outline: 2px solid $focus; }
    QStatusBar { background: $surface; border-top: 1px solid $border; }
    QStatusBar::item { border: none; }
    QLabel#dashboardStatusLine { color: $warning; padding: 6px; }
    QToolTip { background: $surface; color: $foreground; border: 1px solid $border; padding: 5px; }
    ''').substitute(TOKENS)
