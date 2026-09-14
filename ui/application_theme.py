"""Application-level light/dark appearance contract for SlopeForge."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.appearance import normalize_theme, save_theme, selected_theme
from ui.theme import APPLICATION_STYLESHEET, Color


class DarkColor:
    """Dark counterparts to the existing semantic SlopeForge light roles."""

    APP_BACKGROUND = "#171b22"
    SURFACE = "#202630"
    SURFACE_SUBTLE = "#252c36"
    SURFACE_ELEVATED = "#2b3440"
    BORDER = "#3b4654"
    SEPARATOR = "#4a5665"
    TEXT_PRIMARY = "#f2f5f8"
    TEXT_SECONDARY = "#d5dbe3"
    TEXT_MUTED = "#9ca7b4"
    ACCENT = "#5aa7e8"
    ACCENT_HOVER = "#79b9ee"
    SELECTED = "#243f57"
    FOCUS = "#63b3ed"
    SUCCESS = "#8bd39a"
    WARNING = "#f0c66e"
    ERROR = "#f09a94"
    DISABLED = "#6f7a86"


_ICON_ROOT = Path(__file__).resolve().parent.parent / "app" / "icons" / "ui" / "svg" / "neutral"
_DARK_COMBO_CHEVRON = (_ICON_ROOT / "chevron-down-white.svg").as_posix()


LIGHT_STYLESHEET = f"""
/* Theme-aware surfaces that are not part of the older shared light QSS. */
QGraphicsView#DashboardPlanView, QGraphicsView#BoreholeView {{
    background: {Color.SURFACE_SUBTLE}; border: 1px solid {Color.BORDER}; border-radius: 5px;
}}
QLabel#DashboardMetricValue {{ color: {Color.TEXT_PRIMARY}; font-size: 18px; font-weight: 700; }}
QLabel#DashboardStrongText {{ color: {Color.TEXT_SECONDARY}; font-weight: 600; }}
QLabel#DashboardPercentValue {{ color: #1f4f7a; font-size: 18px; font-weight: 700; }}
QProgressBar#DashboardProgressBar {{
    border: 1px solid {Color.BORDER}; border-radius: 8px; background: #eef2f6;
}}
QProgressBar#DashboardProgressBar::chunk {{ background: #4f78a8; border-radius: 7px; }}
QWidget#DashboardActivityRow {{ border-bottom: 1px solid #eef1f5; }}

QFrame#DocumentBatchBulk, QFrame#PhotoMetadataCard {{
    background: #f8fafc; border: 1px solid {Color.BORDER}; border-radius: 7px;
}}
QLabel#AttachmentBatchTitle {{ color: {Color.TEXT_PRIMARY}; font-size: 16px; font-weight: 600; }}
QLabel#PhotoImportPreview {{
    background: {Color.SURFACE_SUBTLE}; color: {Color.TEXT_SECONDARY};
    border: 1px solid {Color.BORDER}; border-radius: 6px;
}}
QLabel#PhotoViewerTitle {{ color: {Color.TEXT_PRIMARY}; font-size: 16px; font-weight: 600; }}
QLabel#PhotoMetadataLabel, QLabel#AttachmentFilename {{ color: {Color.TEXT_MUTED}; font-size: 11px; }}
QLabel#PhotoMetadataValue {{ color: {Color.TEXT_PRIMARY}; font-weight: 500; }}
QToolButton#AttachmentPreviewTile {{
    padding: 0; margin: 0; border: 1px solid {Color.BORDER}; border-radius: 6px;
    background: {Color.SURFACE_SUBTLE};
}}
QToolButton#AttachmentPreviewTile:hover {{ border-color: #8fb4dc; background: #eef4fb; }}
QToolButton#AttachmentPreviewTile:pressed {{ border-color: {Color.ACCENT}; }}
QToolButton#PhotoTile {{ background: transparent; border: 0; padding: 0; margin: 0; }}
QToolButton#PhotoTile:hover {{ background: #eef4fb; border: 2px solid #9bc2ea; border-radius: 11px; }}
QToolButton#AttachmentThumbnail {{ border: 1px solid transparent; border-radius: 10px; padding: 2px; background: transparent; }}
QToolButton#AttachmentThumbnail[selected="true"] {{ border: 2px solid {Color.ACCENT}; }}

QLabel#ConnectionStatus[statusState="info"] {{ color: {Color.TEXT_MUTED}; }}
QLabel#ConnectionStatus[statusState="success"] {{ color: {Color.SUCCESS}; }}
QLabel#ConnectionStatus[statusState="error"] {{ color: {Color.ERROR}; }}
QLabel#ConnectionEnvironmentWarning {{
    background: #fff7e6; color: #725514; border: 1px solid #e8c77d;
    border-radius: 5px; padding: 6px;
}}
"""


DARK_STYLESHEET = f"""
/* Dark theme overrides. The base stylesheet still owns geometry/spacing. */
QMainWindow, QDialog,
QWidget#DashboardPage, QWidget#assessmentWorkflowPage,
QDialog#DomainGeometryEditor,
QWidget#EngineeringWorkspace, QWidget#geomechanicsWorkspace,
QWidget#AnalysisPage {{
    background: {DarkColor.APP_BACKGROUND}; color: {DarkColor.TEXT_PRIMARY};
}}
QToolTip {{
    background: {DarkColor.SURFACE_ELEVATED}; color: {DarkColor.TEXT_PRIMARY};
    border: 1px solid {DarkColor.BORDER}; padding: 4px;
}}
QMenu, QMenuBar {{ background: {DarkColor.SURFACE}; color: {DarkColor.TEXT_PRIMARY}; }}
QMenu {{ border: 1px solid {DarkColor.BORDER}; }}
QMenu::item:selected, QMenuBar::item:selected {{ background: {DarkColor.SELECTED}; color: {DarkColor.TEXT_PRIMARY}; }}
QMenu::separator {{ background: {DarkColor.SEPARATOR}; height: 1px; margin: 4px 7px; }}

QFrame#CardFrame, QFrame#DashboardCard, QFrame#DashboardMetricCard,
QFrame#DashboardHeaderCard, QFrame#ConnectionCard, QFrame#EngineeringCard,
QFrame#CriterionCard, QFrame#ResultCard,
QFrame#assessmentInfoCard, QFrame#assessmentPlanCard, QFrame#assessmentContextCard,
QFrame#assessmentFooter, QFrame#PlanCard, QFrame#InspectorCard, QFrame#DialogFooter,
QFrame#DashboardSummaryRow, QWidget#ProjectLinesDatasetRow, QWidget#StandardRow,
QFrame#DocumentBatchBulk, QFrame#PhotoMetadataCard,
QWidget#geomechanicsWorkspace QWidget#rockMassSection,
QWidget#geomechanicsWorkspace QWidget#jointSetsSection,
QWidget#geomechanicsWorkspace QWidget#qSystemSection,
QWidget#geomechanicsWorkspace QWidget#structuralScreeningSection,
QWidget#geomechanicsWorkspace QWidget#geomechanicsNotes {{
    background: {DarkColor.SURFACE}; border-color: {DarkColor.BORDER};
}}
QWidget#assessmentLinkEventRow {{ background: {DarkColor.SURFACE_SUBTLE}; border-bottom-color: {DarkColor.BORDER}; }}
QFrame#OverviewDivider, QFrame#assessmentStepConnector {{ background: {DarkColor.BORDER}; color: {DarkColor.BORDER}; }}
QFrame#assessmentStepConnector[complete="true"] {{ background: {DarkColor.ACCENT}; }}
QWidget#DashboardActivityRow {{ border-bottom: 1px solid #303946; }}

QLabel#EntityTitle, QLabel#BlockTitle,
QLabel#SummaryValue, QLabel#ActivityTitle,
QLabel#InspectorValue, QLabel#assessmentFieldValue,
QLabel#assessmentStepLabel[stepState="active"],
QLabel#DashboardMetricValue {{ color: {DarkColor.TEXT_PRIMARY}; }}
QLabel#DashboardMetricValue {{ font-size: 18px; font-weight: 700; }}
QLabel#DashboardStrongText {{ color: {DarkColor.TEXT_SECONDARY}; font-weight: 600; }}
QLabel#DashboardPercentValue {{ color: {DarkColor.ACCENT_HOVER}; font-size: 18px; font-weight: 700; }}
QLabel#CardTitle, QLabel#EngineeringSectionTitle, QLabel#RelatedEntityTitle,
QLabel#SectionTitle, QLabel#EngineeringGroupTitle,
QLabel#assessmentCardTitle {{ color: {DarkColor.TEXT_SECONDARY}; }}
QLabel#MutedText, QLabel#EntityContextLine, QLabel#CalculatedCaption,
QLabel#FormHelperText, QLabel#DialogStatus,
QLabel#assessmentSectionTitle, QLabel#assessmentFieldLabel,
QLabel#assessmentPlanStatus, QLabel#assessmentStepLabel[stepState="future"],
QLabel#AttachmentFilename, QLabel#PhotoMetadataLabel {{ color: {DarkColor.TEXT_MUTED}; }}
QLabel#AttachmentFilename, QLabel#PhotoMetadataLabel {{ font-size: 11px; }}
QLabel#EngineeringSummaryText {{ color: {DarkColor.TEXT_SECONDARY}; }}
QLabel#FormValidationText, QLabel#assessmentValidation {{ color: {DarkColor.ERROR}; }}
QLabel#AttachmentBatchTitle {{ color: {DarkColor.TEXT_PRIMARY}; font-size: 16px; font-weight: 600; }}
QLabel#PhotoViewerTitle {{ color: {DarkColor.TEXT_PRIMARY}; font-size: 16px; font-weight: 600; }}
QLabel#PhotoMetadataValue {{ color: {DarkColor.TEXT_PRIMARY}; font-weight: 500; }}
QLabel#assessmentStepCircle[stepState="active"], QLabel#assessmentStepCircle[stepState="complete"] {{
    background: {DarkColor.ACCENT}; color: #ffffff;
}}
QLabel#assessmentStepCircle[stepState="future"] {{ background: {DarkColor.SURFACE_ELEVATED}; color: {DarkColor.TEXT_MUTED}; }}
QLabel#assessmentStepLabel[stepState="complete"] {{ color: {DarkColor.ACCENT}; }}
QFrame#GeometryLegendSwatch[legendRole="marker"] {{ background: {DarkColor.SURFACE}; border-color: {DarkColor.ACCENT}; }}

QPushButton[role="primary"] {{ color: #ffffff; background: #2f78b5; border-color: #2f78b5; }}
QPushButton[role="primary"]:hover {{ background: #3b8ac9; border-color: #3b8ac9; }}
QPushButton[role="primary"]:pressed {{ background: #286a9f; border-color: #286a9f; }}
QPushButton[role="secondary"] {{ color: {DarkColor.TEXT_PRIMARY}; background: {DarkColor.SURFACE}; border-color: {DarkColor.SEPARATOR}; }}
QPushButton[role="secondary"]:hover {{ color: {DarkColor.ACCENT_HOVER}; border-color: {DarkColor.ACCENT}; background: {DarkColor.SURFACE_SUBTLE}; }}
QPushButton[role="secondary"]:pressed {{ color: {DarkColor.ACCENT_HOVER}; background: {DarkColor.SURFACE_ELEVATED}; border-color: {DarkColor.ACCENT}; }}
QPushButton#analysisModeButton:checked {{ color: #ffffff; background: #2f78b5; border-color: #2f78b5; font-weight: 600; }}
QPushButton#analysisModeButton:checked:hover {{ background: #3b8ac9; border-color: #3b8ac9; }}
QPushButton[role="link"] {{ color: {DarkColor.ACCENT}; }}
QPushButton[role="link"]:hover {{ color: {DarkColor.ACCENT_HOVER}; }}
QPushButton[role="danger"] {{ color: {DarkColor.ERROR}; background: {DarkColor.SURFACE}; border-color: #754247; }}
QPushButton:disabled {{ color: {DarkColor.DISABLED}; }}

/* Keep spin boxes on the native Qt/Windows complex-control path. Partial QSS
   styling changes their subcontrol hit geometry on Windows; the application
   palette already supplies the correct dark Base/Text/Button colours. */
QLineEdit, QTextEdit, QPlainTextEdit,
QDateEdit, QTimeEdit, QDateTimeEdit, QComboBox {{
    background: {DarkColor.SURFACE}; color: {DarkColor.TEXT_PRIMARY};
    border: 1px solid {DarkColor.BORDER};
    selection-background-color: {DarkColor.SELECTED}; selection-color: {DarkColor.TEXT_PRIMARY};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover,
QDateEdit:hover, QComboBox:hover {{ border-color: {DarkColor.SEPARATOR}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QDateEdit:focus, QComboBox:focus {{ border-color: {DarkColor.FOCUS}; }}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QDateEdit:disabled, QComboBox:disabled {{
    background: {DarkColor.SURFACE_SUBTLE}; color: {DarkColor.DISABLED}; border-color: {DarkColor.BORDER};
}}
QComboBox QAbstractItemView {{
    background: {DarkColor.SURFACE_ELEVATED}; color: {DarkColor.TEXT_PRIMARY};
    border: 1px solid {DarkColor.BORDER}; selection-background-color: {DarkColor.SELECTED};
    selection-color: {DarkColor.TEXT_PRIMARY}; outline: 0;
}}
QComboBox::drop-down, QDateEdit::drop-down {{ background: {DarkColor.SURFACE_SUBTLE}; border-left-color: {DarkColor.BORDER}; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url("{_DARK_COMBO_CHEVRON}"); width: 12px; height: 12px; }}

/* The light baseline has high-specificity StandardEntityDialog rules. Match
   that specificity in Dark so create/edit dialogs do not retain white fields. */
QDialog#StandardEntityDialog QLineEdit,
QDialog#StandardEntityDialog QTextEdit,
QDialog#StandardEntityDialog QDateEdit,
QDialog#StandardEntityDialog QComboBox {{
    background: {DarkColor.SURFACE}; color: {DarkColor.TEXT_PRIMARY};
    border-color: {DarkColor.BORDER};
    selection-background-color: {DarkColor.SELECTED}; selection-color: {DarkColor.TEXT_PRIMARY};
}}
QDialog#StandardEntityDialog QLineEdit:disabled,
QDialog#StandardEntityDialog QTextEdit:disabled,
QDialog#StandardEntityDialog QDateEdit:disabled,
QDialog#StandardEntityDialog QComboBox:disabled {{
    background: {DarkColor.SURFACE_SUBTLE}; color: {DarkColor.DISABLED}; border-color: {DarkColor.BORDER};
}}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox {{
    background: {DarkColor.SURFACE}; color: {DarkColor.TEXT_PRIMARY}; border-color: {DarkColor.BORDER};
}}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox:disabled {{
    background: {DarkColor.SURFACE_SUBTLE}; color: {DarkColor.DISABLED}; border-color: {DarkColor.BORDER};
}}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton {{
    background: {DarkColor.SURFACE_SUBTLE}; border: 0; border-left: 1px solid {DarkColor.BORDER};
    border-radius: 0; padding: 0; margin: 0;
}}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton#ChevronSpinUpButton {{
    border-top-right-radius: 5px; border-bottom: 1px solid {DarkColor.BORDER};
}}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton#ChevronSpinDownButton {{
    border-bottom-right-radius: 5px;
}}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton:hover {{ background: {DarkColor.SELECTED}; }}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton:pressed {{ background: {DarkColor.SURFACE_ELEVATED}; }}
QDialog#StandardEntityDialog QDoubleSpinBox#ChevronDoubleSpinBox QToolButton:disabled {{
    background: {DarkColor.SURFACE_SUBTLE}; border-color: {DarkColor.BORDER};
}}

QLineEdit#GlobalSearch {{
    background: {DarkColor.SURFACE}; color: {DarkColor.TEXT_PRIMARY}; border-color: {DarkColor.BORDER};
    selection-background-color: {DarkColor.SELECTED}; selection-color: {DarkColor.TEXT_PRIMARY};
}}
QLineEdit#GlobalSearch:hover {{ border-color: {DarkColor.SEPARATOR}; }}
QLineEdit#GlobalSearch:focus {{ border-color: {DarkColor.FOCUS}; background: {DarkColor.SURFACE}; }}

QTableView, QTableWidget, QTreeView, QListView, QListWidget {{
    background: {DarkColor.SURFACE}; alternate-background-color: {DarkColor.SURFACE_SUBTLE};
    color: {DarkColor.TEXT_PRIMARY}; border-color: {DarkColor.BORDER};
    selection-background-color: {DarkColor.SELECTED}; selection-color: {DarkColor.TEXT_PRIMARY};
}}
QHeaderView::section {{
    background: {DarkColor.SURFACE_ELEVATED}; color: {DarkColor.TEXT_SECONDARY};
    border-color: {DarkColor.BORDER};
}}
QTableWidget#StandardTable {{
    background: {DarkColor.SURFACE}; alternate-background-color: {DarkColor.SURFACE_SUBTLE};
    border-color: {DarkColor.BORDER}; selection-background-color: {DarkColor.SELECTED};
    selection-color: {DarkColor.TEXT_PRIMARY};
}}
QTableWidget#StandardTable QHeaderView::section {{
    background: {DarkColor.SURFACE_ELEVATED}; color: {DarkColor.TEXT_SECONDARY};
    border-right-color: {DarkColor.BORDER}; border-bottom-color: {DarkColor.BORDER};
}}
QTableWidget#StandardTable::item {{ border-bottom-color: #303946; }}
QTableWidget#StandardTable::item:hover {{ background: {DarkColor.SURFACE_ELEVATED}; }}
QTableWidget#StandardTable::item:selected {{ background: {DarkColor.SELECTED}; color: {DarkColor.TEXT_PRIMARY}; }}
QLabel#EmptyState {{ color: {DarkColor.TEXT_MUTED}; background: {DarkColor.SURFACE_SUBTLE}; border-color: {DarkColor.SEPARATOR}; }}
QFrame#AnalysisToolbar, QFrame#AnalysisFiltersPanel, QFrame#AnalysisPopulationSummary {{
    background: {DarkColor.SURFACE}; border-color: {DarkColor.BORDER};
}}
QLabel#AnalysisTitle {{ color: {DarkColor.TEXT_PRIMARY}; }}
QLabel#AnalysisSectionTitle {{ color: {DarkColor.TEXT_SECONDARY}; }}
QLabel#AnalysisRecordCount {{ color: {DarkColor.ACCENT_HOVER}; }}
QLabel#AnalysisFilterCount, QLabel#AnalysisRowSemantics,
QLabel#AnalysisPopulationConditions, QLabel#AnalysisDisplayedCount,
QLabel#AnalysisPlotInspection, QLabel#AnalysisValidCount {{ color: {DarkColor.TEXT_MUTED}; }}
QLabel#AnalysisDisplayedCount[truncated="true"] {{ color: {DarkColor.WARNING}; font-weight: 600; }}
QLabel#AnalysisFilterValidation {{ color: {DarkColor.ERROR}; font-size: 11px; }}
QLabel#AnalysisInlineState {{
    color: {DarkColor.WARNING}; background: {DarkColor.SURFACE_SUBTLE};
    border: 1px solid {DarkColor.SEPARATOR}; border-radius: 5px; padding: 6px;
}}
QScrollArea#AnalysisFilterScroll, QScrollArea#AnalysisFilterScroll > QWidget > QWidget {{ background: transparent; border: 0; }}
QToolButton#AnalysisRemoveFilterButton {{
    color: {DarkColor.TEXT_MUTED}; background: transparent; border: 0;
}}
QToolButton#AnalysisRemoveFilterButton:hover {{
    color: {DarkColor.TEXT_PRIMARY}; background: {DarkColor.SURFACE_ELEVATED};
}}
QWidget#AnalysisPage QComboBox, QWidget#AnalysisPage QLineEdit, QWidget#AnalysisPage QDateEdit {{
    background: {DarkColor.SURFACE}; color: {DarkColor.TEXT_PRIMARY}; border-color: {DarkColor.BORDER};
}}
QWidget#AnalysisPage QComboBox {{ padding: 1px 32px 1px 7px; }}
QWidget#AnalysisPage QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 26px; border: 0; border-left: 1px solid {DarkColor.BORDER};
    background: {DarkColor.SURFACE_SUBTLE};
    border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}}
QWidget#AnalysisPage QComboBox::down-arrow {{
    image: url("{_DARK_COMBO_CHEVRON}"); width: 12px; height: 12px;
}}
QWidget#AnalysisPage QComboBox:hover::drop-down {{
    background: {DarkColor.SURFACE_ELEVATED}; border-left-color: {DarkColor.SEPARATOR};
}}
QWidget#AnalysisPage QComboBox:focus::drop-down {{ border-left-color: {DarkColor.FOCUS}; }}
QTableView#AnalysisDataTable {{
    background: {DarkColor.SURFACE}; alternate-background-color: {DarkColor.SURFACE_SUBTLE};
    border-color: {DarkColor.BORDER}; gridline-color: #303946;
    selection-background-color: {DarkColor.SELECTED}; selection-color: {DarkColor.TEXT_PRIMARY};
}}
QTableView#AnalysisDataTable QHeaderView::section {{
    background: {DarkColor.SURFACE_ELEVATED}; color: {DarkColor.TEXT_SECONDARY};
    border-right-color: {DarkColor.BORDER}; border-bottom-color: {DarkColor.BORDER};
}}
QTableWidget#AnalysisStatisticsTable {{
    background: {DarkColor.SURFACE}; alternate-background-color: {DarkColor.SURFACE_SUBTLE};
    border-color: {DarkColor.BORDER}; gridline-color: #303946;
    selection-background-color: {DarkColor.SELECTED}; selection-color: {DarkColor.TEXT_PRIMARY};
}}
QTableWidget#AnalysisStatisticsTable QHeaderView::section {{
    background: {DarkColor.SURFACE_ELEVATED}; color: {DarkColor.TEXT_SECONDARY};
    border-right-color: {DarkColor.BORDER}; border-bottom-color: {DarkColor.BORDER};
}}
QGraphicsView#AnalysisChartView {{
    background: {DarkColor.SURFACE_SUBTLE}; border: 1px solid {DarkColor.BORDER};
    border-radius: 7px;
}}
QToolButton#AnalysisSidebarToggle {{
    color: {DarkColor.TEXT_PRIMARY}; background: {DarkColor.SURFACE};
    border: 1px solid {DarkColor.SEPARATOR}; border-radius: 5px;
}}
QToolButton#AnalysisSidebarToggle:hover {{
    color: {DarkColor.ACCENT_HOVER}; border-color: {DarkColor.ACCENT};
    background: {DarkColor.SURFACE_SUBTLE};
}}

QGraphicsView#DashboardPlanView, QGraphicsView#BoreholeView {{
    background: {DarkColor.SURFACE_SUBTLE}; border: 1px solid {DarkColor.BORDER}; border-radius: 5px;
}}
QProgressBar#DashboardProgressBar {{
    border: 1px solid {DarkColor.SEPARATOR}; border-radius: 8px; background: {DarkColor.SURFACE_SUBTLE};
}}
QProgressBar#DashboardProgressBar::chunk {{ background: {DarkColor.ACCENT}; border-radius: 7px; }}
QLabel#PhotoImportPreview {{
    background: {DarkColor.SURFACE_SUBTLE}; color: {DarkColor.TEXT_SECONDARY};
    border: 1px solid {DarkColor.BORDER}; border-radius: 6px;
}}
QToolButton#AttachmentPreviewTile {{
    padding: 0; margin: 0; border: 1px solid {DarkColor.BORDER}; border-radius: 6px;
    background: {DarkColor.SURFACE_SUBTLE};
}}
QToolButton#AttachmentPreviewTile:hover {{ border-color: {DarkColor.ACCENT}; background: {DarkColor.SELECTED}; }}
QToolButton#AttachmentPreviewTile:pressed {{ border-color: {DarkColor.ACCENT_HOVER}; }}
QToolButton#PhotoTile {{ background: transparent; border: 0; padding: 0; margin: 0; }}
QToolButton#PhotoTile:hover {{ background: {DarkColor.SELECTED}; border: 2px solid {DarkColor.ACCENT}; border-radius: 11px; }}
QToolButton#AttachmentThumbnail {{ border: 1px solid transparent; border-radius: 10px; padding: 2px; background: transparent; }}
QToolButton#AttachmentThumbnail[selected="true"] {{ border: 2px solid {DarkColor.ACCENT}; }}

QLabel#ConnectionStatus[statusState="info"] {{ color: {DarkColor.TEXT_MUTED}; }}
QLabel#ConnectionStatus[statusState="success"] {{ color: {DarkColor.SUCCESS}; }}
QLabel#ConnectionStatus[statusState="error"] {{ color: {DarkColor.ERROR}; }}
QLabel#ConnectionEnvironmentWarning {{
    background: #493b21; color: {DarkColor.WARNING}; border: 1px solid #725c2e;
    border-radius: 5px; padding: 6px;
}}

QWidget#EngineeringWorkspace QGroupBox#drillingGroupCard,
QWidget#EngineeringWorkspace QGroupBox#actualDrillingGroupCard,
QWidget#EngineeringWorkspace QGroupBox#EngineeringCard {{
    background: {DarkColor.SURFACE}; border-color: {DarkColor.BORDER};
}}
QWidget#EngineeringWorkspace QGroupBox#EngineeringCard::title,
QWidget#EngineeringWorkspace QGroupBox#drillingGroupCard::title,
QWidget#EngineeringWorkspace QGroupBox#actualDrillingGroupCard::title {{ color: {DarkColor.TEXT_PRIMARY}; }}
QWidget#EngineeringWorkspace QGroupBox#drillingDesignArea,
QWidget#EngineeringWorkspace QGroupBox#chargeDesignArea,
QWidget#EngineeringWorkspace QGroupBox#actualDrillingArea,
QWidget#EngineeringWorkspace QGroupBox#actualChargeArea,
QWidget#EngineeringWorkspace QGroupBox#actualExceptionArea {{
    background: {DarkColor.SURFACE_SUBTLE}; border-color: {DarkColor.BORDER};
}}
QWidget#EngineeringWorkspace QGroupBox#drillingDesignArea::title,
QWidget#EngineeringWorkspace QGroupBox#chargeDesignArea::title,
QWidget#EngineeringWorkspace QGroupBox#actualDrillingArea::title,
QWidget#EngineeringWorkspace QGroupBox#actualChargeArea::title,
QWidget#EngineeringWorkspace QGroupBox#actualExceptionArea::title {{ color: {DarkColor.TEXT_SECONDARY}; }}
QWidget#EngineeringWorkspace QComboBox, QWidget#geomechanicsWorkspace QComboBox {{
    background: {DarkColor.SURFACE}; color: {DarkColor.TEXT_PRIMARY}; border-color: {DarkColor.BORDER};
}}
QWidget#EngineeringWorkspace QComboBox::drop-down,
QWidget#geomechanicsWorkspace QComboBox::drop-down,
QDialog#StandardEntityDialog QComboBox::drop-down,
QDialog#StandardEntityDialog QDateEdit::drop-down {{
    background: {DarkColor.SURFACE_SUBTLE}; border-left-color: {DarkColor.BORDER};
}}
QWidget#EngineeringWorkspace QComboBox::down-arrow,
QWidget#geomechanicsWorkspace QComboBox::down-arrow,
QDialog#StandardEntityDialog QComboBox::down-arrow,
QDialog#StandardEntityDialog QDateEdit::down-arrow {{ image: url("{_DARK_COMBO_CHEVRON}"); }}

QTabWidget[entityTabs="true"] QTabBar::tab {{ color: {DarkColor.TEXT_MUTED}; }}
QTabWidget[entityTabs="true"] QTabBar::tab:selected {{ color: {DarkColor.ACCENT}; border-bottom-color: {DarkColor.ACCENT}; }}
QTabWidget[entityTabs="true"] QTabBar::tab:hover:!selected {{ color: {DarkColor.TEXT_SECONDARY}; background: {DarkColor.SURFACE_SUBTLE}; }}
QTabWidget[entityTabs="true"] QTabBar::tab:focus {{ outline-color: {DarkColor.FOCUS}; }}

QListWidget#SettingsNavigation {{ background: {DarkColor.SURFACE_SUBTLE}; border-color: {DarkColor.BORDER}; color: {DarkColor.TEXT_PRIMARY}; }}
QListWidget#SettingsNavigation::item:hover:!selected {{ background: {DarkColor.SURFACE_ELEVATED}; color: {DarkColor.TEXT_SECONDARY}; }}
QListWidget#SettingsNavigation::item:selected,
QListWidget#SettingsNavigation::item:selected:hover {{
    color: {DarkColor.ACCENT_HOVER}; background: {DarkColor.SELECTED}; border-left-color: {DarkColor.ACCENT};
}}

QLabel#StatusBadge[statusRole="neutral"] {{ background: #2b323d; color: #c5ced8; border-color: #46515f; }}
QLabel#StatusBadge[statusRole="info"] {{ background: #1f3b52; color: #9bd1f5; border-color: #315c79; }}
QLabel#StatusBadge[statusRole="warning"] {{ background: #493b21; color: {DarkColor.WARNING}; border-color: #725c2e; }}
QLabel#StatusBadge[statusRole="success"] {{ background: #213c2b; color: {DarkColor.SUCCESS}; border-color: #386449; }}
QLabel#StatusBadge[statusRole="error"] {{ background: #46292b; color: {DarkColor.ERROR}; border-color: #754247; }}
QLabel#StatusBadge[statusRole="archived"] {{ background: #2b323d; color: #c5ced8; border-color: #46515f; }}
QLabel#StaleBadge {{ background: #493b21; color: {DarkColor.WARNING}; border-color: #725c2e; }}

QToolButton#SplitSaveButton {{ color: #ffffff; background-color: #2f78b5; border-color: #2f78b5; }}
QToolButton#SplitSaveButton:hover {{ background-color: #3b8ac9; border-color: #3b8ac9; }}
QToolButton#SplitSaveButton:pressed {{ background-color: #286a9f; border-color: #286a9f; }}
QToolButton#SplitSaveButton::menu-button {{ background-color: #2f78b5; border-left-color: #5595c6; }}
QToolButton#SplitSaveButton::menu-button:hover {{ background-color: #3b8ac9; }}

QScrollBar:vertical {{ background: {DarkColor.APP_BACKGROUND}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {DarkColor.APP_BACKGROUND}; height: 12px; margin: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {DarkColor.SEPARATOR}; border-radius: 5px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: #657284; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


_LIGHT_ACTIVE = {
    QPalette.ColorRole.Window: Color.APP_BACKGROUND,
    QPalette.ColorRole.WindowText: Color.TEXT_PRIMARY,
    QPalette.ColorRole.Base: Color.SURFACE,
    QPalette.ColorRole.AlternateBase: Color.SURFACE_SUBTLE,
    QPalette.ColorRole.ToolTipBase: Color.SURFACE,
    QPalette.ColorRole.ToolTipText: Color.TEXT_PRIMARY,
    QPalette.ColorRole.Text: Color.TEXT_PRIMARY,
    QPalette.ColorRole.Button: Color.SURFACE,
    QPalette.ColorRole.ButtonText: Color.TEXT_PRIMARY,
    QPalette.ColorRole.BrightText: "#ffffff",
    QPalette.ColorRole.Light: Color.SURFACE,
    QPalette.ColorRole.Midlight: Color.SURFACE_SUBTLE,
    QPalette.ColorRole.Dark: Color.BORDER,
    QPalette.ColorRole.Mid: Color.SEPARATOR,
    QPalette.ColorRole.Shadow: Color.TEXT_MUTED,
    QPalette.ColorRole.Highlight: Color.SELECTED,
    QPalette.ColorRole.HighlightedText: Color.TEXT_PRIMARY,
    QPalette.ColorRole.Link: Color.ACCENT,
    QPalette.ColorRole.LinkVisited: Color.ACCENT_HOVER,
    QPalette.ColorRole.PlaceholderText: Color.TEXT_MUTED,
}
_LIGHT_DISABLED = {
    **_LIGHT_ACTIVE,
    QPalette.ColorRole.WindowText: Color.DISABLED,
    QPalette.ColorRole.Base: Color.SURFACE_SUBTLE,
    QPalette.ColorRole.Text: Color.DISABLED,
    QPalette.ColorRole.Button: Color.SURFACE_SUBTLE,
    QPalette.ColorRole.ButtonText: Color.DISABLED,
    QPalette.ColorRole.Highlight: "#e5e9ef",
    QPalette.ColorRole.HighlightedText: Color.TEXT_MUTED,
    QPalette.ColorRole.Link: Color.DISABLED,
    QPalette.ColorRole.LinkVisited: Color.DISABLED,
    QPalette.ColorRole.PlaceholderText: Color.DISABLED,
}
_DARK_ACTIVE = {
    QPalette.ColorRole.Window: DarkColor.APP_BACKGROUND,
    QPalette.ColorRole.WindowText: DarkColor.TEXT_PRIMARY,
    QPalette.ColorRole.Base: DarkColor.SURFACE,
    QPalette.ColorRole.AlternateBase: DarkColor.SURFACE_SUBTLE,
    QPalette.ColorRole.ToolTipBase: DarkColor.SURFACE_ELEVATED,
    QPalette.ColorRole.ToolTipText: DarkColor.TEXT_PRIMARY,
    QPalette.ColorRole.Text: DarkColor.TEXT_PRIMARY,
    QPalette.ColorRole.Button: DarkColor.SURFACE,
    QPalette.ColorRole.ButtonText: DarkColor.TEXT_PRIMARY,
    QPalette.ColorRole.BrightText: "#ffffff",
    QPalette.ColorRole.Light: DarkColor.SURFACE_ELEVATED,
    QPalette.ColorRole.Midlight: DarkColor.SURFACE_SUBTLE,
    QPalette.ColorRole.Dark: DarkColor.BORDER,
    QPalette.ColorRole.Mid: DarkColor.SEPARATOR,
    QPalette.ColorRole.Shadow: "#0c0f13",
    QPalette.ColorRole.Highlight: DarkColor.SELECTED,
    QPalette.ColorRole.HighlightedText: DarkColor.TEXT_PRIMARY,
    QPalette.ColorRole.Link: DarkColor.ACCENT,
    QPalette.ColorRole.LinkVisited: DarkColor.ACCENT_HOVER,
    QPalette.ColorRole.PlaceholderText: DarkColor.TEXT_MUTED,
}
_DARK_DISABLED = {
    **_DARK_ACTIVE,
    QPalette.ColorRole.WindowText: DarkColor.DISABLED,
    QPalette.ColorRole.Base: DarkColor.SURFACE_SUBTLE,
    QPalette.ColorRole.Text: DarkColor.DISABLED,
    QPalette.ColorRole.Button: DarkColor.SURFACE_SUBTLE,
    QPalette.ColorRole.ButtonText: DarkColor.DISABLED,
    QPalette.ColorRole.Highlight: DarkColor.SURFACE_ELEVATED,
    QPalette.ColorRole.HighlightedText: DarkColor.TEXT_MUTED,
    QPalette.ColorRole.Link: DarkColor.DISABLED,
    QPalette.ColorRole.LinkVisited: DarkColor.DISABLED,
    QPalette.ColorRole.PlaceholderText: DarkColor.DISABLED,
}

_theme_signal_connected = False


def build_palette(*, dark: bool) -> QPalette:
    """Build a complete palette so native Qt controls match the selected QSS."""
    active = _DARK_ACTIVE if dark else _LIGHT_ACTIVE
    disabled = _DARK_DISABLED if dark else _LIGHT_DISABLED
    palette = QPalette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, value in active.items():
            palette.setColor(group, role, QColor(value))
    for role, value in disabled.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(value))

    accent_role = getattr(QPalette.ColorRole, "Accent", None)
    if accent_role is not None:
        accent = DarkColor.ACCENT if dark else Color.ACCENT
        disabled_accent = DarkColor.DISABLED if dark else Color.DISABLED
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            palette.setColor(group, accent_role, QColor(accent))
        palette.setColor(QPalette.ColorGroup.Disabled, accent_role, QColor(disabled_accent))
    return palette


def _resolved_dark(app: QApplication, theme: str) -> bool:
    if theme == "dark":
        return True
    if theme == "light":
        return False
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def _apply_resolved(app: QApplication, *, dark: bool) -> None:
    # Publish the target theme before palette/style repolish events are emitted so
    # compatibility widgets can react to the *new* theme during those callbacks.
    app.setProperty("slopeforgeTheme", "dark" if dark else "light")
    app.setPalette(build_palette(dark=dark))
    app.setStyleSheet(
        APPLICATION_STYLESHEET + (DARK_STYLESHEET if dark else LIGHT_STYLESHEET)
    )


def apply_application_theme(
    app: QApplication,
    theme: str | None = None,
    *,
    persist: bool = False,
) -> str:
    """Apply System/Light/Dark immediately and optionally persist the choice."""
    normalized = normalize_theme(theme if theme is not None else selected_theme())
    if persist:
        save_theme(normalized)

    hints = app.styleHints()
    set_color_scheme = getattr(hints, "setColorScheme", None)
    if callable(set_color_scheme):
        requested = {
            "system": Qt.ColorScheme.Unknown,
            "light": Qt.ColorScheme.Light,
            "dark": Qt.ColorScheme.Dark,
        }[normalized]
        set_color_scheme(requested)

    _apply_resolved(app, dark=_resolved_dark(app, normalized))
    return normalized


def initialize_application_theme(app: QApplication) -> str:
    """Apply the stored preference and follow Windows changes in System mode."""
    global _theme_signal_connected
    if not _theme_signal_connected:
        signal = getattr(app.styleHints(), "colorSchemeChanged", None)
        if signal is not None:
            def _system_scheme_changed(scheme) -> None:
                if selected_theme() == "system":
                    _apply_resolved(app, dark=scheme == Qt.ColorScheme.Dark)

            signal.connect(_system_scheme_changed)
            _theme_signal_connected = True
    return apply_application_theme(app)
