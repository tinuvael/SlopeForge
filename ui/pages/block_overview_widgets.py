from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.localization import tr
from ui.presentation_labels import history_text
from ui.pages.entity_overview_widgets import (
    InlineAutosaveNotes,
    OverviewLinkButton,
    QuickAttachmentPreview,
    RecentActivityCard,
    RelatedEntityList,
    SquareGeometryCard,
    apply_status_badge,
)


class BlockRelatedEntityList(RelatedEntityList):
    """Stable Block relationship card with an internally scrollable viewport."""

    LIST_HEIGHT = 136
    ROW_HORIZONTAL_INSET = 8
    VISIBLE_BOTTOM_MARGIN = 4
    LIGHT_STATE_COLORS = {
        "completed": ("#edf8f0", "#58a66a"),
        "assessed": ("#edf8f0", "#58a66a"),
        "in_progress": ("#f4f8fd", "#9bc2e8"),
        "planned": ("#f4f8fd", "#9bc2e8"),
        "draft": ("#f7f8fa", "#cfd7e2"),
        "in_preparation": ("#f7f8fa", "#cfd7e2"),
        "unknown": ("#ffffff", "#cfd7e2"),
    }
    DARK_STATE_COLORS = {
        "completed": ("#1f3829", "#4e7d5d"),
        "assessed": ("#1f3829", "#4e7d5d"),
        "in_progress": ("#1d3347", "#497493"),
        "planned": ("#1d3347", "#497493"),
        "draft": ("#2a313b", "#566271"),
        "in_preparation": ("#2a313b", "#566271"),
        "unknown": ("#2a313b", "#566271"),
    }

    def __init__(self, title: str, *, expand_viewport=False):
        super().__init__(title)
        self._expand_viewport = bool(expand_viewport)
        vertical_policy = (
            QSizePolicy.Policy.Expanding
            if self._expand_viewport
            else QSizePolicy.Policy.Fixed
        )
        self.setSizePolicy(QSizePolicy.Policy.Preferred, vertical_policy)
        self.layout.setSpacing(6)
        self.list.setSpacing(2)
        if self._expand_viewport:
            self.list.setMinimumHeight(self.LIST_HEIGHT)
            self.list.setMaximumHeight(16777215)
            self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        else:
            self.list.setFixedHeight(self.LIST_HEIGHT)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet("QListWidget{background:transparent;border:0;}")
        self.list.viewport().installEventFilter(self)
        self.empty_label = QLabel()
        self.empty_label.setObjectName("MutedText")
        if self._expand_viewport:
            self.empty_label.setMinimumHeight(self.LIST_HEIGHT)
            self.empty_label.setMaximumHeight(16777215)
            self.empty_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        else:
            self.empty_label.setFixedHeight(self.LIST_HEIGHT)
        self.empty_label.setContentsMargins(2, 0, 0, 0)
        self.empty_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.empty_label.hide()
        self.layout.addWidget(self.empty_label)
        if self._expand_viewport:
            self.layout.setStretch(self.layout.indexOf(self.list), 1)
            self.layout.setStretch(self.layout.indexOf(self.empty_label), 1)
        self.list.itemSelectionChanged.connect(self._sync_row_styles)
        self._refit_pending = False

    def _dark_theme(self) -> bool:
        app = QApplication.instance()
        if app is not None and app.property("slopeforgeTheme") in {"light", "dark"}:
            return app.property("slopeforgeTheme") == "dark"
        return self.palette().color(QPalette.ColorRole.Window).lightness() < 128

    def eventFilter(self, watched, event):
        if watched is self.list.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_row_widths()
            self._schedule_row_refit()
        return super().eventFilter(watched, event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self._sync_row_styles()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_row_refit()

    def _schedule_row_refit(self) -> None:
        if self._refit_pending:
            return
        self._refit_pending = True
        QTimer.singleShot(0, self._refit_after_layout)

    def _refit_after_layout(self) -> None:
        self._refit_pending = False
        if not self.list.count() or self.list.isHidden():
            return
        self._sync_row_widths()
        self._fit_two_rows(use_visual_geometry=True)

    def _row_available_width(self) -> int:
        return max(1, self.list.viewport().width() - self.list.spacing() * 2)

    def _row_target_width(self) -> int:
        return max(1, self._row_available_width() - self.ROW_HORIZONTAL_INSET * 2)

    def _sync_row_widths(self) -> None:
        target = self._row_target_width()
        for index in range(self.list.count()):
            item = self.list.item(index)
            holder = self._row_card(item)
            if holder is None:
                continue
            wrapper = self.list.itemWidget(item)
            wrapper.setFixedWidth(self._row_available_width())
            holder.setFixedWidth(target)
            item.setSizeHint(QSize(self._row_available_width(), max(1, holder.sizeHint().height())))

    def _fit_two_rows(self, *, use_visual_geometry=False) -> None:
        """Reserve a two-row minimum while allowing a taller Overview viewport."""
        if not self.list.count():
            height = self.LIST_HEIGHT
        else:
            row_heights = [
                self.list.item(index).sizeHint().height()
                for index in range(min(2, self.list.count()))
            ]
            if len(row_heights) == 1:
                row_heights.append(row_heights[0])
            height = (
                sum(row_heights)
                + self.list.spacing() * 4
                + self.list.frameWidth() * 2
                + 2
            )
            if use_visual_geometry:
                second = self.list.item(min(1, self.list.count() - 1))
                rect = self.list.visualItemRect(second)
                if rect.isValid():
                    viewport_chrome = self.list.height() - self.list.viewport().height()
                    height = max(height, rect.bottom() + 1 + viewport_chrome)
        if self._expand_viewport:
            minimum_height = max(self.LIST_HEIGHT, height)
            self.list.setMinimumHeight(minimum_height)
            self.empty_label.setMinimumHeight(minimum_height)
        else:
            self.list.setFixedHeight(height)
            self.empty_label.setFixedHeight(height)

    def set_rows(self, rows, *, empty_text="No linked entities"):
        """Build Block rows directly so QListWidget owns each wrapper only once."""
        rows = list(rows)
        self.list.clear()
        if self._expand_viewport:
            self.list.setMinimumHeight(self.LIST_HEIGHT)
        else:
            self.list.setFixedHeight(self.LIST_HEIGHT)

        if not rows:
            self.list.hide()
            self.empty_label.setText(tr(empty_text))
            self.empty_label.show()
            self.updateGeometry()
            return

        self.empty_label.hide()
        self.list.show()

        for row in rows:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row.entity_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, row.status_state or "unknown")

            wrapper = QWidget()
            wrapper.setObjectName("BlockRelatedEntityWrapper")
            wrapper.setCursor(Qt.CursorShape.PointingHandCursor)
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(
                self.ROW_HORIZONTAL_INSET, 0, self.ROW_HORIZONTAL_INSET, 0
            )
            wrapper_layout.setSpacing(0)
            wrapper_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

            holder = QWidget(wrapper)
            holder.setObjectName("BlockRelatedEntityItem")
            holder.setCursor(Qt.CursorShape.PointingHandCursor)
            holder.setMinimumWidth(0)
            holder.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout = QVBoxLayout(holder)
            layout.setContentsMargins(9, 2, 12, 2)
            layout.setSpacing(1)

            top = QHBoxLayout()
            title = QLabel(row.title)
            title.setObjectName("RelatedEntityTitle")
            title.setMinimumWidth(0)
            top.addWidget(title)
            top.addStretch()
            if row.status_text:
                badge = QLabel(tr(row.status_text))
                apply_status_badge(badge, row.status_state)
                top.addWidget(badge)
            if row.stale:
                stale = QLabel(tr("Stale"))
                stale.setObjectName("StaleBadge")
                top.addWidget(stale)
            if row.action_text:
                action = OverviewLinkButton(row.action_text)
                action.clicked.connect(
                    lambda _checked=False, entity_id=row.entity_id:
                    self.entity_action_requested.emit(str(entity_id))
                )
                top.addWidget(action)
            layout.addLayout(top)

            subtitle = QLabel(row.subtitle)
            subtitle.setObjectName("MutedText")
            subtitle.setMinimumWidth(0)
            layout.addWidget(subtitle)
            wrapper_layout.addWidget(holder)

            target = self._row_target_width()
            holder.setFixedWidth(target)
            item.setSizeHint(QSize(self._row_available_width(), holder.sizeHint().height()))
            self.list.addItem(item)
            self.list.setItemWidget(item, wrapper)

        self._sync_row_widths()
        self._fit_two_rows()
        self._schedule_row_refit()
        self._sync_row_styles()
        self.updateGeometry()

    def _row_card(self, item):
        wrapper = self.list.itemWidget(item)
        if wrapper is None:
            return None
        return wrapper.findChild(QWidget, "BlockRelatedEntityItem")

    def _sync_row_styles(self):
        dark = self._dark_theme()
        colors = self.DARK_STATE_COLORS if dark else self.LIGHT_STATE_COLORS
        for index in range(self.list.count()):
            item = self.list.item(index)
            holder = self._row_card(item)
            if holder is None:
                continue
            state = str(item.data(Qt.ItemDataRole.UserRole + 1) or "unknown")
            background, accent = colors.get(state, colors["unknown"])
            selected = item.isSelected()
            if dark:
                border = "#79b9ee" if selected else accent
                if selected:
                    background = "#243f57"
                text_rules = (
                    "QLabel#RelatedEntityTitle{color:#f2f5f8;}"
                    "QLabel#MutedText{color:#c5ced8;}"
                    "QPushButton[role=\"link\"]{color:#79b9ee;}"
                )
            else:
                border = "#2563a6" if selected else accent
                if selected:
                    background = "#f8fbff"
                text_rules = ""
            width = 2 if selected else 1
            holder.setStyleSheet(
                f"QWidget#BlockRelatedEntityItem{{background:{background};"
                f"border:{width}px solid {border};border-radius:5px;}}"
                f"{text_rules}"
            )


class BlockNotesCard(InlineAutosaveNotes):
    """Notes card that gains a larger editor viewport on taller Overviews."""

    EDITOR_HEIGHT = 46

    def __init__(self, title="Notes", *, expand_editor=False):
        super().__init__(title)
        self._expand_editor = bool(expand_editor)
        vertical_policy = (
            QSizePolicy.Policy.Expanding
            if self._expand_editor
            else QSizePolicy.Policy.Fixed
        )
        self.setSizePolicy(QSizePolicy.Policy.Preferred, vertical_policy)
        self.layout.setSpacing(6)
        if self._expand_editor:
            self.editor.setMinimumHeight(self.EDITOR_HEIGHT)
            self.editor.setMaximumHeight(16777215)
            self.editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.layout.setStretch(self.layout.indexOf(self.editor), 1)
        else:
            self.editor.setFixedHeight(self.EDITOR_HEIGHT)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


class BlockRecentActivityCard(RecentActivityCard):
    """Four stable single-line activity slots shared by all operational overviews."""

    SLOT_COUNT = 4
    SLOT_HEIGHT = 32

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.rows.setSpacing(4)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)

    def set_entries(self, entries, limit=4):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        visible = list(entries[: min(int(limit), self.SLOT_COUNT)])
        for index in range(self.SLOT_COUNT):
            slot = QWidget()
            slot.setFixedHeight(self.SLOT_HEIGHT)
            layout = QHBoxLayout(slot)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            if index < len(visible):
                entry = visible[index]
                title = QLabel(f"●  {history_text(entry.title)}")
                title.setObjectName("ActivityTitle")
                title.setMinimumWidth(0)
                title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                stamp = entry.timestamp.strftime("%d.%m.%Y %H:%M") if entry.timestamp else "—"
                actor = entry.actor or "—"
                meta = QLabel(f"{actor} · {stamp}")
                meta.setObjectName("MutedText")
                meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                layout.addWidget(title, 1)
                layout.addWidget(meta, 0)
            elif index == 0 and not visible:
                empty = QLabel(tr("No history yet"))
                empty.setObjectName("MutedText")
                layout.addWidget(empty)
                layout.addStretch()
            self.rows.addWidget(slot)


class BlockAttachmentPreview(QuickAttachmentPreview):
    """Stable sidebar preview that never resolves files in Database only mode."""

    def set_items(self, service, items, empty_text: str, *, can_add=True) -> None:
        for index in range(self.content.count()):
            item = self.content.itemAt(index)
            widget = item.widget()
            if widget is not None:
                widget.hide()
        storage_available = bool(getattr(service, "storage_available", True)) if service is not None else False
        preview_service = service if storage_available else None
        self.setUpdatesEnabled(False)
        try:
            super().set_items(
                preview_service,
                items,
                empty_text,
                can_add=bool(can_add and storage_available),
            )
        finally:
            self.setUpdatesEnabled(True)
        if not storage_available:
            self.add_button.setToolTip(tr("File storage is unavailable for this connection."))
        else:
            self.add_button.setToolTip("")
        self.update()


class BlockGeometryCard(SquareGeometryCard):
    """Block geometry card whose vertical hint never drives the Overview row."""

    PREFERRED_WIDTH = 700
    MINIMUM_WIDTH = 610
    MAXIMUM_WIDTH = 800

    def __init__(
        self,
        title="Plan / geometry",
        *,
        action_label="Reimport",
        enforce_square=False,
        expand_horizontally=False,
        parent=None,
    ):
        super().__init__(
            title,
            action_label=action_label,
            enforce_square=False,
            parent=parent,
        )
        self.setMinimumWidth(self.MINIMUM_WIDTH)
        self.setMaximumWidth(16777215 if expand_horizontally else self.MAXIMUM_WIDTH)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        horizontal_policy = (
            QSizePolicy.Policy.Expanding
            if expand_horizontally
            else QSizePolicy.Policy.Preferred
        )
        self.setSizePolicy(horizontal_policy, QSizePolicy.Policy.Ignored)
        self.plan.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.plan.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def sizeHint(self):
        return QSize(self.PREFERRED_WIDTH, 0)

    def minimumSizeHint(self):
        return QSize(self.MINIMUM_WIDTH, 0)


class BlockSectionHost(QWidget):
    """Permanent QTabWidget page; only its inner editor is replaced."""

    def __init__(self, content: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._content: QWidget | None = None
        if content is not None:
            self.set_content(content)

    def set_content(self, widget: QWidget) -> None:
        old = self._content
        if old is widget:
            return
        if old is not None:
            self._layout.removeWidget(old)
            old.hide()
            old.deleteLater()
        self._content = widget
        widget.setParent(self)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._layout.addWidget(widget, 1)
        widget.show()
        self.updateGeometry()
