"""Full-width Analysis workspace composition and session state."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.localization import tr
from application.analysis.models import (
    AnalysisDataset,
    DatasetUnavailableError,
    FilterChoice,
    FilterSpec,
    SortSpec,
)
from application.services.analysis import AnalysisDatasetService
from ui.analysis.data_table import AnalysisDataTable
from ui.analysis.filters import AnalysisFilterPanel


logger = logging.getLogger(__name__)


class AnalysisPage(QWidget):
    """Persistent Analysis session coordinating dataset components and state."""

    source_requested = Signal(object)

    def __init__(self, service: AnalysisDatasetService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("AnalysisPage")
        self._definitions = service.datasets()
        self._dataset_id = self._definitions[0].dataset_id
        self._filter_specs: dict[str, FilterSpec] = {}
        self._optional_filter_fields: dict[str, tuple[str, ...]] = {}
        self._visible_columns: dict[str, set[str]] = {}
        self._sort_state: dict[str, SortSpec] = {}
        self._filter_options: dict[
            str, dict[str, tuple[FilterChoice, ...]]
        ] = {}
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
        self.filter_panel = AnalysisFilterPanel()
        self.filter_panel.changed.connect(self._filters_changed)
        splitter.addWidget(self.filter_panel)
        splitter.addWidget(self._build_tabs())
        splitter.setSizes([260, 1100])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

    def _build_tabs(self) -> QTabWidget:
        self.tabs = QTabWidget()
        self.tabs.setProperty("entityTabs", True)
        self.tabs.setObjectName("AnalysisTabs")
        self.data_view = AnalysisDataTable()
        self.data_view.source_requested.connect(self.source_requested)
        self.data_view.sort_requested.connect(self._sort_requested)
        self.data_view.column_visibility_changed.connect(self._column_toggled)
        self.tabs.addTab(self.data_view, tr("Data"))
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
        return FilterSpec(self._dataset_id, self.filter_panel.conditions())

    def refresh(self) -> None:
        definition = self.service.dataset(self._dataset_id)
        spec = self.current_filter_spec()
        self._remember_filter_state(spec)
        self._set_counts(0, 0, spec.active_count)
        if not definition.available:
            self._show_state(tr("This Analysis dataset is not available yet."))
            return
        self._show_state(tr("Loading Analysis data…"))
        sort_spec = self._sort_state.get(self._dataset_id)
        try:
            result = self.service.load(spec, sort_spec=sort_spec)
        except DatasetUnavailableError:
            self._show_state(tr("This Analysis dataset is not available yet."))
            return
        except Exception:
            logger.exception("Could not load Analysis dataset %s", self._dataset_id)
            self._show_state(
                tr(
                    "Could not load Analysis data. Check the database connection and try again."
                )
            )
            return
        visible = self._visible_for(result.dataset)
        self.data_view.set_dataset(result.dataset, result.rows, visible, sort_spec)
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
            self.data_view.show_rows()
        elif result.total_count == 0:
            self._show_state(tr("No completed Assessment results are available."))
        else:
            self._show_state(
                tr("No completed Assessment results match the current filters.")
            )

    def reload(self) -> None:
        """Refresh choices and rows while preserving the Analysis session."""
        self._remember_filter_state(self.current_filter_spec())
        self._filter_options.pop(self._dataset_id, None)
        self._configure_filter_panel(self.service.dataset(self._dataset_id))
        self.refresh()

    def reset_filters(self) -> None:
        self.filter_panel.reset()

    def _dataset_changed(self) -> None:
        dataset_id = self.dataset_combo.currentData()
        if dataset_id:
            self._select_dataset(str(dataset_id))

    def _select_dataset(self, dataset_id: str) -> None:
        if self.filter_panel.definition is not None:
            self._remember_filter_state(self.current_filter_spec())
        self._dataset_id = dataset_id
        definition = self.service.dataset(dataset_id)
        self.data_view.set_row_semantics(tr(definition.row_semantics))
        self._configure_filter_panel(definition)
        self.data_view.set_dataset(
            definition,
            (),
            self._visible_for(definition),
            self._sort_state.get(dataset_id),
        )
        self.refresh()

    def _configure_filter_panel(self, definition: AnalysisDataset) -> None:
        options: dict[str, tuple[FilterChoice, ...]] = {}
        if definition.available:
            if definition.dataset_id not in self._filter_options:
                try:
                    self._filter_options[definition.dataset_id] = (
                        self.service.filter_options(definition.dataset_id)
                    )
                except Exception:
                    logger.exception("Could not load Analysis filter options")
                    self._filter_options[definition.dataset_id] = {}
            options = self._filter_options[definition.dataset_id]
        self.filter_panel.set_dataset(
            definition,
            options,
            self._filter_specs.get(
                definition.dataset_id, FilterSpec(definition.dataset_id)
            ),
            self._optional_filter_fields.get(definition.dataset_id, ()),
        )

    def _remember_filter_state(self, spec: FilterSpec) -> None:
        self._filter_specs[self._dataset_id] = spec
        self._optional_filter_fields[self._dataset_id] = (
            self.filter_panel.optional_keys
        )

    def _filters_changed(self) -> None:
        self.refresh()

    def _visible_for(self, definition: AnalysisDataset) -> set[str]:
        return self._visible_columns.setdefault(
            definition.dataset_id,
            {field.key for field in definition.fields if field.default_visible},
        )

    def _column_toggled(self, field_key: str, checked: bool) -> None:
        visible = self._visible_columns.setdefault(self._dataset_id, set())
        if checked:
            visible.add(field_key)
        else:
            visible.discard(field_key)

    def _sort_requested(self, sort_spec: SortSpec) -> None:
        self._sort_state[self._dataset_id] = sort_spec
        self.refresh()

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
        self.data_view.show_message(message)
