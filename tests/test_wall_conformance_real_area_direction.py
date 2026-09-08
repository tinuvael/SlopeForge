from __future__ import annotations

import pytest

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import SurfaceRoleMapping, WallAlignmentSample, build_transverse_profiles
from domain.wall_conformance.engine import _orient_sample_downwall


MAPPING = SurfaceRoleMapping(
    "COLOUR", ((2, "face"), (5, "berm"), (25, "road"))
)


def _multi_bench_design() -> TriangleSurface:
    ys = (0.0, 15.0, 30.0)
    xs = (-2.0, 0.0, 5.0, 7.0, 12.0, 14.0)
    zs = (30.0, 30.0, 20.0, 20.0, 10.0, 10.0)
    roles = (5, 2, 5, 2, 5)
    vertices = tuple(
        SurfaceVertex(x, y, zs[column])
        for column, x in enumerate(xs)
        for y in ys
    )
    triangles = []
    for column, role in enumerate(roles):
        for row in range(len(ys) - 1):
            a = column * len(ys) + row
            b = a + 1
            c = (column + 1) * len(ys) + row
            d = c + 1
            triangles.extend((
                SurfaceTriangle((a, c, b), source_attributes={"COLOUR": role}),
                SurfaceTriangle((c, d, b), source_attributes={"COLOUR": role}),
            ))
    return TriangleSurface(vertices, tuple(triangles))


def _wide_actual_surface() -> TriangleSurface:
    vertices = (
        SurfaceVertex(-5.0, 0.0, 27.0),
        SurfaceVertex(-5.0, 30.0, 27.0),
        SurfaceVertex(20.0, 0.0, 17.0),
        SurfaceVertex(20.0, 30.0, 17.0),
    )
    return TriangleSurface(
        vertices,
        (
            SurfaceTriangle((0, 2, 1)),
            SurfaceTriangle((2, 3, 1)),
        ),
    )


def _assessment_strip() -> PlanPolygon:
    # End connectors deliberately fall between regular 3 m stations.
    return PlanPolygon((
        PlanPoint(0.0, 1.25),
        PlanPoint(14.0, 1.25),
        PlanPoint(14.0, 28.4),
        PlanPoint(0.0, 28.4),
        PlanPoint(0.0, 1.25),
    ))


def test_exact_design_section_can_flip_wrong_plan_normal_downwall() -> None:
    design = _multi_bench_design()
    raw = WallAlignmentSample(
        15.0,
        SurfaceVertex(0.0, 15.0, 30.0),
        (0.0, 1.0),
        (-1.0, 0.0),
    )

    oriented = _orient_sample_downwall(raw, design, MAPPING)

    assert oriented is not None
    assert oriented.normal_xy == pytest.approx((1.0, 0.0))


def test_profiles_descend_with_positive_u_and_sample_both_area_end_connectors() -> None:
    result = build_transverse_profiles(
        _multi_bench_design(),
        _wide_actual_surface(),
        _assessment_strip(),
        MAPPING,
        spacing_m=3.0,
        tangent_window_m=6.0,
    )

    assert result.profiles
    assert all(abs(profile.alignment.origin.x) < 1e-6 for profile in result.profiles)

    station_y = [profile.alignment.origin.y for profile in result.profiles]
    assert any(value == pytest.approx(1.25) for value in station_y)
    assert any(value == pytest.approx(28.4) for value in station_y)

    for profile in result.profiles:
        assert profile.assessment_u_interval is not None
        lower, upper = profile.assessment_u_interval
        assert lower == pytest.approx(0.0, abs=1e-6)
        assert upper == pytest.approx(14.0, abs=1e-6)

        faces = [
            element
            for element in profile.design_section.elements
            if element.role == "face"
        ]
        assert faces
        assert all(face.vertical_change < 0.0 for face in faces)

        assert profile.actual_segments
        assert all(segment.u_min >= lower - 1e-7 for segment in profile.actual_segments)
        assert all(segment.u_max <= upper + 1e-7 for segment in profile.actual_segments)
