from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QDate, QEvent, Qt
from PySide6.QtWidgets import QApplication

from application.analysis.catalog import assessment_results_dataset
from application.analysis.models import (
    AnalysisPopulationProjection,
    AnalysisRow,
    FilterSpec,
    FilteredDataset,
    PopulationLimitExceededError,
)
from application.services.analysis import AnalysisDatasetService
from ui.pages.analysis_page import AnalysisPage
from tests.test_analysis_foundation import MemoryAnalysisProvider


def _app():
    return QApplication.instance() or QApplication([])


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
