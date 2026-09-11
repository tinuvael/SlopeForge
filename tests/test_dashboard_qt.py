from datetime import date, datetime, timezone
from dataclasses import replace
from types import SimpleNamespace

import pytest

from application.services.project_lines import ProjectLinesDatasetService
from application.state.assessment_domain_state import AssessmentDomainState
from domain.project.project_lines import ProjectLinesDataset
from tests.geometry_test_files import test_line as _line

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel
    from repositories.dashboard_repository import (
        ActivityRow,
        AreaRow,
        BlastRow,
        DomainDashboardSnapshot,
        DomainSummary,
        MapGeometry,
        SiteDashboardSnapshot,
        _project_line_geometries,
    )
    from ui.pages.dashboards.charts import AssessmentTrendCard, CompactChart
    from ui.pages.dashboards.domain_dashboard import DomainDashboardPage
    from ui.pages.dashboards.site_dashboard import SiteDashboardPage
    from ui.pages.dashboards.plan_overview import (
        DashboardPlanCard,
        DashboardPlanOverviewWidget,
    )
    from ui.pages.dashboards.widgets import DashboardRecentActivityCard
except ImportError as exc:
    pytest.skip(f"Qt runtime unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def snapshot():
    domain = DomainSummary(7, "North", 1, 1, 1, 1, 0, .8, .7)
    return DomainDashboardSnapshot(
        domain,
        [AreaRow("AREA-42", "A", "10–20", date.today(), "completed", .8, .7, "unacceptable")],
        [
            BlastRow(99, "Production", "B1", "10", None, "planned"),
            BlastRow("EVENT-55", "Contour", "C1", "10", None, "—"),
        ],
        {"10–20": 1},
        {"unacceptable": 1},
        [],
        (MapGeometry("line", ((0, 0), (20, 0))),),
        (MapGeometry(99, ((0, 0), (10, 0), (10, 10), (0, 0))),),
        (MapGeometry("EVENT-55", ((4, 4), (8, 8))),),
        (
            MapGeometry(
                "AREA-42",
                ((1, 1), (5, 1), (5, 5), (1, 1)),
                "unacceptable",
                "A",
                "North",
                "10–20",
                .8,
                .7,
            ),
        ),
    )


def _stub_domain_version(monkeypatch):
    monkeypatch.setattr(
        "ui.pages.dashboards.domain_dashboard.DomainGeometryRepository.get_domain_version",
        lambda *_args: 0,
    )


def _stub_project_surfaces(monkeypatch):
    service = SimpleNamespace(current=lambda *_args: None)
    monkeypatch.setattr(
        "ui.pages.dashboards.site_dashboard.create_project_surface_dataset_service",
        lambda _context: service,
    )


def test_native_charts_construct_with_and_without_data(app):
    assert CompactChart({"North": 2}).data
    assert CompactChart({}).data == {}


def test_trends_group_same_date_and_activity_shows_entity_actor_and_time(app):
    rows = [
        AreaRow("A1", "1", "10–20", date(2026, 8, 1), "completed", .6, .5, "good_results"),
        AreaRow("A2", "2", "10–20", date(2026, 8, 1), "completed", .8, .7, "good_results"),
        AreaRow("A3", "3", "10–20", date(2026, 8, 3), "completed", 1.0, .9, "good_results"),
    ]
    trend = AssessmentTrendCard()
    trend.set_rows(rows)
    assert [when for when, _value in trend.dai.points] == [date(2026, 8, 1), date(2026, 8, 3)]
    assert trend.dai.points[0][1] == pytest.approx(.7)
    assert trend.dai.points[1][1] == pytest.approx(1.0)
    assert [when for when, _value in trend.fci.points] == [date(2026, 8, 1), date(2026, 8, 3)]
    assert trend.fci.points[0][1] == pytest.approx(.6)
    assert trend.fci.points[1][1] == pytest.approx(.9)

    recent = DashboardRecentActivityCard()
    recent.set_entries([
        ActivityRow("Block", "B23", "Updated", datetime(2026, 8, 20, 8, 24), "Engineer One")
    ])
    holder = recent.list.itemWidget(recent.list.item(0))
    texts = [label.text() for label in holder.findChildren(QLabel)]
    assert any("Block B23" in text and "Updated" in text for text in texts)
    assert any("Engineer One" in text and "20.08.2026 08:24" in text for text in texts)
    trend.close()
    recent.close()
    app.processEvents()


def test_project_domain_summary_filters_first_and_go_to_emits_real_domain_id(app, monkeypatch):
    snap = snapshot()
    site_snap = SiteDashboardSnapshot(3, [snap], None, [])
    monkeypatch.setattr(
        "ui.pages.dashboards.site_dashboard.DashboardRepository.site_snapshot",
        lambda *_: site_snap,
    )
    _stub_project_surfaces(monkeypatch)
    context = SimpleNamespace(
        session_factory=lambda: None,
        current_user=SimpleNamespace(can_edit=False, id=1),
    )
    page = SiteDashboardPage(context, 3, "North Pit")
    received = []
    page.domain_requested.connect(received.append)

    item = page.domain_summary.list.item(0)
    holder = page.domain_summary.list.itemWidget(item)
    holder.clicked.emit()
    assert page.plan_card.plan._filter_state == ("domain", "North")
    assert received == []
    assert page.trend_card.dai.points

    page.domain_summary.go_to_requested.emit("7")
    assert received == [7]
    page.close()
    app.processEvents()


def test_domain_interval_summary_filters_plan_without_virtual_navigation(app, monkeypatch):
    snap = snapshot()
    monkeypatch.setattr(
        "ui.pages.dashboards.domain_dashboard.DashboardRepository.domain_snapshot",
        lambda *_: snap,
    )
    _stub_domain_version(monkeypatch)
    context = SimpleNamespace(
        session_factory=lambda: None,
        current_user=SimpleNamespace(can_edit=False, id=1),
    )
    page = DomainDashboardPage(context, 7, "North")

    assert page.interval_summary.list.count() == 1
    holder = page.interval_summary.list.itemWidget(page.interval_summary.list.item(0))
    assert holder is not None
    assert "10–20 m" in " ".join(label.text() for label in holder.findChildren(QLabel))
    holder.clicked.emit()
    assert page.plan_card.plan._filter_state == ("interval", "10–20")
    assert page.trend_card.fci.points
    assert page.attention_card.fill_available is True
    assert not hasattr(page, "tabs")
    page.close()
    app.processEvents()


def test_domain_summary_lists_adapt_across_supported_window_sizes(app, monkeypatch):
    areas = [
        AreaRow(
            f"AREA-{index}", f"Area {index}", f"{index * 10}–{index * 10 + 10}",
            date(2026, 8, index + 1), "completed", .8, .7, "good_results",
        )
        for index in range(6)
    ]
    snap = replace(snapshot(), areas=areas)
    monkeypatch.setattr(
        "ui.pages.dashboards.domain_dashboard.DashboardRepository.domain_snapshot",
        lambda *_: snap,
    )
    _stub_domain_version(monkeypatch)
    context = SimpleNamespace(
        session_factory=lambda: None,
        current_user=SimpleNamespace(can_edit=False, id=1),
    )
    page = DomainDashboardPage(context, 7, "North")
    page.show()
    compact_baseline = page.interval_summary.row_height * 3 + 4
    observed_heights = []

    for width, height in ((1400, 900), (1920, 1080), (2560, 1440)):
        page.resize(width, height)
        app.processEvents()
        for card in (page.interval_summary, page.latest_assessments):
            assert card.list.geometry().bottom() <= card.contentsRect().bottom()
            assert card.list.height() >= card.row_height + 4
        observed_heights.append(page.interval_summary.list.height())

    assert observed_heights == sorted(observed_heights)
    assert observed_heights[-1] > compact_baseline
    assert page.interval_summary.list.verticalScrollBar().isVisible() is False
    assert page.latest_assessments.list.verticalScrollBar().isVisible() is False
    page.close()
    app.processEvents()


@pytest.mark.parametrize("can_edit", [False, True])
def test_dashboard_rename_headers_are_real_widgets_and_refresh(app, monkeypatch, can_edit):
    snap = snapshot()
    site_snap = SiteDashboardSnapshot(3, [snap], None, [])
    monkeypatch.setattr(
        "ui.pages.dashboards.domain_dashboard.DashboardRepository.domain_snapshot",
        lambda *_: snap,
    )
    monkeypatch.setattr(
        "ui.pages.dashboards.site_dashboard.DashboardRepository.site_snapshot",
        lambda *_: site_snap,
    )
    _stub_domain_version(monkeypatch)
    _stub_project_surfaces(monkeypatch)
    context = SimpleNamespace(
        session_factory=lambda: None,
        current_user=SimpleNamespace(can_edit=can_edit, id=1),
    )
    site = SiteDashboardPage(context, 3, "North Pit")
    domain = DomainDashboardPage(context, 7, "North")
    assert site.title_label.text() == "North Pit" and site.edit_button.isEnabled() is can_edit
    assert domain.title_label.text() == "North" and domain.edit_button.isEnabled() is can_edit
    site.apply_rename_result(3, "Central Pit")
    domain.apply_rename_result(7, "East Wall", domain.expected_version + 1)
    assert site.title_label.text() == "Central Pit"
    assert domain.title_label.text() == "East Wall"
    site.close()
    domain.close()
    app.processEvents()


def test_read_only_map_constructs_empty_and_populated(app):
    empty = DomainDashboardSnapshot(DomainSummary(7, "North"))
    empty_map = DashboardPlanOverviewWidget(empty)
    assert not empty_map.scene.items()

    plan = DashboardPlanOverviewWidget(snapshot())
    assert plan.scene.items()
    assert len(plan._assessment_items) == 1
    assert len(plan._project_items) == 1
    assert not hasattr(plan, "draw_button")
    assert not hasattr(plan, "edit_button")
    assert not hasattr(plan, "confirm_button")

    card = DashboardPlanCard(snapshot(), primary_action_label="Import geometry")
    assert card.center_button.text() == "Center"
    assert card.lines.text() == "Project Lines"
    assert card.primary_action.text() == "Import geometry"
    assert card.plan.view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert card.plan.view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert card.plan._initial_fit_pending is True
    card.plan.resize(600, 420)
    card.plan.show()
    app.processEvents()
    assert card.plan.view.transform().m11() > 0
    card.set_filter("area", "AREA-42")
    assert card.plan._filter_state == ("area", "AREA-42")
    card.clear_filter()
    assert card.plan._filter_state is None
    empty_map.close()
    plan.close()
    card.close()
    app.processEvents()


def test_project_line_parts_survive_serialization_projection_and_render(app):
    service = ProjectLinesDatasetService(AssessmentDomainState())
    imported = service.create_dataset(
        name="Separated parts",
        source_file_name="separated_parts.dxf",
        lines=[
            _line("WEST", [(0,0,0),(10,0,0),(10,10,0)], order=0),
            _line("EAST", [(1000,1000,0),(1010,1000,0),(1010,1010,0)], order=1),
        ],
        imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    reloaded = ProjectLinesDataset.from_dict(imported.to_dict())
    persisted_row = SimpleNamespace(lines_json=[line.to_dict() for line in reloaded.lines])
    projected = _project_line_geometries(persisted_row)

    assert [geometry.entity_id for geometry in projected] == ["WEST", "EAST"]
    assert [geometry.points for geometry in projected] == [
        ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)),
        ((1000.0, 1000.0), (1010.0, 1000.0), (1010.0, 1010.0)),
    ]

    plan = DashboardPlanOverviewWidget(
        DomainDashboardSnapshot(DomainSummary(7, "North"), project_lines=projected)
    )
    assert len(plan._project_items) == 2
    assert [item.path().elementCount() for item in plan._project_items] == [3, 3]
    for item in plan._project_items:
        path = item.path()
        first = path.elementAt(0)
        last = path.elementAt(path.elementCount() - 1)
        assert (first.x, first.y) != (last.x, last.y)
    plan.close()
    app.processEvents()
