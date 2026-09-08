from __future__ import annotations

from math import cos, pi, sin

import pytest

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import (
    SurfaceRoleMapping,
    build_transverse_profiles,
    extract_design_wall_topology,
)


MAPPING = SurfaceRoleMapping(
    "COLOUR", ((2, "face"), (5, "berm"), (3, "road"))
)


def _square_area(extent: float = 13.0) -> PlanPolygon:
    return PlanPolygon((
        PlanPoint(-extent, -extent),
        PlanPoint(extent, -extent),
        PlanPoint(extent, extent),
        PlanPoint(-extent, extent),
        PlanPoint(-extent, -extent),
    ))


def _radial_wall_surface(
    *,
    point_count: int = 16,
    start_angle: float = 0.0,
    end_angle: float = 2.0 * pi,
    closed: bool = True,
) -> TriangleSurface:
    """Berm -> Face -> Berm wall following a circular plan alignment."""
    radii = (12.0, 10.0, 6.0, 4.0)
    elevations = (20.0, 20.0, 10.0, 10.0)
    if closed:
        angles = tuple(2.0 * pi * index / point_count for index in range(point_count))
    else:
        angles = tuple(
            start_angle + (end_angle - start_angle) * index / (point_count - 1)
            for index in range(point_count)
        )

    vertices = tuple(
        SurfaceVertex(radius * cos(angle), radius * sin(angle), elevations[ring])
        for ring, radius in enumerate(radii)
        for angle in angles
    )
    roles = (5, 2, 5)
    triangles = []
    segment_count = point_count if closed else point_count - 1
    for ring, role in enumerate(roles):
        for index in range(segment_count):
            next_index = (index + 1) % point_count
            a = ring * point_count + index
            b = ring * point_count + next_index
            c = (ring + 1) * point_count + index
            d = (ring + 1) * point_count + next_index
            triangles.extend((
                SurfaceTriangle((a, c, b), source_attributes={"COLOUR": role}),
                SurfaceTriangle((c, d, b), source_attributes={"COLOUR": role}),
            ))
    return TriangleSurface(vertices, tuple(triangles))


def _reordered(
    surface: TriangleSurface,
    *,
    reverse_vertices: bool = False,
    reverse_triangles: bool = False,
    reverse_winding: bool = False,
) -> TriangleSurface:
    order = list(range(len(surface.vertices)))
    if reverse_vertices:
        order.reverse()
    new_index = {old: new for new, old in enumerate(order)}
    vertices = tuple(surface.vertices[index] for index in order)
    triangles = list(surface.triangles)
    if reverse_triangles:
        triangles.reverse()
    remapped = []
    for triangle in triangles:
        indices = tuple(new_index[index] for index in triangle.vertex_indices)
        if reverse_winding:
            indices = tuple(reversed(indices))
        remapped.append(SurfaceTriangle(
            indices,
            source_attributes=triangle.source_attributes,
        ))
    return TriangleSurface(vertices, tuple(remapped))


def _physical_signature(surface: TriangleSurface):
    result = build_transverse_profiles(
        surface,
        surface,
        _square_area(),
        MAPPING,
        spacing_m=5.0,
        tangent_window_m=2.5,
    )
    profile_signature = tuple(sorted(
        (
            round(profile.alignment.origin.x, 5),
            round(profile.alignment.origin.y, 5),
            round(profile.alignment.origin.z, 5),
            round(profile.alignment.normal_xy[0], 5),
            round(profile.alignment.normal_xy[1], 5),
            profile.design_section.topology_signature,
        )
        for profile in result.profiles
    ))
    crest_signature = tuple(sorted(
        tuple(sorted(
            (round(point.x, 5), round(point.y, 5), round(point.z, 5))
            for point in line.points[:-1] if line.points[0] == line.points[-1]
        ))
        if line.points[0] == line.points[-1]
        else tuple(sorted(
            (round(point.x, 5), round(point.y, 5), round(point.z, 5))
            for point in line.points
        ))
        for line in result.crest_lines
    ))
    return crest_signature, profile_signature, len(result.toe_lines)


def test_closed_upper_crest_runs_through_full_production_pipeline() -> None:
    surface = _radial_wall_surface(closed=True)
    topology = extract_design_wall_topology(surface, MAPPING)
    closed_crests = [
        boundary.line
        for boundary in topology.alignment_boundaries
        if boundary.line.points[0] == boundary.line.points[-1]
    ]

    assert closed_crests

    result = build_transverse_profiles(
        surface,
        surface,
        _square_area(),
        MAPPING,
        spacing_m=5.0,
        tangent_window_m=2.5,
    )

    assert result.profiles
    assert any(line.points[0] == line.points[-1] for line in result.crest_lines)
    origins = {
        (round(profile.alignment.origin.x, 6), round(profile.alignment.origin.y, 6))
        for profile in result.profiles
    }
    assert len(origins) == len(result.profiles)
    assert {profile.design_section.topology_signature for profile in result.profiles} == {
        "FACE-BERM"
    }


def test_partial_assessment_does_not_display_whole_closed_crest() -> None:
    surface = _radial_wall_surface(closed=True)
    right_half = PlanPolygon((
        PlanPoint(0.0, -13.0),
        PlanPoint(13.0, -13.0),
        PlanPoint(13.0, 13.0),
        PlanPoint(0.0, 13.0),
        PlanPoint(0.0, -13.0),
    ))
    result = build_transverse_profiles(
        surface,
        surface,
        right_half,
        MAPPING,
        spacing_m=3.0,
        tangent_window_m=2.5,
    )

    assert result.profiles
    assert result.crest_lines
    assert all(line.points[0] != line.points[-1] for line in result.crest_lines)
    assert all(point.x >= -1e-8 for line in result.crest_lines for point in line.points)
    assert all(profile.alignment.origin.x >= -1e-8 for profile in result.profiles)


def test_closed_wall_full_pipeline_is_storage_order_invariant() -> None:
    surface = _radial_wall_surface(closed=True)
    expected = _physical_signature(surface)

    variants = (
        _reordered(surface, reverse_vertices=True),
        _reordered(surface, reverse_triangles=True),
        _reordered(surface, reverse_winding=True),
        _reordered(
            surface,
            reverse_vertices=True,
            reverse_triangles=True,
            reverse_winding=True,
        ),
    )
    for variant in variants:
        assert _physical_signature(variant) == expected


def test_strongly_curved_wall_accepts_opposing_local_normals() -> None:
    surface = _radial_wall_surface(
        point_count=17,
        start_angle=-2.0 * pi / 3.0,
        end_angle=2.0 * pi / 3.0,
        closed=False,
    )
    result = build_transverse_profiles(
        surface,
        surface,
        _square_area(),
        MAPPING,
        spacing_m=4.0,
        tangent_window_m=2.5,
    )

    assert result.profiles
    normals = [profile.alignment.normal_xy for profile in result.profiles]
    assert any(
        first[0] * second[0] + first[1] * second[1] < -0.25
        for index, first in enumerate(normals)
        for second in normals[index + 1:]
    )


def _strip_surface(*, length: float = 30.0, multi_bench: bool = False) -> TriangleSurface:
    ys = (0.0, length / 2.0, length)
    if multi_bench:
        xs = (-2.0, 0.0, 5.0, 7.0, 12.0, 14.0)
        zs = (30.0, 30.0, 20.0, 20.0, 10.0, 10.0)
        roles = (5, 2, 5, 2, 5)
    else:
        xs = (-2.0, 0.0, 5.0, 7.0)
        zs = (20.0, 20.0, 10.0, 10.0)
        roles = (5, 2, 5)

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


def _upper_context_surface() -> TriangleSurface:
    ys = (0.0, 15.0, 30.0)
    xs = (-7.0, -2.0, 0.0, 5.0, 7.0)
    zs = (30.0, 20.0, 20.0, 10.0, 10.0)
    roles = (2, 5, 2, 5)
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


def test_small_area_overshoot_keeps_external_crest_and_upper_platform_context() -> None:
    surface = _upper_context_surface()
    area = PlanPolygon((
        PlanPoint(-0.001, 1.0),
        PlanPoint(7.0, 1.0),
        PlanPoint(7.0, 29.0),
        PlanPoint(-0.001, 29.0),
        PlanPoint(-0.001, 1.0),
    ))
    result = build_transverse_profiles(
        surface,
        surface,
        area,
        MAPPING,
        spacing_m=5.0,
        tangent_window_m=3.0,
    )

    assert result.profiles
    assert all(abs(profile.alignment.origin.x) < 1e-6 for profile in result.profiles)
    assert all(
        profile.design_section.upstream_context is not None
        and profile.design_section.upstream_context.role == "berm"
        for profile in result.profiles
    )
    assert all(
        profile.design_section.topology_signature == "FACE-BERM"
        for profile in result.profiles
    )


def test_internal_crest_is_rejected_by_full_pipeline() -> None:
    surface = _strip_surface(multi_bench=True)
    area = PlanPolygon((
        PlanPoint(0.0, 1.0),
        PlanPoint(14.0, 1.0),
        PlanPoint(14.0, 29.0),
        PlanPoint(0.0, 29.0),
        PlanPoint(0.0, 1.0),
    ))
    result = build_transverse_profiles(
        surface,
        surface,
        area,
        MAPPING,
        spacing_m=5.0,
        tangent_window_m=3.0,
    )

    assert result.profiles
    assert all(abs(profile.alignment.origin.x) < 1e-6 for profile in result.profiles)
    assert all(
        all(abs(point.x) < 1e-6 for point in line.points)
        for line in result.crest_lines
    )


def test_concave_area_keeps_disconnected_crest_spans_separate() -> None:
    surface = _strip_surface()
    area = PlanPolygon((
        PlanPoint(0.0, 1.0),
        PlanPoint(7.0, 1.0),
        PlanPoint(7.0, 29.0),
        PlanPoint(0.0, 29.0),
        PlanPoint(0.0, 22.0),
        PlanPoint(3.0, 22.0),
        PlanPoint(3.0, 8.0),
        PlanPoint(0.0, 8.0),
        PlanPoint(0.0, 1.0),
    ))
    result = build_transverse_profiles(
        surface,
        surface,
        area,
        MAPPING,
        spacing_m=3.0,
        tangent_window_m=2.0,
    )

    assert len(result.crest_lines) == 2
    y_ranges = sorted(
        (min(point.y for point in line.points), max(point.y for point in line.points))
        for line in result.crest_lines
    )
    assert y_ranges[0] == pytest.approx((1.0, 8.0))
    assert y_ranges[1] == pytest.approx((22.0, 29.0))
    assert {profile.alignment.boundary_component_index for profile in result.profiles} == {0, 1}
    assert all(
        0 <= profile.alignment.boundary_component_index < len(result.crest_lines)
        for profile in result.profiles
    )


def test_short_crest_below_profile_spacing_still_has_profile_and_visible_span() -> None:
    surface = _strip_surface(length=1.5)
    area = PlanPolygon((
        PlanPoint(0.0, 0.1),
        PlanPoint(7.0, 0.1),
        PlanPoint(7.0, 1.4),
        PlanPoint(0.0, 1.4),
        PlanPoint(0.0, 0.1),
    ))
    result = build_transverse_profiles(
        surface,
        surface,
        area,
        MAPPING,
        spacing_m=3.0,
        tangent_window_m=1.0,
    )

    assert result.profiles
    station_y = sorted(profile.alignment.origin.y for profile in result.profiles)
    assert station_y[0] == pytest.approx(0.1)
    assert station_y[-1] == pytest.approx(1.4)
    assert len({round(value, 8) for value in station_y}) == len(station_y)
    assert len(result.crest_lines) == 1
    crest = result.crest_lines[0]
    assert crest.plan_length == pytest.approx(1.3)
    assert (min(point.y for point in crest.points), max(point.y for point in crest.points)) == pytest.approx(
        (0.1, 1.4)
    )
