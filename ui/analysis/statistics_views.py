"""Qt presentation for complete-population Analysis statistics."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.localization import tr
from application.analysis.models import (
    AnalysisDataset,
    AnalysisField,
    AnalysisPopulationProjection,
)
from application.analysis.statistics_models import (
    GroupedComparisonResult,
    NumericSummary,
)
from application.services.analysis_statistics import AnalysisStatisticsService
from ui.analysis.charts import AnalysisChartPanel
from ui.assessment_result_presentation import assessment_result_presentation
from ui.widgets.design_system import set_button_role


MISSING_VALUE = "—"


def _format_number(value: float | None, decimals: int = 3) -> str:
    return MISSING_VALUE if value is None else f"{value:.{decimals}f}"


def _table() -> QTableWidget:
    table = QTableWidget()
    table.setObjectName("AnalysisStatisticsTable")
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class AnalysisSummaryView(QWidget):
    """Parameter-oriented descriptive and percentile tables."""

    def __init__(self, statistics: AnalysisStatisticsService, parent=None):
        super().__init__(parent)
        self.statistics = statistics
        self.dataset: AnalysisDataset | None = None
        self.population: AnalysisPopulationProjection | None = None
        self._selected_by_dataset: dict[str, set[str]] = {}
        self.last_result = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("Parameters:")))
        self.parameters_button = QPushButton()
        self.parameters_button.setObjectName("AnalysisSummaryParameters")
        self.parameters_menu = QMenu(self.parameters_button)
        self.parameters_button.setMenu(self.parameters_menu)
        set_button_role(self.parameters_button, "secondary")
        controls.addWidget(self.parameters_button)
        controls.addWidget(QLabel(tr("Table:")))
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("AnalysisSummaryMode")
        self.mode_combo.addItem(tr("Descriptive"), "descriptive")
        self.mode_combo.addItem(tr("Percentiles"), "percentiles")
        self.mode_combo.currentIndexChanged.connect(self._render)
        controls.addWidget(self.mode_combo)
        controls.addStretch()
        help_button = QPushButton(tr("Conventions"))
        help_button.setObjectName("AnalysisConventionsHelp")
        set_button_role(help_button, "secondary")
        help_button.setToolTip(
            tr(
                "Sample standard deviation and variance use ddof=1. "
                "Quantiles use linear interpolation. Invalid, non-finite and "
                "missing values are excluded and counted as missing."
            )
        )
        controls.addWidget(help_button)
        layout.addLayout(controls)
        self.message_label = QLabel()
        self.message_label.setObjectName("AnalysisInlineState")
        self.message_label.setWordWrap(True)
        self.message_label.hide()
        layout.addWidget(self.message_label)
        self.table = _table()
        layout.addWidget(self.table, 1)

    @property
    def selected_parameters(self) -> tuple[str, ...]:
        if self.dataset is None:
            return ()
        selected = self._selected_by_dataset.get(self.dataset.dataset_id, set())
        return tuple(
            field.key for field in self.dataset.fields if field.key in selected
        )

    def set_context(
        self,
        dataset: AnalysisDataset,
        population: AnalysisPopulationProjection | None,
        message: str | None = None,
    ) -> None:
        changed = self.dataset is None or self.dataset.dataset_id != dataset.dataset_id
        self.dataset = dataset
        self.population = population
        if population is None:
            self.last_result = None
        if changed:
            numeric = [field.key for field in dataset.fields if field.is_numeric]
            defaults = {key for key in ("dai", "fci") if key in numeric}
            self._selected_by_dataset.setdefault(
                dataset.dataset_id, defaults or set(numeric[:2])
            )
            self._rebuild_parameter_menu()
        self.message_label.setText(message or "")
        self.message_label.setVisible(bool(message))
        self._render()

    def _rebuild_parameter_menu(self) -> None:
        self.parameters_menu.clear()
        if self.dataset is None:
            return
        selected = self._selected_by_dataset[self.dataset.dataset_id]
        for field in self.dataset.fields:
            if not field.is_numeric:
                continue
            action = QAction(tr(field.label), self.parameters_menu)
            action.setCheckable(True)
            action.setChecked(field.key in selected)
            action.toggled.connect(
                lambda checked, key=field.key: self._parameter_toggled(key, checked)
            )
            self.parameters_menu.addAction(action)
        self._update_parameter_button()

    def _parameter_toggled(self, field_key: str, checked: bool) -> None:
        if self.dataset is None:
            return
        selected = self._selected_by_dataset[self.dataset.dataset_id]
        selected.add(field_key) if checked else selected.discard(field_key)
        self._update_parameter_button()
        self._render()

    def _update_parameter_button(self) -> None:
        count = len(self.selected_parameters)
        self.parameters_button.setText(
            tr("No parameters")
            if not count
            else tr("%1 selected").replace("%1", str(count))
        )

    def _render(self) -> None:
        if self.dataset is None or self.population is None:
            self.table.setRowCount(0)
            return
        parameters = self.selected_parameters
        if not parameters:
            self.last_result = self.statistics.summary(self.population, ())
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.message_label.setText(tr("Select one or more numeric parameters."))
            self.message_label.show()
            return
        if self.message_label.text() == tr("Select one or more numeric parameters."):
            self.message_label.hide()
        self.last_result = self.statistics.summary(self.population, parameters)
        if self.mode_combo.currentData() == "percentiles":
            headers = (
                "Parameter", "Unit", "n", "P5", "P10", "Q1 / P25", "P50",
                "Q3 / P75", "P90", "P95",
            )
            rows = [self._percentile_row(item) for item in self.last_result.parameters]
        else:
            headers = (
                "Parameter", "Unit", "n", "Missing", "Mean", "Std. dev.",
                "Variance", "Min", "Median", "Max", "IQR",
            )
            rows = [self._descriptive_row(item) for item in self.last_result.parameters]
        self._populate(headers, rows)

    def _descriptive_row(self, item: NumericSummary) -> tuple[str, ...]:
        field = self.dataset.field(item.field_key)
        decimals = 3 if field.decimals is None else field.decimals
        variance = _format_number(item.sample_variance, max(decimals, 3))
        if item.sample_variance is not None and item.unit:
            variance = f"{variance} {item.unit}²"
        return (
            tr(field.label), field.unit or MISSING_VALUE, str(item.valid_n),
            str(item.missing_count), _format_number(item.mean, decimals),
            _format_number(item.sample_std_dev, decimals), variance,
            _format_number(item.minimum, decimals), _format_number(item.median, decimals),
            _format_number(item.maximum, decimals), _format_number(item.iqr, decimals),
        )

    def _percentile_row(self, item: NumericSummary) -> tuple[str, ...]:
        field = self.dataset.field(item.field_key)
        decimals = 3 if field.decimals is None else field.decimals
        return (
            tr(field.label), field.unit or MISSING_VALUE, str(item.valid_n),
            _format_number(item.p5, decimals), _format_number(item.p10, decimals),
            _format_number(item.q1, decimals), _format_number(item.median, decimals),
            _format_number(item.q3, decimals), _format_number(item.p90, decimals),
            _format_number(item.p95, decimals),
        )

    def _populate(self, headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels([tr(header) for header in headers])
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                item = QTableWidgetItem(value)
                if column >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, column, item)


@dataclass
class _DistributionState:
    parameter: str | None = None
    mode: str = "histogram"
    scale: str = "count"
    show_mean: bool = False
    show_median: bool = False


class AnalysisDistributionView(QWidget):
    """One selected parameter and one dominant distribution plot."""

    def __init__(self, statistics: AnalysisStatisticsService, parent=None):
        super().__init__(parent)
        self.statistics = statistics
        self.dataset: AnalysisDataset | None = None
        self.population: AnalysisPopulationProjection | None = None
        self._states: dict[str, _DistributionState] = {}
        self.last_result = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("Parameter:")))
        self.parameter_combo = QComboBox()
        self.parameter_combo.setObjectName("AnalysisDistributionParameter")
        self.parameter_combo.currentIndexChanged.connect(self._control_changed)
        controls.addWidget(self.parameter_combo)
        controls.addWidget(QLabel(tr("Plot:")))
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("AnalysisDistributionMode")
        self.mode_combo.addItem(tr("Histogram"), "histogram")
        self.mode_combo.addItem(tr("ECDF"), "ecdf")
        self.mode_combo.addItem(tr("Box plot"), "box")
        self.mode_combo.currentIndexChanged.connect(self._control_changed)
        controls.addWidget(self.mode_combo)
        self.scale_combo = QComboBox()
        self.scale_combo.setObjectName("AnalysisHistogramScale")
        self.scale_combo.addItem(tr("Count"), "count")
        self.scale_combo.addItem(tr("Frequency %"), "frequency")
        self.scale_combo.currentIndexChanged.connect(self._control_changed)
        controls.addWidget(self.scale_combo)
        self.mean_check = QCheckBox(tr("Mean"))
        self.mean_check.setObjectName("AnalysisMeanReference")
        self.mean_check.toggled.connect(self._control_changed)
        controls.addWidget(self.mean_check)
        self.median_check = QCheckBox(tr("Median"))
        self.median_check.setObjectName("AnalysisMedianReference")
        self.median_check.toggled.connect(self._control_changed)
        controls.addWidget(self.median_check)
        controls.addStretch()
        self.count_label = QLabel()
        self.count_label.setObjectName("AnalysisValidCount")
        controls.addWidget(self.count_label)
        layout.addLayout(controls)
        self.chart_panel = AnalysisChartPanel()
        layout.addWidget(self.chart_panel, 1)

    def set_context(
        self,
        dataset: AnalysisDataset,
        population: AnalysisPopulationProjection | None,
        message: str | None = None,
    ) -> None:
        changed = self.dataset is None or self.dataset.dataset_id != dataset.dataset_id
        self.dataset = dataset
        self.population = population
        if population is None:
            self.last_result = None
        if changed:
            self._restore_dataset_state()
        if message:
            self.count_label.clear()
            self.chart_panel.show_message(message)
        else:
            self._render()

    def _restore_dataset_state(self) -> None:
        self.parameter_combo.blockSignals(True)
        self.mode_combo.blockSignals(True)
        self.scale_combo.blockSignals(True)
        self.mean_check.blockSignals(True)
        self.median_check.blockSignals(True)
        self.parameter_combo.clear()
        numeric = [field for field in self.dataset.fields if field.is_numeric]
        for field in numeric:
            self.parameter_combo.addItem(tr(field.label), field.key)
        state = self._states.setdefault(
            self.dataset.dataset_id,
            _DistributionState("dai" if any(f.key == "dai" for f in numeric) else (numeric[0].key if numeric else None)),
        )
        self.parameter_combo.setCurrentIndex(max(0, self.parameter_combo.findData(state.parameter)))
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(state.mode)))
        self.scale_combo.setCurrentIndex(max(0, self.scale_combo.findData(state.scale)))
        self.mean_check.setChecked(state.show_mean)
        self.median_check.setChecked(state.show_median)
        for widget in (self.parameter_combo, self.mode_combo, self.scale_combo, self.mean_check, self.median_check):
            widget.blockSignals(False)

    def _control_changed(self, *args) -> None:
        del args
        if self.dataset is None:
            return
        state = self._states.setdefault(self.dataset.dataset_id, _DistributionState())
        state.parameter = self.parameter_combo.currentData()
        state.mode = self.mode_combo.currentData() or "histogram"
        state.scale = self.scale_combo.currentData() or "count"
        state.show_mean = self.mean_check.isChecked()
        state.show_median = self.median_check.isChecked()
        self._render()

    def _render(self) -> None:
        if self.dataset is None or self.population is None:
            return
        field_key = self.parameter_combo.currentData()
        if not field_key:
            self.chart_panel.show_message(tr("No numeric parameters are available."))
            return
        field = self.dataset.field(field_key)
        mode = self.mode_combo.currentData() or "histogram"
        histogram_controls = mode == "histogram"
        for widget in (self.scale_combo, self.mean_check, self.median_check):
            widget.setVisible(histogram_controls)
        if mode == "ecdf":
            result = self.statistics.ecdf(self.population, field_key)
            self.chart_panel.show_ecdf(result, title=tr(field.label), unit=field.unit)
        elif mode == "box":
            result = self.statistics.box(self.population, field_key)
            self.chart_panel.show_box(result, title=tr(field.label), unit=field.unit)
        else:
            result = self.statistics.histogram(self.population, field_key)
            self.chart_panel.show_histogram(
                result,
                title=tr(field.label),
                unit=field.unit,
                frequency=self.scale_combo.currentData() == "frequency",
                show_mean=self.mean_check.isChecked(),
                show_median=self.median_check.isChecked(),
            )
        self.last_result = result
        self.count_label.setText(
            tr("Valid n: %1   Missing: %2")
            .replace("%1", f"{result.valid_n:,}")
            .replace("%2", f"{result.missing_count:,}")
        )


class AnalysisCompareView(QWidget):
    """Grouped Tukey boxes coupled to a same-order statistics table."""

    def __init__(self, statistics: AnalysisStatisticsService, parent=None):
        super().__init__(parent)
        self.statistics = statistics
        self.dataset: AnalysisDataset | None = None
        self.population: AnalysisPopulationProjection | None = None
        self._state_by_dataset: dict[str, tuple[str | None, str | None]] = {}
        self.last_result: GroupedComparisonResult | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("Parameter:")))
        self.parameter_combo = QComboBox()
        self.parameter_combo.setObjectName("AnalysisCompareParameter")
        self.parameter_combo.currentIndexChanged.connect(self._control_changed)
        controls.addWidget(self.parameter_combo)
        controls.addWidget(QLabel(tr("Group by:")))
        self.group_combo = QComboBox()
        self.group_combo.setObjectName("AnalysisCompareGroup")
        self.group_combo.currentIndexChanged.connect(self._control_changed)
        controls.addWidget(self.group_combo)
        controls.addStretch()
        self.count_label = QLabel()
        self.count_label.setObjectName("AnalysisValidCount")
        controls.addWidget(self.count_label)
        layout.addLayout(controls)
        self.message_label = QLabel()
        self.message_label.setObjectName("AnalysisInlineState")
        self.message_label.setWordWrap(True)
        self.message_label.hide()
        layout.addWidget(self.message_label)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.chart_panel = AnalysisChartPanel()
        self.table = _table()
        splitter.addWidget(self.chart_panel)
        splitter.addWidget(self.table)
        splitter.setSizes([720, 480])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

    def set_context(
        self,
        dataset: AnalysisDataset,
        population: AnalysisPopulationProjection | None,
        message: str | None = None,
    ) -> None:
        changed = self.dataset is None or self.dataset.dataset_id != dataset.dataset_id
        self.dataset = dataset
        self.population = population
        if population is None:
            self.last_result = None
        if changed:
            self._restore_dataset_state()
        self.message_label.setText(message or "")
        self.message_label.setVisible(bool(message))
        if message:
            self.count_label.clear()
            self.chart_panel.show_message(message)
            self.table.setRowCount(0)
        else:
            self._render()

    def _restore_dataset_state(self) -> None:
        self.parameter_combo.blockSignals(True)
        self.group_combo.blockSignals(True)
        self.parameter_combo.clear()
        self.group_combo.clear()
        numeric = [field for field in self.dataset.fields if field.is_numeric]
        groups = [field for field in self.dataset.fields if field.groupable]
        for field in numeric:
            self.parameter_combo.addItem(tr(field.label), field.key)
        for field in groups:
            self.group_combo.addItem(tr(field.label), field.key)
        saved = self._state_by_dataset.get(self.dataset.dataset_id)
        parameter = saved[0] if saved else ("fci" if any(f.key == "fci" for f in numeric) else (numeric[0].key if numeric else None))
        group = saved[1] if saved else ("domain" if any(f.key == "domain" for f in groups) else (groups[0].key if groups else None))
        self.parameter_combo.setCurrentIndex(max(0, self.parameter_combo.findData(parameter)))
        self.group_combo.setCurrentIndex(max(0, self.group_combo.findData(group)))
        self.parameter_combo.blockSignals(False)
        self.group_combo.blockSignals(False)

    def _control_changed(self, *args) -> None:
        del args
        if self.dataset is not None:
            self._state_by_dataset[self.dataset.dataset_id] = (
                self.parameter_combo.currentData(), self.group_combo.currentData()
            )
        self._render()

    def _render(self) -> None:
        if self.dataset is None or self.population is None:
            return
        field_key = self.parameter_combo.currentData()
        group_key = self.group_combo.currentData()
        if not field_key or not group_key:
            self.chart_panel.show_message(tr("No comparison fields are available."))
            return
        result = self.statistics.grouped(self.population, field_key, group_key)
        self.last_result = result
        if result.excessive_groups:
            self.count_label.clear()
            message = (
                tr("%1 groups exceed the readable limit of %2. Narrow the filters or choose another group field.")
                .replace("%1", str(result.total_group_count))
                .replace("%2", str(self.statistics.DEFAULT_MAX_GROUPS))
            )
            self.message_label.setText(message)
            self.message_label.show()
            self.chart_panel.show_message(message)
            self.table.setRowCount(0)
            return
        self.message_label.hide()
        labels = tuple(self._group_label(self.dataset.field(group_key), group.group_value) for group in result.groups)
        field = self.dataset.field(field_key)
        self.chart_panel.show_grouped_boxes(
            result, title=tr(field.label), unit=field.unit, labels=labels
        )
        self._populate_table(result, labels, field)
        valid = sum(group.valid_n for group in result.groups)
        missing = sum(group.missing_count for group in result.groups)
        self.count_label.setText(
            tr("Valid n: %1   Missing: %2   Groups: %3")
            .replace("%1", f"{valid:,}")
            .replace("%2", f"{missing:,}")
            .replace("%3", str(len(result.groups)))
        )

    def _populate_table(
        self,
        result: GroupedComparisonResult,
        labels: tuple[str, ...],
        field: AnalysisField,
    ) -> None:
        headers = ("Group", "n", "Missing", "Mean", "Median", "P90", "Std. dev.")
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels([tr(header) for header in headers])
        self.table.setRowCount(len(result.groups))
        decimals = 3 if field.decimals is None else field.decimals
        for row, (label, group) in enumerate(zip(labels, result.groups, strict=True)):
            values = (
                label, str(group.valid_n), str(group.missing_count),
                _format_number(group.mean, decimals), _format_number(group.median, decimals),
                _format_number(group.p90, decimals), _format_number(group.sample_std_dev, decimals),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(label if column == 0 else value)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    @staticmethod
    def _group_label(field: AnalysisField, value: object | None) -> str:
        if value is None or value == "":
            return tr("Missing category")
        if field.format_hint == "assessment_result":
            return assessment_result_presentation(str(value)).label
        return str(value)
