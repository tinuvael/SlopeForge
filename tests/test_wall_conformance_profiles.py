from __future__ import annotations

from math import hypot

import pytest

from domain.geometry.operations import point_in_polygon
from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import (
    SurfaceRoleMapping,
    WallAlignmentSample,
    build_transverse_profiles,
    extract_design_transition_lines,
    intersect_surface_with_profile,
    sample_wall_alignment,
    select_primary_crest_line,
)


ROLE_MAPPING = SurfaceRoleMapping(
    "COLOUR",
    ((2, "face"), (5, "berm"), (3, "road")),
)


def _triangle(indices, colour, source_id):
    return SurfaceTriangle(
        indices,
        source_id=source_id,
        source_attributes={"COLOUR": colour},
    )


def _straight_bench_surface() -> TriangleSurface:
    vertices = (
        SurfaceVertex(-5, 0, 10),
        SurfaceVertex(0, 0, 10),
        SurfaceVertex(-5, 20, 10),
        SurfaceVertex(0, 20, 10),
        SurfaceVertex(5, 0, 0),
        SurfaceVertex(5, 20, 0),
        SurfaceVertex(10, 0, 0),
        SurfaceVertex(10, 20, 0),
    )
    triangles = (
        _triangle((0, 1, 2), 5, "upper-1"),
        _triangle((1, 3, 2), 5, "upper-2"),
        _triangle((1, 4, 3), 2, "face-1"),
        _triangle((4, 5, 3), 2, "face-2"),
        _triangle((4, 6, 5), 3, "road-1"),
        _triangle((6, 7, 5), 3, "road-2"),
    )
    return TriangleSurface(vertices, triangles)


def _curved_bench_surface() -> TriangleSurface:
    crest_xy = ((0.0, 0.0), (0.0, 10.0), (2.0, 20.0), (6.0, 30.0), (12.0, 40.0))
    vertices: list[SurfaceVertex] = []
    rows: list[tuple[int, int, int, int]] = []
    for index, (x, y) in enumerate(crest_xy):
        before = crest_xy[max(0, index - 1)]
        after = crest_xy[min(len(crest_xy) - 1, index + 1)]
        tx, ty = after[0] - before[0], after[1] - before[1]
        length = hypot(tx, ty)
        tx, ty = tx / length, ty / length
        nx, ny = ty, -tx  # choose the +X-ish downslope side for this fixture
        if nx < 0:
            nx, ny = -nx, -ny
        upper = SurfaceVertex(x - nx * 4.0, y - ny * 4.0, 10.0)
        crest = SurfaceVertex(x, y, 10.0)
        toe = SurfaceVertex(x + nx * 5.0, y + ny * 5.0, 0.0)
        lower = SurfaceVertex(toe.x + nx * 4.0, toe.y + ny * 4.0, 0.0)
        start = len(vertices)
        vertices.extend((upper, crest, toe, lower))
        rows.append((start, start + 1, start + 2, start + 3))

    triangles: list[SurfaceTriangle] = []
    for strip, (first, second) in enumerate(zip(rows, rows[1:]), start=1):
        upper_a, crest_a, toe_a, lower_a = first
        upper_b, crest_b, toe_b, lower_b = second
        triangles.extend(
            (
                _triangle((upper_a, crest_a, upper_b), 5, f"upper-{strip}-1"),
                _triangle((crest_a, crest_b, upper_b), 5, f"upper-{strip}-2"),
                _triangle((crest_a, toe_a, crest_b), 2, f"face-{strip}-1"),
                _triangle((toe_a, toe_b, crest_b), 2, f"face-{strip}-2"),
                _triangle((toe_a, lower_a, toe_b), 3, f"road-{strip}-1"),
                _triangle((lower_a, lower_b, toe_b), 3, f"road-{strip}-2"),
            )
        )
    return TriangleSurface(tuple(vertices), tuple(triangles))


def _skew_area() -> PlanPolygon:
    return PlanPolygon(
        (
            PlanPoint(-8, -2),
            PlanPoint(8, 10),
            PlanPoint(10, 18),
            PlanPoint(-6, 14),
            PlanPoint(-8, -2),
        )
    )


def test_surface_role_mapping_normalizes_datamine_colour_values() -> None:
    assert ROLE_MAPPING.resolve({"COLOUR": 2}) == "face"
    assert ROLE_MAPPING.resolve({"colour": 5.0}) == "berm"
    assert ROLE_MAPPING.resolve({"Colour": "3.000"}) == "road"
    assert ROLE_MAPPING.resolve({"COLOUR": 99}) == "unknown"


def test_design_transition_lines_come_from_face_platform_topology() -> None:
    transitions = extract_design_transition_lines(_straight_bench_surface(), ROLE_MAPPING)
    crest = next(line for line in transitions if line.kind == "crest")
    toe = next(line for line in transitions if line.kind == "toe")

    assert crest.plan_length == pytest.approx(20.0)
    assert {(point.x, point.z) for point in crest.points} == {(0, 10)}
    assert toe.plan_length == pytest.approx(20.0)
    assert {(point.x, point.z) for point in toe.points} == {(5, 0)}


def test_alignment_is_normal_to_design_wall_not_assessment_area_edges() -> None:
    surface = _straight_bench_surface()
    transitions = extract_design_transition_lines(surface, ROLE_MAPPING)
    area = _skew_area()
    crest = select_primary_crest_line(transitions, area)
    toes = tuple(line for line in transitions if line.kind == "toe")

    samples = sample_wall_alignment(
        crest,
        toes,
        area,
        spacing_m=5.0,
        tangent_window_m=4.0,
    )

    assert [sample.chainage_m for sample in samples] == [5.0, 10.0, 15.0]
    for sample in samples:
        assert sample.tangent_xy == pytest.approx((0.0, 1.0))
        assert sample.normal_xy == pytest.approx((1.0, 0.0))
        assert point_in_polygon(PlanPoint(sample.origin.x, sample.origin.y), area)

    # The Assessment Area's first edge is deliberately oblique. Its orientation
    # does not leak into the design-derived transverse profile direction.
    area_edge = area.ring[1].x - area.ring[0].x, area.ring[1].y - area.ring[0].y
    edge_length = hypot(*area_edge)
    area_edge = area_edge[0] / edge_length, area_edge[1] / edge_length
    assert abs(samples[0].normal_xy[0] * area_edge[0] + samples[0].normal_xy[1] * area_edge[1]) < 0.9


def test_vertical_section_intersects_design_roles_in_wall_normal_u_coordinates() -> None:
    surface = _straight_bench_surface()
    alignment = WallAlignmentSample(
        chainage_m=10.0,
        origin=SurfaceVertex(0, 10, 10),
        tangent_xy=(0.0, 1.0),
        normal_xy=(1.0, 0.0),
    )

    segments = intersect_surface_with_profile(
        surface,
        alignment,
        role_mapping=ROLE_MAPPING,
    )

    roles = {segment.semantic_role for segment in segments}
    assert {"berm", "face", "road"} <= roles
    face_points = {
        (round(point.u, 6), round(point.z, 6))
        for segment in segments
        if segment.semantic_role == "face"
        for point in (segment.start, segment.end)
    }
    assert (0.0, 10.0) in face_points
    assert (5.0, 0.0) in face_points


def test_curved_design_alignment_rotates_profiles_with_local_wall_orientation() -> None:
    surface = _curved_bench_surface()
    area = PlanPolygon(
        (
            PlanPoint(-10, 7),
            PlanPoint(18, 4),
            PlanPoint(22, 34),
            PlanPoint(-5, 36),
            PlanPoint(-10, 7),
        )
    )

    result = build_transverse_profiles(
        surface,
        surface,
        area,
        ROLE_MAPPING,
        spacing_m=5.0,
        tangent_window_m=7.0,
    )

    assert len(result.profiles) >= 4
    normals = [profile.alignment.normal_xy for profile in result.profiles]
    assert max(normal[1] for normal in normals) - min(normal[1] for normal in normals) > 0.15
    for profile in result.profiles:
        tx, ty = profile.alignment.tangent_xy
        nx, ny = profile.alignment.normal_xy
        assert tx * nx + ty * ny == pytest.approx(0.0, abs=1e-8)
        design_geometry = {
            (round(point.u, 5), round(point.z, 5))
            for segment in profile.design_segments
            for point in (segment.start, segment.end)
            if point.u >= -1e-7
        }
        actual_geometry = {
            (round(point.u, 5), round(point.z, 5))
            for segment in profile.actual_segments
            for point in (segment.start, segment.end)
            if point.u >= -1e-7
        }
        assert design_geometry == actual_geometry
