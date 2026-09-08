from __future__ import annotations

from math import hypot

import pytest

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import (
    SurfaceRoleMapping,
    WallAlignment,
    build_design_section,
    build_alignment_profile_sections,
    place_profiles_from_alignment,
)
from domain.wall_conformance.alignment_placement import (
    _crossing_fragments,
    _face_fragments,
    _upstream_context_for_assessed_section,
)
from domain.wall_conformance.models import SectionPoint, SectionSegment


ROLE_MAPPING = SurfaceRoleMapping(
    "material",
    (("face", "face"), ("berm", "berm"), ("road", "road")),
)


def _polygon(*points: tuple[float, float]) -> PlanPolygon:
    ring = tuple(PlanPoint(*point) for point in points)
    if ring[0] != ring[-1]:
        ring = (*ring, ring[0])
    return PlanPolygon(ring)


def _strip_surface(
    stations: tuple[tuple[float, float, tuple[float, float]], ...],
    cross_sections: tuple[tuple[float, float], ...],
    roles: tuple[str, ...],
) -> TriangleSurface:
    vertices: list[SurfaceVertex] = []
    rows: list[tuple[int, ...]] = []
    for x, y, normal in stations:
        row = []
        for offset, elevation in cross_sections:
            row.append(len(vertices))
            vertices.append(SurfaceVertex(
                x + normal[0] * offset,
                y + normal[1] * offset,
                elevation,
            ))
        rows.append(tuple(row))
    triangles: list[SurfaceTriangle] = []
    for station_index, (first, second) in enumerate(zip(rows, rows[1:])):
        for band_index, role in enumerate(roles):
            a, b = first[band_index], first[band_index + 1]
            c, d = second[band_index], second[band_index + 1]
            triangles.extend((
                SurfaceTriangle(
                    (a, b, c),
                    source_id=f"{role}-{station_index}-{band_index}-a",
                    source_attributes={"material": role},
                ),
                SurfaceTriangle(
                    (b, d, c),
                    source_id=f"{role}-{station_index}-{band_index}-b",
                    source_attributes={"material": role},
                ),
            ))
    return TriangleSurface(tuple(vertices), tuple(triangles))


def _combined_surface(*surfaces: TriangleSurface) -> TriangleSurface:
    vertices: list[SurfaceVertex] = []
    triangles: list[SurfaceTriangle] = []
    for surface in surfaces:
        offset = len(vertices)
        vertices.extend(surface.vertices)
        triangles.extend(
            SurfaceTriangle(
                tuple(index + offset for index in triangle.vertex_indices),
                triangle.source_id,
                triangle.source_attributes,
            )
            for triangle in surface.triangles
        )
    return TriangleSurface(tuple(vertices), tuple(triangles))


def _straight_surface(*, actual_offset: float = 0.0) -> TriangleSurface:
    stations = (
        (0.0, 0.0, (0.0, 1.0)),
        (12.0, 0.0, (0.0, 1.0)),
    )
    sections = (
        (-2.0, 20.0 + actual_offset),
        (0.0, 20.0 + actual_offset),
        (4.0, 10.0 + actual_offset),
        (7.0, 10.0 + actual_offset),
        (11.0, 0.0 + actual_offset),
    )
    return _strip_surface(
        stations,
        sections,
        ("berm", "face", "berm", "face"),
    )


def _surface_with_upper_context(*, upper_role: str, include_road: bool = False):
    sections = (
        ((-4.0, 20.0), (-2.0, 20.0), (0.0, 20.0), (4.0, 10.0),
         (7.0, 10.0), (11.0, 0.0))
        if include_road
        else ((-2.0, 20.0), (0.0, 20.0), (4.0, 10.0),
              (7.0, 10.0), (11.0, 0.0))
    )
    roles = (
        ("road", upper_role, "face", "berm", "face")
        if include_road
        else (upper_role, "face", "berm", "face")
    )
    return _strip_surface(
        ((0.0, 0.0, (0.0, 1.0)), (12.0, 0.0, (0.0, 1.0))),
        sections,
        roles,
    )


def _surface_with_connected_remote_wall() -> TriangleSurface:
    return _strip_surface(
        (
            (0.0, 0.0, (0.0, 1.0)),
            (12.0, 0.0, (0.0, 1.0)),
        ),
        (
            (-2.0, 20.0),
            (0.0, 20.0),
            (4.0, 10.0),
            (7.0, 10.0),
            (11.0, 0.0),
            (14.0, 0.0),
            (16.0, 5.0),  # reverse-slope Face separates the remote wall
            (20.0, 5.0),
            (24.0, -5.0),
        ),
        ("berm", "face", "berm", "face", "road", "face", "berm", "face"),
    )


def _straight_alignment(*, reverse: bool = False) -> WallAlignment:
    points = (
        PlanPoint(0.0, 2.0),
        PlanPoint(0.0, 2.0),  # duplicate input is intentionally harmless
        PlanPoint(12.0, 2.0),
    )
    return WallAlignment(tuple(reversed(points)) if reverse else points)


def _assessment() -> PlanPolygon:
    return _polygon((-1.0, -1.0), (13.0, -1.0), (13.0, 12.0), (-1.0, 12.0))


def _placement_signature(result):
    return tuple(sorted(
        (
            round(item.alignment_point.x, 8),
            round(item.alignment_point.y, 8),
            round(item.downwall_xy[0], 8),
            round(item.downwall_xy[1], 8),
        )
        for item in result.placements
    ))


def test_straight_alignment_stations_by_true_chainage_at_bounded_spacing() -> None:
    result = place_profiles_from_alignment(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        assessment_polygon=_assessment(),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert result.supported
    assert result.diagnostics == ()
    assert result.station_chainages_m == pytest.approx((0.0, 3.0, 6.0, 9.0, 12.0))
    assert max(
        second - first
        for first, second in zip(
            result.station_chainages_m, result.station_chainages_m[1:]
        )
    ) <= 3.0
    for placement in result.placements:
        assert placement.downwall_xy == pytest.approx((0.0, 1.0))
        assert placement.wall_strike_xy[0] * placement.downwall_xy[0] + (
            placement.wall_strike_xy[1] * placement.downwall_xy[1]
        ) == pytest.approx(0.0)
        assert placement.guide_tangent_xy == pytest.approx((1.0, 0.0))


def test_skewed_manual_alignment_does_not_rotate_design_face_profile() -> None:
    alignment = WallAlignment((PlanPoint(0.0, 1.0), PlanPoint(12.0, 7.0)))
    result = place_profiles_from_alignment(
        alignment=alignment,
        design_surface=_straight_surface(),
        assessment_polygon=_assessment(),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert result.supported
    for placement in result.placements:
        expected_point, guide_tangent = alignment.point_and_tangent_at(
            placement.chainage_m
        )
        manual_normal = -guide_tangent[1], guide_tangent[0]
        assert placement.alignment_point == expected_point
        assert placement.guide_tangent_xy == pytest.approx(guide_tangent)
        assert placement.downwall_xy == pytest.approx((0.0, 1.0))
        assert placement.face_downwall_xy == pytest.approx((0.0, 1.0))
        assert placement.wall_strike_xy == pytest.approx((1.0, 0.0))
        assert abs(
            manual_normal[0] * placement.downwall_xy[0]
            + manual_normal[1] * placement.downwall_xy[1]
        ) < 0.9


def test_kinked_manual_alignment_changes_locality_but_not_profile_azimuth() -> None:
    alignment = WallAlignment((
        PlanPoint(0.0, 2.0),
        PlanPoint(3.0, 3.2),
        PlanPoint(6.0, 1.1),
        PlanPoint(9.0, 3.4),
        PlanPoint(12.0, 2.0),
    ))
    result = place_profiles_from_alignment(
        alignment=alignment,
        design_surface=_straight_surface(),
        assessment_polygon=_assessment(),
        role_mapping=ROLE_MAPPING,
        spacing_m=1.5,
    )

    assert result.supported
    assert len({
        tuple(round(value, 3) for value in placement.guide_tangent_xy)
        for placement in result.placements
    }) > 3
    assert all(
        placement.downwall_xy == pytest.approx((0.0, 1.0))
        and placement.wall_strike_xy == pytest.approx((1.0, 0.0))
        for placement in result.placements
    )


def _curved_fixture() -> tuple[TriangleSurface, WallAlignment, PlanPolygon]:
    centers = (
        (0.0, 0.0),
        (5.0, 0.4),
        (10.0, 1.5),
        (15.0, 3.2),
        (20.0, 5.5),
    )
    stations = []
    normals = []
    for index, point in enumerate(centers):
        previous = centers[max(0, index - 1)]
        following = centers[min(len(centers) - 1, index + 1)]
        tangent = following[0] - previous[0], following[1] - previous[1]
        length = hypot(*tangent)
        tangent = tangent[0] / length, tangent[1] / length
        normal = -tangent[1], tangent[0]
        normals.append(normal)
        stations.append((point[0], point[1], normal))
    surface = _strip_surface(
        tuple(stations),
        ((-2.0, 20.0), (0.0, 20.0), (4.0, 10.0), (8.0, 10.0)),
        ("berm", "face", "berm"),
    )
    alignment = WallAlignment(tuple(
        PlanPoint(x + normal[0] * 2.0, y + normal[1] * 2.0)
        for (x, y), normal in zip(centers, normals)
    ))
    upper = tuple(
        PlanPoint(x + normal[0] * -1.0, y + normal[1] * -1.0)
        for (x, y), normal in zip(centers, normals)
    )
    lower = tuple(
        PlanPoint(x + normal[0] * 7.0, y + normal[1] * 7.0)
        for (x, y), normal in zip(centers, normals)
    )
    assessment = PlanPolygon((*upper, *reversed(lower), upper[0]))
    return surface, alignment, assessment


def test_curved_alignment_rotates_smoothly_without_downwall_sign_flips() -> None:
    surface, alignment, assessment = _curved_fixture()
    result = place_profiles_from_alignment(
        alignment=alignment,
        design_surface=surface,
        assessment_polygon=assessment,
        role_mapping=ROLE_MAPPING,
        spacing_m=2.5,
    )

    assert result.supported
    normals = tuple(placement.downwall_xy for placement in result.placements)
    assert len(normals) >= 8
    assert result.diagnostics == ()
    assert max(normal[0] for normal in normals) - min(normal[0] for normal in normals) > 0.25
    assert all(
        first[0] * second[0] + first[1] * second[1] > 0.95
        for first, second in zip(normals, normals[1:])
    )
    assert not any(
        diagnostic.code == "profile_crossing_in_assessment"
        for diagnostic in result.diagnostics
    )


def test_face_not_alignment_order_chooses_physical_downwall_sign() -> None:
    common = dict(
        design_surface=_straight_surface(),
        assessment_polygon=_assessment(),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    forward = place_profiles_from_alignment(
        alignment=_straight_alignment(), **common
    )
    reverse = place_profiles_from_alignment(
        alignment=_straight_alignment(reverse=True), **common
    )

    assert _placement_signature(reverse) == _placement_signature(forward)
    assert all(item.downwall_xy == pytest.approx((0.0, 1.0)) for item in reverse.placements)
    assert all(item.guide_tangent_xy == pytest.approx((1.0, 0.0))
               for item in forward.placements)
    assert all(item.guide_tangent_xy == pytest.approx((-1.0, 0.0))
               for item in reverse.placements)
    assert all(item.wall_strike_xy == pytest.approx((1.0, 0.0))
               for item in forward.placements)
    assert all(item.wall_strike_xy == pytest.approx((-1.0, 0.0))
               for item in reverse.placements)


def test_multi_bench_design_remains_one_semantic_vertical_section() -> None:
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        assessment_polygon=_assessment(),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert len(result.profiles) == 5
    assert all(
        profile.design_section.topology_signature == "FACE-BERM-FACE"
        for profile in result.profiles
    )
    assert all(profile.alignment.origin.z == pytest.approx(20.0) for profile in result.profiles)
    # The Assessment interval is the same exact U interval used for the
    # clipped displayed/evaluated section and the Plan overlay.
    assert all(profile.assessment_u_interval is not None for profile in result.profiles)


def test_all_local_face_tiers_contribute_but_road_has_no_direction_authority() -> None:
    design = _straight_surface()
    contradictory_road = _strip_surface(
        ((0.0, 0.0, (0.0, 1.0)), (12.0, 0.0, (0.0, 1.0))),
        ((0.0, 0.0), (11.0, 1000.0)),
        ("road",),
    )
    common = dict(
        alignment=_straight_alignment(),
        assessment_polygon=_assessment(),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    face_only = place_profiles_from_alignment(design_surface=design, **common)
    with_road = place_profiles_from_alignment(
        design_surface=_combined_surface(design, contradictory_road), **common
    )

    assert _placement_signature(with_road) == _placement_signature(face_only)
    assert all(placement.face_sample_count >= 4 for placement in face_only.placements)
    assert all(
        len(placement.supporting_face_triangle_indices) >= 2
        for placement in face_only.placements
    )
    assert tuple(
        (
            placement.face_angular_dispersion_degrees,
            placement.face_angular_support_degrees,
        )
        for placement in with_road.placements
    ) == tuple(
        (
            placement.face_angular_dispersion_degrees,
            placement.face_angular_support_degrees,
        )
        for placement in face_only.placements
    )


def test_contradictory_local_face_directions_are_rejected_deterministically() -> None:
    downwall_positive = _strip_surface(
        ((0.0, 0.0, (0.0, 1.0)), (12.0, 0.0, (0.0, 1.0))),
        ((0.0, 20.0), (4.0, 10.0)),
        ("face",),
    )
    downwall_negative = _strip_surface(
        ((0.0, 0.0, (0.0, 1.0)), (12.0, 0.0, (0.0, 1.0))),
        ((0.0, 10.0), (4.0, 20.0)),
        ("face",),
    )
    result = place_profiles_from_alignment(
        alignment=WallAlignment((PlanPoint(0.0, 2.0), PlanPoint(12.0, 2.0))),
        design_surface=_combined_surface(downwall_positive, downwall_negative),
        assessment_polygon=_polygon(
            (-1.0, 0.0), (13.0, 0.0), (13.0, 4.0), (-1.0, 4.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert result.placements == ()
    assert tuple(diagnostic.code for diagnostic in result.diagnostics) == (
        "contradictory_face_direction",
    ) * 5


def test_final_face_seeds_are_crossed_by_design_derived_profile_line() -> None:
    design = _strip_surface(
        tuple(
            (x, 0.0, (0.0, 1.0))
            for x in (0.0, 4.0, 8.0, 12.0)
        ),
        ((0.0, 20.0), (4.0, 10.0), (7.0, 10.0), (11.0, 0.0)),
        ("face", "berm", "face"),
    )
    alignment = WallAlignment((PlanPoint(0.0, 1.0), PlanPoint(12.0, 7.0)))
    assessment = _assessment()
    result = place_profiles_from_alignment(
        alignment=alignment,
        design_surface=design,
        assessment_polygon=assessment,
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )
    fragments = _face_fragments(design, assessment, ROLE_MAPPING)

    assert result.supported
    differs_from_guide_seed = False
    for placement in result.placements:
        final_seeds = {
            fragment.triangle_index
            for fragment in _crossing_fragments(
                fragments, placement.alignment_point, placement.wall_strike_xy
            )
        }
        guide_seeds = {
            fragment.triangle_index
            for fragment in _crossing_fragments(
                fragments, placement.alignment_point, placement.guide_tangent_xy
            )
        }
        assert set(placement.supporting_face_triangle_indices) == final_seeds
        differs_from_guide_seed |= final_seeds != guide_seeds
    assert differs_from_guide_seed


def test_local_design_run_stops_at_reverse_face_then_clips_to_assessment() -> None:
    result = build_alignment_profile_sections(
        alignment=WallAlignment((PlanPoint(0.0, 2.5), PlanPoint(12.0, 2.5))),
        design_surface=_surface_with_connected_remote_wall(),
        assessment_polygon=_polygon(
            (-1.0, 2.0), (13.0, 2.0), (13.0, 3.0), (-1.0, 3.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert len(result.profiles) == 5
    assert all(profile.assessment_u_interval == pytest.approx((2.0, 3.0))
               for profile in result.profiles)
    assert all(profile.design_section.topology_signature == "FACE"
               for profile in result.profiles)
    assert all(
        (min(segment.u_min for segment in profile.design_segments),
         max(segment.u_max for segment in profile.design_segments))
        == pytest.approx((2.0, 3.0))
        for profile in result.profiles
    )
    assert all(
        element.vertical_change <= 1e-5
        for profile in result.profiles
        for element in profile.design_section.elements
        if element.role == "face"
    )


def test_assessment_interval_clips_partial_multi_bench_design_and_actual() -> None:
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        actual_surface=_straight_surface(actual_offset=0.5),
        assessment_polygon=_polygon(
            (-1.0, 2.0), (13.0, 2.0), (13.0, 8.0), (-1.0, 8.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert len(result.profiles) == 5
    for profile in result.profiles:
        assert profile.assessment_u_interval == pytest.approx((2.0, 8.0))
        assert profile.design_section.topology_signature == "FACE-BERM-FACE"
        assert min(segment.u_min for segment in profile.design_segments) == pytest.approx(2.0)
        assert max(segment.u_max for segment in profile.design_segments) == pytest.approx(8.0)
        # Design-elevation clipping can trim Actual further, but it may not
        # introduce any section geometry outside the same Assessment interval.
        assert min(segment.u_min for segment in profile.actual_segments) >= 2.0
        assert max(segment.u_max for segment in profile.actual_segments) <= 8.0


def test_assessment_boundary_at_face_keeps_only_immediate_upper_berm_context() -> None:
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_surface_with_upper_context(upper_role="berm", include_road=True),
        assessment_polygon=_polygon(
            (-1.0, 0.0), (13.0, 0.0), (13.0, 8.0), (-1.0, 8.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    for profile in result.profiles:
        context = profile.design_section.upstream_context
        assert profile.assessment_u_interval == pytest.approx((0.0, 8.0))
        assert context is not None
        assert context.role == "berm"
        assert (context.start.u, context.end.u) == pytest.approx((-2.0, 0.0))
        assert all(segment.u_min >= 0.0 for segment in profile.design_segments)
        assert "road" not in profile.design_section.topology_signature.lower()


def test_assessment_boundary_at_face_keeps_immediate_upper_road_context() -> None:
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_surface_with_upper_context(upper_role="road"),
        assessment_polygon=_polygon(
            (-1.0, 0.0), (13.0, 0.0), (13.0, 4.0), (-1.0, 4.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert all(
        profile.design_section.upstream_context is not None
        and profile.design_section.upstream_context.role == "road"
        for profile in result.profiles
    )


def test_partial_face_and_assessed_upper_platform_do_not_create_context() -> None:
    inside_face = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_surface_with_upper_context(upper_role="berm"),
        assessment_polygon=_polygon(
            (-1.0, 2.0), (13.0, 2.0), (13.0, 8.0), (-1.0, 8.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )
    inside_berm = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_surface_with_upper_context(upper_role="berm"),
        assessment_polygon=_polygon(
            (-1.0, -1.0), (13.0, -1.0), (13.0, 8.0), (-1.0, 8.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert all(profile.design_section.upstream_context is None for profile in inside_face.profiles)
    assert all(profile.design_section.upstream_context is None for profile in inside_berm.profiles)


def test_noncontiguous_or_face_predecessor_never_becomes_context() -> None:
    evaluated = build_design_section((
        SectionSegment(
            SectionPoint(0.0, 20.0, 0.0, 0.0),
            SectionPoint(4.0, 10.0, 4.0, 0.0),
            2,
            "face",
        ),
    ))
    gapped_berm = (
        SectionSegment(SectionPoint(-2.0, 20.0, -2.0, 0.0), SectionPoint(-1.0, 20.0, -1.0, 0.0), 1, "berm"),
        SectionSegment(SectionPoint(0.0, 20.0, 0.0, 0.0), SectionPoint(4.0, 10.0, 4.0, 0.0), 2, "face"),
    )
    upstream_face = (
        SectionSegment(SectionPoint(-2.0, 25.0, -2.0, 0.0), SectionPoint(0.0, 20.0, 0.0, 0.0), 1, "face"),
        SectionSegment(SectionPoint(0.0, 20.0, 0.0, 0.0), SectionPoint(4.0, 10.0, 4.0, 0.0), 2, "face"),
    )

    assert _upstream_context_for_assessed_section(evaluated, gapped_berm, (0.0, 4.0)) is None
    assert _upstream_context_for_assessed_section(evaluated, upstream_face, (0.0, 4.0)) is None


def test_assessment_interval_can_start_downwall_of_crest_without_rezeroing() -> None:
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        assessment_polygon=_polygon(
            (-1.0, 6.0), (13.0, 6.0), (13.0, 10.0), (-1.0, 10.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert all(profile.alignment.origin.z == pytest.approx(20.0) for profile in result.profiles)
    assert all(profile.assessment_u_interval == pytest.approx((6.0, 10.0))
               for profile in result.profiles)
    assert all(min(segment.u_min for segment in profile.design_segments) == pytest.approx(6.0)
               for profile in result.profiles)


def test_assessment_interval_retains_negative_u_upstream_of_crest() -> None:
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        assessment_polygon=_polygon(
            (-1.0, -1.0), (13.0, -1.0), (13.0, 3.0), (-1.0, 3.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert all(profile.assessment_u_interval == pytest.approx((-1.0, 3.0))
               for profile in result.profiles)
    assert all(min(segment.u_min for segment in profile.design_segments) == pytest.approx(-1.0)
               for profile in result.profiles)


def _concave_assessment(*, upper_interval: tuple[float, float]) -> PlanPolygon:
    lower_start, lower_end = 1.0, 3.0
    upper_start, upper_end = upper_interval
    return _polygon(
        (-1.0, lower_start), (13.0, lower_start), (13.0, lower_end),
        (1.0, lower_end), (1.0, upper_start), (13.0, upper_start),
        (13.0, upper_end), (-1.0, upper_end),
    )


def test_concave_assessment_uses_only_interval_with_supported_face() -> None:
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        assessment_polygon=_concave_assessment(upper_interval=(5.0, 6.0)),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    # The second interval lies on a Berm. Profile 2 crosses both intervals
    # but retains only the one with the Assessment-supported Face.
    profile = result.profiles[1]
    assert profile.assessment_u_interval == pytest.approx((1.0, 3.0))
    assert max(segment.u_max for segment in profile.design_segments) == pytest.approx(3.0)


def test_ambiguous_concave_supported_face_intervals_are_rejected() -> None:
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        assessment_polygon=_concave_assessment(upper_interval=(8.0, 9.0)),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert any(
        diagnostic.code == "design_section_assembly_failed"
        and "multiple supported Design Face intervals" in diagnostic.message
        for diagnostic in result.diagnostics
    )


def test_missing_face_support_omits_stations_with_deterministic_diagnostics() -> None:
    result = place_profiles_from_alignment(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        assessment_polygon=_polygon(
            (-1.0, -3.0), (13.0, -3.0), (13.0, -1.0), (-1.0, -1.0)
        ),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    assert not result.supported
    assert result.placements == ()
    assert tuple(item.code for item in result.diagnostics) == (
        "insufficient_face_support",
    ) * 5


def test_assessment_ring_rotation_and_orientation_do_not_rotate_profiles() -> None:
    first = _assessment()
    vertices = first.ring[:-1]
    rotated_reversed = tuple(reversed(vertices[2:] + vertices[:2]))
    second = PlanPolygon((*rotated_reversed, rotated_reversed[0]))
    common = dict(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    first_result = place_profiles_from_alignment(
        assessment_polygon=first, **common
    )
    second_result = place_profiles_from_alignment(
        assessment_polygon=second, **common
    )

    assert _placement_signature(second_result) == _placement_signature(first_result)


def test_actual_surface_is_intersected_only_after_identical_placement() -> None:
    common = dict(
        alignment=_straight_alignment(),
        design_surface=_straight_surface(),
        assessment_polygon=_assessment(),
        role_mapping=ROLE_MAPPING,
        spacing_m=3.0,
    )

    first = build_alignment_profile_sections(
        actual_surface=_straight_surface(actual_offset=0.5), **common
    )
    second = build_alignment_profile_sections(
        actual_surface=_straight_surface(actual_offset=-0.5), **common
    )

    assert _placement_signature(second.placement_result) == _placement_signature(
        first.placement_result
    )
    assert first.profiles[0].actual_segments != second.profiles[0].actual_segments
