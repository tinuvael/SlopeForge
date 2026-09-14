"""Read-only Analysis data-table presentation and server-sort interaction."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QAbstractTableModel, QDate, QLocale, QModelIndex, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.localization import tr
from application.analysis.models import (
    AnalysisDataset,
    AnalysisField,
    AnalysisFieldType,
    AnalysisRow,
    SortSpec,
    SourceReference,
)
from ui.assessment_result_presentation import assessment_result_presentation
from ui.widgets.design_system import set_button_role


MISSING_VALUE = "—"


def format_analysis_value(field: AnalysisField, value: object | None) -> str:
    if value is None or value == "":
        return MISSING_VALUE
    if field.field_type is AnalysisFieldType.NUMERIC:
        decimals = 2 if field.decimals is None else field.decimals
        return f"{float(value):.{decimals}f}"
    if field.field_type is AnalysisFieldType.DATE and isinstance(value, date):
        return QLocale.system().toString(
            QDate(value.year, value.month, value.day),
            QLocale.FormatType.ShortFormat,
        )
    if field.format_hint == "assessment_result":
        return assessment_result_presentation(str(value)).label
    return str(value)


class AnalysisTableModel(QAbstractTableModel):
    """Read-only model that delegates sorting to the dataset provider."""

    sort_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dataset: AnalysisDataset | None = None
        self.rows: list[AnalysisRow] = []

    def set_dataset(self, dataset: AnalysisDataset, rows: tuple[AnalysisRow, ...]) -> None:
        self.beginResetModel()
        self.dataset = dataset
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid() or self.dataset is None:
            return 0
        return len(self.dataset.fields)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.dataset is None:
            return None
        row = self.rows[index.row()]
        field = self.dataset.fields[index.column()]
        value = row.values.get(field.key)
        if role == Qt.ItemDataRole.DisplayRole:
            return format_analysis_value(field, value)
        if role == Qt.ItemDataRole.EditRole:
            return value
        if role == Qt.ItemDataRole.UserRole:
            return row.identity
        if role == Qt.ItemDataRole.UserRole + 1:
            return row.source
        if role == Qt.ItemDataRole.ToolTipRole:
            return format_analysis_value(field, value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if field.field_type is AnalysisFieldType.NUMERIC:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            if field.field_type is AnalysisFieldType.DATE:
                return Qt.AlignmentFlag.AlignCenter
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole or self.dataset is None:
            return None
        if orientation is Qt.Orientation.Vertical:
            return section + 1
        field = self.dataset.fields[section]
        label = tr(field.label)
        return f"{label}, {field.unit}" if field.unit else label

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        if self.dataset is None or not 0 <= column < len(self.dataset.fields):
            return
        field = self.dataset.fields[column]
        if field.sortable:
            self.sort_requested.emit(
                SortSpec(
                    field_key=field.key,
                    ascending=order == Qt.SortOrder.AscendingOrder,
                )
            )


class AnalysisDataTable(QWidget):
    """First-class Data view with columns, empty state, and source navigation."""

    sort_requested = Signal(object)
    source_requested = Signal(object)
    column_visibility_changed = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._restoring_sort = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        controls = QHBoxLayout()
        self.row_semantics = QLabel()
        self.row_semantics.setObjectName("AnalysisRowSemantics")
        self.row_semantics.setWordWrap(True)
        controls.addWidget(self.row_semantics, 1)
        self.display_count_label = QLabel()
        self.display_count_label.setObjectName("AnalysisDisplayedCount")
        controls.addWidget(self.display_count_label)
        self.open_source_button = QPushButton(tr("Open source"))
        self.open_source_button.setEnabled(False)
        self.open_source_button.setToolTip(
            tr("Open the source record for the selected observation")
        )
        set_button_role(self.open_source_button, "secondary")
        self.open_source_button.clicked.connect(self._open_selected)
        controls.addWidget(self.open_source_button)
        self.columns_button = QPushButton(tr("Columns"))
        self.columns_menu = QMenu(self.columns_button)
        self.columns_button.setMenu(self.columns_menu)
        set_button_role(self.columns_button, "secondary")
        controls.addWidget(self.columns_button)
        layout.addLayout(controls)

        self.stack = QStackedWidget()
        self.table = QTableView()
        self.table.setObjectName("AnalysisDataTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setDefaultSectionSize(135)
        header.setMinimumSectionSize(72)
        self.table.doubleClicked.connect(self._source_double_clicked)
        self.model = AnalysisTableModel(self.table)
        self.model.sort_requested.connect(self._model_sort_requested)
        self.table.setModel(self.model)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self._restoring_sort = True
        self.table.setSortingEnabled(True)
        self._restoring_sort = False
        self.state_label = QLabel()
        self.state_label.setObjectName("EmptyState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setWordWrap(True)
        self.state_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.state_label)
        layout.addWidget(self.stack, 1)

    def set_dataset(
        self,
        dataset: AnalysisDataset,
        rows: tuple[AnalysisRow, ...],
        visible_columns: set[str],
        sort_spec: SortSpec | None,
    ) -> None:
        dataset_changed = (
            self.model.dataset is None
            or self.model.dataset.dataset_id != dataset.dataset_id
        )
        self.model.set_dataset(dataset, rows)
        if dataset_changed:
            self._build_columns_menu(dataset, visible_columns)
        self._apply_column_visibility(dataset, visible_columns)
        if dataset_changed:
            self._apply_initial_widths(dataset)
        self.open_source_button.setEnabled(False)
        header = self.table.horizontalHeader()
        self._restoring_sort = True
        try:
            header.setSortIndicatorShown(sort_spec is not None)
            if sort_spec is not None:
                for column, field in enumerate(dataset.fields):
                    if field.key == sort_spec.field_key:
                        self.table.sortByColumn(
                            column,
                            Qt.SortOrder.AscendingOrder
                            if sort_spec.ascending
                            else Qt.SortOrder.DescendingOrder,
                        )
                        break
        finally:
            self._restoring_sort = False

    def set_row_semantics(self, text: str) -> None:
        self.row_semantics.setText(text)

    def set_population_counts(
        self, displayed_rows: int, matching_records: int, truncated: bool
    ) -> None:
        if truncated:
            self.display_count_label.setText(
                tr("Showing first %1 of %2 matching records")
                .replace("%1", f"{displayed_rows:,}")
                .replace("%2", f"{matching_records:,}")
            )
            self.display_count_label.setProperty("truncated", True)
        else:
            self.display_count_label.setText(
                tr("%1 rows displayed").replace("%1", f"{displayed_rows:,}")
            )
            self.display_count_label.setProperty("truncated", False)
        self.display_count_label.style().unpolish(self.display_count_label)
        self.display_count_label.style().polish(self.display_count_label)

    def show_rows(self) -> None:
        self.stack.setCurrentWidget(self.table)

    def show_message(self, message: str) -> None:
        self.state_label.setText(message)
        self.stack.setCurrentWidget(self.state_label)

    def _build_columns_menu(
        self, definition: AnalysisDataset, visible_columns: set[str]
    ) -> None:
        self.columns_menu.clear()
        for field in definition.fields:
            action = QAction(tr(field.label), self.columns_menu)
            action.setCheckable(True)
            action.setChecked(field.always_visible or field.key in visible_columns)
            action.setEnabled(not field.always_visible)
            action.toggled.connect(
                lambda checked, key=field.key: self._column_toggled(key, checked)
            )
            self.columns_menu.addAction(action)
        self.columns_button.setEnabled(bool(definition.fields))

    def _column_toggled(self, field_key: str, checked: bool) -> None:
        if self.model.dataset is None:
            return
        visible = {
            field.key
            for column, field in enumerate(self.model.dataset.fields)
            if not self.table.isColumnHidden(column)
        }
        if checked:
            visible.add(field_key)
        else:
            visible.discard(field_key)
        self._apply_column_visibility(self.model.dataset, visible)
        self.column_visibility_changed.emit(field_key, checked)

    def _apply_column_visibility(
        self, definition: AnalysisDataset, visible_columns: set[str]
    ) -> None:
        for column, field in enumerate(definition.fields):
            self.table.setColumnHidden(
                column,
                not field.always_visible and field.key not in visible_columns,
            )

    def _apply_initial_widths(self, definition: AnalysisDataset) -> None:
        widths = {
            "project": 155,
            "domain": 155,
            "assessment_area": 220,
            "elevation_interval": 140,
            "min_elevation_m": 125,
            "max_elevation_m": 125,
            "assessment_date": 120,
            "dai": 88,
            "fci": 88,
            "result_quadrant": 300,
            "inspector": 160,
            "evaluation_revision_id": 180,
        }
        header = self.table.horizontalHeader()
        for column, field in enumerate(definition.fields):
            header.resizeSection(column, widths.get(field.key, 135))

    def _model_sort_requested(self, sort_spec: SortSpec) -> None:
        if not self._restoring_sort:
            self.sort_requested.emit(sort_spec)

    def _source_double_clicked(self, index: QModelIndex) -> None:
        source = self.model.data(index, Qt.ItemDataRole.UserRole + 1)
        if isinstance(source, SourceReference):
            self.source_requested.emit(source)

    def _selection_changed(self, *_args) -> None:
        self.open_source_button.setEnabled(self._selected_source() is not None)

    def _selected_source(self) -> SourceReference | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        source = self.model.data(rows[0], Qt.ItemDataRole.UserRole + 1)
        return source if isinstance(source, SourceReference) else None

    def _open_selected(self) -> None:
        source = self._selected_source()
        if source is not None:
            self.source_requested.emit(source)
