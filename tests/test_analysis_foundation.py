from __future__ import annotations

from datetime import date
import os
from types import SimpleNamespace

import pytest

from application.analysis.catalog import assessment_results_dataset
from application.analysis.models import (
    AnalysisFieldType,
    AnalysisPopulationProjection,
    AnalysisRow,
    DatasetUnavailableError,
    FilterChoice,
    FilterCondition,
    FilterOperator,
    FilterSpec,
    FilteredDataset,
    PopulationLimitExceededError,
    SourceReference,
    SortSpec,
)
from application.services.analysis import AnalysisDatasetService


class MemoryAnalysisProvider:
    def __init__(self):
        self.specs = []
        self.sort_specs = []
        self.rows = (
            AnalysisRow(
                "EVR-1",
                {
                    "project": "Alpha", "domain": "North", "assessment_area": "Wall 1",
                    "elevation_interval": "600–620", "min_elevation_m": 600.0,
                    "max_elevation_m": 620.0, "assessment_date": date(2026, 8, 1),
                    "dai": .8, "fci": .7,
                    "result_quadrant": "good_results", "inspector": "A. Smith",
                    "evaluation_revision_id": "EVR-1", "_project_id": 1,
                    "_domain_id": 10, "_area_id": 100,
                },
                SourceReference("assessment_area", "AA-1", 1, 10),
            ),
            AnalysisRow(
                "EVR-2",
                {
                    "project": "Alpha", "domain": "South", "assessment_area": "Wall 2",
                    "elevation_interval": None, "min_elevation_m": None,
                    "max_elevation_m": None, "assessment_date": date(2026, 8, 3),
                    "dai": None, "fci": .55,
                    "result_quadrant": "geometry_achieved_condition_insufficient",
                    "inspector": None, "evaluation_revision_id": "EVR-2",
                    "_project_id": 1, "_domain_id": 11, "_area_id": 101,
                },
                SourceReference("assessment_area", "AA-2", 1, 11),
            ),
            AnalysisRow(
                "EVR-3",
                {
                    "project": "Beta", "domain": "East", "assessment_area": "Wall 3",
                    "elevation_interval": "580–600", "min_elevation_m": 580.0,
                    "max_elevation_m": 600.0, "assessment_date": date(2026, 8, 5),
                    "dai": .4, "fci": .3,
                    "result_quadrant": "unacceptable", "inspector": "B. Jones",
                    "evaluation_revision_id": "EVR-3", "_project_id": 2,
                    "_domain_id": 12, "_area_id": 102,
                },
                SourceReference("assessment_area", "AA-3", 2, 12),
            ),
        )

    def filter_options(self, dataset_id):
        assert dataset_id == "assessment_results"
        return {
            "project": (FilterChoice(1, "Alpha"), FilterChoice(2, "Beta")),
            "domain": (FilterChoice(10, "Alpha / North"), FilterChoice(11, "Alpha / South"), FilterChoice(12, "Beta / East")),
            "assessment_area": (FilterChoice(100, "Alpha / North / Wall 1"), FilterChoice(101, "Alpha / South / Wall 2"), FilterChoice(102, "Beta / East / Wall 3")),
            "result_quadrant": (
                FilterChoice("good_results", "good_results"),
                FilterChoice("unacceptable", "unacceptable"),
            ),
            "inspector": (FilterChoice("A. Smith", "A. Smith"), FilterChoice("B. Jones", "B. Jones")),
        }

    def load(self, spec, *, sort_spec=None, limit):
        self.specs.append(spec)
        self.sort_specs.append(sort_spec)
        rows = list(self.rows)
        identifiers = {"project": "_project_id", "domain": "_domain_id", "assessment_area": "_area_id"}
        for condition in spec.conditions:
            if condition.operator is FilterOperator.CATEGORICAL_EQUALS:
                key = identifiers.get(condition.field_key, condition.field_key)
                rows = [row for row in rows if row.values.get(key) == condition.value]
            elif condition.operator is FilterOperator.NUMERIC_RANGE:
                lower, upper = condition.value
                rows = [row for row in rows if row.values.get(condition.field_key) is not None
                        and (lower is None or row.values[condition.field_key] >= lower)
                        and (upper is None or row.values[condition.field_key] <= upper)]
            elif condition.operator is FilterOperator.DATE_RANGE:
                lower, upper = condition.value
                rows = [row for row in rows if row.values.get(condition.field_key) is not None
                        and (lower is None or row.values[condition.field_key] >= lower)
                        and (upper is None or row.values[condition.field_key] <= upper)]
        if sort_spec is not None:
            populated = [
                row for row in rows if row.values.get(sort_spec.field_key) is not None
            ]
            missing = [
                row for row in rows if row.values.get(sort_spec.field_key) is None
            ]

            def sort_value(row):
                value = row.values[sort_spec.field_key]
                return value.casefold() if isinstance(value, str) else value

            populated.sort(
                key=lambda row: (sort_value(row), row.identity),
                reverse=not sort_spec.ascending,
            )
            missing.sort(key=lambda row: row.identity)
            rows = populated + missing
        return FilteredDataset(
            assessment_results_dataset(), tuple(rows[:limit]), len(self.rows), len(rows),
            len(rows) > limit,
        )

    def project_population(self, spec, *, field_keys, max_rows):
        rows = list(self.rows)
        identifiers = {
            "project": "_project_id",
            "domain": "_domain_id",
            "assessment_area": "_area_id",
        }
        for condition in spec.conditions:
            if condition.operator is FilterOperator.CATEGORICAL_EQUALS:
                key = identifiers.get(condition.field_key, condition.field_key)
                rows = [row for row in rows if row.values.get(key) == condition.value]
            elif condition.operator in {
                FilterOperator.NUMERIC_RANGE,
                FilterOperator.DATE_RANGE,
            }:
                lower, upper = condition.value
                rows = [
                    row for row in rows
                    if row.values.get(condition.field_key) is not None
                    and (lower is None or row.values[condition.field_key] >= lower)
                    and (upper is None or row.values[condition.field_key] <= upper)
                ]
        if len(rows) > max_rows:
            raise PopulationLimitExceededError(len(rows), max_rows)
        return AnalysisPopulationProjection(
            assessment_results_dataset(),
            len(rows),
            {
                key: tuple(row.values.get(key) for row in rows)
                for key in field_keys
            },
        )


def test_dataset_metadata_has_stable_keys_types_and_explicit_row_semantics():
    dataset = assessment_results_dataset()
    keys = [field.key for field in dataset.fields]
    assert dataset.dataset_id == "assessment_results"
    assert len(keys) == len(set(keys))
    assert "active stored completed Assessment evaluation revision" in dataset.row_semantics
    assert dataset.field("dai").field_type is AnalysisFieldType.NUMERIC
    assert dataset.field("fci").field_type is AnalysisFieldType.NUMERIC
    assert dataset.field("dai").key != dataset.field("fci").key
    assert dataset.field("project").always_visible
    assert dataset.field("project").common_filter
    assert dataset.field("domain").common_filter
    assert dataset.field("assessment_date").supported_operators == (FilterOperator.DATE_RANGE,)
    with pytest.raises(KeyError):
        dataset.field("status")


def test_service_validates_typed_filters_and_unavailable_datasets():
    service = AnalysisDatasetService(MemoryAnalysisProvider())
    result = service.load(FilterSpec("assessment_results", (
        FilterCondition("dai", FilterOperator.NUMERIC_RANGE, (.5, .9)),
    )))
    assert result.filtered_count == 1
    domain = service.load(FilterSpec("assessment_results", (
        FilterCondition("domain", FilterOperator.CATEGORICAL_EQUALS, 11),
    )))
    assert domain.filtered_count == 1 and domain.rows[0].identity == "EVR-2"
    area = service.load(FilterSpec("assessment_results", (
        FilterCondition("assessment_area", FilterOperator.CATEGORICAL_EQUALS, 102),
    )))
    assert area.filtered_count == 1 and area.rows[0].identity == "EVR-3"
    with pytest.raises(ValueError, match="minimum"):
        service.load(FilterSpec("assessment_results", (
            FilterCondition("fci", FilterOperator.NUMERIC_RANGE, (.8, .2)),
        )))
    with pytest.raises(DatasetUnavailableError):
        service.load(FilterSpec("drillhole_qa"))


def test_service_validates_sort_metadata_and_provider_sorts_before_limit():
    provider = MemoryAnalysisProvider()
    service = AnalysisDatasetService(provider)
    result = service.load(
        FilterSpec("assessment_results"),
        sort_spec=SortSpec("dai", ascending=True),
        limit=2,
    )
    assert [row.identity for row in result.rows] == ["EVR-3", "EVR-1"]
    assert result.truncated
    assert provider.sort_specs[-1] == SortSpec("dai", ascending=True)
    with pytest.raises(ValueError, match="not sortable"):
        service.load(
            FilterSpec("assessment_results"),
            sort_spec=SortSpec("elevation_interval"),
        )
    with pytest.raises(ValueError, match="Unknown Analysis sort field"):
        service.load(
            FilterSpec("assessment_results"), sort_spec=SortSpec("invented")
        )


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
from PySide6.QtCore import QDate, QEvent, QPoint, Qt

from ui.header import Header
from ui.main_window import MainWindow
from ui.analysis.data_table import MISSING_VALUE
from ui.analysis.filters import AnalysisFilterPanel
from ui.pages.analysis_page import AnalysisPage


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_analysis_data_table_sort_format_identity_and_column_visibility():
    app = _app()
    provider = MemoryAnalysisProvider()
    page = AnalysisPage(AnalysisDatasetService(provider))
    page.resize(1400, 840)
    page.show(); app.processEvents()
    assert page.record_count_label.text() == "3 / 3 records"
    model = page.data_view.model
    table = page.data_view.table
    assert model.rowCount() == 3
    assert model.data(model.index(0, 0), Qt.ItemDataRole.UserRole) == "EVR-1"

    dai_column = next(i for i, field in enumerate(model.dataset.fields) if field.key == "dai")
    inspector_column = next(i for i, field in enumerate(model.dataset.fields) if field.key == "inspector")
    assert model.data(model.index(0, dai_column)) == "0.800"
    assert model.data(model.index(1, dai_column)) == MISSING_VALUE
    assert table.isColumnHidden(inspector_column)

    inspector_action = next(action for action in page.data_view.columns_menu.actions() if action.text() == "Inspector")
    inspector_action.setChecked(True); app.processEvents()
    assert not table.isColumnHidden(inspector_column)
    table.sortByColumn(dai_column, Qt.SortOrder.DescendingOrder); app.processEvents()
    assert [row.identity for row in model.rows] == ["EVR-1", "EVR-3", "EVR-2"]
    assert provider.sort_specs[-1] == SortSpec("dai", ascending=False)

    page.tabs.setCurrentIndex(2); page.tabs.setCurrentIndex(0)
    assert inspector_action.isChecked()
    page.close()


def test_analysis_workspace_has_light_and_dark_theme_contracts():
    from pathlib import Path

    light = Path("ui/theme.py").read_text(encoding="utf-8")
    dark = Path("ui/application_theme.py").read_text(encoding="utf-8")
    for selector in (
        "AnalysisPage",
        "AnalysisToolbar",
        "AnalysisFiltersPanel",
        "AnalysisRemoveFilterButton",
        "AnalysisDataTable",
    ):
        assert selector in light
        assert selector in dark


def test_analysis_combo_boxes_have_dedicated_theme_chevrons_and_geometry():
    from pathlib import Path

    light = Path("ui/theme.py").read_text(encoding="utf-8")
    dark = Path("ui/application_theme.py").read_text(encoding="utf-8")
    for stylesheet in (light, dark):
        assert "QWidget#AnalysisPage QComboBox::drop-down" in stylesheet
        assert "QWidget#AnalysisPage QComboBox::down-arrow" in stylesheet
        assert "subcontrol-origin: padding" in stylesheet
        assert "subcontrol-position: top right" in stylesheet
        assert "padding: 1px 32px 1px 7px" in stylesheet
        assert "width: 26px" in stylesheet
        assert "width: 12px; height: 12px" in stylesheet
    assert 'image: url("{_COMBO_CHEVRON}")' in light
    assert "chevron-down.svg" in light
    assert 'image: url("{_DARK_COMBO_CHEVRON}")' in dark
    assert "chevron-down-white.svg" in dark


def test_analysis_button_icon_tracks_active_and_runtime_theme_state():
    app = _app()
    previous_theme = app.property("slopeforgeTheme")
    app.setProperty("slopeforgeTheme", "light")
    header = Header(SimpleNamespace(current_user=SimpleNamespace(can_edit=True)))

    def icon_image():
        assert not header.analysis_button.icon().isNull()
        pixmap = header.analysis_button.icon().pixmap(20, 20)
        assert not pixmap.isNull()
        return pixmap.toImage()

    try:
        light_inactive = icon_image()
        header.set_analysis_active(True)
        light_active = icon_image()
        assert light_active != light_inactive

        header.set_analysis_active(False)
        assert icon_image() == light_inactive

        app.setProperty("slopeforgeTheme", "dark")
        header.changeEvent(QEvent(QEvent.Type.StyleChange))
        dark_inactive = icon_image()
        assert dark_inactive != light_inactive

        for _ in range(5):
            header.set_analysis_active(True)
            assert icon_image() == light_active
            header.set_analysis_active(False)
            assert icon_image() == dark_inactive
    finally:
        app.setProperty("slopeforgeTheme", previous_theme)
        header.close()


def test_compact_filters_add_remove_prevent_duplicates_and_reset():
    app = _app()
    provider = MemoryAnalysisProvider()
    page = AnalysisPage(AnalysisDatasetService(provider))
    page.show(); app.processEvents()

    project = page.filter_panel.controls["project"]
    assert set(page.filter_panel.controls) == {"project", "domain"}
    expected_optional = {
        field.key
        for field in assessment_results_dataset().fields
        if field.filterable and not field.common_filter
    }
    assert {
        action.data() for action in page.filter_panel.add_menu.actions()
    } == expected_optional
    project.combo.setCurrentIndex(project.combo.findData(1)); app.processEvents()
    assert page.record_count_label.text() == "2 / 3 records"
    assert page.current_filter_spec().active_count == 1

    assert page.filter_panel.add_optional_filter("dai")
    assert not page.filter_panel.add_optional_filter("dai")
    dai = page.filter_panel.controls["dai"]
    dai.minimum.setText("0.7"); dai.minimum.editingFinished.emit(); app.processEvents()
    assert page.record_count_label.text() == "1 / 3 records"

    page.reset_filters(); app.processEvents()
    assert set(page.filter_panel.controls) == {"project", "domain"}
    assert page.current_filter_spec().active_count == 0
    assert page.filter_panel.add_optional_filter("assessment_date")
    when = page.filter_panel.controls["assessment_date"]
    when.minimum.setDate(QDate(2026, 8, 4))
    when.from_enabled.setChecked(True); app.processEvents()
    assert page.record_count_label.text() == "1 / 3 records"

    page.reset_filters(); app.processEvents()
    assert page.filter_panel.add_optional_filter("dai")
    dai = page.filter_panel.controls["dai"]
    dai.minimum.setText("0.99"); dai.minimum.editingFinished.emit(); app.processEvents()
    assert page.record_count_label.text() == "0 / 3 records"
    assert "match the current filters" in page.data_view.state_label.text()
    page.reset_filters(); app.processEvents()
    assert page.record_count_label.text() == "3 / 3 records"
    assert page.active_count_label.text() == "No active filters"
    page.close()


def test_filter_panel_children_stay_within_narrow_scroll_viewport():
    app = _app()
    dataset = assessment_results_dataset()
    very_long = (
        "Birkachan / South / Assessment Area with a deliberately very long name "
        "that must not widen the filter sidebar"
    )
    options = MemoryAnalysisProvider().filter_options("assessment_results")
    options["project"] = (FilterChoice(1, very_long),)
    options["domain"] = (FilterChoice(10, very_long),)
    options["assessment_area"] = (FilterChoice(100, very_long),)
    options["inspector"] = (FilterChoice("Long Inspector", very_long),)

    panel = AnalysisFilterPanel()
    panel.resize(230, 700)
    panel.set_dataset(dataset, options, FilterSpec("assessment_results"), ())
    for key in ("assessment_area", "fci", "assessment_date", "inspector"):
        assert panel.add_optional_filter(key)
    panel.show()
    app.processEvents()

    viewport = panel.scroll.viewport()
    assert panel.filter_host.width() == viewport.width()
    assert not panel.scroll.horizontalScrollBar().isVisible()
    assert panel.scroll.horizontalScrollBar().maximum() == 0

    def assert_inside_viewport(widget):
        top_left = widget.mapTo(viewport, QPoint(0, 0))
        assert top_left.x() >= 0
        assert top_left.x() + widget.width() <= viewport.width()

    for control in panel.controls.values():
        assert_inside_viewport(control)
        for widget_type in (
            QtWidgets.QComboBox,
            QtWidgets.QLineEdit,
            QtWidgets.QDateEdit,
            QtWidgets.QToolButton,
        ):
            for widget in control.findChildren(widget_type):
                if widget.isVisible():
                    assert_inside_viewport(widget)

    area_combo = panel.controls["assessment_area"].combo
    area_combo.setCurrentIndex(1)
    app.processEvents()
    assert area_combo.minimumWidth() == 0
    assert area_combo.width() <= viewport.width()
    assert area_combo.toolTip() == very_long

    date_filter = panel.controls["assessment_date"]
    assert not date_filter.minimum.isEnabled()
    date_filter.from_enabled.setChecked(True)
    app.processEvents()
    assert date_filter.minimum.isEnabled()
    assert_inside_viewport(date_filter.minimum)

    for button in (panel.add_button, panel.reset_button):
        right_edge = button.mapTo(panel, QPoint(0, 0)).x() + button.width()
        assert right_edge <= panel.width()
    panel.close()


def test_optional_filter_can_be_removed_individually():
    app = _app()
    page = AnalysisPage(AnalysisDatasetService(MemoryAnalysisProvider()))
    page.show(); app.processEvents()
    assert page.filter_panel.add_optional_filter("inspector")
    inspector = page.filter_panel.controls["inspector"]
    inspector.combo.setCurrentIndex(inspector.combo.findData("A. Smith"))
    app.processEvents()
    assert page.current_filter_spec().active_count == 1
    assert page.filter_panel.remove_optional_filter("inspector")
    app.processEvents()
    assert "inspector" not in page.filter_panel.controls
    assert page.current_filter_spec().active_count == 0
    page.close()


def test_unavailable_dataset_is_honest_and_analysis_state_survives_return():
    app = _app()
    page = AnalysisPage(AnalysisDatasetService(MemoryAnalysisProvider()))
    inspector_action = next(action for action in page.data_view.columns_menu.actions() if action.text() == "Inspector")
    inspector_action.setChecked(True)
    assert page.filter_panel.add_optional_filter("inspector")
    inspector = page.filter_panel.controls["inspector"]
    inspector.combo.setCurrentIndex(inspector.combo.findData("B. Jones"))
    dai_column = next(i for i, field in enumerate(page.data_view.model.dataset.fields) if field.key == "dai")
    page.data_view.table.sortByColumn(dai_column, Qt.SortOrder.AscendingOrder)
    app.processEvents()
    page.tabs.setCurrentIndex(2); page.tabs.setCurrentIndex(0)
    assert page.current_filter_spec().active_count == 1
    page.reload(); app.processEvents()
    assert "inspector" in page.filter_panel.optional_keys
    assert page.filter_panel.controls["inspector"].combo.currentData() == "B. Jones"
    page.dataset_combo.setCurrentIndex(1); app.processEvents()
    assert page.selected_dataset_id == "production_technical_cards"
    assert page.data_view.state_label.text() == "This Analysis dataset is not available yet."
    page.dataset_combo.setCurrentIndex(0); app.processEvents()
    inspector_column = next(i for i, field in enumerate(page.data_view.model.dataset.fields) if field.key == "inspector")
    assert not page.data_view.table.isColumnHidden(inspector_column)
    assert page.filter_panel.controls["inspector"].combo.currentData() == "B. Jones"
    assert page._sort_state["assessment_results"] == SortSpec("dai", ascending=True)
    page.close()


def test_analysis_mode_toggle_preserves_normal_workspace_and_does_not_accumulate_pages():
    app = _app()
    host = QtWidgets.QWidget()
    tree = QtWidgets.QWidget()
    normal_page = QtWidgets.QWidget()
    page_stack = QtWidgets.QStackedWidget()
    page_stack.addWidget(normal_page)
    normal_workspace = QtWidgets.QWidget()
    normal_layout = QtWidgets.QHBoxLayout(normal_workspace)
    normal_layout.addWidget(tree)
    normal_layout.addWidget(page_stack)
    analysis_page = QtWidgets.QWidget()
    analysis_page.refresh_count = 0
    analysis_page.reload = lambda: setattr(analysis_page, "refresh_count", analysis_page.refresh_count + 1)
    workspace_stack = QtWidgets.QStackedWidget(host)
    workspace_stack.addWidget(normal_workspace)
    workspace_stack.addWidget(analysis_page)
    header = Header(SimpleNamespace(current_user=SimpleNamespace(can_edit=True)))
    shell = SimpleNamespace(
        _analysis_active=False, _guard_leave=lambda: True,
        assessment_page=None, assessment_domain_id=None, assessment_site_id=None,
        workspace_stack=workspace_stack, normal_workspace=normal_workspace,
        analysis_page=analysis_page, page_stack=page_stack, tree=tree, header=header,
        selected_block_id=None, selected_assessment_area_id=None,
        selected_contour_event_id=None, block_page=SimpleNamespace(current_block=None),
        area_page=None, contour_page=None,
    )
    shell._update_add = lambda: None
    shell._restore_archive_context = lambda: header.set_archive_context(False)
    shell._set_analysis_mode = lambda active: MainWindow._set_analysis_mode(shell, active)

    host.show(); workspace_stack.show(); app.processEvents()
    assert workspace_stack.currentWidget() is normal_workspace
    assert page_stack.currentWidget() is normal_page
    shell._guard_leave = lambda: False
    header.analysis_button.setChecked(True)
    assert not MainWindow._open_analysis(shell)
    assert not header.analysis_button.isChecked()
    shell._guard_leave = lambda: True
    shell.assessment_page = normal_page
    shell.assessment_domain_id = 7
    shell.assessment_site_id = 3
    def guarded_leave():
        shell.assessment_page = None
        shell.assessment_domain_id = None
        shell.assessment_site_id = None
        return True
    shell._guard_leave = guarded_leave
    assert MainWindow._open_analysis(shell)
    assert shell.assessment_page is normal_page
    assert (shell.assessment_domain_id, shell.assessment_site_id) == (7, 3)
    assert MainWindow._open_analysis(shell)
    shell.assessment_page = None
    shell._guard_leave = lambda: True
    counts = (workspace_stack.count(), page_stack.count())
    for _ in range(12):
        assert MainWindow._open_analysis(shell)
        app.processEvents()
        assert workspace_stack.currentWidget() is analysis_page
        assert header.analysis_button.isChecked()
        assert MainWindow._open_analysis(shell)
        app.processEvents()
        assert workspace_stack.currentWidget() is normal_workspace
        assert not header.analysis_button.isChecked()
        assert page_stack.currentWidget() is normal_page
    assert (workspace_stack.count(), page_stack.count()) == counts
    assert analysis_page.refresh_count == 13
    host.close(); header.close()
