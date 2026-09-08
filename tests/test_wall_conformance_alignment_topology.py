from __future__ import annotations

import pytest

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import (
    DesignAlignmentBoundary,
    SurfaceRoleMapping,
    WallAlignmentSample,
    WallTransitionLine,
    build_transverse_profiles,
    extract_design_wall_topology,
    sample_wall_alignment,
)
from domain.wall_conformance.engine import (
    _assemble_alignment_boundaries,
    _downstream_area_exit_u,
)


MAPPING = SurfaceRoleMapping(
    "COLOUR", ((2, "face"), (5, "berm"), (3, "road"))
)


def _multi_face_surface(*, reverse_winding: bool = False) -> TriangleSurface:
    # Rows run along chainage. Columns run down-wall and deliberately contain
    # Face -> Berm -> Face -> Road -> Face -> lower Berm geometry.
    ys = (0.0, 10.0, 20.0)
    xs = (
        (0.0, 0.0, 0.0),
        (4.0, 4.0, 4.0),
        (9.0, 11.0, 9.0),  # longer intermediate crest than the upper rim
        (14.0, 14.0, 14.0),
        (21.0, 23.0, 21.0),
        (26.0, 26.0, 26.0),
        (31.0, 31.0, 31.0),
    )
    base_z = (120.0, 101.0, 100.0, 84.0, 82.0, 70.0, 69.0)
    grade = (0.0, -6.0, 7.0)
    vertices = tuple(
        SurfaceVertex(xs[column][row], y, base_z[column] + grade[row])
        for column in range(len(xs))
        for row, y in enumerate(ys)
    )
    roles = (2, 5, 2, 3, 2, 5)
    triangles = []
    for column, role in enumerate(roles):
        for row in range(len(ys) - 1):
            a = column * 3 + row
            b = a + 1
            c = (column + 1) * 3 + row
            d = c + 1
            indices = ((a, c, b), (c, d, b))
            for triangle in indices:
                if reverse_winding:
                    triangle = tuple(reversed(triangle))
                triangles.append(
                    SurfaceTriangle(
                        triangle, source_attributes={"COLOUR": role}
                    )
                )
    return TriangleSurface(vertices, tuple(triangles))


def _area() -> PlanPolygon:
    return PlanPolygon(
        (
            PlanPoint(-1, 1), PlanPoint(32, 1), PlanPoint(32, 19),
            PlanPoint(-1, 19), PlanPoint(-1, 1),
        )
    )


def _area_ending_at_lower_toe() -> PlanPolygon:
    return PlanPolygon(
        (
            PlanPoint(0, 1), PlanPoint(26, 1), PlanPoint(26, 19),
            PlanPoint(0, 19), PlanPoint(0, 1),
        )
    )


def test_downstream_area_exit_uses_first_exit_for_concave_polygon():
    area = PlanPolygon((
        PlanPoint(0, 0), PlanPoint(10, 0), PlanPoint(10, 4),
        PlanPoint(6, 4), PlanPoint(6, 1), PlanPoint(4, 1),
        PlanPoint(4, 4), PlanPoint(0, 4), PlanPoint(0, 0),
    ))
    sample = WallAlignmentSample(
        0.0,
        SurfaceVertex(0, 2, 10),
        (0.0, 1.0),
        (1.0, 0.0),
    )
    assert _downstream_area_exit_u(sample, area) == 4.0


def test_continuous_mixed_source_crest_fragments_are_assembled():
    fragments = (
        DesignAlignmentBoundary(
            WallTransitionLine("crest", (
                SurfaceVertex(0, 0, 10), SurfaceVertex(10, 0, 10),
            )),
            1,
            (SurfaceVertex(5, 2, 5),),
            "Face/Platform",
        ),
        DesignAlignmentBoundary(
            WallTransitionLine("crest", (
                SurfaceVertex(10 + 1e-6, 0, 10), SurfaceVertex(20, 0, 11),
            )),
            1,
            (SurfaceVertex(15, 2, 6),),
            "outer Face boundary",
        ),
        DesignAlignmentBoundary(
            WallTransitionLine("crest", (
                SurfaceVertex(20, 0, 11), SurfaceVertex(30, 0, 12),
            )),
            1,
            (SurfaceVertex(25, 2, 7),),
            "Face/Platform",
        ),
    )
    assembled = _assemble_alignment_boundaries(fragments)
    assert len(assembled) == 1
    assert len(assembled[0].line.points) == 4
    assert assembled[0].line.plan_length == pytest.approx(30.0)
    assert assembled[0].source == "mixed crest sources"


def test_alignment_uses_upper_face_patch_not_longest_intermediate_crest():
    surface = _multi_face_surface()
    topology = extract_design_wall_topology(surface, MAPPING)
    profiles = build_transverse_profiles(
        surface, surface, _area(), MAPPING,
        spacing_m=5.0, tangent_window_m=4.0,
    )
    other_crests = [
        line
        for line in topology.transitions
        if line.kind == "crest" and line is not profiles.crest_line
    ]
    assert {point.x for point in profiles.crest_line.points} == {0.0}
    assert max(line.plan_length for line in other_crests) > profiles.crest_line.plan_length
    assert len(profiles.toe_lines) == 1
    assert {point.x for point in profiles.toe_lines[0].points} == {26.0}
    assert profiles.profiles
    assert all(profile.alignment.normal_xy[0] > 0 for profile in profiles.profiles)
    assert {profile.design_section.topology_signature for profile in profiles.profiles} == {
        "FACE-BERM-FACE-ROAD-FACE-BERM"
    }
    assert [variant.signature for variant in profiles.design_variants] == [
        "FACE-BERM-FACE-ROAD-FACE-BERM"
    ]


def test_area_may_end_at_lower_toe_with_lower_berm_outside():
    surface = _multi_face_surface()
    result = build_transverse_profiles(
        surface,
        surface,
        _area_ending_at_lower_toe(),
        MAPPING,
        spacing_m=5.0,
        tangent_window_m=4.0,
    )
    assert result.profiles
    assert {point.x for point in result.crest_line.points} == {0.0}
    assert len(result.toe_lines) == 1
    assert {point.x for point in result.toe_lines[0].points} == {26.0}


def test_profile_width_follows_assessment_polygon_without_width_setting():
    surface = _multi_face_surface()
    widths = []
    for downstream_x in (20.0, 31.0):
        area = PlanPolygon((
            PlanPoint(0, 1), PlanPoint(downstream_x, 1),
            PlanPoint(downstream_x, 19), PlanPoint(0, 19), PlanPoint(0, 1),
        ))
        result = build_transverse_profiles(
            surface, surface, area, MAPPING,
            spacing_m=5.0, tangent_window_m=4.0,
        )
        widths.append([
            upper - lower
            for lower, upper in (
                profile.assessment_u_interval for profile in result.profiles
            )
        ])
    assert all(abs(width - 20.0) < 1e-6 for width in widths[0])
    assert all(abs(width - 31.0) < 1e-6 for width in widths[1])


def test_irregular_assessment_polygon_produces_station_specific_widths():
    surface = _multi_face_surface()
    area = PlanPolygon((
        PlanPoint(0, 1), PlanPoint(18, 1), PlanPoint(31, 19),
        PlanPoint(0, 19), PlanPoint(0, 1),
    ))
    result = build_transverse_profiles(
        surface, surface, area, MAPPING,
        spacing_m=4.0, tangent_window_m=4.0,
    )
    widths = {
        round(upper - lower, 2)
        for lower, upper in (
            profile.assessment_u_interval for profile in result.profiles
        )
    }
    assert len(widths) > 1


def test_face_patch_orientation_does_not_depend_on_triangle_winding():
    for reverse_winding in (False, True):
        profiles = build_transverse_profiles(
            _multi_face_surface(reverse_winding=reverse_winding),
            _multi_face_surface(reverse_winding=reverse_winding),
            _area(), MAPPING,
            spacing_m=7.0, tangent_window_m=4.0,
        )
        assert profiles.profiles
        assert all(profile.alignment.normal_xy[0] > 0 for profile in profiles.profiles)


def test_adjacent_face_patch_overrides_a_nearby_unrelated_toe():
    topology = extract_design_wall_topology(_multi_face_surface(), MAPPING)
    alignment = next(
        boundary
        for boundary in topology.alignment_boundaries
        if {point.x for point in boundary.line.points} == {0.0}
    )
    wrong_side_toe = WallTransitionLine(
        "toe",
        (SurfaceVertex(-0.1, 0, 0), SurfaceVertex(-0.1, 20, 0)),
    )
    samples = sample_wall_alignment(
        alignment.line,
        (wrong_side_toe,),
        _area(),
        spacing_m=7.0,
        tangent_window_m=4.0,
        interior_points=alignment.interior_points,
    )
    assert samples
    assert all(sample.normal_xy[0] > 0 for sample in samples)


def _connected_ramp_surface() -> TriangleSurface:
    """Road cells form one ramp corridor through multiple bench levels."""
    ys = (0.0, 7.0, 9.0, 11.0, 13.0, 20.0)
    xs = (0.0, 4.0, 9.0, 14.0, 21.0, 26.0, 31.0)
    base_z = (120.0, 101.0, 100.0, 84.0, 82.0, 70.0, 69.0)
    vertices = tuple(
        SurfaceVertex(x, y, base_z[column] + 0.18 * y)
        for column, x in enumerate(xs)
        for y in ys
    )
    triangles = []
    normal_roles = (2, 5, 2, 3, 2, 5)
    for column, normal_role in enumerate(normal_roles):
        for row in range(len(ys) - 1):
            # Between y=9 and y=11 the Road cuts through both the upper
            # platform and Face 2, joining the lower Road into one component.
            role = 3 if row == 2 and column in {1, 2, 3} else normal_role
            a = column * len(ys) + row
            b = a + 1
            c = (column + 1) * len(ys) + row
            d = c + 1
            triangles.extend((
                SurfaceTriangle((a, c, b), source_attributes={"COLOUR": role}),
                SurfaceTriangle((c, d, b), source_attributes={"COLOUR": role}),
            ))
    return TriangleSurface(vertices, tuple(triangles))


def test_connected_road_ramp_does_not_define_global_face_hierarchy():
    surface = _connected_ramp_surface()
    profiles = build_transverse_profiles(
        surface, surface, _area(), MAPPING,
        spacing_m=2.0, tangent_window_m=3.0,
    )
    assert {point.x for point in profiles.crest_line.points} == {0.0}
    assert profiles.profiles
    assert all(profile.alignment.normal_xy[0] > 0 for profile in profiles.profiles)
    signatures = {variant.signature for variant in profiles.design_variants}
    assert signatures == {
        "FACE-BERM-FACE-ROAD-FACE-BERM",
        "FACE-ROAD-FACE-BERM",
    }


def _folded_crest_surface() -> TriangleSurface:
    crest_xy = ((0.0, 0.0), (0.0, 10.0), (10.0, 10.0),
                (10.0, 0.0), (2.0, 0.0))
    inward = ((1.0, 0.0), (0.7, -0.7), (-0.7, -0.7),
              (-0.7, 0.7), (0.0, 1.0))
    vertices = []
    for (x, y), (nx, ny) in zip(crest_xy, inward):
        vertices.extend((
            SurfaceVertex(x - nx * 3.0, y - ny * 3.0, 20.0),
            SurfaceVertex(x, y, 20.0),
            SurfaceVertex(x + nx * 3.0, y + ny * 3.0, 10.0),
            SurfaceVertex(x + nx * 6.0, y + ny * 6.0, 10.0),
        ))
    triangles = []
    for row in range(len(crest_xy) - 1):
        first, second = row * 4, (row + 1) * 4
        for offset, role in ((0, 5), (1, 2), (2, 3)):
            a, b = first + offset, second + offset
            c, d = a + 1, b + 1
            triangles.extend((
                SurfaceTriangle((a, c, b), source_attributes={"COLOUR": role}),
                SurfaceTriangle((c, d, b), source_attributes={"COLOUR": role}),
            ))
    return TriangleSurface(tuple(vertices), tuple(triangles))


def test_folded_upper_crest_extra_crossing_does_not_replace_profile_origin():
    surface = _folded_crest_surface()
    area = PlanPolygon((
        PlanPoint(-4, -4), PlanPoint(14, -4), PlanPoint(14, 14),
        PlanPoint(-4, 14), PlanPoint(-4, -4),
    ))
    result = build_transverse_profiles(
        surface, surface, area, MAPPING,
        spacing_m=4.0, tangent_window_m=3.0,
    )
    assert result.profiles
    folded_profiles = [
        profile
        for profile in result.profiles
        if abs(profile.alignment.origin.x) < 1e-6
        and 0.0 < profile.alignment.origin.y < 10.0
    ]
    assert folded_profiles
    assert any(
        sum(
            1
            for first, second in zip(
                result.crest_line.points, result.crest_line.points[1:]
            )
            if (
                profile.alignment.normal_xy[0]
                * (first.y - profile.alignment.origin.y)
                - profile.alignment.normal_xy[1]
                * (first.x - profile.alignment.origin.x)
            )
            * (
                profile.alignment.normal_xy[0]
                * (second.y - profile.alignment.origin.y)
                - profile.alignment.normal_xy[1]
                * (second.x - profile.alignment.origin.x)
            )
            <= 1e-9
        )
        >= 2
        for profile in folded_profiles
    )
    assert any(
        abs(profile.alignment.origin.x) < 1e-6
        and 0.0 < profile.alignment.origin.y < 10.0
        for profile in result.profiles
    )
    assert all(
        any(
            abs(point.u) < 1e-6
            for segment in profile.design_segments
            for point in (segment.start, segment.end)
        )
        for profile in result.profiles
    )


def _with_remote_high_face(surface: TriangleSurface) -> tuple[TriangleSurface, TriangleSurface]:
    """Add a disconnected same-plane Face well above the evaluated wall."""
    vertices = list(surface.vertices)
    first = len(vertices)
    vertices.extend((
        SurfaceVertex(2.0, 0.0, 680.0),
        SurfaceVertex(2.0, 20.0, 680.0),
        SurfaceVertex(4.0, 0.0, 670.0),
        SurfaceVertex(4.0, 20.0, 670.0),
    ))
    remote_triangles = (
        SurfaceTriangle(
            (first, first + 2, first + 1),
            source_attributes={"COLOUR": 2},
        ),
        SurfaceTriangle(
            (first + 2, first + 3, first + 1),
            source_attributes={"COLOUR": 2},
        ),
    )
    design = TriangleSurface(
        tuple(vertices), (*surface.triangles, *remote_triangles)
    )
    actual = TriangleSurface(tuple(vertices[first:]), (
        SurfaceTriangle((0, 2, 1)), SurfaceTriangle((2, 3, 1)),
    ))
    return design, actual


def _upper_wall_profiles(result):
    return tuple(
        profile for profile in result.profiles
        if abs(profile.alignment.origin.x) < 1e-6
    )


def test_remote_folded_design_does_not_return_to_local_profile_geometry():
    base_surface = _multi_face_surface()
    design_with_remote, high_actual = _with_remote_high_face(base_surface)
    base = build_transverse_profiles(
        base_surface, high_actual, _area(), MAPPING,
        spacing_m=5.0, tangent_window_m=4.0,
    )
    augmented = build_transverse_profiles(
        design_with_remote, high_actual, _area(), MAPPING,
        spacing_m=5.0, tangent_window_m=4.0,
    )

    base_profiles = _upper_wall_profiles(base)
    augmented_profiles = _upper_wall_profiles(augmented)
    assert len(augmented_profiles) == len(base_profiles)
    assert [profile.design_segments for profile in augmented_profiles] == [
        profile.design_segments for profile in base_profiles
    ]
    assert [profile.design_section.topology_signature for profile in augmented_profiles] == [
        profile.design_section.topology_signature for profile in base_profiles
    ]
    assert all(
        max(
            point.z
            for segment in profile.design_segments
            for point in (segment.start, segment.end)
        ) < 650.0
        for profile in augmented_profiles
    )
    assert all(not profile.actual_segments for profile in augmented_profiles)


def _boundary(start: float, end: float) -> DesignAlignmentBoundary:
    return DesignAlignmentBoundary(
        WallTransitionLine(
            "crest",
            (SurfaceVertex(start, 0.0, 10.0), SurfaceVertex(end, 0.0, 10.0)),
        ),
        0,
        (SurfaceVertex((start + end) / 2.0, 1.0, 9.0),),
    )


def test_alignment_fragment_assembly_is_order_independent() -> None:
    assembled = _assemble_alignment_boundaries((_boundary(10.0, 20.0), _boundary(0.0, 10.0)))

    assert len(assembled) == 1
    assert [point.x for point in assembled[0].line.points] == [0.0, 10.0, 20.0]


def test_alignment_fragment_assembly_joins_small_tin_gap_only() -> None:
    joined = _assemble_alignment_boundaries((_boundary(0.0, 10.0), _boundary(10.005, 20.0)))
    separate = _assemble_alignment_boundaries((_boundary(0.0, 10.0), _boundary(11.0, 20.0)))

    assert len(joined) == 1
    assert len(separate) == 2
