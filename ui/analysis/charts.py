"""Theme-aware QtCharts rendering for Analysis distribution results."""

from __future__ import annotations

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QBoxPlotSeries,
    QBoxSet,
    QChart,
    QChartView,
    QLineSeries,
    QScatterSeries,
    QValueAxis,
)
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from app.localization import tr
from application.analysis.statistics_models import (
    EcdfResult,
    GroupedComparisonResult,
    HistogramResult,
    TukeyBoxResult,
)
from ui.application_theme import DarkColor
from ui.theme import Color


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


class AnalysisChartPanel(QWidget):
    """A single dominant chart with keyboard-visible inspection text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.chart_view = QChartView()
        self.chart_view.setObjectName("AnalysisChartView")
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.chart_view.setMinimumHeight(320)
        self.inspection_label = QLabel()
        self.inspection_label.setObjectName("AnalysisPlotInspection")
        self.inspection_label.setWordWrap(True)
        layout.addWidget(self.chart_view, 1)
        layout.addWidget(self.inspection_label)
        self.chart: QChart | None = None
        self._set_chart(tr("No plot data"))

    def show_message(self, message: str) -> None:
        self._set_chart(message)
        self.inspection_label.setText(message)

    def show_histogram(
        self,
        result: HistogramResult,
        *,
        title: str,
        unit: str | None,
        frequency: bool,
        show_mean: bool,
        show_median: bool,
    ) -> None:
        chart = self._set_chart(title)
        values = result.frequencies_percent if frequency else result.counts
        if not values:
            self.inspection_label.setText(tr("No valid numeric values."))
            return
        categories = [
            f"{result.bin_edges[index]:.4g}–{result.bin_edges[index + 1]:.4g}"
            for index in range(len(values))
        ]
        bar_set = QBarSet(tr("Frequency %") if frequency else tr("Count"))
        bar_set.append([float(value) for value in values])
        bar_set.setColor(self._colors()["accent"])
        bar_series = QBarSeries()
        bar_series.append(bar_set)
        bar_series.setBarWidth(0.92)
        chart.addSeries(bar_series)
        x_axis = QBarCategoryAxis()
        x_axis.append(categories)
        x_axis.setTitleText(f"{title}{f' ({unit})' if unit else ''}")
        y_axis = QValueAxis()
        y_axis.setTitleText(tr("Frequency %") if frequency else tr("Count"))
        maximum = max(float(value) for value in values)
        y_axis.setRange(0.0, maximum * 1.12 if maximum else 1.0)
        chart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)
        bar_series.attachAxis(x_axis)
        bar_series.attachAxis(y_axis)
        bar_set.hovered.connect(
            lambda status, index: self._inspect_histogram(
                status, index, result
            )
        )
        bar_set.clicked.connect(lambda index: self._inspect_histogram(True, index, result))
        if show_mean:
            self._add_reference_line(chart, x_axis, y_axis, result, result.mean, tr("Mean"))
        if show_median:
            self._add_reference_line(
                chart, x_axis, y_axis, result, result.median, tr("Median")
            )
        self._style_axes(chart)
        references = []
        if show_mean:
            references.append(f"{tr('Mean')}: {_number(result.mean)}")
        if show_median:
            references.append(f"{tr('Median')}: {_number(result.median)}")
        self.inspection_label.setText(
            "   ".join(references) if references else tr("Hover a bin to inspect it.")
        )

    def show_ecdf(self, result: EcdfResult, *, title: str, unit: str | None) -> None:
        chart = self._set_chart(title)
        if not result.observed_values:
            self.inspection_label.setText(tr("No valid numeric values."))
            return
        line = QLineSeries()
        line.setName("ECDF")
        line.setPen(QPen(self._colors()["accent"], 2.0))
        previous = 0.0
        for value, proportion in zip(
            result.observed_values, result.cumulative_proportions, strict=True
        ):
            line.append(value, previous)
            line.append(value, proportion)
            previous = proportion
        points = QScatterSeries()
        points.setMarkerSize(7.0)
        points.setColor(self._colors()["accent"])
        for value, proportion in zip(
            result.observed_values, result.cumulative_proportions, strict=True
        ):
            points.append(value, proportion)
        chart.addSeries(line)
        chart.addSeries(points)
        x_axis = QValueAxis()
        x_axis.setTitleText(f"{title}{f' ({unit})' if unit else ''}")
        y_axis = QValueAxis()
        y_axis.setTitleText(tr("Cumulative proportion"))
        y_axis.setRange(0.0, 1.0)
        y_axis.setLabelFormat("%.2f")
        minimum, maximum = result.observed_values[0], result.observed_values[-1]
        padding = (maximum - minimum) * 0.04 or 0.5
        x_axis.setRange(minimum - padding, maximum + padding)
        chart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)
        for series in (line, points):
            series.attachAxis(x_axis)
            series.attachAxis(y_axis)
        points.hovered.connect(
            lambda point, status: self._inspect_ecdf(point, status, result)
        )
        points.clicked.connect(lambda point: self._inspect_ecdf(point, True, result))
        self._style_axes(chart)
        self.inspection_label.setText(tr("Hover an observation to inspect value and cumulative proportion."))

    def show_box(
        self,
        result: TukeyBoxResult,
        *,
        title: str,
        unit: str | None,
    ) -> None:
        chart = self._set_chart(title)
        if result.valid_n == 0:
            self.inspection_label.setText(tr("No valid numeric values."))
            return
        box_set = self._box_set(title, result)
        series = QBoxPlotSeries()
        series.append(box_set)
        chart.addSeries(series)
        categories = QBarCategoryAxis()
        categories.append([title])
        value_axis = self._box_value_axis((result,), unit)
        chart.addAxis(categories, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(value_axis, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(categories)
        series.attachAxis(value_axis)
        series.hovered.connect(
            lambda status, _box: self._inspect_box(status, title, result)
        )
        series.clicked.connect(
            lambda selected: self._select_box(series, selected, title, result)
        )
        if result.outliers:
            scatter = QScatterSeries()
            scatter.setMarkerSize(7.0)
            scatter.setColor(self._colors()["outlier"])
            scatter.append([QPointF(0.0, value) for value in result.outliers])
            chart.addSeries(scatter)
            scatter.attachAxis(categories)
            scatter.attachAxis(value_axis)
        self._style_axes(chart)
        self._inspect_box(True, title, result)

    def show_grouped_boxes(
        self,
        result: GroupedComparisonResult,
        *,
        title: str,
        unit: str | None,
        labels: tuple[str, ...],
    ) -> None:
        chart = self._set_chart(title)
        boxes = tuple(group.box for group in result.groups if group.valid_n)
        if not boxes:
            self.inspection_label.setText(tr("No valid numeric values for these groups."))
            return
        series = QBoxPlotSeries()
        box_lookup: dict[QBoxSet, tuple[str, TukeyBoxResult]] = {}
        valid_labels: list[str] = []
        for label, group in zip(labels, result.groups, strict=True):
            if not group.valid_n:
                continue
            valid_labels.append(label)
            box_set = self._box_set(label, group.box)
            series.append(box_set)
            box_lookup[box_set] = (label, group.box)
        chart.addSeries(series)
        categories = QBarCategoryAxis()
        categories.append([
            label if len(label) <= 18 else f"{label[:17]}…"
            for label in valid_labels
        ])
        value_axis = self._box_value_axis(boxes, unit)
        chart.addAxis(categories, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(value_axis, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(categories)
        series.attachAxis(value_axis)
        series.hovered.connect(
            lambda status, box_set: self._inspect_box(
                status, *box_lookup.get(box_set, ("", boxes[0]))
            )
        )
        series.clicked.connect(
            lambda selected: self._select_box(
                series, selected, *box_lookup.get(selected, ("", boxes[0]))
            )
        )
        outliers = QScatterSeries()
        outliers.setMarkerSize(7.0)
        outliers.setColor(self._colors()["outlier"])
        category_index = 0
        for group in result.groups:
            if not group.valid_n:
                continue
            outliers.append([
                QPointF(float(category_index), value) for value in group.box.outliers
            ])
            category_index += 1
        if outliers.count():
            chart.addSeries(outliers)
            outliers.attachAxis(categories)
            outliers.attachAxis(value_axis)
        self._style_axes(chart)
        self.inspection_label.setText(
            tr("Hover or select a group to inspect its Tukey statistics.")
        )

    def refresh_theme(self) -> None:
        chart = getattr(self, "chart", None)
        if chart is not None:
            self._apply_theme(chart)
            self._style_axes(chart)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            self.refresh_theme()

    def _set_chart(self, title: str) -> QChart:
        chart = QChart()
        chart.setTitle(title)
        chart.legend().hide()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        chart.setBackgroundRoundness(0)
        self._apply_theme(chart)
        self.chart_view.setChart(chart)
        self.chart = chart
        return chart

    def _apply_theme(self, chart: QChart) -> None:
        colors = self._colors()
        chart.setBackgroundBrush(QBrush(colors["surface"]))
        chart.setBackgroundPen(QPen(colors["border"]))
        chart.setPlotAreaBackgroundVisible(True)
        chart.setPlotAreaBackgroundBrush(QBrush(colors["plot"]))
        chart.setPlotAreaBackgroundPen(QPen(colors["border"]))
        chart.setTitleBrush(QBrush(colors["text"]))

    def _style_axes(self, chart: QChart) -> None:
        colors = self._colors()
        for axis in chart.axes():
            axis.setLabelsBrush(QBrush(colors["muted"]))
            axis.setTitleBrush(QBrush(colors["text"]))
            axis.setLinePen(QPen(colors["border"]))
            axis.setGridLinePen(QPen(colors["grid"]))

    @staticmethod
    def _colors() -> dict[str, QColor]:
        app = QApplication.instance()
        dark = bool(app is not None and app.property("slopeforgeTheme") == "dark")
        return {
            "surface": QColor(DarkColor.SURFACE if dark else Color.SURFACE),
            "plot": QColor(DarkColor.SURFACE_SUBTLE if dark else Color.SURFACE_SUBTLE),
            "border": QColor(DarkColor.BORDER if dark else Color.BORDER),
            "grid": QColor("#35404d" if dark else "#e1e6ed"),
            "text": QColor(DarkColor.TEXT_PRIMARY if dark else Color.TEXT_PRIMARY),
            "muted": QColor(DarkColor.TEXT_MUTED if dark else Color.TEXT_MUTED),
            "accent": QColor(DarkColor.ACCENT if dark else Color.ACCENT),
            "reference": QColor("#d39b52" if dark else "#9b5b16"),
            "outlier": QColor("#d18ce0" if dark else "#7c3f91"),
        }

    def _add_reference_line(
        self,
        chart: QChart,
        x_axis: QBarCategoryAxis,
        y_axis: QValueAxis,
        result: HistogramResult,
        value: float | None,
        name: str,
    ) -> None:
        if value is None or not result.bin_edges:
            return
        minimum, maximum = result.bin_edges[0], result.bin_edges[-1]
        position = 0.0 if maximum == minimum else (
            (value - minimum) / (maximum - minimum) * len(result.counts) - 0.5
        )
        line = QLineSeries()
        line.setName(name)
        pen = QPen(self._colors()["reference"], 1.5, Qt.PenStyle.DashLine)
        line.setPen(pen)
        line.append(position, y_axis.min())
        line.append(position, y_axis.max())
        chart.addSeries(line)
        line.attachAxis(x_axis)
        line.attachAxis(y_axis)

    def _box_set(self, label: str, result: TukeyBoxResult) -> QBoxSet:
        box_set = QBoxSet(label)
        box_set.append([
            float(result.lower_whisker),
            float(result.q1),
            float(result.median),
            float(result.q3),
            float(result.upper_whisker),
        ])
        box_set.setBrush(QBrush(self._colors()["accent"]))
        box_set.setPen(QPen(self._colors()["text"], 1.0))
        return box_set

    def _box_value_axis(
        self, boxes: tuple[TukeyBoxResult, ...], unit: str | None
    ) -> QValueAxis:
        values = [
            value
            for box in boxes
            for value in (box.lower_whisker, box.upper_whisker, *box.outliers)
            if value is not None
        ]
        minimum, maximum = min(values), max(values)
        padding = (maximum - minimum) * 0.08 or 0.5
        axis = QValueAxis()
        axis.setTitleText(tr("Value") + (f" ({unit})" if unit else ""))
        axis.setRange(minimum - padding, maximum + padding)
        return axis

    def _inspect_histogram(
        self, status: bool, index: int, result: HistogramResult
    ) -> None:
        if not status or not 0 <= index < len(result.counts):
            return
        self.inspection_label.setText(
            tr("Interval %1 to %2 — %3 observations (%4%)")
            .replace("%1", _number(result.bin_edges[index]))
            .replace("%2", _number(result.bin_edges[index + 1]))
            .replace("%3", str(result.counts[index]))
            .replace("%4", f"{result.frequencies_percent[index]:.3g}")
        )

    def _inspect_ecdf(
        self, point: QPointF, status: bool, result: EcdfResult
    ) -> None:
        if not status:
            return
        rank = min(
            range(result.valid_n),
            key=lambda index: abs(result.observed_values[index] - point.x())
            + abs(result.cumulative_proportions[index] - point.y()),
        )
        self.inspection_label.setText(
            tr("Value %1 — cumulative proportion %2 (%3 of %4)")
            .replace("%1", _number(result.observed_values[rank]))
            .replace("%2", f"{result.cumulative_proportions[rank]:.3f}")
            .replace("%3", str(rank + 1))
            .replace("%4", str(result.valid_n))
        )

    def _inspect_box(
        self, status: bool, label: str, result: TukeyBoxResult
    ) -> None:
        if not status:
            return
        self.inspection_label.setText(
            tr("%1 — n %2; Q1 %3; median %4; Q3 %5; whiskers %6 to %7; outliers %8")
            .replace("%1", label)
            .replace("%2", str(result.valid_n))
            .replace("%3", _number(result.q1))
            .replace("%4", _number(result.median))
            .replace("%5", _number(result.q3))
            .replace("%6", _number(result.lower_whisker))
            .replace("%7", _number(result.upper_whisker))
            .replace("%8", str(len(result.outliers)))
        )

    def _select_box(
        self,
        series: QBoxPlotSeries,
        selected: QBoxSet,
        label: str,
        result: TukeyBoxResult,
    ) -> None:
        for box_set in series.boxSets():
            box_set.setBrush(QBrush(self._colors()["accent"]))
        selected.setBrush(QBrush(self._colors()["reference"]))
        self._inspect_box(True, label, result)
