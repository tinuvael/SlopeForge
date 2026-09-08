from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from domain.geometry.surfaces import SurfaceVertex
from domain.wall_conformance.models import (
    DesignSection,
    ProfileMeasurementContext,
    SectionPoint,
    SectionSegment,
    TransverseProfile,
    WallAlignmentSample,
)
from ui.pages.wall_conformance_tab import WallConformanceTab, WallProfilePlot


def _app():
    return QApplication.instance() or QApplication([])


def _segment(start_u: float, end_u: float, z: float = 100.0) -> SectionSegment:
    return SectionSegment(
        SectionPoint(start_u, z, start_u, 0.0),
        SectionPoint(end_u, z - (end_u - start_u), end_u, 0.0),
        1,
    )


def _profile(*, actual=(), context_actual=(), interval=(0.0, 10.0)):
    return TransverseProfile(
        WallAlignmentSample(
            0.0, SurfaceVertex(0.0, 0.0, 100.0), (1.0, 0.0), (0.0, 1.0)
        ),
        (),
        actual,
        DesignSection(()),
        assessment_u_interval=interval,
        measurement_context=ProfileMeasurementContext(
            DesignSection(()), (), context_actual, ((-5.0, 15.0),)
        ),
    )


def _measurement(
    upper_berm_start: float | None,
    lower_toe: float | None,
    upper_crest: float | None = 0.0,
    crest_source: str = "physical_breakpoint",
):
    def landmark(value, source="physical_breakpoint"):
        return SimpleNamespace(
            point=SectionPoint(value, 100.0 - value, value, 0.0) if value is not None else None,
            detection=SimpleNamespace(reliable=value is not None),
            source=source,
        )

    return SimpleNamespace(
        actual_landmarks=SimpleNamespace(
            upper_berm_start=landmark(upper_berm_start),
            upper_crest=landmark(upper_crest, crest_source),
            lower_toe=landmark(lower_toe),
        )
    )


def test_selected_profile_uses_physical_landmarks_not_assessment_interval_without_mutation() -> None:
    _app()
    evaluated = (_segment(0.0, 10.0),)
    context = (_segment(-3.0, 3.0), _segment(3.0, 7.0), _segment(7.0, 13.0))
    profile = _profile(actual=evaluated, context_actual=context, interval=(2.0, 7.0))
    plot = WallProfilePlot()
    plot.set_profile(profile, _measurement(0.0, 10.0))

    normal, dashed = plot._actual_render_layers()

    assert [(segment.start.u, segment.end.u) for segment in normal] == [
        (0.0, 3.0),
        (3.0, 7.0),
        (7.0, 10.0),
    ]
    assert [(segment.start.u, segment.end.u) for segment in dashed] == [
        (-1.0, 0.0),
        (10.0, 11.0),
    ]
    assert profile.measurement_context.actual_segments is context
    assert profile.assessment_u_interval == (2.0, 7.0)
    plot.deleteLater()


def test_selected_profile_context_supports_both_sides_and_no_coverage_is_not_invented() -> None:
    _app()
    plot = WallProfilePlot()
    plot.set_profile(
        _profile(context_actual=(_segment(-4.0, 0.0), _segment(10.0, 14.0))),
        _measurement(0.0, 10.0),
    )

    assert [(segment.start.u, segment.end.u) for segment in plot._measurement_context_geometry()] == [
        (-1.0, 0.0),
        (10.0, 11.0),
    ]

    plot.set_profile(_profile())
    assert plot._measurement_context_geometry() == ()
    plot.deleteLater()


def test_boundary_face_run_crest_has_compact_marker_provenance() -> None:
    _app()
    plot = WallProfilePlot()
    plot.set_profile(
        _profile(actual=(_segment(0.0, 10.0),), context_actual=(_segment(0.0, 10.0),)),
        _measurement(
            None,
            10.0,
            crest_source="boundary_face_run_onset",
        ),
    )

    labels = [label for label, _point in plot._reliable_actual_landmarks()]

    assert "Actual upper crest · face-run onset" in labels
    plot.deleteLater()


def test_selected_profile_without_measurement_context_does_not_invent_extension() -> None:
    _app()
    profile = replace(
        _profile(actual=(_segment(0.0, 10.0),)),
        measurement_context=None,
    )
    plot = WallProfilePlot()
    plot.set_profile(profile, _measurement(0.0, 10.0))

    solid, dashed = plot._actual_render_layers()

    assert [(segment.start.u, segment.end.u) for segment in solid] == [(0.0, 10.0)]
    assert dashed == ()
    plot.deleteLater()


def test_context_is_selected_profile_only_and_dashed_pen_is_secondary() -> None:
    _app()
    profile = _profile(context_actual=(_segment(-4.0, 0.0),))
    plot = WallProfilePlot()
    plot.set_profile(profile, _measurement(0.0, 10.0))
    assert plot._measurement_context_geometry()

    plot.set_overview(SimpleNamespace(design_variants=(), profiles=(profile,)))
    assert plot._measurement_context_geometry() == ()

    color = plot._colors()["actual"]
    pen = plot._measurement_context_pen(color)
    assert pen.style() == Qt.PenStyle.DashLine
    assert pen.isCosmetic()
    assert pen.color() == color
    assert pen.widthF() < 2.2
    plot.deleteLater()


def test_selected_profile_legend_distinguishes_evaluated_actual_and_context() -> None:
    _app()
    plot = WallProfilePlot()
    plot.set_profile(
        _profile(actual=(_segment(0.0, 10.0),), context_actual=(_segment(-4.0, -1.0),)),
        _measurement(0.0, 10.0),
    )
    label = QLabel()
    fake_tab = SimpleNamespace(profile_plot=plot, profile_legend=label)

    WallConformanceTab._update_profile_legend(fake_tab)

    assert "ACTUAL" in label.text()
    assert "Evaluated" in label.text()
    assert "Context" in label.text()
    assert label.isVisible()
    plot.deleteLater()
    label.deleteLater()


def test_incompatible_raw_actual_is_not_a_solid_evaluated_section() -> None:
    _app()
    raw = (_segment(-4.0, 14.0, z=140.0),)
    plot = WallProfilePlot()
    plot.set_profile(
        _profile(actual=(), context_actual=raw), _measurement(0.0, 10.0),
    )

    solid, diagnostic_context = plot._actual_render_layers()

    assert solid == ()
    assert diagnostic_context
    assert plot.profile.measurement_context.actual_segments is raw
    plot.deleteLater()


def test_incompatible_actual_legend_does_not_claim_an_evaluated_section() -> None:
    _app()
    plot = WallProfilePlot()
    plot.set_profile(
        _profile(actual=(), context_actual=(_segment(-4.0, 14.0, z=140.0),)),
        _measurement(0.0, 10.0),
    )
    label = QLabel()
    fake_tab = SimpleNamespace(profile_plot=plot, profile_legend=label)

    WallConformanceTab._update_profile_legend(fake_tab)

    assert "Context" in label.text()
    assert "Evaluated" not in label.text()
    plot.deleteLater()
    label.deleteLater()


def test_overview_excludes_raw_context_without_compatible_actual_wall_section() -> None:
    _app()
    raw_context = (_segment(-2.0, 12.0),)
    compatible = _profile(
        actual=(_segment(0.0, 10.0),), context_actual=raw_context,
    )
    incompatible = _profile(actual=(), context_actual=raw_context)
    measurement = _measurement(0.0, 10.0)
    variant = SimpleNamespace(
        profile_indices=(0, 1), upstream_context=None, elements=(),
    )
    plot = WallProfilePlot()
    plot.set_overview(
        SimpleNamespace(profiles=(compatible, incompatible), design_variants=(variant,)),
        measurements=(measurement, measurement),
    )

    assert plot._profile_has_overview_actual_display(compatible, measurement)
    assert not plot._profile_has_overview_actual_display(incompatible, measurement)
    assert [(segment.start.u, segment.end.u) for segment in plot._geometry()[1]] == [
        (0.0, 10.0),
    ]

    plot.set_profile(incompatible, measurement)
    solid, context = plot._actual_render_layers()
    assert solid == ()
    assert context
    plot.deleteLater()


def test_compatible_actual_and_high_current_toe_still_render_normally() -> None:
    _app()
    actual = (_segment(0.0, 10.0, z=100.0),)
    plot = WallProfilePlot()
    plot.set_profile(
        _profile(actual=actual, context_actual=actual), _measurement(0.0, 10.0),
    )

    solid, _ = plot._actual_render_layers()

    assert solid
    plot.deleteLater()


def test_boundary_crest_and_toe_render_as_evaluated_without_berm_start() -> None:
    _app()
    actual = (_segment(-1.0, 11.0, z=100.0),)
    plot = WallProfilePlot()
    plot.set_profile(
        _profile(actual=actual, context_actual=actual),
        _measurement(None, 11.0, upper_crest=-1.0),
    )

    solid, _ = plot._actual_render_layers()

    assert solid
    assert solid[0].start.u == -1.0
    assert solid[-1].end.u == 11.0
    plot.deleteLater()
