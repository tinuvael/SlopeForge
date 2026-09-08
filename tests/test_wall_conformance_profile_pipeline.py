from __future__ import annotations

from dataclasses import replace
from math import acos, cos, degrees, hypot, pi, sin
from pathlib import Path

import pytest

import domain.wall_conformance.profile_pipeline as pipeline_module
from domain.geometry.surfaces import (
    SurfaceTriangle,
    SurfaceVertex,
    TriangleSurface,
)
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance.design_topology import build_design_topology_index
from domain.wall_conformance.models import SurfaceRoleMapping
from domain.wall_conformance.profile_pipeline import (
    build_wall_profile_placements,
    place_wall_sector_extraction_profiles,
)
from domain.wall_conformance.profile_placement import (
    FaceDirectionSample,
    WallGuide,
)
from domain.wall_conformance.wall_sectors import (
    GuideStationMapping,
    StationInterval,
    WallSector,
    WallSectorDiagnostics,
    WallSectorExtractionResult,
    extract_wall_sectors,
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

    def triangle(
        self,
        indices: tuple[int, int, int],
        role: str,
        source_id: str,
    ) -> None:
        self.triangles.append(SurfaceTriangle(
            indices,
            source_id,
            {"ROLE": role.upper()},
        ))

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
            self.triangle((a, c, d), role, f"{prefix}:{index}:0")
            self.triangle((a, d, b), role, f"{prefix}:{index}:1")

    def surface(self) -> TriangleSurface:
        return TriangleSurface(tuple(self.vertices), tuple(self.triangles))


def _line_points(
    x: float, y_values: tuple[float, ...]
) -> tuple[tuple[float, float], ...]:
    return tuple((x, y) for y in y_values)


def _layered(
    roles: tuple[str, ...],
    *,
    y_values: tuple[float, ...] = (0.0, 10.0),
) -> TriangleSurface:
    builder = _MeshBuilder()
    x = 0.0
    z = 40.0
    for index, role in enumerate(roles):
        next_x = x + 4.0
        next_z = z - 10.0 if role == "face" else z
        builder.strip(
            _line_points(x, y_values),
            z,
            _line_points(next_x, y_values),
            next_z,
            role,
            f"{role}:{index}",
        )
        x, z = next_x, next_z
    return builder.surface()


def _arc_points(
    radius: float, angles: tuple[float, ...]
) -> tuple[tuple[float, float], ...]:
    return tuple((radius * cos(angle), radius * sin(angle)) for angle in angles)


def _layered_arc(
    roles: tuple[str, ...],
    *,
    segment_count: int = 12,
    start_degrees: float = 0.0,
    end_degrees: float = 60.0,
) -> TriangleSurface:
    angles = tuple(
        (start_degrees + (end_degrees - start_degrees) * index / segment_count)
        * pi
        / 180.0
        for index in range(segment_count + 1)
    )
    builder = _MeshBuilder()
    radius = 20.0
    z = 50.0
    for index, role in enumerate(roles):
        next_radius = radius + (4.0 if role == "face" else 3.0)
        next_z = z - 10.0 if role == "face" else z
        builder.strip(
            _arc_points(radius, angles),
            z,
            _arc_points(next_radius, angles),
            next_z,
            role,
            f"{role}:{index}",
        )
        radius, z = next_radius, next_z
    return builder.surface()


def _rectangle(
    x0: float, y0: float, x1: float, y1: float
) -> PlanPolygon:
    return PlanPolygon((
        PlanPoint(x0, y0),
        PlanPoint(x1, y0),
        PlanPoint(x1, y1),
        PlanPoint(x0, y1),
        PlanPoint(x0, y0),
    ))


def _annular_assessment(
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
    return PlanPolygon(points + (points[0],))


def _two_lobe_assessment() -> PlanPolygon:
    return PlanPolygon(tuple(PlanPoint(*point) for point in (
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


def _integrate(
    surface: TriangleSurface,
    assessment: PlanPolygon,
    *,
    spacing: float = 2.0,
):
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    return build_wall_profile_placements(
        surface,
        topology,
        assessment,
        requested_spacing_m=spacing,
    )


def _guide(
    kind: str, points: tuple[tuple[float, float], ...]
) -> WallGuide:
    return WallGuide(tuple(PlanPoint(*point) for point in points), kind)


def _sample(
    station_fraction: float,
    point: tuple[float, float],
    downwall_xy: tuple[float, float],
    source_id: str,
) -> FaceDirectionSample:
    return FaceDirectionSample(
        PlanPoint(*point),
        station_fraction,
        downwall_xy,
        1.0,
        "face",
        source_id,
    )


def _manual_sector(
    *,
    sector_id: str = "wall-sector:test",
    upper: WallGuide | None = None,
    lower: WallGuide | None = None,
    downstream_extent: WallGuide | None = None,
    samples: tuple[FaceDirectionSample, ...] | None = None,
    intervals: tuple[StationInterval, ...] = (StationInterval(0.0, 1.0),),
    supported: bool = True,
    closed: bool = False,
    codes: tuple[str, ...] = (),
    lower_station_mapping: GuideStationMapping | None = None,
    downstream_station_mapping: GuideStationMapping | None = None,
) -> WallSector:
    upper = upper or _guide("upper", ((0.0, 0.0), (20.0, 0.0)))
    lower = lower or (
        None
        if downstream_extent is not None
        else _guide("lower", ((0.0, 10.0), (20.0, 10.0)))
    )
    samples = samples or tuple(
        _sample(fraction, (20.0 * fraction, 5.0), (0.0, 1.0), f"face:{index}")
        for index, fraction in enumerate((0.0, 0.25, 0.5, 0.75, 1.0))
    )
    if lower is not None and lower_station_mapping is None:
        lower_station_mapping = GuideStationMapping(
            (0.0, lower.length_m), (0.0, 1.0)
        )
    if downstream_extent is not None and downstream_station_mapping is None:
        downstream_station_mapping = GuideStationMapping(
            (0.0, downstream_extent.length_m), (0.0, 1.0)
        )
    return WallSector(
        sector_id=sector_id,
        upper_guide=upper,
        lower_guide=lower,
        downstream_extent=downstream_extent,
        face_direction_samples=samples,
        assessed_station_intervals=intervals,
        closed_along_strike=closed,
        seam_point=upper.points[0] if closed else None,
        supported=supported,
        face_component_ids=(),
        portal_ids=(),
        connection_ids=(),
        fragment_ids=(),
        span_states=(),
        portal_correspondences=(),
        diagnostics=WallSectorDiagnostics(codes),
        lower_station_mapping=lower_station_mapping,
        downstream_station_mapping=downstream_station_mapping,
    )


def _extraction(*sectors: WallSector) -> WallSectorExtractionResult:
    return WallSectorExtractionResult(
        tuple(sectors), (), WallSectorDiagnostics()
    )


def _mask() -> PlanPolygon:
    return _rectangle(-50.0, -50.0, 50.0, 50.0)


def _angle(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    first_length = hypot(*first)
    second_length = hypot(*second)
    dot = (
        first[0] * second[0] + first[1] * second[1]
    ) / (first_length * second_length)
    return degrees(acos(max(-1.0, min(1.0, dot))))


def _assert_supported(result, spacing: float) -> None:
    assert len(result.sector_results) == 1
    sector_result = result.sector_results[0]
    assert sector_result.supported, sector_result.diagnostics
    assert sector_result.profiles
    assert sector_result.invariants is not None
    assert sector_result.invariants.all_valid
    assert sector_result.invariants.max_upper_spacing_m <= spacing + 1e-8
    assert sector_result.invariants.max_downstream_spacing_m <= spacing + 1e-8
    if sector_result.invariants.max_lower_spacing_m is not None:
        assert sector_result.invariants.max_lower_spacing_m <= spacing + 1e-8


def _result_signature(result) -> tuple[object, ...]:
    def q(value: float) -> float:
        return round(value, 10)

    return tuple(
        (
            sector_result.sector_id,
            sector_result.supported,
            tuple(
                (
                    interval_result.interval,
                    interval_result.supported,
                    tuple(
                        (
                            q(profile.upper_chainage_m),
                            q(profile.downstream_chainage_m),
                            q(profile.plan_start.x),
                            q(profile.plan_start.y),
                            q(profile.plan_end.x),
                            q(profile.plan_end.y),
                            tuple(q(value) for value in profile.downwall_xy),
                        )
                        for profile in interval_result.profiles
                    ),
                    interval_result.diagnostic_codes,
                )
                for interval_result in sector_result.interval_results
            ),
            sector_result.diagnostic_codes,
        )
        for sector_result in result.sector_results
    )


def _reordered(
    surface: TriangleSurface,
    *,
    vertices: bool = False,
    triangles: bool = False,
    winding: bool = False,
) -> TriangleSurface:
    surface_vertices = list(surface.vertices)
    surface_triangles = list(surface.triangles)
    if vertices:
        order = tuple(reversed(range(len(surface_vertices))))
        mapping = {old: new for new, old in enumerate(order)}
        surface_vertices = [surface_vertices[index] for index in order]
        surface_triangles = [replace(
            triangle,
            vertex_indices=tuple(
                mapping[index] for index in triangle.vertex_indices
            ),
        ) for triangle in surface_triangles]
    if winding:
        surface_triangles = [replace(
            triangle,
            vertex_indices=(
                triangle.vertex_indices[0],
                triangle.vertex_indices[2],
                triangle.vertex_indices[1],
            ),
        ) for triangle in surface_triangles]
    if triangles:
        surface_triangles.reverse()
    return TriangleSurface(tuple(surface_vertices), tuple(surface_triangles))


def test_straight_single_face_composes_phase2b_and_phase1() -> None:
    spacing = 2.0
    result = _integrate(
        _layered(("face",)),
        _rectangle(1.0, 2.0, 3.0, 8.0),
        spacing=spacing,
    )

    _assert_supported(result, spacing)
    assert all(
        profile.downwall_xy == pytest.approx((1.0, 0.0))
        for profile in result.profiles
    )


def test_face_berm_face_profiles_cross_the_full_corridor() -> None:
    result = _integrate(
        _layered(("face", "berm", "face")),
        _rectangle(1.0, 2.0, 11.0, 8.0),
    )

    _assert_supported(result, 2.0)
    assert all(
        profile.plan_start.x == pytest.approx(0.0)
        and profile.plan_end.x == pytest.approx(12.0)
        for profile in result.profiles
    )


def test_three_bench_wall_remains_one_profile_family() -> None:
    result = _integrate(
        _layered(("face", "berm", "face", "road", "face")),
        _rectangle(1.0, 2.0, 19.0, 8.0),
    )

    _assert_supported(result, 2.0)
    assert len(result.sector_results[0].interval_results) == 1
    assert all(
        profile.plan_end.x - profile.plan_start.x == pytest.approx(20.0)
        for profile in result.profiles
    )


def test_smoothly_curved_wall_rotates_straight_profiles() -> None:
    surface = _layered_arc(("face",), segment_count=16)
    result = _integrate(
        surface,
        _annular_assessment(19.0, 25.0, 0.0, 60.0),
        spacing=3.0,
    )

    _assert_supported(result, 3.0)
    azimuth_changes = tuple(
        _angle(first.downwall_xy, second.downwall_xy)
        for first, second in zip(result.profiles, result.profiles[1:])
    )
    assert azimuth_changes
    assert 0.0 < max(azimuth_changes) < 15.0


def test_converging_corridor_preserves_order_and_spacing() -> None:
    upper = _guide("upper", ((0.0, 0.0), (30.0, 0.0)))
    lower = _guide("lower", ((5.0, 10.0), (25.0, 10.0)))
    samples = tuple(
        _sample(
            fraction,
            (30.0 * fraction, 5.0),
            (5.0 - 10.0 * fraction, 10.0),
            f"converging:{index}",
        )
        for index, fraction in enumerate(index / 6 for index in range(7))
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(_manual_sector(upper=upper, lower=lower, samples=samples)),
        _mask(),
        requested_spacing_m=5.0,
    )

    _assert_supported(result, 5.0)
    assert result.sector_results[0].invariants.order_preserved


def test_diverging_corridor_preserves_order_and_spacing() -> None:
    upper = _guide("upper", ((5.0, 0.0), (25.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (30.0, 10.0)))
    samples = tuple(
        _sample(
            fraction,
            (5.0 + 20.0 * fraction, 5.0),
            (-5.0 + 10.0 * fraction, 10.0),
            f"diverging:{index}",
        )
        for index, fraction in enumerate(index / 6 for index in range(7))
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(_manual_sector(upper=upper, lower=lower, samples=samples)),
        _mask(),
        requested_spacing_m=5.0,
    )

    _assert_supported(result, 5.0)
    assert result.sector_results[0].invariants.order_preserved


def test_missing_lower_uses_downstream_extent_mode() -> None:
    result = _integrate(
        _layered(("face",)),
        _rectangle(1.0, 2.0, 3.0, 8.0),
    )

    _assert_supported(result, 2.0)
    assert all(not profile.lower_guide_constrained for profile in result.profiles)
    assert all(profile.lower_point is None for profile in result.profiles)


def test_disconnected_station_intervals_are_partitioned_before_phase1() -> None:
    intervals = (StationInterval(0.0, 0.2), StationInterval(0.8, 1.0))
    samples = tuple(
        _sample(fraction, (20.0 * fraction, 5.0), (0.0, 1.0), f"face:{index}")
        for index, fraction in enumerate((0.0, 0.1, 0.2, 0.8, 0.9, 1.0))
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(_manual_sector(intervals=intervals, samples=samples)),
        _mask(),
        requested_spacing_m=2.0,
    )

    _assert_supported(result, 2.0)
    sector_result = result.sector_results[0]
    assert len(sector_result.interval_results) == 2
    fractions = tuple(
        profile.upper_chainage_m / 20.0 for profile in sector_result.profiles
    )
    assert all(value <= 0.2 + 1e-9 or value >= 0.8 - 1e-9 for value in fractions)
    assert 0.2 == pytest.approx(max(value for value in fractions if value < 0.5))
    assert 0.8 == pytest.approx(min(value for value in fractions if value > 0.5))


@pytest.mark.parametrize("terminal_kind", ("lower", "downstream_extent"))
def test_disconnected_intervals_use_nonlinear_terminal_station_mapping(
    terminal_kind: str,
) -> None:
    upper = _guide("upper", ((0.0, 0.0), (100.0, 0.0)))
    terminal = _guide(
        terminal_kind,
        ((0.0, 10.0), (25.0, 40.0), (50.0, 10.0), (100.0, 10.0)),
    )
    mapping = GuideStationMapping(
        terminal.cumulative_chainages_m,
        (0.0, 0.25, 0.5, 1.0),
    )
    intervals = (StationInterval(0.0, 0.25), StationInterval(0.5, 1.0))
    samples = tuple(
        _sample(
            station,
            (100.0 * station, 5.0),
            (0.0, 1.0),
            f"nonlinear:{index}",
        )
        for index, station in enumerate(
            (0.0, 0.125, 0.25, 0.5, 0.75, 1.0)
        )
    )
    sector = _manual_sector(
        upper=upper,
        lower=terminal if terminal_kind == "lower" else None,
        downstream_extent=(
            terminal if terminal_kind == "downstream_extent" else None
        ),
        samples=samples,
        intervals=intervals,
        lower_station_mapping=(mapping if terminal_kind == "lower" else None),
        downstream_station_mapping=(
            mapping if terminal_kind == "downstream_extent" else None
        ),
    )

    result = place_wall_sector_extraction_profiles(
        _extraction(sector), _mask(), requested_spacing_m=10.0
    )

    _assert_supported(result, 10.0)
    interval_results = result.sector_results[0].interval_results
    assert len(interval_results) == 2
    assert (
        interval_results[0].profiles[0].downstream_chainage_m,
        interval_results[0].profiles[-1].downstream_chainage_m,
    ) == pytest.approx((0.0, mapping.chainages_m[1]))
    assert (
        interval_results[1].profiles[0].downstream_chainage_m,
        interval_results[1].profiles[-1].downstream_chainage_m,
    ) == pytest.approx((mapping.chainages_m[2], mapping.chainages_m[-1]))
    assert all(
        profile.downstream_chainage_m <= mapping.chainages_m[1] + 1e-8
        or profile.downstream_chainage_m >= mapping.chainages_m[2] - 1e-8
        for profile in result.profiles
    )
    assert result.sector_results[0].invariants.all_valid


@pytest.mark.parametrize("assessment_x", ((9.0, 11.0), (17.0, 19.0)))
def test_assessment_on_middle_or_lower_face_keeps_full_multibench_width(
    assessment_x: tuple[float, float],
) -> None:
    result = _integrate(
        _layered(("face", "berm", "face", "berm", "face")),
        _rectangle(assessment_x[0], 2.0, assessment_x[1], 8.0),
    )

    _assert_supported(result, 2.0)
    assert all(
        profile.plan_start.x == pytest.approx(0.0)
        and profile.plan_end.x == pytest.approx(20.0)
        for profile in result.profiles
    )


def test_external_upper_and_lower_may_both_lie_outside_assessment() -> None:
    surface = _layered(("face", "berm"))
    assessment = _rectangle(1.0, 2.0, 3.0, 8.0)
    extraction = extract_wall_sectors(
        surface, build_design_topology_index(surface, ROLE_MAPPING), assessment
    )
    sector = extraction.sectors[0]
    assert all(point.x < 1.0 for point in sector.upper_guide.points)
    assert sector.lower_guide is not None
    assert all(point.x > 3.0 for point in sector.lower_guide.points)

    result = place_wall_sector_extraction_profiles(
        extraction, assessment, requested_spacing_m=2.0
    )
    _assert_supported(result, 2.0)


def test_supported_sector_survives_unrelated_unsupported_sector(
    monkeypatch,
) -> None:
    good = _manual_sector(sector_id="wall-sector:good")
    bad = replace(
        good,
        sector_id="wall-sector:bad",
        supported=False,
        diagnostics=WallSectorDiagnostics(("ambiguous_corridor_branch",)),
    )
    phase1_calls = 0
    original = pipeline_module.place_profile_traces

    def count_calls(*args, **kwargs):
        nonlocal phase1_calls
        phase1_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "place_profile_traces", count_calls)
    result = place_wall_sector_extraction_profiles(
        _extraction(good, bad), _mask(), requested_spacing_m=5.0
    )

    assert phase1_calls == 1
    assert result.sector_results[0].supported
    assert not result.sector_results[1].supported
    assert not result.sector_results[1].profiles
    assert "ambiguous_corridor_branch" in result.sector_results[1].diagnostic_codes


@pytest.mark.parametrize(
    "code",
    (
        "ambiguous_corridor_branch",
        "local_non_manifold_topology",
        "abrupt_local_direction_break",
    ),
)
def test_phase2b_ambiguous_tainted_and_direction_break_sectors_are_skipped(
    code: str,
    monkeypatch,
) -> None:
    sector = _manual_sector(supported=False, codes=(code,))

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("Phase 1 must not be called for an unsupported sector")

    monkeypatch.setattr(
        pipeline_module, "place_profile_traces", fail_if_called
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(sector), _mask(), requested_spacing_m=5.0
    )

    assert not result.sector_results[0].supported
    assert code in result.sector_results[0].diagnostic_codes


def test_closed_sector_is_skipped_with_periodic_defer_diagnostic(
    monkeypatch,
) -> None:
    sector = _manual_sector(
        supported=False,
        closed=True,
        codes=("phase1_periodic_spacing_deferred",),
    )

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("Phase 1 must not be called for a closed sector")

    monkeypatch.setattr(
        pipeline_module, "place_profile_traces", fail_if_called
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(sector), _mask(), requested_spacing_m=5.0
    )

    sector_result = result.sector_results[0]
    assert not sector_result.profiles
    assert "periodic_profile_placement_deferred" in (
        sector_result.diagnostic_codes
    )


def test_local_spacing_bound_is_enforced_on_upper_guide() -> None:
    result = place_wall_sector_extraction_profiles(
        _extraction(_manual_sector()), _mask(), requested_spacing_m=3.0
    )

    _assert_supported(result, 3.0)
    assert result.sector_results[0].invariants.max_upper_spacing_m <= 3.0


@pytest.mark.parametrize("downstream_mode", (False, True))
def test_local_spacing_bound_is_enforced_on_lower_or_downstream_guide(
    downstream_mode: bool,
) -> None:
    downstream = _guide(
        "downstream_extent", ((0.0, 10.0), (30.0, 10.0))
    ) if downstream_mode else None
    lower = None if downstream_mode else _guide(
        "lower", ((0.0, 10.0), (30.0, 10.0))
    )
    upper = _guide("upper", ((0.0, 0.0), (20.0, 0.0)))
    samples = tuple(
        _sample(
            fraction,
            (20.0 * fraction, 5.0),
            (10.0 * fraction, 10.0),
            f"face:{index}",
        )
        for index, fraction in enumerate(index / 6 for index in range(7))
    )
    sector = _manual_sector(
        upper=upper,
        lower=lower,
        downstream_extent=downstream,
        samples=samples,
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(sector), _mask(), requested_spacing_m=5.0
    )

    _assert_supported(result, 5.0)
    invariants = result.sector_results[0].invariants
    assert invariants.max_downstream_spacing_m <= 5.0 + 1e-8
    if downstream_mode:
        assert invariants.max_lower_spacing_m is None
    else:
        assert invariants.max_lower_spacing_m <= 5.0 + 1e-8


def test_adjacent_profiles_are_non_crossing() -> None:
    result = place_wall_sector_extraction_profiles(
        _extraction(_manual_sector()), _mask(), requested_spacing_m=2.0
    )

    _assert_supported(result, 2.0)
    assert result.sector_results[0].invariants.non_crossing


def test_profile_plus_u_is_transverse_to_design_face_samples() -> None:
    result = _integrate(
        _layered(("face", "berm", "face")),
        _rectangle(1.0, 2.0, 11.0, 8.0),
    )

    _assert_supported(result, 2.0)
    assert all(
        profile.transversality_valid
        and _angle(profile.downwall_xy, profile.face_downwall_xy) < 1e-5
        for profile in result.profiles
    )


def test_zero_width_convergence_endpoint_produces_no_fake_profile() -> None:
    upper = _guide("upper", ((0.0, 0.0), (20.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (20.0, 0.0)))
    samples = tuple(
        _sample(
            fraction,
            (20.0 * fraction, 5.0 * (1.0 - fraction)),
            (0.0, 1.0),
            f"face:{index}",
        )
        for index, fraction in enumerate(index / 5 for index in range(6))
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(_manual_sector(upper=upper, lower=lower, samples=samples)),
        _mask(),
        requested_spacing_m=5.0,
    )

    _assert_supported(result, 5.0)
    assert all(
        hypot(
            profile.plan_end.x - profile.plan_start.x,
            profile.plan_end.y - profile.plan_start.y,
        ) > 0.0
        for profile in result.profiles
    )
    phase1 = result.sector_results[0].interval_results[0].phase1_result
    assert phase1 is not None
    assert phase1.diagnostics.omitted_zero_width_stations == 1


def test_result_is_deterministic_under_triangle_reorder_and_winding() -> None:
    surface = _layered(("face", "berm", "face"), y_values=(0.0, 5.0, 10.0))
    assessment = _rectangle(1.0, 2.0, 11.0, 8.0)
    baseline = _result_signature(_integrate(surface, assessment))

    for variant in (
        _reordered(surface, triangles=True),
        _reordered(surface, winding=True),
        _reordered(surface, vertices=True, triangles=True, winding=True),
    ):
        assert _result_signature(_integrate(variant, assessment)) == baseline


def test_local_remeshing_preserves_profile_stationing_and_count() -> None:
    assessment = _rectangle(1.0, 2.0, 3.0, 8.0)
    coarse = _integrate(
        _layered(("face",), y_values=(0.0, 5.0, 10.0)),
        assessment,
    )
    refined = _integrate(
        _layered(("face",), y_values=(0.0, 2.5, 5.0, 7.5, 10.0)),
        assessment,
    )

    _assert_supported(coarse, 2.0)
    _assert_supported(refined, 2.0)
    assert len(coarse.profiles) == len(refined.profiles)
    for first, second in zip(coarse.profiles, refined.profiles, strict=True):
        assert first.upper_chainage_m == pytest.approx(second.upper_chainage_m)
        assert first.plan_start == second.plan_start
        assert first.plan_end == second.plan_end


def test_open_u_shaped_wall_does_not_jump_profile_order() -> None:
    angles = tuple((-150.0 + 300.0 * index / 30.0) * pi / 180.0
                   for index in range(31))
    upper = _guide("upper", _arc_points(10.0, angles))
    lower = _guide("lower", _arc_points(14.0, angles))
    samples = tuple(
        _sample(
            index / 30.0,
            (12.0 * cos(angle), 12.0 * sin(angle)),
            (cos(angle), sin(angle)),
            f"u:{index}",
        )
        for index, angle in enumerate(angles)
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(_manual_sector(upper=upper, lower=lower, samples=samples)),
        _mask(),
        requested_spacing_m=2.0,
    )

    _assert_supported(result, 2.0)
    assert result.sector_results[0].invariants.order_preserved
    assert result.sector_results[0].invariants.non_crossing


def test_two_assessment_lobes_on_one_corridor_place_only_two_families() -> None:
    surface = _layered(
        ("face", "berm", "face"),
        y_values=(0.0, 10.0, 20.0, 30.0, 40.0),
    )
    result = _integrate(surface, _two_lobe_assessment(), spacing=3.0)

    _assert_supported(result, 3.0)
    interval_results = result.sector_results[0].interval_results
    assert len(interval_results) == 2
    y_values = tuple(profile.plan_start.y for profile in result.profiles)
    assert all(y <= 9.0 + 1e-8 or y >= 31.0 - 1e-8 for y in y_values)
    assert 9.0 == pytest.approx(max(y for y in y_values if y < 20.0))
    assert 31.0 == pytest.approx(min(y for y in y_values if y > 20.0))


@pytest.mark.parametrize(
    "intervals",
    (
        (),
        (StationInterval(0.0, 0.6), StationInterval(0.5, 1.0)),
    ),
)
def test_empty_or_invalid_assessed_interval_coverage_is_rejected(
    intervals: tuple[StationInterval, ...],
    monkeypatch,
) -> None:
    sector = _manual_sector(intervals=intervals)

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("Phase 1 must not be called for invalid interval coverage")

    monkeypatch.setattr(
        pipeline_module, "place_profile_traces", fail_if_called
    )
    result = place_wall_sector_extraction_profiles(
        _extraction(sector), _mask(), requested_spacing_m=5.0
    )

    assert "invalid_assessed_interval_coverage" in (
        result.sector_results[0].diagnostic_codes
    )


def test_primary_curved_multibench_acceptance_case() -> None:
    surface = _layered_arc(
        ("face", "berm", "face", "road", "face"),
        segment_count=18,
    )
    assessment = _annular_assessment(28.0, 30.5, 15.0, 45.0)
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    extraction = extract_wall_sectors(surface, topology, assessment)

    assert len(extraction.sectors) == 1
    sector = extraction.sectors[0]
    assert sector.supported, sector.diagnostics.codes
    assert len(sector.face_component_ids) == 3
    assert all(hypot(point.x, point.y) < 22.0
               for point in sector.upper_guide.points)
    assert sector.downstream_extent is not None
    assert all(hypot(point.x, point.y) > 37.5
               for point in sector.downstream_extent.points)

    spacing = 2.5
    result = place_wall_sector_extraction_profiles(
        extraction, assessment, requested_spacing_m=spacing
    )
    _assert_supported(result, spacing)
    sector_result = result.sector_results[0]
    assert len(sector_result.interval_results) == 1
    assert all(
        16.0 < hypot(profile.plan_end.x, profile.plan_end.y)
        - hypot(profile.plan_start.x, profile.plan_start.y) < 20.0
        for profile in sector_result.profiles
    )
    assert all(
        _angle(profile.downwall_xy, profile.face_downwall_xy)
        <= profile.face_alignment_allowance_degrees + 1e-7
        for profile in sector_result.profiles
    )
    neighbour_turns = tuple(
        _angle(first.downwall_xy, second.downwall_xy)
        for first, second in zip(
            sector_result.profiles, sector_result.profiles[1:]
        )
    )
    assert neighbour_turns
    assert 0.0 < max(neighbour_turns) < 15.0
    assert sector_result.invariants.non_crossing
    assert sector_result.invariants.spacing_within_bound


def test_phase3_module_remains_pure_domain_and_outside_production_engine() -> None:
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "domain/wall_conformance/profile_pipeline.py",
            "domain/wall_conformance/horizontal_invariants.py",
        )
    )
    forbidden = (
        "PySide6",
        "application.services",
        "database.",
        "Actual Survey",
        "DAI",
        "FCI",
        "domain.wall_conformance.engine",
        "domain.wall_conformance.sections",
    )
    assert all(name not in source for name in forbidden)
