"""SlopeForge's compact, semantic Qt Widgets presentation theme."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication


class Color:
    APP_BACKGROUND = "#f4f6f9"
    SURFACE = "#ffffff"
    SURFACE_SUBTLE = "#f8fafc"
    BORDER = "#d7dde6"
    SEPARATOR = "#c5ccd5"
    TEXT_PRIMARY = "#111827"
    TEXT_SECONDARY = "#374151"
    TEXT_MUTED = "#6b7280"
    ACCENT = "#1261a0"
    ACCENT_HOVER = "#0b4f86"
    SELECTED = "#eaf3ff"
    FOCUS = "#0b63ce"
    SUCCESS = "#2f6f3e"
    WARNING = "#8a5a00"
    ERROR = "#a33a32"
    DISABLED = "#9ca3af"


class Spacing:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    PAGE = 10
    CARD_HORIZONTAL = 14
    CARD_VERTICAL = 12
    RADIUS = 7


APPLICATION_STYLESHEET = """
QMainWindow, QDialog { background: #f4f6f9; color: #111827; }
QWidget#DashboardPage { background: #f4f6f9; }
QWidget#assessmentWorkflowPage { background: #f4f6f9; }
QWidget#AnalysisPage { background: #f4f6f9; }
QFrame#CardFrame, QFrame#DashboardCard, QFrame#DashboardMetricCard,
QFrame#DashboardHeaderCard, QFrame#ConnectionCard, QFrame#EngineeringCard,
QFrame#CriterionCard, QFrame#ResultCard {
    background: #ffffff; border: 1px solid #d7dde6; border-radius: 7px;
}
QFrame#assessmentInfoCard, QFrame#assessmentPlanCard, QFrame#assessmentContextCard,
QFrame#assessmentFooter { background: #ffffff; border: 1px solid #d7dde6; border-radius: 7px; }
QLabel#assessmentCardTitle { color: #1f2937; font-weight: 600; }
QLabel#assessmentSectionTitle { color: #6b7280; font-weight: 600; }
QLabel#assessmentFieldLabel, QLabel#assessmentPlanStatus { color: #6b7280; }
QLabel#assessmentFieldValue { color: #111827; }
QLabel#assessmentValidation { color: #a33a32; }
QLabel#assessmentStepCircle {
    min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px;
    border-radius: 10px; font-weight: 600;
}
QLabel#assessmentStepCircle[stepState="active"], QLabel#assessmentStepCircle[stepState="complete"] {
    background: #1261a0; color: #ffffff;
}
QLabel#assessmentStepCircle[stepState="future"] { background: #e5e9ef; color: #6b7280; }
QLabel#assessmentStepLabel[stepState="active"] { color: #111827; font-weight: 600; }
QLabel#assessmentStepLabel[stepState="complete"] { color: #1261a0; font-weight: 600; }
QLabel#assessmentStepLabel[stepState="future"] { color: #6b7280; }
QFrame#assessmentStepConnector { background: #d7dde6; border: 0; }
QFrame#assessmentStepConnector[complete="true"] { background: #1261a0; }
QFrame#GeometryLegendSwatch[legendRole="traced"] { background: #2563eb; border-radius: 1px; }
QFrame#GeometryLegendSwatch[legendRole="connector"] { background: #d97706; border-radius: 1px; }
QFrame#GeometryLegendSwatch[legendRole="marker"] { background: #ffffff; border: 2px solid #1261a0; border-radius: 4px; }
QWidget#assessmentLinkEventRow { background: #f8fafc; border-bottom: 1px solid #e5e7eb; }
QFrame#PlanCard, QFrame#InspectorCard, QFrame#DialogFooter {
    background: #ffffff; border: 1px solid #d7dde6; border-radius: 7px;
}
QDialog#DomainGeometryEditor { background: #f4f6f9; }
QLabel#InspectorValue { color: #111827; font-weight: 600; }
QLabel#DialogStatus { color: #6b7280; }
QFrame#DashboardSummaryRow, QWidget#ProjectLinesDatasetRow, QWidget#StandardRow {
    background: #ffffff; border: 1px solid #d7dde6; border-radius: 5px;
}
QLabel#EntityTitle, QLabel#BlockTitle { color: #111827; font-size: 22px; font-weight: 700; }
QLabel#CardTitle, QLabel#EngineeringSectionTitle, QLabel#RelatedEntityTitle,
QLabel#SectionTitle, QLabel#EngineeringGroupTitle { color: #1f2937; font-weight: 600; }
QLabel#MutedText, QLabel#EntityContextLine, QLabel#CalculatedCaption { color: #6b7280; }
QLabel#FormHelperText { color: #6b7280; }
QLabel#FormValidationText { color: #a33a32; }
QLabel#SummaryValue, QLabel#ActivityTitle { color: #111827; font-weight: 600; }
QLabel#EngineeringSummaryText { color: #374151; }
QFrame#OverviewDivider { color: #e5e7eb; background: #e5e7eb; max-height: 1px; border: 0; }

QPushButton { min-height: 26px; padding: 2px 10px; }
QPushButton[role="primary"] { color: white; background: #1261a0; border: 1px solid #1261a0; border-radius: 5px; font-weight: 600; }
QPushButton[role="primary"]:hover { background: #0b4f86; }
QPushButton[role="secondary"] { color: #1f2937; background: #ffffff; border: 1px solid #c5ccd5; border-radius: 5px; }
QPushButton[role="secondary"]:hover { color: #1261a0; border-color: #1261a0; background: #f8fafc; }
QPushButton#analysisModeButton:checked { color: #ffffff; background: #1261a0; border-color: #1261a0; font-weight: 600; }
QPushButton#analysisModeButton:checked:hover { background: #0b4f86; border-color: #0b4f86; }
QPushButton[role="link"] { color: #1261a0; background: transparent; border: 0; padding: 2px 4px; font-weight: 600; }
QPushButton[role="link"]:hover { color: #0b4f86; text-decoration: underline; }
QPushButton[role="danger"] { color: #a33a32; background: #ffffff; border: 1px solid #d9a6a2; border-radius: 5px; }
QPushButton:disabled { color: #9ca3af; }

QWidget#DialogActions QPushButton {
    min-height: 32px; max-height: 32px; padding: 0 12px;
    border-radius: 5px; text-align: center;
}
QWidget#DialogActions QPushButton[role="primary"]:pressed { background: #083f70; border-color: #083f70; }
QWidget#DialogActions QPushButton[role="primary"]:focus { border: 1px solid #083f70; }
QWidget#DialogActions QPushButton[role="secondary"]:pressed { background: #eef3f8; border-color: #8d99a8; color: #0b4f86; }
QWidget#DialogActions QPushButton[role="secondary"]:focus { border: 1px solid #0b63ce; }

QDialog#StandardEntityDialog QLineEdit,
QDialog#StandardEntityDialog QTextEdit,
QDialog#StandardEntityDialog QDateEdit,
QDialog#StandardEntityDialog QComboBox {
    min-height: 26px; background: #ffffff; color: #111827;
    border: 1px solid #cfd6df; border-radius: 5px; padding: 2px 7px;
    selection-background-color: #eaf3ff; selection-color: #111827;
}
QDialog#StandardEntityDialog QComboBox,
QDialog#StandardEntityDialog QDateEdit { padding-right: 27px; }
QDialog#StandardEntityDialog QLineEdit:disabled,
QDialog#StandardEntityDialog QTextEdit:disabled,
QDialog#StandardEntityDialog QDateEdit:disabled,
QDialog#StandardEntityDialog QComboBox:disabled { background: #f1f3f5; color: #9ca3af; }
QDialog#StandardEntityDialog QDoubleSpinBox {
    min-height: 26px; background: #ffffff; color: #111827;
    border: 1px solid #cfd6df; border-radius: 5px;
}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox:disabled {
    background: #f1f3f5; color: #9ca3af;
}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton {
    background: #f8fafc; border: 0; border-left: 1px solid #e1e6ed;
    border-radius: 0; padding: 0; margin: 0;
}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton#ChevronSpinUpButton {
    border-top-right-radius: 5px; border-bottom: 1px solid #e1e6ed;
}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton#ChevronSpinDownButton {
    border-bottom-right-radius: 5px;
}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton:hover {
    background: #eef3f8;
}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton:pressed {
    background: #dfe8f2;
}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton:disabled {
    background: #eef0f3; border-color: #d7dde6;
}

QLineEdit#GlobalSearch {
    min-height: 28px; background: #ffffff; color: #111827;
    border: 1px solid #c5ccd5; border-radius: 6px; padding: 2px 10px;
    selection-background-color: #eaf3ff; selection-color: #111827;
}
QLineEdit#GlobalSearch:hover { border-color: #9aa6b5; }
QLineEdit#GlobalSearch:focus { border: 1px solid #0b63ce; background: #ffffff; }

QTableWidget#StandardTable {
    background: #ffffff; alternate-background-color: #f8fafc;
    border: 1px solid #d7dde6; border-radius: 7px; outline: 0;
    gridline-color: transparent; selection-background-color: #eaf3ff;
    selection-color: #111827;
}
QTableWidget#StandardTable QHeaderView::section {
    background: #f1f4f8; color: #374151; border: 0;
    border-right: 1px solid #e1e6ed; border-bottom: 1px solid #cfd6df;
    padding: 7px 9px; font-weight: 600;
}
QTableWidget#StandardTable::item { border-bottom: 1px solid #edf0f4; padding: 5px 8px; }
QTableWidget#StandardTable::item:hover { background: #f3f7fc; }
QTableWidget#StandardTable::item:selected { background: #eaf3ff; color: #111827; }
QLabel#EmptyState { color: #6b7280; background: #f8fafc; border: 1px dashed #c5ccd5; border-radius: 7px; padding: 24px; }

QFrame#AnalysisToolbar, QFrame#AnalysisFiltersPanel {
    background: #ffffff; border: 1px solid #d7dde6; border-radius: 7px;
}
QLabel#AnalysisTitle { color: #111827; font-size: 18px; font-weight: 700; }
QLabel#AnalysisSectionTitle { color: #374151; font-weight: 700; }
QLabel#AnalysisRecordCount { color: #1261a0; font-weight: 700; padding-left: 10px; }
QLabel#AnalysisFilterCount, QLabel#AnalysisRowSemantics { color: #6b7280; }
QScrollArea#AnalysisFilterScroll { background: transparent; border: 0; }
QScrollArea#AnalysisFilterScroll > QWidget > QWidget { background: transparent; }
QToolButton#AnalysisRemoveFilterButton {
    min-width: 22px; min-height: 22px; border: 0; border-radius: 4px;
    color: #6b7280; background: transparent; font-weight: 700;
}
QToolButton#AnalysisRemoveFilterButton:hover { color: #111827; background: #edf2f7; }
QWidget#AnalysisPage QComboBox, QWidget#AnalysisPage QLineEdit, QWidget#AnalysisPage QDateEdit {
    min-height: 26px; background: #ffffff; color: #111827;
    border: 1px solid #cfd6df; border-radius: 5px; padding: 1px 7px;
}
QTableView#AnalysisDataTable {
    background: #ffffff; alternate-background-color: #f8fafc;
    border: 1px solid #d7dde6; border-radius: 7px; outline: 0;
    gridline-color: #edf0f4; selection-background-color: #eaf3ff;
    selection-color: #111827;
}
QTableView#AnalysisDataTable QHeaderView::section {
    background: #f1f4f8; color: #374151; border: 0;
    border-right: 1px solid #e1e6ed; border-bottom: 1px solid #cfd6df;
    padding: 7px 9px; font-weight: 600;
}

QWidget#EngineeringWorkspace, QWidget#geomechanicsWorkspace { background: #f4f6f9; }
QWidget#EngineeringWorkspace QGroupBox#drillingGroupCard,
QWidget#EngineeringWorkspace QGroupBox#actualDrillingGroupCard {
    background: #ffffff; border: 1px solid #d7dde6; border-radius: 7px;
    margin-top: 14px; padding: 12px 10px 10px 10px; font-weight: 600;
}
QWidget#EngineeringWorkspace QGroupBox#EngineeringCard {
    background: #ffffff; border: 1px solid #d7dde6; border-radius: 7px;
    margin-top: 14px; padding: 10px; font-weight: 600;
}
QWidget#EngineeringWorkspace QGroupBox#EngineeringCard::title {
    subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #111827;
}
QWidget#EngineeringWorkspace QGroupBox#drillingGroupCard::title,
QWidget#EngineeringWorkspace QGroupBox#actualDrillingGroupCard::title {
    subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #111827;
}
QWidget#EngineeringWorkspace QGroupBox#drillingDesignArea,
QWidget#EngineeringWorkspace QGroupBox#chargeDesignArea,
QWidget#EngineeringWorkspace QGroupBox#actualDrillingArea,
QWidget#EngineeringWorkspace QGroupBox#actualChargeArea,
QWidget#EngineeringWorkspace QGroupBox#actualExceptionArea {
    background: #f8fafc; border: 1px solid #e1e6ed; border-radius: 6px;
    margin-top: 13px; padding: 9px 8px 7px 8px; font-weight: 600;
}
QWidget#EngineeringWorkspace QGroupBox#drillingDesignArea::title,
QWidget#EngineeringWorkspace QGroupBox#chargeDesignArea::title,
QWidget#EngineeringWorkspace QGroupBox#actualDrillingArea::title,
QWidget#EngineeringWorkspace QGroupBox#actualChargeArea::title,
QWidget#EngineeringWorkspace QGroupBox#actualExceptionArea::title {
    subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #374151;
}
QWidget#EngineeringWorkspace QComboBox, QWidget#geomechanicsWorkspace QComboBox {
    min-height: 26px; background: #ffffff; color: #111827;
    border: 1px solid #cfd6df; border-radius: 5px; padding: 1px 26px 1px 7px;
}
QWidget#EngineeringWorkspace QComboBox:hover, QWidget#geomechanicsWorkspace QComboBox:hover { border-color: #9aa6b5; }
QWidget#EngineeringWorkspace QComboBox:focus, QWidget#geomechanicsWorkspace QComboBox:focus { border-color: #0b63ce; }
QWidget#geomechanicsWorkspace QWidget#rockMassSection,
QWidget#geomechanicsWorkspace QWidget#jointSetsSection,
QWidget#geomechanicsWorkspace QWidget#qSystemSection,
QWidget#geomechanicsWorkspace QWidget#structuralScreeningSection,
QWidget#geomechanicsWorkspace QWidget#geomechanicsNotes {
    background: #ffffff; border: 1px solid #d7dde6; border-radius: 7px;
}

QTabWidget[entityTabs="true"]::pane { border: 0; background: transparent; top: -1px; }
QTabWidget[entityTabs="true"] QTabBar::tab {
    color: #6b7280; background: transparent; border: 0; border-bottom: 2px solid transparent;
    padding: 7px 8px; margin-right: 0;
}
QTabWidget[entityTabs="true"] QTabBar::tab:selected { color: #1261a0; border-bottom-color: #1261a0; font-weight: 600; }
QTabWidget[entityTabs="true"] QTabBar::tab:hover:!selected { color: #374151; background: #f8fafc; }
QTabWidget[entityTabs="true"] QTabBar::tab:focus { outline: 1px solid #0b63ce; }

QLabel#StatusBadge { border-radius: 5px; padding: 3px 7px; font-weight: 600; }
QLabel#StatusBadge[statusRole="neutral"] { background: #f3f4f6; color: #4b5563; border: 1px solid #d1d5db; }
QLabel#StatusBadge[statusRole="info"] { background: #eaf3ff; color: #155fa0; border: 1px solid #9bc2e8; }
QLabel#StatusBadge[statusRole="warning"] { background: #fff4d6; color: #8a5a00; border: 1px solid #f4c76b; }
QLabel#StatusBadge[statusRole="success"] { background: #edf8f0; color: #2f6f3e; border: 1px solid #9bcaa6; }
QLabel#StatusBadge[statusRole="error"] { background: #fff0f0; color: #a33a32; border: 1px solid #e0aaa5; }
QLabel#StatusBadge[statusRole="archived"] { background: #eef0f3; color: #4b5563; border: 1px solid #cfd4dc; }
QLabel#StaleBadge { background: #fff1c2; color: #8a5a00; border: 1px solid #e5b94d; border-radius: 4px; padding: 2px 5px; }

QListWidget#SettingsNavigation { background: #f8fafc; border: 1px solid #d7dde6; border-radius: 6px; padding: 4px; }
QListWidget#SettingsNavigation::item { min-height: 30px; margin: 2px 0; padding: 2px 8px; border-left: 3px solid transparent; }
QListWidget#SettingsNavigation::item:hover:!selected { background: #eef1f5; color: #374151; }
QListWidget#SettingsNavigation::item:selected { color: #1261a0; background: #eaf3ff; border-left-color: #1261a0; font-weight: 600; }
QListWidget#SettingsNavigation::item:selected:hover { color: #1261a0; background: #eaf3ff; border-left-color: #1261a0; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #0b63ce; }
"""

_ICON_ROOT = Path(__file__).resolve().parent.parent / "app" / "icons" / "ui" / "svg" / "neutral"
_COMBO_CHEVRON = (_ICON_ROOT / "chevron-down.svg").as_posix()
_SPLIT_SAVE_CHEVRON = (_ICON_ROOT / "chevron-down-white.svg").as_posix()
APPLICATION_STYLESHEET += f"""
QToolButton#SplitSaveButton {{
    color: #ffffff; background-color: #1261a0; border: 1px solid #1261a0;
    border-radius: 5px; padding: 2px 34px 2px 10px; font-weight: 600;
}}
QToolButton#SplitSaveButton:hover {{ background-color: #0b4f86; border-color: #0b4f86; }}
QToolButton#SplitSaveButton:pressed {{ background-color: #083f70; border-color: #083f70; }}
QToolButton#SplitSaveButton:disabled {{
    color: #eef3f8; background-color: #9bb7cc; border-color: #9bb7cc;
}}
QToolButton#SplitSaveButton::menu-button {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 26px; background-color: #1261a0; border: 0; border-left: 1px solid #397caf;
    border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}}
QToolButton#SplitSaveButton::menu-button:hover {{ background-color: #0b4f86; border-left-color: #39739f; }}
QToolButton#SplitSaveButton::menu-button:pressed {{ background-color: #083f70; border-left-color: #32688d; }}
QToolButton#SplitSaveButton::menu-button:disabled {{ background-color: #9bb7cc; border-left-color: #b7cbd9; }}
QToolButton#SplitSaveButton::menu-arrow {{
    image: url("{_SPLIT_SAVE_CHEVRON}"); width: 12px; height: 12px;
}}
QDialog#StandardEntityDialog QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 25px; border: 0; border-left: 1px solid #e1e6ed;
    background: #f8fafc; border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}}
QDialog#StandardEntityDialog QComboBox::down-arrow {{ image: url("{_COMBO_CHEVRON}"); width: 12px; height: 12px; }}
QDialog#StandardEntityDialog QDateEdit::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 25px; border: 0; border-left: 1px solid #e1e6ed;
    background: #f8fafc; border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}}
QDialog#StandardEntityDialog QDateEdit::down-arrow {{ image: url("{_COMBO_CHEVRON}"); width: 12px; height: 12px; }}
QDialog#StandardEntityDialog QComboBox:hover::drop-down,
QDialog#StandardEntityDialog QDateEdit:hover::drop-down {{ background: #eef3f8; }}
QDialog#StandardEntityDialog QComboBox:focus::drop-down,
QDialog#StandardEntityDialog QDateEdit:focus::drop-down {{ border-left-color: #9bc2e8; }}
QDialog#StandardEntityDialog QComboBox:disabled::drop-down,
QDialog#StandardEntityDialog QDateEdit:disabled::drop-down {{ background: #eef0f3; border-left-color: #d7dde6; }}
QWidget#EngineeringWorkspace QComboBox::drop-down,
QWidget#geomechanicsWorkspace QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 25px; border: 0; border-left: 1px solid #e1e6ed;
    background: #f8fafc; border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}}
QWidget#EngineeringWorkspace QComboBox::down-arrow,
QWidget#geomechanicsWorkspace QComboBox::down-arrow {{ image: url("{_COMBO_CHEVRON}"); width: 12px; height: 12px; }}
QWidget#EngineeringWorkspace QComboBox:disabled,
QWidget#geomechanicsWorkspace QComboBox:disabled {{ background: #f1f3f5; color: #9ca3af; border-color: #d7dde6; }}
QWidget#EngineeringWorkspace QComboBox:disabled::drop-down,
QWidget#geomechanicsWorkspace QComboBox:disabled::drop-down {{ background: #eef0f3; border-left-color: #d7dde6; }}
"""

# Compatibility value for small widgets that cannot inherit the application
# stylesheet (for example, independently rendered row delegates).
STANDARD_ROW_STYLESHEET = (
    "background:#ffffff;border:1px solid #d7dde6;border-radius:5px;"
)


def apply_theme(app: QApplication) -> None:
    """Apply the presentation-only theme once, before any application dialogs."""
    app.setStyleSheet(APPLICATION_STYLESHEET)
