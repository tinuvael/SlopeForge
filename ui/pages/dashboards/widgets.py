from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.icons.ui.ui_icons import ui_icon
from app.localization import tr
from ui.assessment_result_presentation import (
    AssessmentResultPresentation,
    assessment_result_presentation,
)
from ui.widgets.design_system import CardFrame, set_status_role
from ui.pages.entity_overview_widgets import OverviewLinkButton


QuadrantPresentation = AssessmentResultPresentation


def quadrant_presentation(value):
    return assessment_result_presentation(value)


def metric(value):
    return "—" if value is None else f"{value:.2f}"


def format_dashboard_datetime(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return "—"


class DashboardEntityHeader(CardFrame):
    """Wide Project/Domain header matching the operational entity card language."""

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__()
        if parent is not None:
            self.setParent(parent)
        self.setObjectName("DashboardHeaderCard")
        self.setMinimumHeight(58)
        self.setMaximumHeight(72)
        self.layout.setContentsMargins(12, 8, 12, 8)
        self.layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.title_label = QLabel(str(title))
        self.title_label.setObjectName("EntityTitle")
        self.edit_button = QPushButton(tr("Edit"))
        self.edit_button.setProperty("role", "secondary")
        self.edit_button.setIcon(ui_icon("edit", "blue"))
        row.addWidget(self.title_label)
        row.addStretch()
        row.addWidget(self.edit_button)
        self.layout.addLayout(row)

        self.subtitle_label = QLabel(tr(subtitle))
        self.subtitle_label.setObjectName("MutedText")
        self.layout.addWidget(self.subtitle_label)


class MetricCard(CardFrame):
    def __init__(self, title, value, detail="", icon="analytics"):
        super().__init__()
        self.setObjectName("DashboardMetricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(74)
        self.setMaximumHeight(84)
        self.layout.setContentsMargins(12, 10, 12, 10)
        row = QHBoxLayout()
        row.setSpacing(10)
        image = QLabel()
        image.setPixmap(ui_icon(icon, "blue").pixmap(22, 22))
        image.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(image)
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(1)
        title_label = QLabel(tr(str(title)))
        title_label.setObjectName("MutedText")
        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("DashboardMetricValue")
        detail_label = QLabel(str(detail))
        detail_label.setObjectName("MutedText")
        detail_label.setWordWrap(False)
        box.addWidget(title_label)
        box.addWidget(self.value_label)
        box.addWidget(detail_label)
        row.addLayout(box, 1)
        self.layout.addLayout(row)


class EmptyStateWidget(QWidget):
    def __init__(self, text, icon="info"):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        image = QLabel()
        image.setPixmap(ui_icon(icon).pixmap(20, 20))
        row.addWidget(image)
        label = QLabel(tr(str(text)))
        label.setObjectName("MutedText")
        row.addWidget(label)
        row.addStretch()


class DashboardCard(CardFrame):
    """Shared dashboard card shell with a fixed header band."""

    HEADER_HEIGHT = 26

    def __init__(self, title: str, parent=None):
        super().__init__()
        if parent is not None:
            self.setParent(parent)
        self.setObjectName("DashboardCard")
        self.layout.setContentsMargins(12, 10, 12, 10)
        self.layout.setSpacing(7)

        self.header_host = QWidget()
        self.header_host.setFixedHeight(self.HEADER_HEIGHT)
        self.header_host.setMinimumWidth(0)
        self.header_host.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.header = QHBoxLayout(self.header_host)
        self.header.setContentsMargins(0, 0, 0, 0)
        self.header.setSpacing(6)
        self.heading = QLabel(tr(title))
        self.heading.setObjectName("CardTitle")
        self.heading.setMinimumWidth(0)
        self.heading.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.subtitle = QLabel()
        self.subtitle.setObjectName("MutedText")
        self.subtitle.setMinimumWidth(0)
        self.subtitle.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.subtitle.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.header.addWidget(self.heading, 0, Qt.AlignmentFlag.AlignVCenter)
        self.header.addSpacing(6)
        self.header.addWidget(self.subtitle, 1, Qt.AlignmentFlag.AlignVCenter)
        self.header.addStretch()
        self.layout.addWidget(self.header_host)

    def set_subtitle(self, text: str | None):
        value = str(text or "")
        self.subtitle.setText(value)
        self.subtitle.setVisible(bool(value))

    def add_header_action(self, text: str) -> OverviewLinkButton:
        button = OverviewLinkButton(text)
        button.setFixedHeight(self.HEADER_HEIGHT)
        button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.header.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return button


@dataclass(frozen=True)
class SummaryRow:
    key: str
    title: str
    detail: str = ""
    trailing: str = ""
    accent: str | None = None


class SummaryRowWidget(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ViewportBoundListWidget(QListWidget):
    """Keep persistent item widgets bound to the current viewport geometry.

    Project dashboard lists are populated while their page is still outside the
    final QStackedWidget layout. On Windows, QListWidget can therefore assign a
    persistent item widget the pre-layout viewport width and keep that stale
    geometry until the model is changed again. A later import repopulates the
    list and incidentally fixes it. Relayout on show/resize instead so the first
    render is correct as well.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preferred_height: int | None = None

    def set_preferred_height(self, height: int) -> None:
        value = max(1, int(height))
        if value == self._preferred_height:
            return
        self._preferred_height = value
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self._preferred_height is not None:
            hint.setHeight(self._preferred_height)
        return hint

    def _sync_item_widget_geometries(self) -> None:
        if self.count() <= 0:
            return
        self.doItemsLayout()
        viewport_rect = self.viewport().rect()
        for index in range(self.count()):
            item = self.item(index)
            widget = self.itemWidget(item)
            if widget is None:
                continue
            rect = self.visualItemRect(item)
            if rect.isValid() and rect.width() > 0:
                rect.setLeft(max(rect.left(), viewport_rect.left()))
                rect.setRight(min(rect.right(), viewport_rect.right()))
                widget.setGeometry(rect)

    def refresh_item_widgets(self) -> None:
        self._sync_item_widget_geometries()
        QTimer.singleShot(0, self._sync_item_widget_geometries)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_item_widgets()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_item_widgets()


class CompactSummaryList(DashboardCard):
    """Bounded dashboard rows: row click filters, optional Go to navigates."""

    activated = Signal(str)
    go_to_requested = Signal(str)
    ROW_HEIGHT = 46
    SELECTION_MARKER_WIDTH = 2
    SELECTION_MARKER_HEIGHT = 26

    def __init__(
        self,
        title: str,
        parent=None,
        *,
        visible_rows: int = 3,
        show_go_to: bool = False,
        fill_available: bool = False,
        row_height: int | None = None,
        row_spacing: int = 4,
    ):
        super().__init__(title, parent)
        self.visible_rows = max(1, int(visible_rows))
        self.show_go_to = bool(show_go_to)
        self.fill_available = bool(fill_available)
        self.row_height = max(28, int(row_height or self.ROW_HEIGHT))
        self.row_spacing = max(0, int(row_spacing))
        self._selected_key: str | None = None
        self._row_widgets: dict[str, SummaryRowWidget] = {}
        self._selection_markers: dict[str, QLabel] = {}
        self.list = ViewportBoundListWidget()
        self.list.setMinimumWidth(0)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setSpacing(self.row_spacing)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setStyleSheet(
            "QListWidget{background:transparent;border:0;margin:0;padding:0;}"
            "QListWidget::item{background:transparent;border:0;outline:0;margin:0;padding:0;}"
        )
        self.list.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        self.layout.addWidget(self.list, 1)
        self.set_rows([])

    @staticmethod
    def _set_selection_marker(marker: QLabel, selected: bool) -> None:
        if selected:
            color = marker.palette().color(QPalette.ColorRole.Highlight).name()
        else:
            color = "transparent"
        marker.setStyleSheet(
            f"background-color:{color};border:none;border-radius:1px;"
        )

    def _apply_row_selection(self) -> None:
        for key, marker in self._selection_markers.items():
            self._set_selection_marker(marker, key == self._selected_key)

    def _activate_row(self, key: str) -> None:
        self._selected_key = None if self._selected_key == key else key
        self._apply_row_selection()
        self.activated.emit(key)

    def clear_selection(self):
        self._selected_key = None
        self.list.setCurrentItem(None)
        self._apply_row_selection()

    def set_rows(self, rows: list[SummaryRow], *, empty_text="No data yet"):
        self.list.clear()
        self._selected_key = None
        self._row_widgets = {}
        self._selection_markers = {}
        rows = list(rows)
        if self.fill_available:
            self.list.setMinimumHeight(self.row_height + 4)
            self.list.set_preferred_height(self.row_height + 4)
            self.list.setMaximumHeight(16777215)
        else:
            visible_height = (
                self.row_height * min(max(1, len(rows)), self.visible_rows) + 4
            )
            # One row is the contraction floor at the minimum supported window;
            # ``visible_rows`` remains the preferred compact baseline.
            self.list.setMinimumHeight(self.row_height + 4)
            self.list.set_preferred_height(visible_height)
            # ``visible_rows`` is the compact baseline, not a ceiling. Let the
            # surrounding card allocate more viewport height when available;
            # the list still scrolls when its assigned space is constrained.
            self.list.setMaximumHeight(16777215)
        if not rows:
            item = QListWidgetItem(tr(empty_text))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(0, self.row_height))
            self.list.addItem(item)
            self.list.refresh_item_widgets()
            return
        for row in rows:
            key = str(row.key)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(0, self.row_height))
            holder = SummaryRowWidget()
            holder.setObjectName("DashboardSummaryRow")
            holder.setMinimumWidth(0)
            holder.setFixedHeight(self.row_height - 2)
            holder.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
            holder.setCursor(Qt.CursorShape.PointingHandCursor)
            holder.clicked.connect(
                lambda current_key=key: self._activate_row(current_key)
            )
            self._row_widgets[key] = holder
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(6, 4, 8, 4)
            layout.setSpacing(4)

            selection_marker = QLabel()
            selection_marker.setObjectName("DashboardSelectionMarker")
            selection_marker.setFixedSize(
                self.SELECTION_MARKER_WIDTH, self.SELECTION_MARKER_HEIGHT
            )
            selection_marker.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            self._set_selection_marker(selection_marker, False)
            self._selection_markers[key] = selection_marker
            layout.addWidget(selection_marker, 0, Qt.AlignmentFlag.AlignVCenter)

            if row.accent:
                accent = QFrame()
                accent.setFixedWidth(4)
                accent.setSizePolicy(
                    QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
                )
                accent.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
                accent.setStyleSheet(
                    f"background:{row.accent};border:0;border-radius:2px;"
                )
                layout.addWidget(accent)
            text = QVBoxLayout()
            text.setContentsMargins(0, 0, 0, 0)
            text.setSpacing(0)
            title = QLabel(row.title)
            title.setObjectName("RelatedEntityTitle")
            title.setMinimumWidth(0)
            title.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            title.setToolTip(row.title)
            title.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            detail = QLabel(row.detail)
            detail.setObjectName("MutedText")
            detail.setMinimumWidth(0)
            detail.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            detail.setToolTip(row.detail)
            detail.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            text.addWidget(title)
            text.addWidget(detail)
            layout.addLayout(text, 1)
            if row.trailing:
                trailing = QLabel(row.trailing)
                trailing.setObjectName("SummaryValue")
                trailing.setMinimumWidth(0)
                trailing.setSizePolicy(
                    QSizePolicy.Policy.Ignored,
                    QSizePolicy.Policy.Preferred,
                )
                trailing.setToolTip(row.trailing)
                trailing.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                trailing.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
                layout.addWidget(trailing)
            if self.show_go_to:
                go_to = OverviewLinkButton(tr("Go to ›"))
                go_to.setToolTip(tr("Open"))
                go_to.setSizePolicy(
                    QSizePolicy.Policy.Fixed,
                    QSizePolicy.Policy.Fixed,
                )
                go_to.clicked.connect(
                    lambda _checked=False, current_key=key: self.go_to_requested.emit(
                        current_key
                    )
                )
                layout.addWidget(go_to)
            self.list.addItem(item)
            self.list.setItemWidget(item, holder)
        self.list.refresh_item_widgets()


class AssessmentProgressCard(DashboardCard):
    def __init__(self, parent=None):
        super().__init__("Assessment progress", parent)
        self.setMinimumHeight(118)
        summary = QHBoxLayout()
        summary.setContentsMargins(0, 2, 0, 3)
        self.counts = QLabel()
        self.counts.setObjectName("DashboardStrongText")
        self.percent = QLabel("0%")
        self.percent.setObjectName("DashboardPercentValue")
        self.percent.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        summary.addWidget(self.counts, 1)
        summary.addWidget(self.percent)
        self.layout.addLayout(summary)
        self.progress = QProgressBar()
        self.progress.setObjectName("DashboardProgressBar")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(17)
        self.layout.addWidget(self.progress)
        self.layout.addStretch()

    def set_counts(self, total: int, completed: int, drafts: int):
        total = max(0, int(total))
        completed = max(0, int(completed))
        drafts = max(0, int(drafts))
        pending = max(0, total - completed - drafts)
        percentage = round(100 * completed / total) if total else 0
        self.counts.setText(
            tr("Completed: %1  ·  Draft: %2  ·  Not evaluated: %3")
            .replace("%1", str(completed))
            .replace("%2", str(drafts))
            .replace("%3", str(pending))
        )
        self.percent.setText(f"{percentage}%")
        self.progress.setValue(percentage)


class BlastActivityCard(DashboardCard):
    def __init__(self, parent=None):
        super().__init__("Blast activity", parent)
        self.setMinimumHeight(118)
        self.counts = QLabel()
        self.counts.setObjectName("DashboardStrongText")
        self.latest = QLabel()
        self.latest.setObjectName("MutedText")
        self.layout.addWidget(self.counts)
        self.layout.addWidget(self.latest)
        self.layout.addStretch()

    def set_data(self, production: int, contour: int, blasts):
        self.counts.setText(
            tr("Production: %1  ·  Contour: %2")
            .replace("%1", str(production))
            .replace("%2", str(contour))
        )
        dated = [
            item for item in blasts if getattr(item, "event_date", None) is not None
        ]
        latest = max(dated, key=lambda item: item.event_date) if dated else None
        if latest is None:
            self.latest.setText(tr("No dated Blast Events yet"))
        else:
            self.latest.setText(
                f"{tr('Latest blast')}: {latest.name}  ·  "
                f"{format_dashboard_datetime(latest.event_date)}"
            )


class ProjectLinesCard(DashboardCard):
    VISIBLE_ROWS = 3
    ROW_HEIGHT = 44

    def __init__(self, parent=None):
        super().__init__("Project Lines", parent)
        self.setMinimumHeight(118)
        self.list = ViewportBoundListWidget()
        self.list.setMinimumWidth(0)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setSpacing(3)
        self.list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setStyleSheet(
            "QListWidget{background:transparent;border:0;margin:0;padding:0;}"
            "QListWidget::item{margin:0;padding:0;}"
        )
        self.list.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        self.list.setMinimumHeight(self.ROW_HEIGHT + 4)
        self.list.setMaximumHeight(self.ROW_HEIGHT * self.VISIBLE_ROWS + 5)
        self.layout.addWidget(self.list, 1)

    def set_datasets(self, datasets):
        self.list.clear()
        datasets = list(datasets)
        if not datasets:
            item = QListWidgetItem(tr("No Project Lines loaded"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(0, self.ROW_HEIGHT))
            self.list.addItem(item)
            self.list.refresh_item_widgets()
            return
        for dataset in datasets:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, self.ROW_HEIGHT))
            holder = QWidget()
            holder.setObjectName("ProjectLinesDatasetRow")
            holder.setMinimumWidth(0)
            holder.setFixedHeight(self.ROW_HEIGHT - 2)
            holder.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(8, 4, 9, 4)
            layout.setSpacing(8)
            text = QVBoxLayout()
            text.setContentsMargins(0, 0, 0, 0)
            text.setSpacing(0)
            title_text = str(dataset.name)
            title = QLabel(title_text)
            title.setObjectName("RelatedEntityTitle")
            title.setMinimumWidth(0)
            title.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            title.setToolTip(title_text)
            stamp = getattr(dataset, "imported_at", None)
            detail_text = (
                f"{format_dashboard_datetime(stamp)}  ·  "
                f"{getattr(dataset, 'source_file_name', '') or '—'}"
            )
            detail = QLabel(detail_text)
            detail.setObjectName("MutedText")
            detail.setMinimumWidth(0)
            detail.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            detail.setToolTip(detail_text)
            text.addWidget(title)
            text.addWidget(detail)
            layout.addLayout(text, 1)
            state = QLabel(
                tr("Active")
                if getattr(dataset, "is_active", False)
                else tr("Inactive")
            )
            state.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
            set_status_role(
                state,
                "success" if getattr(dataset, "is_active", False) else "neutral",
            )
            layout.addWidget(state)
            self.list.addItem(item)
            self.list.setItemWidget(item, holder)
        self.list.refresh_item_widgets()


class DashboardRecentActivityCard(DashboardCard):
    ROW_HEIGHT = 30
    META_WIDTH = 190

    def __init__(self, parent=None):
        super().__init__("Recent activity", parent)
        self.setMinimumHeight(142)
        self.list = ViewportBoundListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setSpacing(0)
        self.list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setStyleSheet(
            "QListWidget{background:transparent;border:0;}"
            "QListWidget::item{background:transparent;border:0;}"
        )
        self.layout.addWidget(self.list, 1)

    @staticmethod
    def _entry_parts(entry):
        if hasattr(entry, "changed_at"):
            entity_type = tr(str(getattr(entry, "entity_type", "")))
            entity_name = str(getattr(entry, "entity_name", "") or "")
            action = tr(str(getattr(entry, "action", "") or ""))
            title = " ".join(part for part in (entity_type, entity_name) if part)
            if action:
                title = f"{title}  ·  {action}" if title else action
            return (
                title,
                getattr(entry, "changed_at", None),
                str(getattr(entry, "actor", "") or ""),
            )
        name, changed = entry[:2]
        author = str(entry[2] or "") if len(entry) > 2 else ""
        return str(name), changed, author

    def set_entries(self, entries):
        self.list.clear()
        visible = list(entries[:10])
        if not visible:
            item = QListWidgetItem(tr("No recent activity"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(0, self.ROW_HEIGHT))
            self.list.addItem(item)
            self.list.refresh_item_widgets()
            return
        for entry in visible:
            title_text, changed, author = self._entry_parts(entry)
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(0, self.ROW_HEIGHT))
            holder = QWidget()
            holder.setObjectName("DashboardActivityRow")
            holder.setFixedHeight(self.ROW_HEIGHT - 1)
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(2, 0, 12, 0)
            layout.setSpacing(8)
            title = QLabel(title_text)
            title.setObjectName("ActivityTitle")
            title.setMinimumWidth(0)
            title.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            title.setToolTip(title_text)
            stamp_text = format_dashboard_datetime(changed)
            meta_text = f"{author}  ·  {stamp_text}" if author else stamp_text
            meta = QLabel(meta_text)
            meta.setObjectName("MutedText")
            meta.setFixedWidth(self.META_WIDTH)
            meta.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            meta.setToolTip(meta_text)
            layout.addWidget(title, 1)
            layout.addWidget(meta)
            self.list.addItem(item)
            self.list.setItemWidget(item, holder)
        self.list.refresh_item_widgets()


def section(title, child):
    card = DashboardCard(title)
    card.layout.addWidget(child)
    return card
