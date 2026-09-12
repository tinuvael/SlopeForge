from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCharts import (
    QBarSeries,
    QBoxPlotSeries,
    QLineSeries,
    QScatterSeries,
    QValueAxis,
)
from PySide6.QtCore import QDate, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel

from application.analysis.catalog import assessment_results_dataset
from application.analysis.models import (
    AnalysisPopulationProjection,
    AnalysisRow,
    FilterSpec,
    FilteredDataset,
    PopulationLimitExceededError,
)
from application.services.analysis import AnalysisDatasetService
from application.services.analysis_statistics import AnalysisStatisticsService
from app.localization import TsTranslator
from ui.analysis.charts import AnalysisChartPanel
from ui.analysis.statistics_views import AnalysisCompareView, VIEW_TOP_SPACING
from ui.pages.analysis_page import AnalysisPage
from tests.test_analysis_foundation import MemoryAnalysisProvider


def _app():
    return QApplication.instance() or QApplication([])


def _population(**columns):
    size = len(next(iter(columns.values()))) if columns else 0
    return AnalysisPopulationProjection(
        assessment_results_dataset(),
        size,
        {key: tuple(values) for key, values in columns.items()},
    )


class LargePopulationProvider:
    SIZE = 6001

    def __init__(self):
        self.projection_calls = 0
        self.rows = tuple(
            AnalysisRow(
                f"EVR-{index}",
                {
                    "project": "Large project",
                    "domain": "Even" if index % 2 == 0 else "Odd",
                    "assessment_area": f"Area {index}",
                    "elevation_interval": None,
                    "min_elevation_m": None,
                    "max_elevation_m": None,
                    "assessment_date": None,
                    "dai": float(index),
                    "fci": None if index % 10 == 0 else float(index % 100) / 100,
                    "result_quadrant": None,
                    "inspector": None,
                    "evaluation_revision_id": f"EVR-{index}",
                },
            )
            for index in range(self.SIZE)
        )

    def filter_options(self, dataset_id):
        assert dataset_id == "assessment_results"
        return {key: () for key in ("project", "domain", "assessment_area", "result_quadrant", "inspector")}

    def load(self, spec, *, sort_spec=None, limit):
        assert spec == FilterSpec("assessment_results")
        del sort_spec
        return FilteredDataset(
            assessment_results_dataset(),
            self.rows[:limit],
            self.SIZE,
            self.SIZE,
            self.SIZE > limit,
        )

    def project_population(self, spec, *, field_keys, max_rows):
        assert spec == FilterSpec("assessment_results")
        self.projection_calls += 1
        if self.SIZE > max_rows:
            raise PopulationLimitExceededError(self.SIZE, max_rows)
        return AnalysisPopulationProjection(
            assessment_results_dataset(),
            self.SIZE,
            {key: tuple(row.values.get(key) for row in self.rows) for key in field_keys},
        )


def test_population_over_5000_keeps_data_bounded_and_all_statistics_complete():
    app = _app()
    provider = LargePopulationProvider()
    page = AnalysisPage(AnalysisDatasetService(provider))
    page.resize(1500, 900)
    page.show()
    app.processEvents()

    assert page.data_view.model.rowCount() == 5000
    assert page.record_count_label.text() == "6,001 / 6,001 records"
    assert page.data_view.display_count_label.text() == (
        "Showing first 5,000 of 6,001 matching records"
    )
    assert page.summary_view.last_result.matching_count == 6001
    assert {item.field_key: item.valid_n for item in page.summary_view.last_result.parameters} == {
        "dai": 6001,
        "fci": 5400,
    }
    assert sum(page.distribution_view.last_result.counts) == 6001

    page.distribution_view.mode_combo.setCurrentIndex(
        page.distribution_view.mode_combo.findData("ecdf")
    )
    app.processEvents()
    assert page.distribution_view.last_result.valid_n == 6001
    assert len(page.distribution_view.last_result.observed_values) == 6001
    page.distribution_view.mode_combo.setCurrentIndex(
        page.distribution_view.mode_combo.findData("box")
    )
    app.processEvents()
    assert page.distribution_view.last_result.valid_n == 6001

    assert sum(group.valid_n for group in page.compare_view.last_result.groups) == 5400
    assert sum(group.missing_count for group in page.compare_view.last_result.groups) == 601
    assert provider.projection_calls == 1
    page.close()


def test_summary_defaults_modes_and_zero_one_multiple_parameter_selection():
    app = _app()
    page = AnalysisPage(AnalysisDatasetService(MemoryAnalysisProvider()))
    page.show(); app.processEvents()
    assert page.summary_view.selected_parameters == ("dai", "fci")
    assert page.summary_view.mode_combo.currentData() == "descriptive"

    actions = {action.text(): action for action in page.summary_view.parameters_menu.actions()}
    actions["DAI"].setChecked(False)
    assert page.summary_view.selected_parameters == ("fci",)
    actions["FCI"].setChecked(False)
    assert page.summary_view.selected_parameters == ()
    assert page.summary_view.table.rowCount() == 0
    actions["DAI"].setChecked(True)
    actions["FCI"].setChecked(True)
    assert page.summary_view.selected_parameters == ("dai", "fci")
    page.summary_view.mode_combo.setCurrentIndex(1)
    page.tabs.setCurrentIndex(0)
    page.tabs.setCurrentIndex(1)
    assert page.summary_view.mode_combo.currentData() == "percentiles"
    page.close()


def test_invalid_numeric_range_is_local_and_retains_previous_population():
    app = _app()
    provider = MemoryAnalysisProvider()
    page = AnalysisPage(AnalysisDatasetService(provider))
    page.show(); app.processEvents()
    assert page.filter_panel.add_optional_filter("dai")
    control = page.filter_panel.controls["dai"]
    control.minimum.setText("0.7")
    control.minimum.editingFinished.emit(); app.processEvents()
    previous_calls = len(provider.specs)
    assert page.record_count_label.text() == "1 / 3 records"
    assert page.summary_view.last_result.matching_count == 1

    control.maximum.setText("0.2")
    control.maximum.editingFinished.emit(); app.processEvents()
    assert control.validation_label.isVisible()
    assert "not applied" in control.validation_label.text()
    assert len(provider.specs) == previous_calls
    assert page.record_count_label.text() == "1 / 3 records"
    assert page.current_filter_spec().conditions[0].value == (0.7, None)
    assert page.summary_view.last_result.matching_count == 1
    page.close()


def test_invalid_date_range_is_local_and_retains_previous_population():
    app = _app()
    provider = MemoryAnalysisProvider()
    page = AnalysisPage(AnalysisDatasetService(provider))
    page.show(); app.processEvents()
    assert page.filter_panel.add_optional_filter("assessment_date")
    control = page.filter_panel.controls["assessment_date"]
    control.minimum.setDate(QDate(2025, 2, 1))
    control.from_enabled.setChecked(True); app.processEvents()
    previous_spec = page.current_filter_spec()
    previous_calls = len(provider.specs)

    control.maximum.setDate(QDate(2025, 1, 1))
    control.to_enabled.setChecked(True); app.processEvents()
    assert control.validation_label.isVisible()
    assert "not applied" in control.validation_label.text()
    assert len(provider.specs) == previous_calls
    assert page.current_filter_spec() == previous_spec
    page.close()


def test_statistical_population_limit_is_explicit_and_data_remains_available():
    app = _app()
    page = AnalysisPage(
        AnalysisDatasetService(MemoryAnalysisProvider(), population_limit=2)
    )
    page.show(); app.processEvents()

    assert page.data_view.model.rowCount() == 3
    assert page.record_count_label.text() == "3 / 3 records"
    assert "safe statistical limit of 2" in page.summary_view.message_label.text()
    assert "Narrow the filters" in page.summary_view.message_label.text()
    assert "safe statistical limit of 2" in (
        page.distribution_view.chart_panel.inspection_label.text()
    )
    page.close()


def test_population_summary_sidebar_and_view_state_preserve_filters():
    app = _app()
    page = AnalysisPage(AnalysisDatasetService(MemoryAnalysisProvider()))
    page.resize(1200, 760)
    page.show(); app.processEvents()
    project = page.filter_panel.controls["project"]
    project.combo.setCurrentIndex(project.combo.findData(1)); app.processEvents()
    spec = page.current_filter_spec()
    assert "Project: Alpha" in page.population_summary_label.text()
    assert page.record_count_label.x() >= page.dataset_combo.x() + page.dataset_combo.width()

    page.sidebar_button.click(); app.processEvents()
    assert not page.filter_panel.isVisible()
    assert page.current_filter_spec() == spec
    page.sidebar_button.click(); app.processEvents()
    assert page.filter_panel.isVisible()
    for index in range(page.tabs.count()):
        page.tabs.setCurrentIndex(index)
        assert page.current_filter_spec() == spec
    assert page.tabs.count() == 5
    page.close()


def test_distribution_compare_controls_and_chart_theme_contract():
    app = _app()
    previous = app.property("slopeforgeTheme")
    app.setProperty("slopeforgeTheme", "light")
    page = AnalysisPage(AnalysisDatasetService(MemoryAnalysisProvider()))
    page.show(); app.processEvents()
    assert page.distribution_view.parameter_combo.currentData() == "dai"
    assert page.distribution_view.mode_combo.currentData() == "histogram"
    assert "Valid n: 2" in page.distribution_view.count_label.text()
    page.distribution_view.scale_combo.setCurrentIndex(1)
    page.distribution_view.mean_check.setChecked(True)
    page.distribution_view.median_check.setChecked(True)
    app.processEvents()
    assert page.distribution_view.scale_combo.currentData() == "frequency"
    assert len(page.distribution_view.chart_panel.chart.series()) == 3
    assert page.compare_view.parameter_combo.currentData() == "fci"
    assert page.compare_view.group_combo.currentData() == "domain"

    light_plot = page.distribution_view.chart_panel.chart.plotAreaBackgroundBrush().color()
    app.setProperty("slopeforgeTheme", "dark")
    QApplication.sendEvent(
        page.distribution_view.chart_panel,
        QEvent(QEvent.Type.StyleChange),
    )
    dark_plot = page.distribution_view.chart_panel.chart.plotAreaBackgroundBrush().color()
    assert dark_plot != light_plot
    assert dark_plot.name().lower() != "#ffffff"
    app.setProperty("slopeforgeTheme", previous)
    page.close()


def test_statistical_views_are_separated_from_tabs_and_conventions_open():
    app = _app()
    page = AnalysisPage(AnalysisDatasetService(MemoryAnalysisProvider()))
    page.resize(1366, 768)
    page.show(); app.processEvents()

    for view in (
        page.summary_view,
        page.distribution_view,
        page.compare_view,
        page.relationships_view,
    ):
        assert view.layout().contentsMargins().top() == VIEW_TOP_SPACING

    page.summary_view.conventions_button.click(); app.processEvents()
    dialog = page.summary_view.conventions_dialog
    assert dialog.isVisible()
    text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "ddof=1" in text
    assert "NumPy's linear method" in text
    assert "1.5×IQR" in text
    assert "statistical outliers" in text
    dialog.reject()
    page.close()


def test_statistical_conventions_have_russian_translations():
    app = _app()
    translator = TsTranslator(app)
    assert translator.load(Path("translations/slopeforge_ru_analysis.ts"))
    assert translator.translate("SlopeForge", "Statistical conventions") == (
        "Статистическая методика"
    )
    assert "линейным методом NumPy" in translator.translate(
        "SlopeForge", "Quantiles use NumPy's linear method."
    )
    assert "инженерными дефектами" in translator.translate(
        "SlopeForge",
        "Observations outside the fences are statistical outliers, not automatically engineering defects.",
    )


def test_histogram_mean_and_median_have_distinct_theme_aware_pen_styles():
    app = _app()
    previous = app.property("slopeforgeTheme")
    panel = AnalysisChartPanel()
    result = AnalysisStatisticsService(None).histogram(
        _population(dai=(0.1, 0.4, 0.55, 0.8)), "dai"
    )

    for theme in ("light", "dark"):
        app.setProperty("slopeforgeTheme", theme)
        panel.show_histogram(
            result,
            title="DAI",
            unit=None,
            frequency=False,
            show_mean=True,
            show_median=True,
        )
        references = {
            series.name(): series
            for series in panel.chart.series()
            if isinstance(series, QLineSeries)
        }
        assert references["Mean"].pen().style() == Qt.PenStyle.SolidLine
        assert references["Median"].pen().style() == Qt.PenStyle.DashLine
        assert references["Mean"].pen().color() != references["Median"].pen().color()
        assert "Mean (solid)" in panel.inspection_label.text()
        assert "Median (dashed)" in panel.inspection_label.text()
        bars = next(
            series for series in panel.chart.series() if isinstance(series, QBarSeries)
        )
        assert bars.barWidth() == pytest.approx(0.76)

    app.setProperty("slopeforgeTheme", previous)
    panel.close()


@pytest.mark.parametrize("values", [(3.0,), (3.0, 7.0)])
def test_small_sample_histogram_ecdf_and_box_plots_have_valid_axes(values):
    _app()
    service = AnalysisStatisticsService(None)
    population = _population(dai=values)
    panel = AnalysisChartPanel()

    panel.show_histogram(
        service.histogram(population, "dai"),
        title="DAI", unit=None, frequency=False,
        show_mean=True, show_median=True,
    )
    assert all(
        axis.min() < axis.max()
        for axis in panel.chart.axes()
        if isinstance(axis, QValueAxis)
    )

    panel.show_ecdf(service.ecdf(population, "dai"), title="DAI", unit=None)
    assert all(
        axis.min() < axis.max()
        for axis in panel.chart.axes()
        if isinstance(axis, QValueAxis)
    )

    panel.show_box(service.box(population, "dai"), title="DAI", unit=None)
    assert any(isinstance(series, QBoxPlotSeries) for series in panel.chart.series())
    assert all(
        axis.min() < axis.max()
        for axis in panel.chart.axes()
        if isinstance(axis, QValueAxis)
    )
    panel.close()


def test_box_plot_outlier_series_is_present_only_when_statistically_required():
    _app()
    service = AnalysisStatisticsService(None)
    panel = AnalysisChartPanel()

    panel.show_box(
        service.box(_population(dai=(1.0, 2.0, 3.0, 4.0)), "dai"),
        title="DAI",
        unit=None,
    )
    assert not any(
        isinstance(series, QScatterSeries) for series in panel.chart.series()
    )

    panel.show_box(
        service.box(_population(dai=(1.0, 2.0, 3.0, 4.0, 100.0)), "dai"),
        title="DAI",
        unit=None,
    )
    assert any(isinstance(series, QScatterSeries) for series in panel.chart.series())
    panel.close()


def test_compare_small_and_missing_groups_stay_readable_during_resize():
    app = _app()
    view = AnalysisCompareView(AnalysisStatisticsService(None))
    view.set_context(
        assessment_results_dataset(),
        _population(
            fci=(1.0, 2.0, 4.0, None, float("nan")),
            domain=(
                "A single observation with a long group label",
                "Two observations",
                "Two observations",
                "All numeric values missing",
                "All numeric values missing",
            ),
        ),
    )

    for width in (1060, 1614):
        view.resize(width, 650)
        view.show(); app.processEvents()
        assert view.splitter.sizes()[0] >= 480
        assert view.splitter.sizes()[1] >= 350
        assert not view.splitter.childrenCollapsible()

    groups = {group.group_value: group for group in view.last_result.groups}
    assert groups["A single observation with a long group label"].valid_n == 1
    assert groups["A single observation with a long group label"].sample_std_dev is None
    assert groups["Two observations"].valid_n == 2
    assert groups["All numeric values missing"].valid_n == 0
    assert view.table.rowCount() == 3
    assert view.table.item(0, 0).toolTip() == view.table.item(0, 0).text()
    assert "No valid numeric values" not in view.chart_panel.inspection_label.text()
    view.close()
