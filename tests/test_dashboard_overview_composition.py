from pathlib import Path
from types import SimpleNamespace

import pytest


def source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_project_and_domain_dashboards_are_single_non_scrolling_overviews():
    project = source("ui/pages/dashboards/site_dashboard.py")
    domain = source("ui/pages/dashboards/domain_dashboard.py")

    for page in (project, domain):
        assert "QTabWidget" not in page
        assert "QScrollArea" not in page
        assert "QTableWidget" not in page
        assert "DashboardPlanCard" in page
        assert "MetricCard" in page
        assert "DashboardEntityHeader" in page
        assert "DashboardRecentActivityCard" in page
        assert "CompactSummaryList" in page
        assert '"Attention required",\n            visible_rows=4' in page
        assert "fill_available=True" in page
        assert "AssessmentTrendCard" in page
        assert "self.trend_card.set_rows(self.snapshot.trend_rows)" in page
        assert 'setObjectName("DashboardPage")' in page
        assert "workspace.setColumnStretch(0, 1)" in page
        assert "workspace.setColumnStretch(1, 1)" in page
        assert "workspace.setRowMinimumHeight(0, 405)" in page
        assert "workspace.setRowStretch(0, 1)" in page
        assert "root.addLayout(workspace, 1)" in page
        assert "self.result_card.setMinimumHeight(210)" in page
        assert "self.result_card.setMaximumHeight(225)" in page
        assert "self.attention_card.setMinimumHeight(160)" in page

    assert 'DashboardEntityHeader(name, "Project overview")' in project
    assert "ProjectLinesCard" in project
    assert 'primary_action_label="Project Lines"' in project
    assert '"Domain summary"' in project
    assert "visible_rows=3" in project
    assert "show_go_to=True" in project
    assert "AssessmentProgressCard" not in project
    assert "lines_card.add_header_action(\"Add\")" in project
    assert "assessment_area_requested = Signal(str, int)" in project
    assert "self._filter_domain" in project and 'set_filter("domain"' in project
    assert 'action_label = tr("Update lines")' in project

    assert 'DashboardEntityHeader(name or domain.name, "Domain overview")' in domain
    assert 'CompactSummaryList("Elevation intervals", visible_rows=3)' in domain
    assert '"Latest assessments", visible_rows=3, show_go_to=True' in domain
    assert "BlastActivityCard" not in domain
    assert 'primary_action_label="Import"' in domain
    assert 'secondary_action_label="Draw geometry"' in domain
    assert 'add_header_action("Clear")' not in domain
    assert 'set_filter("interval"' in domain and 'set_filter("area"' in domain


def test_dashboard_plan_has_one_line_header_auto_fit_and_wheel_zoom():
    plan = source("ui/pages/dashboards/plan_overview.py")
    assert "FRAME_FACTOR = 1.5" in plan
    assert "class DashboardGraphicsView" in plan
    assert "clear_filter_requested = Signal()" in plan
    assert "Qt.Key.Key_Escape" in plan
    assert "self.itemAt(event.position().toPoint()) is None" in plan
    assert "def wheelEvent" in plan
    assert "self.scale(factor, factor)" in plan
    assert "AnchorUnderMouse" in plan
    assert "reset_zoom_state" in plan
    assert "def showEvent" in plan
    assert "def resizeEvent" in plan
    assert "viewport.width() < 50 or viewport.height() < 50" in plan
    assert "QTimer.singleShot(0, self._fit_initial_view)" in plan
    assert "self.view.setMinimumHeight(320)" in plan
    assert "self.setMinimumHeight(405)" in plan
    assert "self.setMaximumHeight(455)" in plan
    assert "self.header.addWidget(self.lines)" in plan
    assert "self.header.addWidget(self.center_button)" in plan
    assert "self.layout.addLayout(self.controls)" not in plan
    assert "QSizePolicy.Policy.Expanding" in plan
    assert "return QSize(600, 430)" in plan
    assert "def set_filter" in plan and "def clear_filter" in plan
    assert 'kind == "domain"' in plan
    assert 'kind == "interval"' in plan
    assert 'kind == "area"' in plan
    assert 'QColor("#aeb8c5")' in plan
    assert 'getattr(self.snapshot, "assessment_geometries", ())' in plan
    assert "production_geometries" not in plan
    assert "contour_geometries" not in plan


def test_dashboard_projection_uses_only_stored_assessment_results():
    repository = source("repositories/dashboard_repository.py")
    assert 'current_completed = status == "completed"' in repository
    assert "quadrant=row.quadrant if row else None" in repository
    assert "dai=row.dai if row else None" in repository
    assert "fci=row.fci if row else None" in repository
    assert "def assessment_geometries(self)" in repository
    assert "class TrendRow" in repository
    assert "trend_history" in repository
    assert 'a.AssessmentAreaEvaluationRevision.status == "completed"' in repository
    assert "revision.design_achievement_index" in repository
    assert "revision.face_condition_index" in repository
    assert "calculate_revision" not in repository


def test_dashboard_result_palette_matches_full_assessment_matrix():
    from ui.assessment_result_presentation import ASSESSMENT_RESULT_PRESENTATIONS

    expected = {
        "geometry_achieved_condition_insufficient": "#f6df72",
        "good_results": "#8bd17c",
        "unacceptable": "#ef7770",
        "condition_good_geometry_unacceptable": "#f2b764",
    }
    assert {
        key: value.color for key, value in ASSESSMENT_RESULT_PRESENTATIONS.items()
    } == expected

    editor = source("ui/editors/assessment_evaluation_editor.py")
    for colour in expected.values():
        assert colour in editor


def test_dashboard_internal_lists_have_consistent_bordered_rows_and_actions():
    widgets = source("ui/pages/dashboards/widgets.py")
    theme = source("ui/theme.py")
    dark_theme = source("ui/application_theme.py")
    assert "show_go_to: bool = False" in widgets
    assert "fill_available: bool = False" in widgets
    assert "go_to_requested = Signal(str)" in widgets
    assert "class SummaryRowWidget" in widgets
    assert 'OverviewLinkButton(tr("Go to ›"))' in widgets
    assert "WA_TransparentForMouseEvents" in widgets
    assert "def clear_selection" in widgets
    assert "ProjectLinesDatasetRow" in widgets
    assert 'holder.setObjectName("DashboardSummaryRow")' in widgets
    assert 'self.setObjectName("DashboardCard")' in widgets
    assert 'self.setObjectName("DashboardMetricCard")' in widgets
    assert 'self.setObjectName("DashboardHeaderCard")' in widgets
    assert "QFrame#DashboardSummaryRow, QWidget#ProjectLinesDatasetRow, QWidget#StandardRow" in theme
    assert "QFrame#DashboardSummaryRow, QWidget#ProjectLinesDatasetRow, QWidget#StandardRow" in dark_theme
    assert "QFrame#DashboardCard" in theme
    assert "QFrame#DashboardMetricCard" in theme
    assert "QFrame#DashboardHeaderCard" in theme
    assert "ScrollBarAsNeeded" in widgets
    assert "ScrollBarAlwaysOff" in widgets
    assert "self.list.setMaximumHeight(16777215)" in widgets
    assert "self.layout.addWidget(self.list, 1)" in widgets
    assert "self.row_height * self.visible_rows + 4" not in widgets


def test_donut_trends_and_activity_cards_are_compact_without_new_engineering_metrics():
    widgets = source("ui/pages/dashboards/widgets.py")
    charts = source("ui/pages/dashboards/charts.py")
    repository = source("repositories/dashboard_repository.py")
    application_theme = source("ui/application_theme.py")
    assert "self.setMinimumHeight(150 if kind == \"donut\" else 92)" in charts
    assert "shadow.setWidth(width + 4)" in charts
    assert "center_font.setBold(True)" in charts
    assert "Qt.TextFlag.TextWordWrap" in charts
    assert "class IndexTrendChart" in charts
    assert "class AssessmentTrendCard" in charts
    assert 'super().__init__("DAI / FCI over time", parent)' in charts
    assert 'Daily average · all completed assessments' not in charts
    assert 'IndexTrendChart("DAI", "dai")' in charts
    assert 'IndexTrendChart("FCI", "fci")' in charts
    assert "grouped[when].append(float(value))" in charts
    assert "sum(values) / len(values)" in charts
    assert 'holder.setObjectName("DashboardActivityRow")' in widgets
    assert "QWidget#DashboardActivityRow" in application_theme
    assert "border-bottom: 1px solid #eef1f5" in application_theme
    assert "META_WIDTH = 190" in widgets
    assert "meta.setFixedWidth(self.META_WIDTH)" in widgets
    assert "layout.setContentsMargins(2, 0, 12, 0)" in widgets
    assert "hasattr(entry, \"changed_at\")" in widgets
    assert "ActivityRow" in repository
    assert "_audit_actor_maps" in repository
    assert "revision_actor.get(evaluation.logical_id" in repository
    assert '"Block"' in repository
    assert '"Contour blast"' in repository
    assert '"Assessment Area"' in repository
    assert "risk_score" not in widgets.lower()
    assert "risk_score" not in charts.lower()
    assert "risk_score" not in repository.lower()


def test_operational_edit_and_global_report_actions_use_existing_icons():
    entity_widgets = source("ui/pages/entity_overview_widgets.py")
    header = source("ui/header.py")
    assert 'self.edit_button.setIcon(ui_icon("edit", "blue"))' in entity_widgets
    assert 'self.report_button.setIcon(ui_icon("report","blue"))' in header


def test_attention_uses_existing_assessment_result_severity_not_new_scoring():
    project = source("ui/pages/dashboards/site_dashboard.py")
    domain = source("ui/pages/dashboards/domain_dashboard.py")
    for page in (project, domain):
        assert "presentation.requires_attention" in page
        assert "presentation.severity" in page
        assert "assessment_result_presentation(area.quadrant)" in page
        assert "risk_score" not in page.lower()


def test_plan_filter_dims_non_matches_and_restores_original_quadrant_colour():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    gui = pytest.importorskip("PySide6.QtGui", exc_type=ImportError)
    from ui.assessment_result_presentation import assessment_result_presentation
    from ui.pages.dashboards.plan_overview import DashboardPlanOverviewWidget

    app = widgets.QApplication.instance() or widgets.QApplication([])
    north = SimpleNamespace(
        entity_id="AA-001",
        points=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
        quadrant="good_results",
        name="Area 1",
        domain_name="North",
        interval="600–630",
        dai=0.75,
        fci=0.80,
    )
    south = SimpleNamespace(
        entity_id="AA-002",
        points=((20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0)),
        quadrant="unacceptable",
        name="Area 2",
        domain_name="South",
        interval="570–600",
        dai=0.30,
        fci=0.40,
    )
    snapshot = SimpleNamespace(
        domain_geometries=(),
        project_lines=(),
        assessment_geometries=(north, south),
    )
    plan = DashboardPlanOverviewWidget(snapshot)
    base = plan._items_rect(plan._assessment_items)
    focus = plan.focus_rect()
    assert focus.width() == pytest.approx(base.width() * 1.5)

    plan.set_filter("domain", "North")
    north_item, south_item = plan._assessment_items
    assert north_item.pen().color() == gui.QColor(
        assessment_result_presentation("good_results").color
    )
    assert south_item.pen().widthF() == pytest.approx(1.0)

    plan.clear_filter()
    assert south_item.pen().color() == gui.QColor(
        assessment_result_presentation("unacceptable").color
    )
    assert south_item.pen().widthF() == pytest.approx(2.8)

    plan.close()
    app.processEvents()
