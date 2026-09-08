from __future__ import annotations

from dataclasses import replace
from math import cos, hypot, pi, sin
from pathlib import Path

import pytest

import domain.wall_conformance.profile_sections as profile_sections_module
from domain.geometry.surfaces import (
    SurfaceTriangle,
    SurfaceVertex,
    TriangleSurface,
)
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance.models import SurfaceRoleMapping
from domain.wall_conformance.profile_pipeline import ProfilePipelineDiagnostic
from domain.wall_conformance.profile_sections import (
    ProfileSectionAssemblyError,
    build_v2_profile_sections,
)


ROLE_MAPPING = SurfaceRoleMapping(
    "ROLE",
    (("FACE", "face"), ("BERM", "berm"), ("ROAD", "road")),
)


class _MeshBuilder:
    def __init__(self) -> None:
        self.vertices: list[SurfaceVertex] = []
        self.triangles: list[SurfaceTriangle] = []
        self._indices: dict[tuple[float, float, float], int] = {}

    def vertex(self, x: float, y: float, z: float) -> int:
        key = float(x), float(y), float(z)
        if key not in self._indices:
            self._indices[key] = len(self.vertices)
            self.vertices.append(SurfaceVertex(*key))
        return self._indices[key]

    def strip(
        self,
        upper: tuple[tuple[float, float], ...],
        upper_z: float,
        lower: tuple[tuple[float, float], ...],
        lower_z: float,
        role: str,
        prefix: str,
    ) -> None:
        for index in range(len(upper) - 1):
            a = self.vertex(*upper[index], upper_z)
            b = self.vertex(*upper[index + 1], upper_z)
            c = self.vertex(*lower[index], lower_z)
            d = self.vertex(*lower[index + 1], lower_z)
            attributes = {"ROLE": role.upper()}
            self.triangles.extend((
                SurfaceTriangle((a, c, d), f"{prefix}:{index}:0", attributes),
                SurfaceTriangle((a, d, b), f"{prefix}:{index}:1", attributes),
            ))

    def triangle(
        self,
        indices: tuple[int, int, int],
        role: str,
        source_id: str,
    ) -> None:
        self.triangles.append(SurfaceTriangle(
            indices, source_id, {"ROLE": role.upper()}
        ))

    def surface(self) -> TriangleSurface:
        return TriangleSurface(tuple(self.vertices), tuple(self.triangles))


def _line(x: float, values: tuple[float, ...]) -> tuple[tuple[float, float], ...]:
    return tuple((x, value) for value in values)


def _layered(
    roles: tuple[str, ...],
    *,
    y_values: tuple[float, ...] = (0.0, 10.0, 20.0),
) -> TriangleSurface:
    builder = _MeshBuilder()
    x, z = 0.0, 40.0
    for index, role in enumerate(roles):
        next_x = x + 4.0
        next_z = z - 10.0 if role == "face" else z
        builder.strip(
            _line(x, y_values),
            z,
            _line(next_x, y_values),
            next_z,
            role,
            f"{role}:{index}",
        )
        x, z = next_x, next_z
    return builder.surface()


def _arc(radius: float, angles: tuple[float, ...]):
    return tuple((radius * cos(angle), radius * sin(angle)) for angle in angles)


def _layered_arc(
    roles: tuple[str, ...], *, segment_count: int = 18
) -> TriangleSurface:
    angles = tuple(
        (60.0 * index / segment_count) * pi / 180.0
        for index in range(segment_count + 1)
    )
    builder = _MeshBuilder()
    radius, z = 20.0, 50.0
    for index, role in enumerate(roles):
        next_radius = radius + (4.0 if role == "face" else 3.0)
        next_z = z - 10.0 if role == "face" else z
        builder.strip(
            _arc(radius, angles),
            z,
            _arc(next_radius, angles),
            next_z,
            role,
            f"{role}:{index}",
        )
        radius, z = next_radius, next_z
    return builder.surface()


def _rectangle(x0: float, y0: float, x1: float, y1: float) -> PlanPolygon:
    return PlanPolygon((
        PlanPoint(x0, y0),
        PlanPoint(x1, y0),
        PlanPoint(x1, y1),
        PlanPoint(x0, y1),
        PlanPoint(x0, y0),
    ))


def _annular_area(
    inner_radius: float,
    outer_radius: float,
    start_degrees: float,
    end_degrees: float,
) -> PlanPolygon:
    start = start_degrees * pi / 180.0
    end = end_degrees * pi / 180.0
    points = (
        PlanPoint(inner_radius * cos(start), inner_radius * sin(start)),
        PlanPoint(outer_radius * cos(start), outer_radius * sin(start)),
        PlanPoint(outer_radius * cos(end), outer_radius * sin(end)),
        PlanPoint(inner_radius * cos(end), inner_radius * sin(end)),
    )
    return PlanPolygon((*points, points[0]))


def _shift_x(surface: TriangleSurface, value: float) -> TriangleSurface:
    return replace(
        surface,
        vertices=tuple(
            replace(vertex, x=vertex.x + value) for vertex in surface.vertices
        ),
    )


def _shift_z(surface: TriangleSurface, value: float) -> TriangleSurface:
    return replace(
        surface,
        vertices=tuple(
            replace(vertex, z=vertex.z + value) for vertex in surface.vertices
        ),
    )


def _reordered(surface: TriangleSurface) -> TriangleSurface:
    vertex_order = tuple(reversed(range(len(surface.vertices))))
    old_to_new = {old: new for new, old in enumerate(vertex_order)}
    triangles = tuple(
        replace(
            triangle,
            vertex_indices=tuple(
                old_to_new[index]
                for index in reversed(triangle.vertex_indices)
            ),
        )
        for triangle in reversed(surface.triangles)
    )
    return TriangleSurface(
        tuple(surface.vertices[index] for index in vertex_order), triangles
    )


def _non_manifold_and_clean() -> TriangleSurface:
    builder = _MeshBuilder()
    builder.strip(
        _line(0.0, (0.0, 10.0)),
        10.0,
        _line(4.0, (0.0, 10.0)),
        0.0,
        "face",
        "affected",
    )
    a = builder.vertex(4.0, 0.0, 0.0)
    b = builder.vertex(4.0, 10.0, 0.0)
    c = builder.vertex(8.0, 0.0, 0.0)
    d = builder.vertex(8.0, 10.0, 0.0)
    builder.triangle((a, c, b), "berm", "affected:platform:a")
    builder.triangle((a, b, d), "berm", "affected:platform:b")
    builder.strip(
        _line(20.0, (0.0, 10.0)),
        10.0,
        _line(24.0, (0.0, 10.0)),
        0.0,
        "face",
        "clean",
    )
    return builder.surface()


def _zero_width_convergence() -> TriangleSurface:
    builder = _MeshBuilder()
    first = builder.vertex(0.0, 0.0, 10.0)
    second = builder.vertex(0.0, 10.0, 10.0)
    apex = builder.vertex(4.0, 5.0, 0.0)
    builder.triangle((first, apex, second), "face", "converging")
    return builder.surface()


def _points(segments):
    return {
        (round(point.u, 6), round(point.z, 6))
        for segment in segments
        for point in (segment.start, segment.end)
    }


def _placement_signature(result):
    return tuple(
        (
            round(profile.alignment.chainage_m, 8),
            round(profile.alignment.origin.x, 8),
            round(profile.alignment.origin.y, 8),
            round(profile.alignment.origin.z, 8),
            tuple(round(value, 8) for value in profile.alignment.normal_xy),
            profile.design_section.topology_signature,
        )
        for profile in result.profile_set.profiles
    )


def test_straight_face_uses_exact_upper_and_phase3_plus_u() -> None:
    design = _layered(("face",))
    result = build_v2_profile_sections(
        design,
        design,
        _rectangle(1.0, 4.0, 3.0, 16.0),
        ROLE_MAPPING,
        requested_spacing_m=5.0,
    )

    assert result.profile_set.profiles
    for profile in result.profile_set.profiles:
        assert profile.alignment.origin.x == pytest.approx(0.0)
        assert profile.alignment.origin.z == pytest.approx(40.0)
        assert profile.alignment.normal_xy == pytest.approx((1.0, 0.0))
        assert profile.alignment.tangent_xy == pytest.approx((0.0, 1.0))
        assert profile.assessment_u_interval == pytest.approx((0.0, 4.0))
        assert profile.design_section.topology_signature == "FACE"
        assert profile.design_section.elements[0].start.u == pytest.approx(0.0)
        assert profile.design_section.elements[0].start.z == pytest.approx(40.0)


@pytest.mark.parametrize(
    ("roles", "signature"),
    (
        (("face", "berm", "face"), "FACE-BERM-FACE"),
        (
            ("face", "berm", "face", "road", "face"),
            "FACE-BERM-FACE-ROAD-FACE",
        ),
    ),
)
def test_multibench_trace_remains_one_full_design_section(
    roles: tuple[str, ...], signature: str
) -> None:
    design = _layered(roles)
    result = build_v2_profile_sections(
        design,
        design,
        _rectangle(5.0, 5.0, 11.0, 15.0),
        ROLE_MAPPING,
        requested_spacing_m=20.0,
    )

    assert len(result.profile_set.profiles) == len(
        result.placement_result.profiles
    )
    assert {
        profile.design_section.topology_signature
        for profile in result.profile_set.profiles
    } == {signature}


def test_smooth_curve_uses_exact_phase3_planes_without_normal_recomputation() -> None:
    design = _layered_arc(("face",))
    result = build_v2_profile_sections(
        design,
        design,
        _annular_area(20.5, 23.5, 10.0, 50.0),
        ROLE_MAPPING,
        requested_spacing_m=3.0,
    )
    placed = result.placement_result.supported_sector_results[0].profiles
    assembled = result.profile_set.profiles

    assert len(assembled) == len(placed)
    for profile, trace in zip(assembled, placed, strict=True):
        assert profile.alignment.normal_xy == pytest.approx(trace.downwall_xy)
        assert profile.alignment.origin.x == pytest.approx(trace.plan_start.x)
        assert profile.alignment.origin.y == pytest.approx(trace.plan_start.y)
        assert profile.alignment.chainage_m == pytest.approx(
            trace.upper_chainage_m
        )
        assert profile.alignment.tangent_xy == pytest.approx(
            (-trace.downwall_xy[1], trace.downwall_xy[0])
        )


def test_middle_only_assessment_keeps_external_upper_and_full_corridor() -> None:
    design = _layered(("face", "berm", "face", "road", "face"))
    assessment = _rectangle(9.0, 4.0, 11.0, 16.0)
    result = build_v2_profile_sections(
        design,
        design,
        assessment,
        ROLE_MAPPING,
        requested_spacing_m=5.0,
    )
    reversed_result = build_v2_profile_sections(
        design,
        design,
        PlanPolygon(tuple(reversed(assessment.ring))),
        ROLE_MAPPING,
        requested_spacing_m=5.0,
    )

    assert _placement_signature(reversed_result) == _placement_signature(result)
    for profile in result.profile_set.profiles:
        assert profile.alignment.origin.x == pytest.approx(0.0)
        assert profile.assessment_u_interval == pytest.approx((0.0, 20.0))
        assert (
            profile.design_section.topology_signature
            == "FACE-BERM-FACE-ROAD-FACE"
        )


def test_design_equal_actual_intersections_are_coincident() -> None:
    design = _layered(("face", "berm", "face"))
    result = build_v2_profile_sections(
        design,
        design,
        _rectangle(1.0, 4.0, 11.0, 16.0),
        ROLE_MAPPING,
        requested_spacing_m=5.0,
    )

    for profile in result.profile_set.profiles:
        assert _points(profile.actual_segments) == _points(
            tuple(
                segment
                for segment in profile.design_segments
                if segment.u_min >= -1e-8
            )
        )


def test_actual_shifted_one_metre_downwall_does_not_change_placement() -> None:
    design = _layered(("face",))
    area = _rectangle(1.0, 4.0, 3.0, 16.0)
    baseline = build_v2_profile_sections(
        design, design, area, ROLE_MAPPING, requested_spacing_m=5.0
    )
    shifted = build_v2_profile_sections(
        design,
        _shift_x(design, 1.0),
        area,
        ROLE_MAPPING,
        requested_spacing_m=5.0,
    )

    assert _placement_signature(shifted) == _placement_signature(baseline)
    assert all(
        (1.0, 40.0) in _points(profile.actual_segments)
        for profile in shifted.profile_set.profiles
    )


def test_very_different_actuals_change_only_actual_segments() -> None:
    design = _layered(("face", "berm", "face"))
    area = _rectangle(9.0, 4.0, 11.0, 16.0)
    first = build_v2_profile_sections(
        design, design, area, ROLE_MAPPING, requested_spacing_m=5.0
    )
    second = build_v2_profile_sections(
        design,
        _shift_z(_shift_x(design, 1.0), -2.0),
        area,
        ROLE_MAPPING,
        requested_spacing_m=5.0,
    )

    assert _placement_signature(second) == _placement_signature(first)
    assert tuple(
        profile.actual_segments for profile in second.profile_set.profiles
    ) != tuple(profile.actual_segments for profile in first.profile_set.profiles)


def test_downstream_extent_is_not_a_fake_lower_toe() -> None:
    design = _layered(("face",))
    result = build_v2_profile_sections(
        design,
        design,
        _rectangle(1.0, 4.0, 3.0, 16.0),
        ROLE_MAPPING,
        requested_spacing_m=5.0,
    )

    assert result.profile_set.toe_lines == ()
    assert all(
        profile.external_toe is None
        and profile.assessment_u_interval[1] == pytest.approx(4.0)
        for profile in result.profile_set.profiles
    )
    assert all(
        any(
            point.u == pytest.approx(4.0)
            and point.x == pytest.approx(4.0)
            and point.z == pytest.approx(30.0)
            for segment in profile.design_segments
            for point in (segment.start, segment.end)
        )
        for profile in result.profile_set.profiles
    )


def test_nonincident_downstream_extent_rejects_the_interval(
    monkeypatch,
) -> None:
    design = _layered(("face",))
    original = profile_sections_module._design_section_for_trace

    def shorten_design_corridor(*args, **kwargs):
        segments, _section = original(*args, **kwargs)
        downstream_u = args[-1]
        shortened = profile_sections_module.clip_section_segments_to_u_interval(
            segments, 0.0, downstream_u - 1.0
        )
        return shortened, profile_sections_module.build_design_section(shortened)

    monkeypatch.setattr(
        profile_sections_module,
        "_design_section_for_trace",
        shorten_design_corridor,
    )
    with pytest.raises(ProfileSectionAssemblyError) as caught:
        build_v2_profile_sections(
            design,
            design,
            _rectangle(1.0, 4.0, 3.0, 16.0),
            ROLE_MAPPING,
            requested_spacing_m=5.0,
        )

    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert "design_section_not_incident_to_downstream_extent" in codes
    assert "interval_section_assembly_failed" in codes


def test_explicit_lower_is_exact_toe_and_only_true_toe_line() -> None:
    design = _layered(("face", "berm"))
    result = build_v2_profile_sections(
        design,
        design,
        _rectangle(1.0, 4.0, 3.0, 16.0),
        ROLE_MAPPING,
        requested_spacing_m=5.0,
    )

    assert len(result.profile_set.toe_lines) == 1
    assert all(point.x == pytest.approx(4.0) for point in result.profile_set.toe_lines[0].points)
    assert all(
        profile.external_toe is not None
        and profile.external_toe.u == pytest.approx(4.0)
        and profile.external_toe.z == pytest.approx(30.0)
        for profile in result.profile_set.profiles
    )


def test_disconnected_assessed_intervals_produce_no_gap_profiles() -> None:
    design = _layered(("face",), y_values=(0.0, 10.0, 20.0, 30.0, 40.0))
    assessment = PlanPolygon(tuple(PlanPoint(*point) for point in (
        (-2.0, 1.0),
        (3.0, 1.0),
        (3.0, 9.0),
        (-1.0, 9.0),
        (-1.0, 31.0),
        (3.0, 31.0),
        (3.0, 39.0),
        (-2.0, 39.0),
        (-2.0, 1.0),
    )))
    result = build_v2_profile_sections(
        design,
        design,
        assessment,
        ROLE_MAPPING,
        requested_spacing_m=3.0,
    )
    origins_y = tuple(
        profile.alignment.origin.y
        for profile in result.profile_set.profiles
    )

    assert any(value < 10.0 for value in origins_y)
    assert any(value > 30.0 for value in origins_y)
    assert all(
        value <= 9.0 + 1e-7 or value >= 31.0 - 1e-7
        for value in origins_y
    )
    assert len(result.profile_set.crest_lines) == 1
    assert all(
        profile.alignment.boundary_component_index == 0
        for profile in result.profile_set.profiles
    )


def test_supported_interval_survives_failed_sibling_interval(
    monkeypatch,
) -> None:
    design = _layered(("face",), y_values=(0.0, 10.0, 20.0, 30.0, 40.0))
    assessment = PlanPolygon(tuple(PlanPoint(*point) for point in (
        (-2.0, 1.0),
        (3.0, 1.0),
        (3.0, 9.0),
        (-1.0, 9.0),
        (-1.0, 31.0),
        (3.0, 31.0),
        (3.0, 39.0),
        (-2.0, 39.0),
        (-2.0, 1.0),
    )))
    original = profile_sections_module.place_wall_sector_extraction_profiles

    def one_interval_fails(*args, **kwargs):
        placement = original(*args, **kwargs)
        sector_result = placement.sector_results[0]
        assert len(sector_result.interval_results) == 2
        good, rejected = sector_result.interval_results
        rejected = replace(
            rejected,
            profiles=(),
            supported=False,
            diagnostics=(ProfilePipelineDiagnostic(
                "synthetic_interval_failure",
                "Deterministic Phase-3 interval failure fixture",
                "phase1",
            ),),
        )
        sector_result = replace(
            sector_result,
            interval_results=(good, rejected),
            supported=False,
            diagnostics=(*sector_result.diagnostics, ProfilePipelineDiagnostic(
                "partial_assessed_interval_success",
                "Only one independently supported interval remains",
            )),
        )
        return replace(placement, sector_results=(sector_result,))

    monkeypatch.setattr(
        profile_sections_module,
        "place_wall_sector_extraction_profiles",
        one_interval_fails,
    )
    result = build_v2_profile_sections(
        design,
        design,
        assessment,
        ROLE_MAPPING,
        requested_spacing_m=3.0,
    )

    sector_result = result.placement_result.sector_results[0]
    assert not sector_result.supported
    assert sector_result.interval_results[0].supported
    assert not sector_result.interval_results[1].supported
    assert result.profile_set.profiles
    assert all(
        profile.alignment.origin.y <= 9.0 + 1e-7
        for profile in result.profile_set.profiles
    )
    assert "partial_assessed_interval_success" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_middle_trace_failure_discards_complete_supported_interval(
    monkeypatch,
) -> None:
    design = _layered(("face",))
    original = profile_sections_module._design_section_for_trace
    calls = 0

    def fail_second_trace(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError(
                "Deterministic middle-trace Design section failure fixture"
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(
        profile_sections_module,
        "_design_section_for_trace",
        fail_second_trace,
    )
    with pytest.raises(ProfileSectionAssemblyError) as caught:
        build_v2_profile_sections(
            design,
            design,
            _rectangle(1.0, 4.0, 3.0, 16.0),
            ROLE_MAPPING,
            requested_spacing_m=3.0,
        )

    interval = caught.value.placement_result.sector_results[0].interval_results[0]
    assert interval.supported
    assert len(interval.profiles) > 2
    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert "profile_section_assembly_failed" in codes
    assert "interval_section_assembly_failed" in codes


def test_valid_sector_survives_unrelated_unsupported_sector() -> None:
    design = _non_manifold_and_clean()
    result = build_v2_profile_sections(
        design,
        design,
        _rectangle(-3.0, 0.5, 23.0, 9.5),
        ROLE_MAPPING,
        requested_spacing_m=3.0,
    )

    assert any(item.supported for item in result.placement_result.sector_results)
    assert any(
        not item.supported for item in result.placement_result.sector_results
    )
    assert all(
        profile.alignment.origin.x == pytest.approx(20.0)
        for profile in result.profile_set.profiles
    )
    assert "local_non_manifold_topology" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_zero_width_convergence_produces_no_fake_production_profile() -> None:
    design = _zero_width_convergence()

    with pytest.raises(ProfileSectionAssemblyError) as caught:
        build_v2_profile_sections(
            design,
            design,
            _rectangle(0.5, 2.0, 3.9, 8.0),
            ROLE_MAPPING,
            requested_spacing_m=3.0,
        )

    assert not caught.value.placement_result.profiles
    assert "zero_width_convergence" in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


def test_storage_order_and_triangle_winding_are_deterministic() -> None:
    design = _layered(("face", "berm", "face", "road", "face"))
    area = _rectangle(9.0, 4.0, 11.0, 16.0)
    baseline = build_v2_profile_sections(
        design, design, area, ROLE_MAPPING, requested_spacing_m=5.0
    )
    reordered = _reordered(design)
    variant = build_v2_profile_sections(
        reordered, reordered, area, ROLE_MAPPING, requested_spacing_m=5.0
    )

    assert _placement_signature(variant) == _placement_signature(baseline)


def test_primary_curved_multibench_production_assembly() -> None:
    design = _layered_arc(("face", "berm", "face", "road", "face"))
    actual = _shift_z(design, -0.5)
    result = build_v2_profile_sections(
        design,
        actual,
        _annular_area(28.0, 30.5, 15.0, 45.0),
        ROLE_MAPPING,
        requested_spacing_m=2.5,
    )
    sector = result.placement_result.supported_sector_results[0]

    assert len(result.placement_result.supported_sector_results) == 1
    assert len(result.profile_set.profiles) == len(sector.profiles)
    assert result.profile_set.toe_lines == ()
    assert result.profile_set.design_variants[0].signature == (
        "FACE-BERM-FACE-ROAD-FACE"
    )
    assert sector.invariants is not None
    assert sector.invariants.non_crossing
    for profile, trace in zip(
        result.profile_set.profiles, sector.profiles, strict=True
    ):
        assert profile.alignment.normal_xy == pytest.approx(trace.downwall_xy)
        assert profile.alignment.origin.x == pytest.approx(trace.plan_start.x)
        assert profile.alignment.origin.y == pytest.approx(trace.plan_start.y)
        assert profile.design_section.elements[0].start.u == pytest.approx(0.0)
        assert (
            profile.design_section.topology_signature
            == "FACE-BERM-FACE-ROAD-FACE"
        )
        assert profile.actual_segments
        actual_start = min(
            (
                point
                for segment in profile.actual_segments
                for point in (segment.start, segment.end)
            ),
            key=lambda point: point.u,
        )
        assert actual_start.u == pytest.approx(0.0)
        assert actual_start.z == pytest.approx(
            profile.alignment.origin.z - 0.5
        )
        tx, ty = profile.alignment.tangent_xy
        assert all(
            (
                (point.x - profile.alignment.origin.x) * tx
                + (point.y - profile.alignment.origin.y) * ty
            )
            == pytest.approx(0.0, abs=1e-7)
            for segment in profile.actual_segments
            for point in (segment.start, segment.end)
        )


def test_phase4a_source_is_pure_and_has_no_legacy_placement_calls() -> None:
    source = Path("domain/wall_conformance/profile_sections.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "sample_wall_alignment",
        "select_primary_crest_line",
        "select_design_alignment",
        "_collect_external_upper_stations",
        "evaluate_upper_crest_station",
        "PySide6",
        "application.services",
        "ui.",
        "_external_toe_lines",
        "enforce_profile_engineering_invariants",
    )
    assert all(name not in source for name in forbidden)
