"""Full-width Analysis workspace shell and functional Data view."""

from __future__ import annotations

from datetime import date
import logging

from PySide6.QtCore import QAbstractTableModel, QDate, QLocale, QModelIndex, Qt, Signal
from PySide6.QtGui import QAction, QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
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
    DatasetUnavailableError,
    FilterChoice,
    FilterCondition,
    FilterOperator,
    FilterSpec,
    SourceReference,
)
from application.services.analysis import AnalysisDatasetService
from ui.assessment_result_presentation import assessment_result_presentation
from ui.widgets.design_system import set_button_role


logger = logging.getLogger(__name__)
MISSING_VALUE = "—"


def _localized_value(field: AnalysisField, value: object) -> str:
    if field.format_hint == "assessment_result":
        return assessment_result_presentation(str(value)).label
    if field.format_hint == "status":
        return {
            "completed": tr("Completed"),
            "draft": tr("Draft"),
        }.get(str(value), str(value).replace("_", " ").title())
    return str(value)


def format_analysis_value(field: AnalysisField, value: object | None) -> str:
    if value is None or value == "":
        return MISSING_VALUE
    if field.field_type is AnalysisFieldType.NUMERIC:
        decimals = 2 if field.decimals is None else field.decimals
        return f"{float(value):.{decimals}f}"
    if field.field_type is AnalysisFieldType.DATE and isinstance(value, date):
        return QLocale.system().toString(
            QDate(value.year, value.month, value.day), QLocale.FormatType.ShortFormat
        )
    return _localized_value(field, value)


class AnalysisTableModel(QAbstractTableModel):
    """Read-only table model driven entirely by Analysis field metadata."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dataset: AnalysisDataset | None = None
        self.rows: list[AnalysisRow] = []
        self.sort_key: str | None = None
        self.sort_order = Qt.SortOrder.AscendingOrder

    def set_dataset(self, dataset: AnalysisDataset, rows: tuple[AnalysisRow, ...]) -> None:
        self.beginResetModel()
        self.dataset = dataset
        self.rows = list(rows)
        self.endResetModel()
        if self.sort_key and any(field.key == self.sort_key for field in dataset.fields):
            column = next(
                index for index, field in enumerate(dataset.fields)
                if field.key == self.sort_key
            )
            self.sort(column, self.sort_order)

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
        if not field.sortable:
            return
        self.layoutAboutToBeChanged.emit()
        populated = [row for row in self.rows if row.values.get(field.key) is not None]
        missing = [row for row in self.rows if row.values.get(field.key) is None]

        def key(row: AnalysisRow):
            value = row.values.get(field.key)
            return value.casefold() if isinstance(value, str) else value

        populated.sort(key=key, reverse=order is Qt.SortOrder.DescendingOrder)
        self.rows = populated + missing
        self.sort_key = field.key
        self.sort_order = order
        self.layoutChanged.emit()


class _FilterControl(QWidget):
    changed = Signal()

    def condition(self) -> FilterCondition | None:
        raise NotImplementedError

    def set_condition(self, condition: FilterCondition | None) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        self.set_condition(None)


class _CategoricalFilter(_FilterControl):
    def __init__(self, field: AnalysisField, choices: tuple[FilterChoice, ...], parent=None):
        super().__init__(parent)
        self.field = field
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(QLabel(tr(field.label)))
        self.combo = QComboBox()
        self.combo.addItem(tr("All"), None)
        for choice in choices:
            self.combo.addItem(_localized_value(field, choice.label), choice.value)
        self.combo.currentIndexChanged.connect(self.changed)
        layout.addWidget(self.combo)

    def condition(self) -> FilterCondition | None:
        value = self.combo.currentData()
        if value is None:
            return None
        return FilterCondition(
            self.field.key, FilterOperator.CATEGORICAL_EQUALS, value
        )

    def set_condition(self, condition: FilterCondition | None) -> None:
        value = condition.value if condition is not None else None
        index = self.combo.findData(value)
        self.combo.setCurrentIndex(max(0, index))


class _NumericRangeFilter(_FilterControl):
    def __init__(self, field: AnalysisField, parent=None):
        super().__init__(parent)
        self.field = field
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        label = tr(field.label)
        layout.addWidget(QLabel(f"{label}, {field.unit}" if field.unit else label))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.minimum = QLineEdit()
        self.maximum = QLineEdit()
        validator = QDoubleValidator(self)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.minimum.setValidator(validator)
        self.maximum.setValidator(validator)
        self.minimum.setPlaceholderText(tr("Min"))
        self.maximum.setPlaceholderText(tr("Max"))
        self.minimum.editingFinished.connect(self.changed)
        self.maximum.editingFinished.connect(self.changed)
        row.addWidget(self.minimum)
        row.addWidget(self.maximum)
        layout.addLayout(row)

    def condition(self) -> FilterCondition | None:
        lower = self._value(self.minimum)
        upper = self._value(self.maximum)
        if lower is None and upper is None:
            return None
        return FilterCondition(
            self.field.key, FilterOperator.NUMERIC_RANGE, (lower, upper)
        )

    @staticmethod
    def _value(editor: QLineEdit) -> float | None:
        text = editor.text().strip().replace(",", ".")
        return float(text) if text else None

    def set_condition(self, condition: FilterCondition | None) -> None:
        lower, upper = condition.value if condition is not None else (None, None)
        self.minimum.setText("" if lower is None else f"{float(lower):g}")
        self.maximum.setText("" if upper is None else f"{float(upper):g}")


class _DateRangeFilter(_FilterControl):
    def __init__(self, field: AnalysisField, parent=None):
        super().__init__(parent)
        self.field = field
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(QLabel(tr(field.label)))
        self.from_enabled = QCheckBox(tr("From"))
        self.to_enabled = QCheckBox(tr("To"))
        self.minimum = self._date_edit()
        self.maximum = self._date_edit()
        for enabled, editor in (
            (self.from_enabled, self.minimum),
            (self.to_enabled, self.maximum),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            row.addWidget(enabled)
            row.addWidget(editor, 1)
            layout.addLayout(row)
            enabled.toggled.connect(editor.setEnabled)
            enabled.toggled.connect(self.changed)
            editor.dateChanged.connect(self._date_changed)

    @staticmethod
    def _date_edit() -> QDateEdit:
        editor = QDateEdit(QDate.currentDate())
        editor.setCalendarPopup(True)
        editor.setDisplayFormat(QLocale.system().dateFormat(QLocale.FormatType.ShortFormat))
        editor.setEnabled(False)
        return editor

    def _date_changed(self):
        if self.sender().isEnabled():
            self.changed.emit()

    def condition(self) -> FilterCondition | None:
        lower = self.minimum.date().toPython() if self.from_enabled.isChecked() else None
        upper = self.maximum.date().toPython() if self.to_enabled.isChecked() else None
        if lower is None and upper is None:
            return None
        return FilterCondition(self.field.key, FilterOperator.DATE_RANGE, (lower, upper))

    def set_condition(self, condition: FilterCondition | None) -> None:
        lower, upper = condition.value if condition is not None else (None, None)
        self.from_enabled.setChecked(lower is not None)
        self.to_enabled.setChecked(upper is not None)
        if lower is not None:
            self.minimum.setDate(QDate(lower.year, lower.month, lower.day))
        if upper is not None:
            self.maximum.setDate(QDate(upper.year, upper.month, upper.day))


class AnalysisPage(QWidget):
    """Persistent Analysis session containing filters, tabs, and the Data table."""

    source_requested = Signal(object)

    def __init__(self, service: AnalysisDatasetService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("AnalysisPage")
        self._definitions = service.datasets()
        self._dataset_id = self._definitions[0].dataset_id
        self._filter_specs: dict[str, FilterSpec] = {}
        self._visible_columns: dict[str, set[str]] = {}
        self._sort_state: dict[str, tuple[str, Qt.SortOrder]] = {}
        self._filter_options: dict[str, dict[str, tuple[FilterChoice, ...]]] = {}
        self._filter_controls: dict[str, _FilterControl] = {}
        self._rebuilding_filters = False
        self._build_ui()
        self._select_dataset(self._dataset_id)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        toolbar = QFrame()
        toolbar.setObjectName("AnalysisToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 7, 12, 7)
        toolbar_layout.setSpacing(8)
        title = QLabel(tr("Analysis"))
        title.setObjectName("AnalysisTitle")
        toolbar_layout.addWidget(title)
        toolbar_layout.addSpacing(12)
        toolbar_layout.addWidget(QLabel(tr("Dataset:")))
        self.dataset_combo = QComboBox()
        self.dataset_combo.setObjectName("AnalysisDatasetSelector")
        self.dataset_combo.setMinimumWidth(310)
        for definition in self._definitions:
            label = tr(definition.label)
            if not definition.available:
                label = f"{label} — {tr('Not available yet')}"
            self.dataset_combo.addItem(label, definition.dataset_id)
        self.dataset_combo.currentIndexChanged.connect(self._dataset_changed)
        toolbar_layout.addWidget(self.dataset_combo)
        toolbar_layout.addStretch()
        self.active_count_label = QLabel()
        self.active_count_label.setObjectName("AnalysisFilterCount")
        toolbar_layout.addWidget(self.active_count_label)
        self.record_count_label = QLabel()
        self.record_count_label.setObjectName("AnalysisRecordCount")
        toolbar_layout.addWidget(self.record_count_label)
        root.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("AnalysisWorkspaceSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_filters_panel())
        splitter.addWidget(self._build_tabs())
        splitter.setSizes([260, 1100])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

    def _build_filters_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("AnalysisFiltersPanel")
        panel.setMinimumWidth(230)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        heading = QLabel(tr("FILTERS"))
        heading.setObjectName("AnalysisSectionTitle")
        layout.addWidget(heading)
        self.filter_scroll = QScrollArea()
        self.filter_scroll.setObjectName("AnalysisFilterScroll")
        self.filter_scroll.setWidgetResizable(True)
        self.filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.filter_host = QWidget()
        self.filter_layout = QVBoxLayout(self.filter_host)
        self.filter_layout.setContentsMargins(0, 0, 4, 0)
        self.filter_layout.setSpacing(9)
        self.filter_scroll.setWidget(self.filter_host)
        layout.addWidget(self.filter_scroll, 1)
        self.reset_button = QPushButton(tr("Reset filters"))
        set_button_role(self.reset_button, "secondary")
        self.reset_button.clicked.connect(self.reset_filters)
        layout.addWidget(self.reset_button)
        return panel

    def _build_tabs(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.setProperty("entityTabs", True)
        self.tabs.setObjectName("AnalysisTabs")
        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(6)
        controls = QHBoxLayout()
        self.row_semantics = QLabel()
        self.row_semantics.setObjectName("AnalysisRowSemantics")
        self.row_semantics.setWordWrap(True)
        controls.addWidget(self.row_semantics, 1)
        self.columns_button = QPushButton(tr("Columns"))
        self.columns_menu = QMenu(self.columns_button)
        self.columns_button.setMenu(self.columns_menu)
        set_button_role(self.columns_button, "secondary")
        controls.addWidget(self.columns_button)
        data_layout.addLayout(controls)

        self.data_stack = QStackedWidget()
        self.table = QTableView()
        self.table.setObjectName("AnalysisDataTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(135)
        self.table.horizontalHeader().setMinimumSectionSize(72)
        self.table.doubleClicked.connect(self._source_double_clicked)
        self.model = AnalysisTableModel(self.table)
        self.table.setModel(self.model)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._sort_changed)
        self.state_label = QLabel()
        self.state_label.setObjectName("EmptyState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setWordWrap(True)
        self.state_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.data_stack.addWidget(self.table)
        self.data_stack.addWidget(self.state_label)
        data_layout.addWidget(self.data_stack, 1)
        self.tabs.addTab(data_page, tr("Data"))
        for label in ("Summary", "Distribution", "Compare", "Relationships"):
            placeholder = QLabel(tr("Planned for a later Analysis stage."))
            placeholder.setObjectName("EmptyState")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabs.addTab(placeholder, tr(label))
        return self.tabs

    @property
    def selected_dataset_id(self) -> str:
        return self._dataset_id

    def current_filter_spec(self) -> FilterSpec:
        conditions = tuple(
            condition
            for control in self._filter_controls.values()
            if (condition := control.condition()) is not None
        )
        return FilterSpec(self._dataset_id, conditions)

    def refresh(self) -> None:
        definition = self.service.dataset(self._dataset_id)
        spec = self.current_filter_spec()
        self._filter_specs[self._dataset_id] = spec
        self._set_counts(0, 0, spec.active_count)
        if not definition.available:
            self._show_state(tr("This Analysis dataset is not available yet."))
            return
        self._show_state(tr("Loading Analysis data…"))
        try:
            result = self.service.load(spec)
        except DatasetUnavailableError:
            self._show_state(tr("This Analysis dataset is not available yet."))
            return
        except Exception:
            logger.exception("Could not load Analysis dataset %s", self._dataset_id)
            self._show_state(tr("Could not load Analysis data. Check the database connection and try again."))
            return
        self.model.set_dataset(result.dataset, result.rows)
        self._restore_sort(result.dataset)
        self._apply_column_visibility(result.dataset)
        self._set_counts(result.filtered_count, result.total_count, spec.active_count)
        if result.truncated:
            self.record_count_label.setToolTip(
                tr("The table is limited to the first %1 matching records.").replace(
                    "%1", str(len(result.rows))
                )
            )
        else:
            self.record_count_label.setToolTip("")
        if result.rows:
            self.data_stack.setCurrentWidget(self.table)
        elif result.total_count == 0:
            self._show_state(tr("No completed Assessment results are available."))
        else:
            self._show_state(tr("No completed Assessment results match the current filters."))

    def reload(self) -> None:
        """Refresh filter choices and rows while preserving the current session state."""
        definition = self.service.dataset(self._dataset_id)
        self._filter_specs[self._dataset_id] = self.current_filter_spec()
        self._filter_options.pop(self._dataset_id, None)
        self._build_filter_controls(definition)
        self.refresh()

    def reset_filters(self) -> None:
        self._rebuilding_filters = True
        try:
            for control in self._filter_controls.values():
                control.reset()
        finally:
            self._rebuilding_filters = False
        self._filter_specs[self._dataset_id] = FilterSpec(self._dataset_id)
        self.refresh()

    def _dataset_changed(self) -> None:
        dataset_id = self.dataset_combo.currentData()
        if dataset_id:
            self._select_dataset(str(dataset_id))

    def _select_dataset(self, dataset_id: str) -> None:
        if self._filter_controls:
            self._filter_specs[self._dataset_id] = self.current_filter_spec()
        self._dataset_id = dataset_id
        definition = self.service.dataset(dataset_id)
        self.row_semantics.setText(tr(definition.row_semantics))
        self._build_filter_controls(definition)
        self._build_columns_menu(definition)
        self.refresh()

    def _build_filter_controls(self, definition: AnalysisDataset) -> None:
        self._rebuilding_filters = True
        try:
            while self.filter_layout.count():
                item = self.filter_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._filter_controls.clear()
            options: dict[str, tuple[FilterChoice, ...]] = {}
            if definition.available:
                if definition.dataset_id not in self._filter_options:
                    try:
                        self._filter_options[definition.dataset_id] = self.service.filter_options(
                            definition.dataset_id
                        )
                    except Exception:
                        logger.exception("Could not load Analysis filter options")
                        self._filter_options[definition.dataset_id] = {}
                options = self._filter_options[definition.dataset_id]
            saved = {
                item.field_key: item
                for item in self._filter_specs.get(
                    definition.dataset_id, FilterSpec(definition.dataset_id)
                ).conditions
            }
            for field in definition.fields:
                if not field.filterable:
                    continue
                if field.field_type is AnalysisFieldType.CATEGORICAL:
                    control = _CategoricalFilter(field, options.get(field.key, ()))
                elif field.field_type is AnalysisFieldType.NUMERIC:
                    control = _NumericRangeFilter(field)
                elif field.field_type is AnalysisFieldType.DATE:
                    control = _DateRangeFilter(field)
                else:
                    continue
                control.changed.connect(self._filters_changed)
                control.set_condition(saved.get(field.key))
                self._filter_controls[field.key] = control
                self.filter_layout.addWidget(control)
            self.filter_layout.addStretch()
        finally:
            self._rebuilding_filters = False
        self.reset_button.setEnabled(definition.available)

    def _filters_changed(self) -> None:
        if not self._rebuilding_filters:
            self.refresh()

    def _build_columns_menu(self, definition: AnalysisDataset) -> None:
        self.columns_menu.clear()
        visible = self._visible_columns.setdefault(
            definition.dataset_id,
            {field.key for field in definition.fields if field.default_visible},
        )
        for field in definition.fields:
            action = QAction(tr(field.label), self.columns_menu)
            action.setCheckable(True)
            action.setChecked(field.always_visible or field.key in visible)
            action.setEnabled(not field.always_visible)
            action.toggled.connect(
                lambda checked, key=field.key: self._column_toggled(key, checked)
            )
            self.columns_menu.addAction(action)
        self.columns_button.setEnabled(bool(definition.fields))

    def _column_toggled(self, field_key: str, checked: bool) -> None:
        visible = self._visible_columns.setdefault(self._dataset_id, set())
        if checked:
            visible.add(field_key)
        else:
            visible.discard(field_key)
        self._apply_column_visibility(self.service.dataset(self._dataset_id))

    def _apply_column_visibility(self, definition: AnalysisDataset) -> None:
        visible = self._visible_columns.setdefault(
            definition.dataset_id,
            {field.key for field in definition.fields if field.default_visible},
        )
        for column, field in enumerate(definition.fields):
            self.table.setColumnHidden(
                column, not field.always_visible and field.key not in visible
            )

    def _sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        definition = self.service.dataset(self._dataset_id)
        if 0 <= column < len(definition.fields):
            self._sort_state[self._dataset_id] = (definition.fields[column].key, order)

    def _restore_sort(self, definition: AnalysisDataset) -> None:
        state = self._sort_state.get(definition.dataset_id)
        if state is None:
            return
        key, order = state
        for column, field in enumerate(definition.fields):
            if field.key == key:
                self.table.sortByColumn(column, order)
                break

    def _source_double_clicked(self, index: QModelIndex) -> None:
        source = self.model.data(index, Qt.ItemDataRole.UserRole + 1)
        if isinstance(source, SourceReference):
            self.source_requested.emit(source)

    def _set_counts(self, filtered: int, total: int, active: int) -> None:
        self.record_count_label.setText(
            tr("%1 / %2 records").replace("%1", str(filtered)).replace("%2", str(total))
        )
        self.active_count_label.setText(
            tr("No active filters")
            if active == 0
            else tr("%1 active filters").replace("%1", str(active))
        )

    def _show_state(self, message: str) -> None:
        self.state_label.setText(message)
        self.data_stack.setCurrentWidget(self.state_label)
