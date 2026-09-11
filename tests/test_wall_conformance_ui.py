from __future__ import annotations

import os
from dataclasses import replace
from math import hypot
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QTableWidget
from PySide6.QtCore import QEvent, QPoint, QRectF, Qt
from PySide6.QtTest import QTest

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from ui.pages import wall_conformance_tab as module
from application.services.wall_conformance import (
    DesignSemanticInspection, SurfaceAttributeValueCount,
)
from domain.wall_conformance import AlignmentPlacementDiagnostic, SurfaceRoleMapping, WallAlignment
from domain.wall_conformance.models import (
    DesignSection,
    DesignSectionElement,
    DesignVariant,
    RepresentativeElement,
    SectionPoint,
    SectionSegment,
    TransverseProfile,
    WallAlignmentSample,
)
from ui.dialogs.design_surface_semantics_dialog import DesignSurfaceSemanticsDialog


_APP = None


def test_profile_plot_uses_equal_metric_scale() -> None:
    bounds = module.WallProfilePlot._equal_aspect_bounds(
        QRectF(0.0, 0.0, 400.0, 200.0), 0.0, 10.0, 0.0, 10.0
    )
    u_min, u_max, z_min, z_max = bounds

    assert 400.0 / (u_max - u_min) == pytest.approx(
        200.0 / (z_max - z_min)
    )


def test_profile_plot_keeps_equal_scale_but_allocates_spare_u_range_rightward() -> None:
    u_min, u_max, z_min, z_max = module.WallProfilePlot._equal_aspect_bounds(
        QRectF(0.0, 0.0, 400.0, 200.0), 0.0, 10.0, 0.0, 10.0
    )

    assert 400.0 / (u_max - u_min) == pytest.approx(200.0 / (z_max - z_min))
    assert abs(u_min) < abs(u_max - 10.0)


def test_selected_profile_plot_prepends_display_only_upstream_context() -> None:
    _app()
    berm_start = SectionPoint(-2.0, 20.0, -2.0, 0.0)
    crest = SectionPoint(0.0, 20.0, 0.0, 0.0)
    toe = SectionPoint(4.0, 10.0, 4.0, 0.0)
    face = SectionSegment(crest, toe, 2, "face")
    profile = TransverseProfile(
        WallAlignmentSample(
            0.0, SurfaceVertex(0.0, 0.0, 20.0), (1.0, 0.0), (0.0, 1.0)
        ),
        (face,),
        (),
        DesignSection(
            (DesignSectionElement("face", crest, toe, (2,)),),
            DesignSectionElement("berm", berm_start, crest, (1,)),
        ),
        assessment_u_interval=(0.0, 4.0),
    )
    plot = module.WallProfilePlot()
    plot.set_profile(profile)

    design, actual = plot._geometry()

    assert [segment.semantic_role for segment in design] == ["berm", "face"]
    assert [(segment.start.u, segment.end.u) for segment in design] == [
        (-2.0, 0.0),
        (0.0, 4.0),
    ]
    assert actual == ()
    plot.deleteLater()


def _ruler_plot():
    _app()
    crest = SectionPoint(0.0, 20.0, 0.0, 0.0)
    toe = SectionPoint(10.0, 10.0, 10.0, 0.0)
    profile = TransverseProfile(
        WallAlignmentSample(
            0.0, SurfaceVertex(0.0, 0.0, 20.0), (1.0, 0.0), (0.0, 1.0)
        ),
        (SectionSegment(crest, toe, 1, "face"),),
        (),
        DesignSection((DesignSectionElement("face", crest, toe, (1,)),)),
    )
    plot = module.WallProfilePlot()
    plot.resize(640, 420)
    plot.set_profile(profile)
    return plot


def test_manual_ruler_transform_round_trip_and_equal_metric_scale() -> None:
    plot = _ruler_plot()
    bounds = plot._plot_data_bounds()
    assert bounds is not None
    point = plot._map_data_to_widget(3.25, 16.75, bounds)
    assert plot._map_widget_to_data(point, bounds) == pytest.approx((3.25, 16.75))
    plot_rect = plot.plot_rect()
    assert plot_rect.width() / (bounds[1] - bounds[0]) == pytest.approx(
        plot_rect.height() / (bounds[3] - bounds[2])
    )
    plot.deleteLater()


def test_manual_ruler_click_sequence_is_overlay_only_and_resize_stable() -> None:
    plot = _ruler_plot()
    automatic_measurement = object()
    plot.set_profile(plot.profile, automatic_measurement)
    original_points = plot._points()
    original_bounds = plot._plot_data_bounds()
    plot.set_measure_mode(True)
    first = plot._map_data_to_widget(2.0, 18.0)
    second = plot._map_data_to_widget(8.0, 12.0)
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=QPoint(round(first.x()), round(first.y())))
    assert plot.measure_point_a == pytest.approx((2.0, 18.0), abs=0.1)
    assert plot.measure_point_b is None
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=QPoint(round(second.x()), round(second.y())))
    assert plot.measure_point_b == pytest.approx((8.0, 12.0), abs=0.1)
    third = plot._map_data_to_widget(4.0, 16.0)
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=QPoint(round(third.x()), round(third.y())))
    assert plot.measure_point_a == pytest.approx((4.0, 16.0), abs=0.1)
    assert plot.measure_point_b is None
    assert plot._points() == original_points
    assert plot._plot_data_bounds() == original_bounds
    assert plot.measurement is automatic_measurement
    stored = plot.measure_point_a
    plot.resize(900, 520)
    plot.repaint()
    assert plot.measure_point_a == stored
    plot.deleteLater()


def test_manual_ruler_math_clear_and_non_measure_clicks() -> None:
    plot = _ruler_plot()
    assert plot._manual_measurement_values((3.0, 5.0), (-1.0, 8.0)) == pytest.approx(
        (-4.0, 3.0, 5.0, 36.8698976458)
    )
    assert plot._manual_measurement_values((0.0, 0.0), (4.0, 0.0))[3] == pytest.approx(0.0)
    assert plot._manual_measurement_values((0.0, 0.0), (0.0, -4.0))[3] == pytest.approx(90.0)
    inside = plot.plot_rect().center()
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=inside.toPoint())
    assert plot.measure_point_a is None
    plot.set_measure_mode(True)
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert plot.measure_point_a is None
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=inside.toPoint())
    assert plot.measure_point_a is not None
    plot.set_profile(plot.profile)
    assert plot.measure_point_a is None and plot.measure_point_b is None
    plot.set_measure_mode(True)
    QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=inside.toPoint())
    plot.clear_measurement()
    assert plot.measure_point_a is None and plot.measure_point_b is None
    plot.deleteLater()


def _representative_element(role, start_u, end_u, start_dz, end_dz):
    width = abs(end_u - start_u)
    height = abs(end_dz - start_dz)
    angle = 65.0 if role == "face" else None
    return RepresentativeElement(
        role, start_u, start_dz, end_u, end_dz, width, width, (width, width),
        height, (height, height), angle, (angle, angle) if angle is not None else None,
    )


def test_representative_plot_and_schedule_show_context_separately(monkeypatch) -> None:
    _app()
    context = _representative_element("road", -12.0, 0.0, 1.0, 0.0)
    face = _representative_element("face", 0.0, 5.0, 0.0, -10.0)
    variant = DesignVariant("FACE", (0,), (face,), context)
    profile = TransverseProfile(
        WallAlignmentSample(
            0.0, SurfaceVertex(0.0, 0.0, 20.0), (1.0, 0.0), (0.0, 1.0)
        ),
        (), (), DesignSection(()), assessment_u_interval=(0.0, 5.0),
    )
    profile_set = SimpleNamespace(profiles=(profile,), design_variants=(variant,))
    plot = module.WallProfilePlot()
    plot.set_overview(profile_set)

    design, actual = plot._geometry()

    assert [segment.semantic_role for segment in design] == ["road", "face"]
    assert [(segment.start.u, segment.end.u) for segment in design] == [
        (-12.0, 0.0),
        (0.0, 5.0),
    ]
    assert actual == ()

    tab = _tab(monkeypatch)
    tab.result = SimpleNamespace(profile_sections=profile_set)
    tab.variant_selector.addItem(tab._variant_context_label(variant))
    tab._show_representative_details()
    assert tab.variant_selector.currentText() == "Road context"
    assert tab.details_title.text() == "Representative Design"
    detail_text = [label.text() for label in tab.details_content.findChildren(module.QLabel)]
    assert "Upstream Road" in detail_text
    assert "W 12.0 m" in detail_text
    assert "H / A" in detail_text
    assert any("10.0 m · 65.0°" in text for text in detail_text)
    assert "Lower toe" in detail_text
    assert tab.details_metadata.toolTip() == "FACE"
    tab.deleteLater()
    plot.deleteLater()


def test_overview_actual_is_precisely_clipped_to_each_physical_measurement() -> None:
    _app()
    context = _representative_element("berm", -2.0, 2.0, 0.0, 0.0)
    face = _representative_element("face", 2.0, 10.0, 0.0, -10.0)
    variant = DesignVariant("FACE", (0,), (face,), context)
    origin = SurfaceVertex(0.0, 0.0, 20.0)
    profile = TransverseProfile(
        WallAlignmentSample(0.0, origin, (1.0, 0.0), (0.0, 1.0)),
        (),
        (
            SectionSegment(SectionPoint(-1.0, 21.0, -1.0, 0.0), SectionPoint(3.0, 20.0, 3.0, 0.0), 1),
            SectionSegment(SectionPoint(3.0, 20.0, 3.0, 0.0), SectionPoint(6.0, 20.0, 6.0, 0.0), 2),
            SectionSegment(SectionPoint(6.0, 20.0, 6.0, 0.0), SectionPoint(12.0, 10.0, 12.0, 0.0), 3),
        ),
        DesignSection(()), assessment_u_interval=(0.0, 12.0),
    )
    landmarks = SimpleNamespace(
        upper_berm_start=SimpleNamespace(
            point=SectionPoint(2.0, 20.25, 2.0, 0.0),
            detection=SimpleNamespace(reliable=True),
        ),
        upper_crest=SimpleNamespace(
            point=None, detection=SimpleNamespace(reliable=False),
        ),
        lower_toe=SimpleNamespace(
            point=SectionPoint(10.0, 13.3333333333, 10.0, 0.0),
            detection=SimpleNamespace(reliable=True),
        ),
    )
    profile_set = SimpleNamespace(profiles=(profile,), design_variants=(variant,))
    plot = module.WallProfilePlot()
    plot.set_overview(profile_set, measurements=(SimpleNamespace(actual_landmarks=landmarks),))

    _design, actual = plot._geometry()
    rendered, display_context = plot._actual_render_layers()

    assert [(segment.start.u, segment.end.u) for segment in actual] == [
        (2.0, 3.0), (3.0, 6.0), (6.0, 10.0),
    ]
    assert rendered == actual
    assert display_context == ()
    plot.deleteLater()


def test_overview_uses_all_evaluated_measurement_sections_not_raw_profile_geometry() -> None:
    _app()
    face = _representative_element("face", 0.0, 10.0, 0.0, -10.0)
    variant = DesignVariant("FACE", (0, 1), (face,))
    origin = SurfaceVertex(0.0, 0.0, 20.0)
    measured_segments = (
        SectionSegment(SectionPoint(-2.0, 20.0, -2.0, 0.0), SectionPoint(2.0, 20.0, 2.0, 0.0), 1),
        SectionSegment(SectionPoint(2.0, 20.0, 2.0, 0.0), SectionPoint(10.0, 10.0, 10.0, 0.0), 2),
    )
    profiles = tuple(
        TransverseProfile(
            WallAlignmentSample(float(index), origin, (1.0, 0.0), (0.0, 1.0)),
            (), measured_segments, DesignSection(()),
            measurement_context=SimpleNamespace(actual_segments=measured_segments),
        )
        for index in range(2)
    )
    landmarks = SimpleNamespace(
        upper_berm_start=SimpleNamespace(
            point=SectionPoint(-1.0, 20.0, -1.0, 0.0),
            detection=SimpleNamespace(reliable=True),
        ),
        upper_crest=SimpleNamespace(point=None, detection=SimpleNamespace(reliable=False)),
        lower_toe=SimpleNamespace(
            point=SectionPoint(9.0, 11.25, 9.0, 0.0),
            detection=SimpleNamespace(reliable=True),
        ),
    )
    plot = module.WallProfilePlot()
    plot.set_overview(
        SimpleNamespace(profiles=profiles, design_variants=(variant,)),
        measurements=tuple(SimpleNamespace(actual_landmarks=landmarks) for _ in profiles),
    )

    _design, actual = plot._geometry()

    assert [(segment.start.u, segment.end.u) for segment in actual] == [
        (-1.0, 2.0), (2.0, 9.0), (-1.0, 2.0), (2.0, 9.0),
    ]
    assert all(segment.start.u >= -1.0 and segment.end.u <= 9.0 for segment in actual)
    plot.deleteLater()


def test_measurement_summary_uses_domain_aggregates_with_independent_counts(monkeypatch) -> None:
    tab = _tab(monkeypatch)
    aggregate = lambda median, mean, minimum, maximum, valid: SimpleNamespace(
        median=median, mean=mean, minimum=minimum, maximum=maximum,
        valid_count=valid, total_count=29,
    )
    tab.result = SimpleNamespace(measurement_summary=SimpleNamespace(
        angle_deviation_deg=aggregate(1.8, 1.6, -0.6, 4.2, 27),
        upper_berm_width_deviation_m=aggregate(-0.7, -0.8, -2.1, 0.3, 21),
        toe_signed_offset_u_m=aggregate(0.9, 1.0, -0.4, 2.8, 26),
    ))
    tab._clear_detail_rows()
    tab._add_measurement_summary(0)
    text = [label.text() for label in tab.details_content.findChildren(module.QLabel)]

    assert "Measurements" in text
    assert "Q2 +1.8°" in text
    assert "27/29" in text
    assert "Q2 -0.7 m" in text
    assert "21/29" in text
    assert "Q2 +0.9 m" in text
    assert "Median" not in text
    assert any(
        label.toolTip() == "Mean +1.6° · Range -0.6° … +4.2°"
        for label in tab.details_content.findChildren(module.QLabel)
    )
    metric_widgets = [
        tab.details_rows.itemAtPosition(row, column).widget()
        for row in range(tab.details_rows.rowCount())
        for column in range(3)
        if tab.details_rows.itemAtPosition(row, column) is not None
        and tab.details_rows.itemAtPosition(row, column).widget() is not None
        and tab.details_rows.itemAtPosition(row, column).widget().objectName() != "SummaryValue"
    ]
    assert metric_widgets
    assert all(
        widget.sizePolicy().horizontalPolicy() == module.QSizePolicy.Policy.Ignored
        for widget in metric_widgets
    )
    tab.deleteLater()


def test_selected_measurement_schedule_shows_actual_deviations_and_boundary_berm_na(monkeypatch) -> None:
    tab = _tab(monkeypatch)
    crest = SectionPoint(0.0, 20.0, 0.0, 0.0)
    toe = SectionPoint(8.0, 10.0, 8.0, 0.0)
    profile = TransverseProfile(
        WallAlignmentSample(35.1, SurfaceVertex(0.0, 0.0, 20.0), (1.0, 0.0), (0.0, 1.0)),
        (), (), DesignSection((DesignSectionElement("face", crest, toe, (1,)),)),
    )
    landmark = lambda point, reason="detected": SimpleNamespace(
        point=point, detection=SimpleNamespace(reason_code=reason),
    )
    tab.result = SimpleNamespace(measurements=(SimpleNamespace(
        design_overall_angle_deg=65.0, actual_overall_angle_deg=62.8,
        design_upper_berm_width_m=12.0, actual_upper_berm_width_m=None,
        toe_signed_offset_u_m=1.5, angle_deviation_deg=-2.2,
        upper_berm_width_deviation_m=None,
        design_landmarks=SimpleNamespace(lower_toe=landmark(SectionPoint(25.5, 0, 0, 0))),
        actual_landmarks=SimpleNamespace(
            upper_berm_start=landmark(None, "boundary_truncated"),
            lower_toe=landmark(SectionPoint(27.0, 0, 0, 0)),
        ),
    ),))
    tab.profile_selector.addItem("Overview")
    tab.profile_selector.addItem("Profile 1")
    tab.profile_selector.setCurrentIndex(1)
    tab._show_profile_details(profile)
    text = [label.text() for label in tab.details_content.findChildren(module.QLabel)]

    assert {"Design", "Actual", "Deviation"} <= set(text)
    assert "65.0°" in text and "62.8°" in text and "-2.2°" in text
    assert "25.5 m" in text and "27.0 m" in text and "+1.5 m" in text
    assert text.count("N/A · Pit boundary") == 2
    tab.deleteLater()


def test_profile_schedule_uses_compact_design_geometry_rows(monkeypatch) -> None:
    _app()
    origin = SurfaceVertex(0.0, 0.0, 20.0)
    crest = SectionPoint(0.0, 20.0, 0.0, 0.0)
    berm_end = SectionPoint(3.0, 20.0, 3.0, 0.0)
    toe = SectionPoint(8.0, 10.0, 8.0, 0.0)
    profile = TransverseProfile(
        WallAlignmentSample(71.7, origin, (1.0, 0.0), (0.0, 1.0)),
        (),
        (),
        DesignSection(
            (
                DesignSectionElement("berm", crest, berm_end, (1,)),
                DesignSectionElement("face", berm_end, toe, (2,)),
            )
        ),
    )
    tab = _tab(monkeypatch)
    tab.profile_selector.addItem("Profile 1")
    tab.profile_selector.setCurrentIndex(1)
    tab._show_profile_details(profile)

    labels = [label.text() for label in tab.details_content.findChildren(module.QLabel)]
    assert "Design" in labels
    assert "Berm 1" in labels
    assert "Face 1" in labels
    assert "H / A" in labels
    assert "Lower toe" in labels
    assert "Chainage" not in labels
    assert "Assessment span" not in labels
    assert any("H 10.0 m · A" in label for label in labels)
    assert any("U 8.0 m · Z 10.0 m" in label for label in labels)
    assert tab.details_rows.rowStretch(tab._detail_stretch_row) == 1
    assert all(
        tab.details_rows.rowStretch(row) == 0
        for row in range(tab._detail_stretch_row)
    )
    tab.deleteLater()


def _app():
    """Keep one explicit application owner for the whole test module."""
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _surface(dx=0.0):
    vertices = (
        SurfaceVertex(-5 + dx, 0, 10), SurfaceVertex(0 + dx, 0, 10),
        SurfaceVertex(-5 + dx, 20, 10), SurfaceVertex(0 + dx, 20, 10),
        SurfaceVertex(5 + dx, 0, 0), SurfaceVertex(5 + dx, 20, 0),
        SurfaceVertex(10 + dx, 0, 0), SurfaceVertex(10 + dx, 20, 0),
    )
    specs = (((0, 1, 2), 5), ((1, 3, 2), 5), ((1, 4, 3), 2),
             ((4, 5, 3), 2), ((4, 6, 5), 3), ((6, 7, 5), 3))
    return TriangleSurface(
        vertices,
        tuple(SurfaceTriangle(indices, source_attributes={"COLOUR": colour})
              for indices, colour in specs),
    )


def _area():
    return PlanPolygon((PlanPoint(-2, 4), PlanPoint(8, 4), PlanPoint(8, 16),
                        PlanPoint(-2, 16), PlanPoint(-2, 4)))


class _Surfaces:
    storage_available = True

    def __init__(self):
        self.design = SimpleNamespace(logical_id="D", revision_number=2,
                                      source_format="datamine", triangle_count=6)
        self.actual = SimpleNamespace(logical_id="A", revision_number=7,
                                      source_format="dxf", triangle_count=6)

    def current(self, _site_id, kind):
        return self.design if kind == "design" else self.actual

    def load_dataset(self, _site_id, logical_id):
        dataset = self.design if logical_id == "D" else self.actual
        return dataset, SimpleNamespace(surface=_surface(0 if logical_id == "D" else 1))


def _tab(monkeypatch):
    _app()
    monkeypatch.setattr(module, "create_project_surface_dataset_service", lambda _context: _Surfaces())
    return module.WallConformanceTab(object(), 1, _area())


def _ruler_overview(profile):
    face = _representative_element("face", 0.0, 10.0, 0.0, -10.0)
    return SimpleNamespace(
        profiles=(profile,), design_variants=(DesignVariant("FACE", (0,), (face,)),)
    )


def test_manual_ruler_controls_support_overview_reset_and_escape(monkeypatch) -> None:
    tab = _tab(monkeypatch)
    source = _ruler_plot()
    tab.resize(1366, 768)
    tab.show()
    _app().processEvents()
    assert tab.measure_button.text() == "Measure"
    assert tab.measure_button.width() >= tab.measure_button.minimumSizeHint().width()
    assert tab.measure_button.geometry().right() <= tab.profile_header.contentsRect().right()
    tab.profile_plot.set_profile(source.profile)
    tab._sync_measure_controls()
    assert tab.measure_button.isEnabled()
    tab.profile_plot.set_measure_mode(True)
    tab.profile_plot.measure_point_a = (2.0, 18.0)
    escape = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    assert tab.eventFilter(tab.profile_plot, escape)
    assert tab.profile_plot.measure_point_a is None
    assert tab.profile_plot.measure_mode
    assert tab.eventFilter(tab.profile_plot, escape)
    assert not tab.profile_plot.measure_mode

    overview = _ruler_overview(source.profile)
    tab.profile_plot.set_overview(overview)
    assert tab.profile_plot.measure_point_a is None
    assert not tab.profile_plot.measure_mode
    assert tab.measure_button.isEnabled()
    tab.profile_plot.set_measure_mode(True)
    first = tab.profile_plot._map_data_to_widget(2.0, -2.0)
    second = tab.profile_plot._map_data_to_widget(8.0, -8.0)
    QTest.mouseClick(
        tab.profile_plot,
        Qt.MouseButton.LeftButton,
        pos=QPoint(round(first.x()), round(first.y())),
    )
    QTest.mouseClick(
        tab.profile_plot,
        Qt.MouseButton.LeftButton,
        pos=QPoint(round(second.x()), round(second.y())),
    )
    assert tab.profile_plot.measure_point_a is not None
    assert tab.profile_plot.measure_point_b is not None
    assert any(line.startswith("ΔdZ ") for line in tab.profile_plot._manual_measurement_annotation_lines())

    tab.profile_plot.set_profile(source.profile)
    assert tab.profile_plot.measure_point_a is None
    assert not tab.profile_plot.measure_mode
    assert tab.measure_button.isEnabled()
    tab.profile_plot.measure_point_a = (2.0, 18.0)
    tab.profile_plot.measure_point_b = (8.0, 12.0)
    assert any(line.startswith("ΔZ ") for line in tab.profile_plot._manual_measurement_annotation_lines())
    tab.profile_plot.set_overview(overview)
    assert tab.profile_plot.measure_point_a is None
    assert tab.profile_plot.measure_point_b is None

    tab.profile_plot.set_profile(source.profile)
    tab.profile_plot.measure_point_a = (2.0, 18.0)
    tab._clear_calculated_result()
    assert tab.profile_plot.measure_point_a is None
    assert tab.profile_plot.mode == "empty"
    assert not tab.measure_button.isEnabled()
    source.deleteLater()
    tab.deleteLater()


def _complete_alignment(tab):
    tab._begin_alignment_drawing()
    tab.plan._handle_scene_click(0.0, 4.0)
    tab.plan._handle_scene_click(0.0, 16.0)
    assert tab.plan.complete_alignment_drawing() is not None


def test_dataset_metadata_is_separated_and_explicit(monkeypatch):
    tab = _tab(monkeypatch)
    assert tab.design_title.text() == "DESIGN"
    assert "R2" in tab.design_metadata.text()
    assert "DATAMINE" in tab.design_metadata.text()
    assert "6 triangles" in tab.design_metadata.text()
    assert "R7" in tab.actual_metadata.text()
    assert "DXF" in tab.actual_metadata.text()
    tab.deleteLater()
    _app().sendPostedEvents()


def test_calculation_and_plan_click_keep_selection_synchronized(monkeypatch):
    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.spacing.setValue(5.0)
    tab.calculate()
    assert tab.profile_selector.count() > 1
    tab.plan.profile_selected.emit(1)
    assert tab.profile_selector.currentIndex() == 2
    assert tab.plan._selected_index == 1
    assert "Profile 2" in tab.profile_summary.text()
    assert tab.details_title.text() == "Profile 2"
    assert tab.details_metadata.text().startswith("Ch.")
    assert "Design" in [label.text() for label in tab.details_content.findChildren(module.QLabel)]
    assert tab.plan._direction_annotation is not None
    assert tab.plan.view._direction_annotation is not None
    tab.deleteLater()
    _app().sendPostedEvents()


def test_empty_and_semantic_palette_states_are_explanatory(monkeypatch):
    tab = _tab(monkeypatch)
    assert tab.profile_plot.profile is None
    assert "Face" in tab.semantic_mapping.text()
    light = module.WallProfilePlot._colors()
    _app().setProperty("slopeforgeTheme", "dark")
    dark = module.WallProfilePlot._colors()
    assert light["background"] != dark["background"]
    assert {"face", "berm", "road"} <= dark.keys()
    _app().setProperty("slopeforgeTheme", "light")
    tab.deleteLater()
    _app().sendPostedEvents()


def test_calculated_plan_recolors_all_geometry_without_recalculation(monkeypatch):
    _app().setProperty("slopeforgeTheme", "light")
    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.calculate()
    result_before = tab.result
    light = module.WallConformancePlanWidget._colors()
    assert tab.plan._area_item.pen().color() == light["area"]
    assert tab.plan._alignment_item.pen().color() == light["alignment"]

    _app().setProperty("slopeforgeTheme", "dark")
    tab.plan._apply_theme()
    dark = module.WallConformancePlanWidget._colors()
    assert tab.result is result_before
    assert tab.plan._area_item.pen().color() == dark["area"]
    assert tab.plan._area_item.brush().color() == dark["area_fill"]
    assert tab.plan._alignment_item.pen().color() == dark["alignment"]
    assert all(
        item.pen().color() in (dark["profile"], dark["selected"])
        for item in tab.plan._profile_items
    )
    _app().setProperty("slopeforgeTheme", "light")
    tab.deleteLater()
    _app().sendPostedEvents()


def test_legends_render_at_compact_minimum_width(monkeypatch):
    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.calculate()
    tab.profile_plot.resize(tab.profile_plot.minimumWidth(), 300)
    tab.profile_plot.show()
    _app().processEvents()
    image = tab.profile_plot.grab().toImage()
    assert not image.isNull()
    assert tab.profile_plot.minimumWidth() == 340
    assert tab.plan.legend.wordWrap()
    assert tab.plan.legend.sizePolicy().horizontalPolicy().name == "Ignored"
    assert tab.profile_legend.wordWrap()
    assert tab.profile_legend.sizePolicy().horizontalPolicy().name == "Ignored"
    assert "Skipped station" not in tab.plan.legend.text()
    tab.deleteLater()
    _app().sendPostedEvents()


def test_semantics_dialog_lists_counts_and_saves_through_service():
    class Service:
        saved = None

        def inspect_design_semantics(self, _site_id):
            return DesignSemanticInspection(
                SimpleNamespace(logical_id="D", revision_number=4),
                {"COLOUR": (
                    SurfaceAttributeValueCount(2, 12_450),
                    SurfaceAttributeValueCount(5, 8_620),
                    SurfaceAttributeValueCount(7, 120),
                )},
                SurfaceRoleMapping("COLOUR", ((2, "face"), (5, "berm"))),
                False,
            )

        def save_design_semantics(self, site_id, logical_id, mapping):
            self.saved = (site_id, logical_id, mapping)

    service = Service()
    dialog = DesignSurfaceSemanticsDialog(service, 1)
    assert dialog.table.rowCount() == 3
    assert dialog.table.item(0, 1).text() == "12,450"
    assert "Unknown: 120" in dialog.summary.text()
    dialog._save()
    assert service.saved[:2] == (1, "D")
    assert service.saved[2].resolve({"COLOUR": 2}) == "face"
    dialog.deleteLater()
    _app().sendPostedEvents()


def test_engineering_parameter_labels_and_legend_contract(monkeypatch):
    tab = _tab(monkeypatch)
    labels = [label.text() for label in tab.findChildren(module.QLabel)]
    assert "Strike smoothing radius" not in labels
    assert "Section extent" not in labels
    assert not hasattr(tab, "tangent_window")
    settings = tab._settings()
    assert settings.spacing_m == tab.spacing.value()
    assert vars(settings) == {"spacing_m": tab.spacing.value()}
    _complete_alignment(tab)
    tab.calculate()
    tab._select_profile(1)
    design_entries, actual_entries = tab.profile_plot._legend_rows(
        tab.profile_plot.profile
    )
    assert [label for label, _ in design_entries] == ["Face", "Berm", "Road"]
    assert actual_entries == (("Survey", "actual"),)
    assert "Design" not in [label for label, _ in (*design_entries, *actual_entries)]
    tab.deleteLater()
    _app().sendPostedEvents()


def test_overview_renders_every_evaluated_actual_section_counted_as_coverage(monkeypatch):
    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.calculate()

    variant = tab.result.profile_sections.design_variants[0]
    covered = tuple(
        index for index in variant.profile_indices
        if module.has_compatible_actual_wall_section(
            tab.result.profile_sections.profiles[index], tab.result.measurements[index]
        )
    )
    _design, actual = tab.profile_plot._geometry()
    rendered, context = tab.profile_plot._actual_render_layers()

    assert len(actual) == sum(
        len(tab.profile_plot._clip_segments_to_u_interval(
            tab.profile_plot._profile_actual_measurement_geometry(
                tab.result.profile_sections.profiles[index]
            ),
            (
                (
                    tab.result.measurements[index].actual_landmarks.upper_berm_start.point
                    if tab.result.measurements[index].actual_landmarks.upper_berm_start.detection.reliable
                    else tab.result.measurements[index].actual_landmarks.upper_crest.point
                ).u,
                tab.result.measurements[index].actual_landmarks.lower_toe.point.u,
            ),
        ))
        for index in covered
    )
    assert rendered == actual
    assert context == ()
    assert "Actual coverage: %s / %s" % (len(covered), len(variant.profile_indices)) in (
        tab.profile_summary.text()
    )
    tab.deleteLater()
    _app().sendPostedEvents()


def test_overview_coverage_and_actual_traces_share_the_compatibility_gate(monkeypatch):
    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.calculate()
    variant = tab.result.profile_sections.design_variants[0]

    monkeypatch.setattr(module, "has_compatible_actual_wall_section", lambda *_args: False)
    tab._select_profile(0)

    assert "Actual coverage: 0 / %s profiles" % len(variant.profile_indices) in (
        tab.profile_summary.text()
    )
    assert tab.profile_plot._geometry()[1] == ()
    tab.deleteLater()
    _app().sendPostedEvents()


def test_overview_selected_and_escape_modes_are_distinct(monkeypatch):
    from PySide6.QtTest import QTest

    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.calculate()
    assert tab.profile_plot.mode == "overview"
    assert tab.profile_plot.profile is None
    assert len(tab.profile_plot._geometry()[1]) > len(
        tab.result.profile_sections.profiles[0].actual_segments
    )
    assert "Actual coverage:" in tab.profile_summary.text()
    assert "dZ" in module.tr("dZ (m, local Design crest = 0)")

    tab._select_profile(1)
    exact = tab.result.profile_sections.profiles[0]
    assert tab.profile_plot.mode == "selected"
    assert tab.profile_plot.profile is exact
    assert tab.profile_plot._geometry() == (
        tuple(s for s in exact.design_segments if s.semantic_role != "ignore"),
        exact.actual_segments,
    )
    assert tab.plan._selected_index == 0

    tab.show()
    QTest.keyClick(tab, module.Qt.Key.Key_Escape)
    assert tab.profile_plot.mode == "overview"
    assert tab.plan._selected_index == -1
    assert tab.profile_selector.currentIndex() == 0

    for child in (tab.plan.view, tab.profile_selector, tab.profile_plot):
        tab._select_profile(1)
        child.setFocus()
        QTest.keyClick(child, module.Qt.Key.Key_Escape)
        assert tab.profile_plot.mode == "overview"
        assert tab.plan._selected_index == -1

    # Escape in Overview must not close or otherwise clear the calculated tab.
    tab.profile_selector.setFocus()
    QTest.keyClick(tab.profile_selector, module.Qt.Key.Key_Escape)
    assert tab.isVisible()
    assert tab.result is not None
    tab.deleteLater()
    _app().sendPostedEvents()


def test_mapping_summary_keeps_multiple_values_and_save_clears_stale_plan(monkeypatch):
    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.calculate()
    assert tab.plan.scene.items()
    tab.service.surface_service.design.semantic_mapping_json = {
        "attribute_name": "COLOUR",
        "assignments": [
            {"value": 9, "role": "face"}, {"value": 2, "role": "face"},
            {"value": 6, "role": "face"}, {"value": 5, "role": "berm"},
            {"value": 3, "role": "road"},
        ],
    }
    tab._refresh_dataset_metadata()
    assert "Face=2,6,9" in tab.semantic_mapping.text()
    tab.plan.clear_result()
    tab.result = None
    tab.profile_plot.set_profile(None)
    assert tab.plan._area_item is not None
    assert tab.plan._alignment_item is not None
    assert tab.profile_plot.mode == "empty"
    tab.deleteLater()
    _app().sendPostedEvents()


def test_initial_alignment_workflow_and_clear_preserve_assessment(monkeypatch):
    tab = _tab(monkeypatch)
    assert tab.plan._area_item is not None
    assert tab.plan.wall_alignment is None
    assert not tab.calculate_button.isEnabled()
    _complete_alignment(tab)
    assert tab.set_alignment_button.text() == "Change Wall Alignment"
    assert "2 vertices" in tab.alignment_metadata.text()
    assert tab.calculate_button.isEnabled()
    tab.calculate()
    assert tab.result is not None
    tab._clear_wall_alignment()
    assert tab.plan.wall_alignment is None
    assert tab.result is None
    assert not tab.calculate_button.isEnabled()
    assert tab.set_alignment_button.text() == "Set Wall Alignment"
    assert tab.plan._area_item is not None
    tab.deleteLater()
    _app().sendPostedEvents()


def test_alignment_hint_tracks_points_and_clears_after_enter(monkeypatch):
    tab = _tab(monkeypatch)
    assert tab.set_alignment_button.text() == "Set Wall Alignment"
    assert not tab.alignment_hint.isVisible()

    tab.show()
    tab._begin_alignment_drawing()
    assert tab.alignment_hint.isVisible()
    assert tab.alignment_hint.text() == "Click to place Wall Alignment points"
    assert tab.alignment_hint.property("statusRole") == "info"
    tab.plan._handle_scene_click(0.0, 4.0)
    assert tab.alignment_hint.text() == "Click to place Wall Alignment points"
    tab.plan._handle_scene_click(0.0, 16.0)
    assert tab.alignment_hint.text() == (
        "Press Enter or double-click to finish · Esc to cancel"
    )

    tab.plan._handle_workflow_key("enter")
    assert tab.plan.wall_alignment is not None
    assert not tab.alignment_hint.isVisible()
    assert tab.alignment_hint.text() == ""
    assert tab.set_alignment_button.text() == "Change Wall Alignment"
    tab.deleteLater()
    _app().sendPostedEvents()


class _AlignmentController:
    def __init__(self, alignment=None):
        self.alignment = alignment
        self.loaded = []
        self.saved = []
        self.cleared = []

    def load_wall_alignment(self, area, revision):
        self.loaded.append((area, revision))
        return self.alignment

    def save_wall_alignment(self, area, alignment):
        self.saved.append((area, alignment))
        self.alignment = alignment

    def clear_wall_alignment(self, area):
        self.cleared.append(area)
        self.alignment = None


class _FailingAlignmentController(_AlignmentController):
    def save_wall_alignment(self, area, alignment):
        raise RuntimeError("Wall Alignment could not be saved")


def test_saved_alignment_loads_edits_and_clears_through_controller(monkeypatch):
    saved = WallAlignment((PlanPoint(0, 4), PlanPoint(0, 16)))
    controller = _AlignmentController(saved)
    area, revision = object(), object()
    _app()
    monkeypatch.setattr(module, "create_project_surface_dataset_service", lambda _context: _Surfaces())
    tab = module.WallConformanceTab(
        object(), 1, _area(), area=area, geometry_revision=revision,
        controller=controller,
    )

    assert controller.loaded == [(area, revision)]
    assert tab.plan.wall_alignment == saved
    assert tab.set_alignment_button.text() == "Change Wall Alignment"
    assert "2 vertices" in tab.alignment_metadata.text()
    tab.calculate()
    assert tab.result is not None

    tab._begin_alignment_drawing()
    tab.plan._handle_scene_click(1.0, 4.0)
    tab.plan._complete_draft_from_double_click(1.0, 16.0)
    assert controller.alignment == tab.plan.wall_alignment
    assert controller.alignment != saved
    assert tab.result is None

    tab.calculate()
    assert tab.result is not None
    tab._clear_wall_alignment()
    assert controller.alignment is None
    assert controller.cleared == [area]
    assert tab.plan.wall_alignment is None
    assert tab.result is None
    assert tab.set_alignment_button.text() == "Set Wall Alignment"
    tab.deleteLater()


def test_read_only_alignment_controls_do_not_modify_persisted_state(monkeypatch):
    controller = _AlignmentController(WallAlignment((PlanPoint(0, 4), PlanPoint(0, 16))))
    _app()
    monkeypatch.setattr(module, "create_project_surface_dataset_service", lambda _context: _Surfaces())
    tab = module.WallConformanceTab(
        object(), 1, _area(), area=object(), geometry_revision=object(),
        controller=controller, read_only=True,
    )

    assert not tab.set_alignment_button.isEnabled()
    assert not tab.clear_alignment_button.isEnabled()
    tab._begin_alignment_drawing()
    tab._clear_wall_alignment()
    assert not controller.saved and not controller.cleared
    assert tab.plan.wall_alignment == controller.alignment
    assert not tab.alignment_hint.isVisible()
    tab.deleteLater()


def test_read_only_design_semantics_control_cannot_open_persistent_editor(monkeypatch):
    _app()
    monkeypatch.setattr(module, "create_project_surface_dataset_service", lambda _context: _Surfaces())
    tab = module.WallConformanceTab(
        object(), 1, _area(), area=object(), geometry_revision=object(),
        controller=_AlignmentController(), read_only=True,
    )

    assert not tab.edit_semantics.isEnabled()
    monkeypatch.setattr(
        "ui.dialogs.design_surface_semantics_dialog.DesignSurfaceSemanticsDialog",
        lambda *_args: pytest.fail("read-only Wall Conformance must not open semantics editing"),
    )
    tab._edit_design_semantics()
    tab.deleteLater()


def test_historical_geometry_alignment_is_displayed_but_not_editable(monkeypatch):
    saved = WallAlignment((PlanPoint(0, 4), PlanPoint(0, 16)))
    controller = _AlignmentController(saved)
    area = SimpleNamespace(active_geometry_revision_id="CURRENT")
    revision = SimpleNamespace(id="HISTORICAL")
    _app()
    monkeypatch.setattr(module, "create_project_surface_dataset_service", lambda _context: _Surfaces())
    tab = module.WallConformanceTab(
        object(), 1, _area(), area=area, geometry_revision=revision,
        controller=controller,
    )

    assert tab.plan.wall_alignment == saved
    assert not tab.set_alignment_button.isEnabled()
    assert not tab.clear_alignment_button.isEnabled()
    tab._clear_wall_alignment()
    assert not controller.cleared
    tab.deleteLater()


def test_alignment_save_failure_restores_the_previous_displayed_alignment(monkeypatch):
    saved = WallAlignment((PlanPoint(0, 4), PlanPoint(0, 16)))
    controller = _FailingAlignmentController(saved)
    _app()
    monkeypatch.setattr(module, "create_project_surface_dataset_service", lambda _context: _Surfaces())
    tab = module.WallConformanceTab(
        object(), 1, _area(), area=object(), geometry_revision=object(),
        controller=controller,
    )

    tab._begin_alignment_drawing()
    tab.plan._handle_scene_click(1.0, 4.0)
    tab.plan._complete_draft_from_double_click(1.0, 16.0)
    assert tab.plan.wall_alignment == saved
    assert "could not be saved" in tab.status.text()
    tab.deleteLater()


def test_spacing_change_and_failed_recalculation_clear_stale_wall_display(monkeypatch):
    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.calculate()
    tab._select_profile(1)
    tab.profile_plot.measure_point_a = (2.0, 18.0)

    tab.spacing.setValue(5.0)

    assert tab.result is None
    assert not tab.plan._profile_items
    assert tab.profile_plot.mode == "empty"
    assert tab.profile_plot.measure_point_a is None
    assert "spacing changed" in tab.status.text().lower()

    tab.calculate()
    assert tab.result is not None
    tab.service.calculate_current = lambda *_args: (_ for _ in ()).throw(RuntimeError("surface read failed"))
    tab.calculate()

    assert tab.result is None
    assert not tab.plan._profile_items
    assert tab.profile_plot.mode == "empty"
    assert "surface read failed" in tab.status.text()
    tab.deleteLater()


def test_two_pane_workspace_uses_matching_canvas_hosts_and_wider_plan(monkeypatch):
    tab = _tab(monkeypatch)
    tab.resize(1600, 860)
    tab.show()
    _app().processEvents()

    assert tab.splitter.count() == 2
    assert tab.plan.minimumWidth() == 480
    assert tab.plan.maximumWidth() == 720
    assert tab.splitter.sizes()[0] in range(580, 641)
    assert tab.splitter.sizes()[1] > tab.splitter.sizes()[0]
    assert isinstance(tab.plan.plan_canvas, module.WallCanvasHost)
    assert isinstance(tab.profile_canvas, module.WallCanvasHost)
    assert tab.plan.legend.parentWidget() is tab.plan.plan_header
    assert tab.profile_legend.parentWidget() is tab.profile_header
    assert tab.plan.plan_header.parentWidget() is tab.plan.plan_canvas
    assert tab.profile_header.parentWidget() is tab.profile_canvas
    assert tab.details_schedule.parentWidget() is tab.profile_canvas.drawing_body
    assert tab.details_schedule.isHidden()
    tab.deleteLater()
    _app().sendPostedEvents()


def test_result_status_variant_elision_and_representative_schedule(monkeypatch):
    tab = _tab(monkeypatch)
    tab.resize(1600, 860)
    tab.show()
    _complete_alignment(tab)
    tab.calculate()
    _app().processEvents()

    assert "profiles" in tab.status.text()
    assert "coverage" in tab.status.text()
    assert "FACE-BERM" not in tab.variant_selector.currentText()
    assert tab.variant_selector.itemData(0, module.Qt.ItemDataRole.ToolTipRole)
    assert tab.details_title.text() == "Representative Design"
    assert not tab.details_schedule.isHidden()
    assert 260 <= tab.details_schedule.width() <= 320
    assert tab.profile_canvas.drawing_body.layout().count() == 2
    assert tab.profile_canvas.drawing_body.layout().itemAt(0).widget() is tab.profile_plot
    assert tab.profile_canvas.drawing_body.layout().itemAt(1).widget() is tab.details_schedule
    assert tab.details_schedule.x() == tab.profile_plot.geometry().right() + 1
    assert tab.details_schedule.height() == tab.profile_plot.height()
    assert (
        tab.details_scroll.verticalScrollBarPolicy()
        == module.Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert (
        tab.details_scroll.horizontalScrollBarPolicy()
        == module.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert not tab.findChildren(QTableWidget)
    bounds = tab.profile_plot._equal_aspect_bounds(
        tab.profile_plot.plot_rect(), 0.0, 10.0, 0.0, 20.0
    )
    assert tab.profile_plot.plot_rect().width() / (bounds[1] - bounds[0]) == pytest.approx(
        tab.profile_plot.plot_rect().height() / (bounds[3] - bounds[2])
    )
    assert tab.details_schedule.background_color() == tab.profile_plot._colors()["background"]
    tab.deleteLater()
    _app().sendPostedEvents()


def test_profile_schedule_visibility_changes_only_the_drawing_body_width(monkeypatch):
    tab = _tab(monkeypatch)
    tab.resize(1600, 860)
    tab.show()
    _complete_alignment(tab)
    tab.calculate()
    _app().processEvents()

    before = tab.profile_plot.geometry()
    tab._clear_details()
    _app().processEvents()
    assert tab.profile_plot.width() > before.width()
    tab._show_representative_details()
    _app().processEvents()
    assert tab.profile_plot.width() == before.width()
    assert tab.profile_canvas.canvas is tab.profile_canvas.drawing_body
    assert tab.profile_canvas.drawing_body.profile_plot is tab.profile_plot
    tab.deleteLater()
    _app().sendPostedEvents()


def test_profile_legend_is_a_widget_and_selected_u_annotation_does_not_fit_scene(monkeypatch):
    tab = _tab(monkeypatch)
    tab.resize(1600, 860)
    tab.show()
    _complete_alignment(tab)
    tab.calculate()
    _app().processEvents()

    assert "DESIGN" in tab.profile_legend.text()
    assert "ACTUAL" in tab.profile_legend.text()
    assert tab.profile_legend.parentWidget() is tab.profile_header
    assert not hasattr(module.WallProfilePlot, "_draw_legend_row")
    before = tab.plan.scene.itemsBoundingRect()
    before_scrollbars = (
        tab.plan.view.horizontalScrollBar().minimum(),
        tab.plan.view.horizontalScrollBar().maximum(),
        tab.plan.view.verticalScrollBar().minimum(),
        tab.plan.view.verticalScrollBar().maximum(),
    )
    tab._select_profile(1)
    after = tab.plan.scene.itemsBoundingRect()
    assert tab.plan._direction_annotation is not None
    assert after == before
    assert (
        tab.plan.view.horizontalScrollBar().minimum(),
        tab.plan.view.horizontalScrollBar().maximum(),
        tab.plan.view.verticalScrollBar().minimum(),
        tab.plan.view.verticalScrollBar().maximum(),
    ) == before_scrollbars
    first_start, first_end = tab.plan.view.direction_annotation_screen_points()
    first_length = hypot(first_end.x() - first_start.x(), first_end.y() - first_start.y())
    tab.plan.view.scale(1.6, 1.6)
    second_start, second_end = tab.plan.view.direction_annotation_screen_points()
    second_length = hypot(second_end.x() - second_start.x(), second_end.y() - second_start.y())
    assert first_length == pytest.approx(22.0, abs=1.0)
    assert second_length == pytest.approx(first_length, abs=1.0)
    tab.deleteLater()
    _app().sendPostedEvents()


def test_profile_annotation_does_not_change_equal_aspect_framing():
    plot = module.WallProfilePlot()
    full = QRectF(0.0, 0.0, 900.0, 500.0)
    bounds = plot._equal_aspect_bounds(full, 0.0, 10.0, 0.0, 20.0)

    assert full.width() / (bounds[1] - bounds[0]) == pytest.approx(
        full.height() / (bounds[3] - bounds[2])
    )
    assert bounds[0] <= 0.0 <= bounds[1]
    assert bounds[0] <= 10.0 <= bounds[1]
    assert bounds[2] <= 0.0 <= bounds[3]
    assert bounds[2] <= 20.0 <= bounds[3]
    plot.deleteLater()


def test_profile_schedule_stays_beside_plot_after_resize(monkeypatch):
    tab = _tab(monkeypatch)
    tab.resize(1366, 768)
    tab.show()
    _complete_alignment(tab)
    tab.calculate()
    tab._select_profile(1)
    _app().processEvents()

    assert tab.details_title.text() == "Profile 1"
    first = tab.details_schedule.geometry()
    tab.resize(1920, 1080)
    _app().processEvents()
    second = tab.details_schedule.geometry()
    assert second.x() == tab.profile_plot.geometry().right() + 1
    assert second.top() == tab.profile_plot.geometry().top()
    assert second.bottom() == tab.profile_plot.geometry().bottom()
    assert second.x() >= first.x()
    tab.deleteLater()
    _app().sendPostedEvents()


def test_skipped_station_marker_and_tooltip_are_presentation_only(monkeypatch):
    tab = _tab(monkeypatch)
    _complete_alignment(tab)
    tab.calculate()
    before_extent = tab.plan.scene.itemsBoundingRect()
    diagnostic = AlignmentPlacementDiagnostic(
        "insufficient_face_support", "Insufficient Design Face support", 1, 5.0
    )
    tab.result = replace(tab.result, diagnostics=(diagnostic,))
    tab.plan.set_result(tab.result)

    assert len(tab.plan._skipped_annotations) == 1
    assert "Profile skipped" in tab.plan._skipped_annotations[0][1]
    assert tab.plan.scene.itemsBoundingRect() == before_extent
    assert "Skipped station" in tab.plan.legend.text()
    assert "1 skipped" in tab._result_status_text()
    tab.deleteLater()
    _app().sendPostedEvents()


def test_double_click_completes_alignment_and_escape_keeps_existing_alignment(monkeypatch):
    tab = _tab(monkeypatch)
    tab.show()
    _app().processEvents()
    _complete_alignment(tab)
    existing = tab.plan.wall_alignment
    tab._begin_alignment_drawing()
    tab.plan._handle_scene_click(1.0, 4.0)
    assert tab.alignment_hint.text() == "Click to place Wall Alignment points"
    tab.plan._complete_draft_from_double_click(1.0, 16.0)
    assert tab.plan.wall_alignment != existing
    assert not tab.alignment_hint.isVisible()
    replacement = tab.plan.wall_alignment
    tab._begin_alignment_drawing()
    tab.plan._handle_scene_click(2.0, 4.0)
    assert tab.alignment_hint.isVisible()
    tab.plan.cancel_alignment_drawing()
    assert tab.plan.wall_alignment == replacement
    assert not tab.alignment_hint.isVisible()
    tab.deleteLater()
    _app().sendPostedEvents()
