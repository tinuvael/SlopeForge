from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGraphicsPathItem

from application.services.assessment_areas import AssessmentAreaService
from application.services.project_lines import ProjectLinesDatasetService
from application.state.assessment_domain_state import AssessmentDomainState
from domain.assessment.geometry import ProjectLineSpan, SpatialPoint, StraightConnector, extract_project_line_span
from domain.geometry.types import PlanPoint, PlanPolygon
from tests.assessment_boundary_fixtures import boundary_from_polygon
from tests.geometry_test_files import test_line as _line
from ui.editors.assessment_geometry_editor import (ASSESSMENT_CONTEXT_ROLE, PROJECT_LINE_ROLE, SNAP_MARKER_ROLE,
                                                    AssessmentGeometryEditorWidget)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def state():
    result = AssessmentDomainState()
    ProjectLinesDatasetService(result).create_dataset(
        name="project",
        source_file_name="project.dxf",
        lines=[
            _line("A", [(0, 10, 110), (5, 12, 115), (10, 10, 120)], order=0),
            _line("B", [(0, 0, 90), (5, -2, 95), (10, 0, 100)], order=1),
        ],
    )
    return result


def committer(state):
    def commit(**values):
        service = AssessmentAreaService(state)
        area_id = values.pop("assessment_area_id")
        if area_id:
            area = next(item for item in state.assessment_areas if item.id == area_id)
            service.revise_area(area, boundary=values["boundary"])
        else:
            area = service.create_area(name=values["name"], assessment_date=values["assessment_date"],
                                       boundary=values["boundary"])
        return SimpleNamespace(area_id=area.id)
    return commit


def test_editor_starts_idle_and_navigation_does_not_commit(state, app):
    editor = AssessmentGeometryEditorWidget(state, committer(state))
    assert editor.workflow_state == "IDLE"
    assert editor._segments == [] and editor._first_point is None
    assert editor.plan_view.dragMode() == editor.plan_view.DragMode.ScrollHandDrag
    assert not [item for item in editor.scene.items() if item.data(ASSESSMENT_CONTEXT_ROLE)]


@pytest.mark.parametrize(
    ("theme", "expected_color"),
    (("light", "#526273"), ("dark", "#c5ced8")),
)
def test_existing_area_context_is_visible_noninteractive_and_theme_aware(
    state, app, theme, expected_color
):
    context = SimpleNamespace(
        assessment_area_id="AA-OTHER", domain_id=2,
        ring=(PlanPoint(1000, 1000), PlanPoint(1010, 1000),
              PlanPoint(1010, 1010), PlanPoint(1000, 1000)),
    )
    app.setProperty("slopeforgeTheme", theme)
    editor = AssessmentGeometryEditorWidget(state, committer(state))
    snap_before = editor._snap(1, 10.4)
    editor.set_existing_area_context((context,))

    items = [item for item in editor.scene.items() if item.data(ASSESSMENT_CONTEXT_ROLE)]
    assert len(items) == 1 and isinstance(items[0], QGraphicsPathItem)
    item = items[0]
    assert not item.data(PROJECT_LINE_ROLE)
    assert item.zValue() == 15
    assert item.brush().style() == Qt.BrushStyle.NoBrush
    assert item.pen().style() == Qt.PenStyle.DashLine
    assert item.pen().isCosmetic() and item.pen().widthF() == 2.2
    assert item.pen().color() == QColor(expected_color)
    assert not (item.flags() & QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable)
    assert editor._snap(1, 10.4) == snap_before
    inactive_width = item.pen().widthF()
    inactive_z = item.zValue()
    editor.start_new_area()
    editor._drawing_click(1, 10.4)
    editor._drawing_click(9, 10.4)
    active = max(editor.scene.items(), key=lambda candidate: candidate.zValue())
    assert active.pen().style() == Qt.PenStyle.SolidLine
    assert active.pen().widthF() > inactive_width
    assert active.zValue() > inactive_z
    app.setProperty("slopeforgeTheme", "light")


def test_creation_page_does_not_auto_start_drawing():
    from pathlib import Path
    source = Path("ui/pages/assessment_area_creation_page.py").read_text(encoding="utf-8")
    constructor = source[source.index("    def __init__"):source.index("    def _start_drawing")]
    assert "self._start_drawing()" not in constructor
    assert '"Click near a Project Line to snap."' in source
    assert '"Close the boundary when finished."' in source


def test_trace_preview_commit_jump_and_new_active_source(state, app):
    editor = AssessmentGeometryEditorWidget(state, committer(state)); editor.start_new_area()
    editor._drawing_click(1, 10.4)
    editor._drawing_move(9, 10.4)
    candidate = editor._candidate.anchor
    line_a = state.active_dataset().lines[0]
    preview = editor._segment_points(extract_project_line_span(line_a, editor._last_anchor, candidate))
    assert len(preview) == 3 and preview[1].y == 12
    assert [item for item in editor.scene.items() if item.data(SNAP_MARKER_ROLE)]
    assert len([item for item in editor.scene.items() if item.data(PROJECT_LINE_ROLE)]) == 2
    editor._drawing_click(9, 10.4)
    assert isinstance(editor._segments[-1], ProjectLineSpan)


def test_same_anchor_hover_preview_is_ignored(state, app):
    editor=AssessmentGeometryEditorWidget(state,committer(state)); editor.start_new_area()
    editor._drawing_click(1,10.4)
    editor._drawing_move(1,10.4)
    assert editor._segments==[] and editor._last_anchor is not None

    editor._drawing_move(9, .4)
    assert editor._last_anchor.source_line_id == "A"
    editor._drawing_click(9, .4)
    assert isinstance(editor._segments[-1], StraightConnector)
    assert editor._last_anchor.source_line_id == "B"
    editor._drawing_click(1, .4)
    assert isinstance(editor._segments[-1], ProjectLineSpan)
    assert editor._segments[-1].frozen_trace_xyz[1].y == -2


def test_draw_close_save_emits_and_does_not_restart(state, app):
    editor = AssessmentGeometryEditorWidget(state, committer(state)); emitted=[]
    editor.area_created.connect(emitted.append); editor.start_new_area()
    editor._drawing_click(0,10); editor._drawing_click(10,10)
    editor._drawing_click(10,0); editor._drawing_click(0,0)
    before_close = len(editor._segments)
    editor.finish_polygon()
    assert editor.workflow_state == "CLOSED"
    assert len(editor._segments) == before_close + 1
    assert isinstance(editor._segments[-1], StraightConnector)
    saved_date=date(2026,8,13)
    editor.confirm_boundaries(name="West wall",assessment_date=saved_date)
    assert len(state.assessment_areas) == 1 and emitted == [state.assessment_areas[0].id]
    assert state.assessment_areas[0].name=="West wall" and state.assessment_areas[0].assessment_date==saved_date
    assert editor.workflow_state == "IDLE" and not editor.has_active_workflow()


def test_already_closed_boundary_adds_no_duplicate_connector(state, app):
    editor = AssessmentGeometryEditorWidget(state, committer(state)); editor.start_new_area()
    for point in ((20,20),(30,20),(30,30),(20,30),(20,20)):
        editor._drawing_click(*point)
    segment_count = len(editor._segments)
    editor.finish_polygon()
    assert editor.workflow_state == "CLOSED"
    assert len(editor._segments) == segment_count


def test_already_closed_invalid_boundary_stays_drawing(monkeypatch, state, app):
    warnings=[]
    monkeypatch.setattr("ui.editors.assessment_geometry_editor.QMessageBox.warning",
                        lambda *args: warnings.append(args))
    editor = AssessmentGeometryEditorWidget(state, committer(state)); editor.start_new_area()
    for point in ((20,20),(30,30),(20,30),(30,20),(20,20)):
        editor._drawing_click(*point)
    segment_count = len(editor._segments)
    editor.finish_polygon()
    assert editor.workflow_state == "DRAWING"
    assert len(editor._segments) == segment_count
    assert warnings


def test_enter_in_closed_state_does_not_persist(state, app):
    commits=[]
    editor=AssessmentGeometryEditorWidget(state,lambda **values: commits.append(values))
    editor.start_new_area()
    for point in ((20,20),(30,20),(30,30),(20,30)):
        editor._drawing_click(*point)
    editor.finish_polygon()
    assert editor.workflow_state == "CLOSED"
    editor._workflow_key("enter")
    assert commits == [] and editor.workflow_state == "CLOSED"


def test_closed_boundary_exposes_exact_draft_without_persisting(state, app):
    commits = []
    editor = AssessmentGeometryEditorWidget(state, lambda **values: commits.append(values))
    assert editor.closed_boundary() is None
    editor.start_new_area()
    for point in ((20, 20), (30, 20), (30, 30), (20, 30)):
        editor._drawing_click(*point)
    editor.finish_polygon()
    first = editor.closed_boundary()
    assert first is not None and commits == []
    assert editor.closed_boundary().to_dict() == first.to_dict()
    assert editor.workflow_state == "CLOSED"


def test_undo_closed_reopens_and_cancel_clears_draft(state, app):
    editor = AssessmentGeometryEditorWidget(state, committer(state)); editor.start_new_area()
    for point in ((0,10),(10,10),(10,0),(0,0)): editor._drawing_click(*point)
    editor.finish_polygon(); editor.undo_vertex()
    assert editor.workflow_state == "DRAWING"
    editor.cancel_workflow()
    assert editor.workflow_state == "IDLE" and editor._segments == []


def test_edit_existing_boundary_creates_r2_without_changing_r1(state, app):
    service = AssessmentAreaService(state)
    shape = PlanPolygon((PlanPoint(0, 10), PlanPoint(10, 10), PlanPoint(10, 0),
                         PlanPoint(0, 0), PlanPoint(0, 10)))
    boundary = boundary_from_polygon(
        shape, dataset_id=state.active_dataset().id, line_id="A", minimum=110, maximum=120)
    area = service.create_area(name="West wall", assessment_date=date(2026, 8, 13), boundary=boundary)
    frozen_r1 = area.geometry_revisions[0].to_dict()

    editor = AssessmentGeometryEditorWidget(state, committer(state))
    editor.start_edit(area.id)
    assert editor.workflow_state == "DRAWING"
    editor.undo_vertex()
    editor._drawing_click(0, 1)
    editor.finish_polygon()
    editor.confirm_boundaries(name="ignored", assessment_date=date(2000, 1, 1))

    assert area.active_geometry_revision().revision_number == 2
    assert area.geometry_revisions[0].to_dict() == frozen_r1
    assert area.name == "West wall" and area.assessment_date == date(2026, 8, 13)

def test_close_preserves_first_snapped_anchor(state, app):
    editor=AssessmentGeometryEditorWidget(state,committer(state)); editor.start_new_area()
    editor._drawing_click(0,10); first=editor._first_anchor
    editor._drawing_click(10,10); editor._drawing_click(10,0); editor._drawing_click(0,0)
    editor.finish_polygon()
    assert editor._segments[-1].end_anchor==first


def test_page_owns_name_and_date_fields_and_grid_is_not_exposed():
    from pathlib import Path
    source=Path("ui/pages/assessment_area_creation_page.py").read_text(encoding="utf-8")
    assert "self.area_name" in source and "self.assessment_date" in source
    assert "name=self.area_name.text()" in source
    assert "self.grid" not in source
    assert 'tr("Grid")' not in source and '"Grid size"' not in source


def test_dense_parallel_lines_use_active_line_hysteresis(app):
    state=AssessmentDomainState()
    ProjectLinesDatasetService(state).create_dataset(
        name="dense",
        source_file_name="dense.dxf",
        lines=[
            _line("A", [(0,0,700.1234567),(5,1,704.8765432),(10,0,711.3337777)], order=0),
            _line("B", [(0,1,701.1234567),(5,2,705.8765432),(10,1,712.3337777)], order=1),
            _line("C", [(0,2,702.1234567),(5,3,706.8765432),(10,2,713.3337777)], order=2),
        ],
    )
    editor=AssessmentGeometryEditorWidget(state,committer(state)); editor.plan_view.scale(10,10); editor.start_new_area()
    editor._drawing_click(0,0); assert editor._last_anchor.source_line_id=="A"
    editor._drawing_move(5,1.51); assert editor._candidate.anchor.source_line_id=="A"
    active=state.active_dataset().lines[0]
    preview=extract_project_line_span(active,editor._last_anchor,editor._candidate.anchor)
    assert preview.frozen_trace_xyz[1].y==1
    editor._drawing_move(5,2); assert editor._candidate.anchor.source_line_id=="B"
    editor._drawing_click(5,2); assert isinstance(editor._segments[-1],StraightConnector)
    assert editor._last_anchor.source_line_id=="B"
    editor._drawing_click(10,1); assert isinstance(editor._segments[-1],ProjectLineSpan)


def test_closed_contour_active_trace_crosses_source_seam(app):
    state=AssessmentDomainState()
    ProjectLinesDatasetService(state).create_dataset(
        name="closed",
        source_file_name="closed.dxf",
        lines=[_line("C", [(0,0,100),(10,0,101),(10,10,102),(0,10,103),(0,0,100)], order=0)],
    )
    editor=AssessmentGeometryEditorWidget(state,committer(state)); editor.plan_view.scale(10,10); editor.start_new_area()
    editor._drawing_click(0,5)
    assert editor._last_anchor.source_segment_index==3
    editor._drawing_move(5,0)
    assert editor._candidate.anchor.source_line_id=="C" and editor._candidate.anchor.source_segment_index==0
    contour=state.active_dataset().lines[0]
    preview=extract_project_line_span(contour,editor._last_anchor,editor._candidate.anchor)
    assert preview.frozen_trace_xyz==(editor._last_anchor.frozen_point_xyz,
        SpatialPoint(0,0,100),editor._candidate.anchor.frozen_point_xyz)
    editor._drawing_click(5,0)
    assert isinstance(editor._segments[-1],ProjectLineSpan)
    assert editor._segments[-1].frozen_trace_xyz==preview.frozen_trace_xyz
