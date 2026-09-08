from __future__ import annotations

from math import acos, cos, degrees, hypot, pi, sin

import pytest

import domain.wall_conformance.profile_placement as placement_module
from domain.geometry.surfaces import SurfaceVertex
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance.profile_placement import (
    FaceDirectionSample,
    WallGuide,
    aggregate_face_direction,
    direction_sample_from_triangle,
    place_profile_traces,
)
from domain.wall_conformance.profile_placement import (
    _FaceDirectionField,
    _StationPair,
    _insert_for_spacing,
)


def _mask(
    min_x: float = -50.0,
    min_y: float = -50.0,
    max_x: float = 50.0,
    max_y: float = 50.0,
) -> PlanPolygon:
    return PlanPolygon((
        PlanPoint(min_x, min_y),
        PlanPoint(max_x, min_y),
        PlanPoint(max_x, max_y),
        PlanPoint(min_x, max_y),
        PlanPoint(min_x, min_y),
    ))


def _guide(kind: str, points: tuple[tuple[float, float], ...]) -> WallGuide:
    return WallGuide(tuple(PlanPoint(*point) for point in points), kind)


def _sample(
    fraction: float,
    direction: tuple[float, float],
    *,
    point: tuple[float, float] | None = None,
    weight: float = 1.0,
    role: str = "face",
    source_id: str = "",
) -> FaceDirectionSample:
    return FaceDirectionSample(
        PlanPoint(*(point or (fraction * 30.0, 5.0))),
        fraction,
        direction if role == "face" else None,
        weight,
        role,
        source_id,
    )


def _parallel_samples(*, length: float = 30.0) -> tuple[FaceDirectionSample, ...]:
    return tuple(
        _sample(
            fraction,
            (0.0, 1.0),
            point=(length * fraction, 5.0),
            source_id=f"face-{index}",
        )
        for index, fraction in enumerate((0.0, 0.25, 0.5, 0.75, 1.0))
    )


def _trace_signature(result) -> tuple[tuple[float, ...], ...]:
    return tuple(
        (
            round(trace.upper_chainage_m, 8),
            round(trace.downstream_chainage_m, 8),
            round(trace.plan_start.x, 8),
            round(trace.plan_start.y, 8),
            round(trace.plan_end.x, 8),
            round(trace.plan_end.y, 8),
            round(trace.downwall_xy[0], 8),
            round(trace.downwall_xy[1], 8),
            round(trace.face_alignment_residual_degrees, 8),
        )
        for trace in result.traces
    )


def _assert_supported(result, spacing: float) -> None:
    diagnostics = result.diagnostics
    assert diagnostics.supported, diagnostics.unsupported_reason
    assert diagnostics.structural_mapping_valid
    assert diagnostics.transversality_valid
    assert diagnostics.order_preserved
    assert diagnostics.non_crossing
    assert diagnostics.spacing_within_bound
    assert diagnostics.max_upper_spacing_m <= spacing + 1e-8
    assert diagnostics.max_downstream_spacing_m <= spacing + 1e-8
    if diagnostics.max_lower_spacing_m is not None:
        assert diagnostics.max_lower_spacing_m <= spacing + 1e-8
    assert [trace.station_index for trace in result.traces] == list(
        range(len(result.traces))
    )
    assert all(
        second.upper_chainage_m > first.upper_chainage_m
        for first, second in zip(result.traces, result.traces[1:])
    )
    assert all(
        second.downstream_chainage_m > first.downstream_chainage_m
        for first, second in zip(result.traces, result.traces[1:])
    )
    assert all(trace.transversality_valid for trace in result.traces)
    assert all(
        trace.face_alignment_residual_degrees
        <= trace.face_alignment_allowance_degrees
        for trace in result.traces
    )


def _angle_between(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    dot = first[0] * second[0] + first[1] * second[1]
    return degrees(acos(max(-1.0, min(1.0, dot))))


def _segments_cross_inside(
    first: tuple[PlanPoint, PlanPoint],
    second: tuple[PlanPoint, PlanPoint],
) -> bool:
    a, b = first
    c, d = second
    r = b.x - a.x, b.y - a.y
    s = d.x - c.x, d.y - c.y
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) <= 1e-12:
        return False
    offset = c.x - a.x, c.y - a.y
    first_fraction = (offset[0] * s[1] - offset[1] * s[0]) / denominator
    second_fraction = (offset[0] * r[1] - offset[1] * r[0]) / denominator
    return 0.0 < first_fraction < 1.0 and 0.0 < second_fraction < 1.0


def test_straight_parallel_wall() -> None:
    spacing = 6.0
    result = place_profile_traces(
        _parallel_samples(),
        _guide("upper", ((0.0, 0.0), (30.0, 0.0))),
        _mask(),
        requested_spacing_m=spacing,
        lower_guide=_guide("lower", ((0.0, 10.0), (30.0, 10.0))),
    )

    _assert_supported(result, spacing)
    assert len(result.traces) == 6
    assert result.diagnostics.max_alignment_residual_degrees == pytest.approx(0.0)
    assert result.diagnostics.max_neighbour_azimuth_change_degrees == pytest.approx(0.0)
    assert all(trace.downwall_xy == pytest.approx((0.0, 1.0)) for trace in result.traces)


def test_structural_mapping_is_not_supported_when_face_transversality_fails() -> None:
    result = place_profile_traces(
        tuple(
            _sample(
                fraction,
                (1.0, 0.0),
                point=(20.0 * fraction, 5.0),
            )
            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)
        ),
        _guide("upper", ((0.0, 0.0), (20.0, 0.0))),
        _mask(),
        requested_spacing_m=5.0,
        lower_guide=_guide("lower", ((0.0, 10.0), (20.0, 10.0))),
    )

    assert result.traces
    assert result.diagnostics.structural_mapping_valid
    assert result.diagnostics.order_preserved
    assert result.diagnostics.non_crossing
    assert result.diagnostics.spacing_within_bound
    assert not result.diagnostics.transversality_valid
    assert not result.diagnostics.supported
    assert "not transverse" in result.diagnostics.unsupported_reason
    assert result.diagnostics.max_alignment_residual_degrees == pytest.approx(90.0)
    assert result.diagnostics.max_alignment_allowance_degrees < 1e-5
    assert result.diagnostics.max_alignment_excess_degrees > 89.0


def test_straight_converging_wall_uses_longer_upper_guide_for_spacing() -> None:
    spacing = 5.0
    upper = _guide("upper", ((0.0, 0.0), (30.0, 0.0)))
    lower = _guide("lower", ((5.0, 10.0), (25.0, 10.0)))
    fractions = tuple(index / 6 for index in range(7))
    samples = tuple(
        _sample(
            fraction,
            (5.0 - 10.0 * fraction, 10.0),
            point=(30.0 * fraction, 5.0),
        )
        for fraction in fractions
    )

    result = place_profile_traces(
        samples,
        upper,
        _mask(),
        requested_spacing_m=spacing,
        lower_guide=lower,
    )

    _assert_supported(result, spacing)
    assert len(result.traces) == 7
    assert result.diagnostics.max_upper_spacing_m == pytest.approx(5.0)
    assert result.diagnostics.max_lower_spacing_m < spacing
    assert result.diagnostics.max_alignment_excess_degrees == pytest.approx(0.0)
    assert result.diagnostics.max_neighbour_azimuth_change_degrees < 12.0


def test_exact_zero_width_convergence_apex_is_omitted() -> None:
    upper = _guide("upper", ((0.0, 0.0), (20.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (20.0, 0.0)))
    result = place_profile_traces(
        tuple(
            _sample(
                fraction,
                (0.0, 1.0),
                point=(20.0 * fraction, 5.0 * (1.0 - fraction)),
            )
            for fraction in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
        ),
        upper,
        _mask(),
        requested_spacing_m=5.0,
        lower_guide=lower,
    )

    _assert_supported(result, 5.0)
    assert result.diagnostics.omitted_zero_width_stations == 1
    assert result.traces[-1].upper_chainage_m < upper.length_m
    assert result.traces[-1].lower_chainage_m < lower.length_m
    assert all(
        hypot(
            trace.plan_end.x - trace.plan_start.x,
            trace.plan_end.y - trace.plan_start.y,
        )
        > 0.0
        for trace in result.traces
    )


def test_straight_diverging_wall_uses_longer_lower_guide_for_spacing() -> None:
    spacing = 5.0
    upper = _guide("upper", ((5.0, 0.0), (25.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (30.0, 10.0)))
    fractions = tuple(index / 6 for index in range(7))
    samples = tuple(
        _sample(
            fraction,
            (-5.0 + 10.0 * fraction, 10.0),
            point=(5.0 + 20.0 * fraction, 5.0),
        )
        for fraction in fractions
    )

    result = place_profile_traces(
        samples,
        upper,
        _mask(),
        requested_spacing_m=spacing,
        lower_guide=lower,
    )

    _assert_supported(result, spacing)
    assert len(result.traces) >= 7
    assert result.diagnostics.max_upper_spacing_m <= spacing
    assert result.diagnostics.max_lower_spacing_m <= spacing
    assert result.diagnostics.max_alignment_excess_degrees == pytest.approx(0.0)


def test_smoothly_curved_wall_rotates_profiles_smoothly() -> None:
    point_count = 33
    fractions = tuple(index / (point_count - 1) for index in range(point_count))
    angles = tuple(fraction * pi / 2.0 for fraction in fractions)
    upper = _guide("upper", tuple(
        (10.0 * cos(angle), 10.0 * sin(angle)) for angle in angles
    ))
    lower = _guide("lower", tuple(
        (20.0 * cos(angle), 20.0 * sin(angle)) for angle in angles
    ))
    samples = tuple(
        _sample(
            fraction,
            (cos(angle), sin(angle)),
            point=(15.0 * cos(angle), 15.0 * sin(angle)),
            source_id=f"arc-{index}",
        )
        for index, (fraction, angle) in enumerate(zip(fractions, angles))
    )

    result = place_profile_traces(
        samples,
        upper,
        _mask(-1.0, -1.0, 21.0, 21.0),
        requested_spacing_m=3.0,
        lower_guide=lower,
    )

    _assert_supported(result, 3.0)
    assert result.diagnostics.max_alignment_excess_degrees == pytest.approx(0.0)
    assert 0.0 < result.diagnostics.max_neighbour_azimuth_change_degrees < 10.0


def test_multi_bench_equivalent_face_samples_share_one_direction_field() -> None:
    upper = _guide("upper", ((0.0, 0.0), (30.0, 0.0)))
    lower = _guide("lower", ((0.0, 15.0), (30.0, 15.0)))
    samples = tuple(
        _sample(
            fraction,
            (0.0, 1.0),
            point=(30.0 * fraction, downwall),
            weight=weight,
            source_id=f"face-{fraction}-{downwall}",
        )
        for fraction in (0.0, 0.5, 1.0)
        for downwall, weight in ((3.0, 3.0), (11.0, 2.0))
    )

    result = place_profile_traces(
        samples,
        upper,
        _mask(),
        requested_spacing_m=5.0,
        lower_guide=lower,
    )

    _assert_supported(result, 5.0)
    assert result.diagnostics.max_alignment_residual_degrees == pytest.approx(0.0)
    assert all(trace.downwall_xy == pytest.approx((0.0, 1.0)) for trace in result.traces)


def test_road_above_upper_guide_has_zero_directional_authority() -> None:
    upper = _guide("upper", ((0.0, 0.0), (30.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (30.0, 10.0)))
    faces = _parallel_samples()
    road = tuple(
        _sample(
            fraction,
            (1.0, 0.0),
            point=(30.0 * fraction, -4.0),
            weight=10_000.0,
            role="road",
            source_id=f"road-{index}",
        )
        for index, fraction in enumerate((0.0, 0.5, 1.0))
    )

    baseline = place_profile_traces(
        faces, upper, _mask(), requested_spacing_m=5.0, lower_guide=lower
    )
    with_road = place_profile_traces(
        (*road, *faces),
        upper,
        _mask(),
        requested_spacing_m=5.0,
        lower_guide=lower,
    )

    assert _trace_signature(with_road) == _trace_signature(baseline)
    assert with_road.diagnostics.ignored_non_face_samples == len(road)


def test_missing_lower_uses_face_direction_and_downstream_extent() -> None:
    upper = _guide("upper", ((0.0, 0.0), (30.0, 0.0)))
    extent = _guide("downstream_extent", ((0.0, 12.0), (30.0, 12.0)))

    result = place_profile_traces(
        _parallel_samples(),
        upper,
        _mask(),
        requested_spacing_m=6.0,
        downstream_extent=extent,
    )

    _assert_supported(result, 6.0)
    assert all(trace.lower_chainage_m is None for trace in result.traces)
    assert all(trace.lower_point is None for trace in result.traces)
    assert all(not trace.lower_guide_constrained for trace in result.traces)
    assert all(trace.plan_end.y == pytest.approx(12.0) for trace in result.traces)
    assert result.diagnostics.max_alignment_residual_degrees == pytest.approx(0.0)


def test_abrupt_90_degree_corner_requires_sector_split() -> None:
    upper = _guide("upper", ((0.0, 0.0), (20.0, 0.0)))
    extent = _guide("downstream_extent", ((0.0, 10.0), (20.0, 10.0)))
    samples = (
        _sample(0.0, (0.0, 1.0), point=(0.0, 5.0)),
        _sample(0.2, (0.0, 1.0), point=(4.0, 5.0)),
        _sample(0.4, (0.0, 1.0), point=(8.0, 5.0)),
        _sample(0.6, (1.0, 0.0), point=(12.0, 5.0)),
        _sample(0.8, (1.0, 0.0), point=(16.0, 5.0)),
        _sample(1.0, (1.0, 0.0), point=(20.0, 5.0)),
    )

    result = place_profile_traces(
        samples,
        upper,
        _mask(),
        requested_spacing_m=2.0,
        downstream_extent=extent,
    )

    assert not result.diagnostics.supported
    assert result.traces == ()
    assert "sector split" in result.diagnostics.unsupported_reason
    assert result.diagnostics.sector_break_station_fractions == pytest.approx((0.5,))
    assert result.diagnostics.sector_break_indices


def test_noisy_face_samples_are_weighted_explicitly_and_order_independent() -> None:
    upper = _guide("upper", ((0.0, 0.0), (30.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (30.0, 10.0)))
    stable = tuple(
        _sample(
            fraction,
            (0.0, magnitude),
            point=(30.0 * fraction, 5.0),
            weight=10.0,
            source_id=f"stable-{index}",
        )
        for index, (fraction, magnitude) in enumerate(
            ((0.0, 1.0), (0.5, 1000.0), (1.0, 0.001))
        )
    )
    noise = tuple(
        _sample(
            fraction,
            direction,
            point=(30.0 * fraction, 5.1 + index * 0.01),
            weight=0.001,
            source_id=f"noise-{fraction}-{index}",
        )
        for fraction in (0.0, 0.5, 1.0)
        for index, direction in enumerate(((1.0, 0.0), (-1.0, 0.0)))
    )

    ordered = place_profile_traces(
        (*stable, *noise),
        upper,
        _mask(),
        requested_spacing_m=5.0,
        lower_guide=lower,
    )
    reordered = place_profile_traces(
        tuple(reversed((*stable, *noise))),
        upper,
        _mask(),
        requested_spacing_m=5.0,
        lower_guide=lower,
    )

    _assert_supported(ordered, 5.0)
    assert _trace_signature(reordered) == _trace_signature(ordered)
    assert ordered.diagnostics.max_alignment_residual_degrees < 0.01
    aggregate = aggregate_face_direction((*stable, *noise), 0.5, _mask())
    assert aggregate.downwall_xy == pytest.approx((0.0, 1.0), abs=1e-8)


def test_irregular_local_face_support_is_smooth_and_order_independent() -> None:
    fractions = (0.0, 0.08, 0.21, 0.37, 0.56, 0.74, 0.90, 1.0)
    perturbations = (-1.5, 1.0, -0.5, 1.5, -1.0, 0.5, -1.0, 1.0)
    stable = tuple(
        _sample(
            fraction,
            (
                sin((40.0 * fraction + noise) * pi / 180.0),
                cos((40.0 * fraction + noise) * pi / 180.0),
            ),
            point=(30.0 * fraction, 5.0),
            weight=1.0 + index * 0.05,
            source_id=f"irregular-{index}",
        )
        for index, (fraction, noise) in enumerate(zip(fractions, perturbations))
    )
    tiny_noise = tuple(
        _sample(
            fraction,
            (-1.0, 0.0),
            point=(30.0 * fraction, 5.2),
            weight=0.0001,
            source_id=f"tiny-{index}",
        )
        for index, fraction in enumerate((0.13, 0.48, 0.82))
    )
    queries = tuple(index / 20.0 for index in range(21))

    ordered = tuple(
        aggregate_face_direction((*stable, *tiny_noise), query, _mask())
        for query in queries
    )
    reordered = tuple(
        aggregate_face_direction(
            tuple(reversed((*stable, *tiny_noise))), query, _mask()
        )
        for query in queries
    )

    assert all(
        second.downwall_xy == pytest.approx(first.downwall_xy, abs=1e-12)
        for first, second in zip(ordered, reordered)
    )
    assert max(
        _angle_between(first.downwall_xy, second.downwall_xy)
        for first, second in zip(ordered, ordered[1:])
    ) < 5.0
    assert max(
        _angle_between(
            aggregate.downwall_xy,
            (
                sin(40.0 * query * pi / 180.0),
                cos(40.0 * query * pi / 180.0),
            ),
        )
        for query, aggregate in zip(queries, ordered)
    ) < 10.0
    assert all(aggregate.sample_count >= 3 for aggregate in ordered)
    assert all(aggregate.station_span_fraction > 0.0 for aggregate in ordered)


def test_triangle_direction_is_normalized_and_geometric_weight_is_explicit() -> None:
    shallow = direction_sample_from_triangle(
        (
            SurfaceVertex(0.0, 0.0, 10.0),
            SurfaceVertex(1.0, 0.0, 9.0),
            SurfaceVertex(0.0, 1.0, 10.0),
        ),
        station_fraction=0.5,
        geometric_weight=2.5,
    )
    steep = direction_sample_from_triangle(
        (
            SurfaceVertex(0.0, 0.0, 10.0),
            SurfaceVertex(1.0, 0.0, -90.0),
            SurfaceVertex(0.0, 1.0, 10.0),
        ),
        station_fraction=0.5,
        geometric_weight=2.5,
    )
    road = direction_sample_from_triangle(
        (
            SurfaceVertex(0.0, 0.0, 1.0),
            SurfaceVertex(1.0, 0.0, 0.0),
            SurfaceVertex(0.0, 1.0, 1.0),
        ),
        station_fraction=0.5,
        semantic_role="road",
        geometric_weight=999.0,
    )
    reordered = direction_sample_from_triangle(
        (
            SurfaceVertex(0.0, 1.0, 10.0),
            SurfaceVertex(1.0, 0.0, 9.0),
            SurfaceVertex(0.0, 0.0, 10.0),
        ),
        station_fraction=0.5,
        geometric_weight=2.5,
    )

    assert shallow.downwall_xy == pytest.approx((1.0, 0.0))
    assert steep.downwall_xy == pytest.approx((1.0, 0.0))
    assert reordered.downwall_xy == pytest.approx(shallow.downwall_xy)
    assert shallow.geometric_weight == steep.geometric_weight == 2.5
    assert road.downwall_xy is None


def test_face_authority_avoids_crossing_naive_wavy_upper_normals() -> None:
    upper = _guide("upper", (
        (0.0, 0.0),
        (5.0, 3.0),
        (10.0, -3.0),
        (15.0, 3.0),
        (20.0, 0.0),
    ))
    lower = _guide("lower", ((0.0, 12.0), (20.0, 12.0)))
    samples = tuple(
        _sample(
            fraction,
            (0.0, 1.0),
            point=(20.0 * fraction, 6.0),
            source_id=f"face-{index}",
        )
        for index, fraction in enumerate(
            tuple(index / 12 for index in range(13))
        )
    )

    result = place_profile_traces(
        samples,
        upper,
        _mask(),
        requested_spacing_m=3.0,
        lower_guide=lower,
    )

    naive_traces = []
    for first, second in zip(upper.points, upper.points[1:]):
        dx, dy = second.x - first.x, second.y - first.y
        downwall = -dy, dx
        if downwall[1] < 0.0:
            downwall = -downwall[0], -downwall[1]
        length = hypot(*downwall)
        direction = downwall[0] / length, downwall[1] / length
        midpoint = PlanPoint(
            (first.x + second.x) / 2.0,
            (first.y + second.y) / 2.0,
        )
        naive_traces.append((
            midpoint,
            PlanPoint(
                midpoint.x + direction[0] * 15.0,
                midpoint.y + direction[1] * 15.0,
            ),
        ))

    _assert_supported(result, 3.0)
    assert any(
        _segments_cross_inside(first, second)
        for index, first in enumerate(naive_traces)
        for second in naive_traces[index + 1 :]
    )
    assert result.diagnostics.max_alignment_residual_degrees < 1e-6
    assert result.diagnostics.max_neighbour_azimuth_change_degrees < 1e-6
    assert all(abs(trace.plan_end.x - trace.plan_start.x) < 1e-8 for trace in result.traces)


def test_local_spacing_refinement_repairs_distorted_correspondence() -> None:
    upper = _guide("upper", ((0.0, 0.0), (12.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (12.0, 10.0)))
    samples = (
        _sample(0.0, (0.0, 1.0), point=(0.0, 5.0)),
        _sample(1.0 / 3.0, (-2.0, 10.0), point=(4.0, 5.0)),
        _sample(2.0 / 3.0, (2.0, 10.0), point=(8.0, 5.0)),
        _sample(1.0, (0.0, 1.0), point=(12.0, 5.0)),
    )

    result = place_profile_traces(
        samples,
        upper,
        _mask(),
        requested_spacing_m=4.0,
        lower_guide=lower,
    )

    _assert_supported(result, 4.0)
    assert len(result.traces) > 4  # N_initial=3 would otherwise yield four traces.
    assert result.diagnostics.max_lower_spacing_m <= 4.0
    assert result.diagnostics.spacing_refinement_insertions > 0
    assert result.diagnostics.max_alignment_excess_degrees == pytest.approx(0.0)


def test_assessment_side_boundary_orientation_does_not_change_direction() -> None:
    upper = _guide("upper", ((0.0, 0.0), (30.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (30.0, 10.0)))
    samples = _parallel_samples()
    rectangular = _mask(-2.0, -1.0, 32.0, 11.0)
    oblique = PlanPolygon((
        PlanPoint(-8.0, -1.0),
        PlanPoint(32.0, -1.0),
        PlanPoint(38.0, 11.0),
        PlanPoint(-2.0, 11.0),
        PlanPoint(-8.0, -1.0),
    ))

    first = place_profile_traces(
        samples,
        upper,
        rectangular,
        requested_spacing_m=5.0,
        lower_guide=lower,
    )
    second = place_profile_traces(
        tuple(reversed(samples)),
        upper,
        oblique,
        requested_spacing_m=5.0,
        lower_guide=lower,
    )

    _assert_supported(first, 5.0)
    _assert_supported(second, 5.0)
    assert _trace_signature(second) == _trace_signature(first)


def _spacing_refinement_fixture(
    direction: tuple[float, float],
) -> tuple[list[_StationPair], WallGuide, WallGuide, _FaceDirectionField]:
    upper = _guide("upper", ((0.0, 0.0), (12.0, 0.0)))
    lower = _guide("lower", ((0.0, 10.0), (12.0, 10.0)))
    field = _FaceDirectionField(
        tuple(
            _sample(
                fraction,
                direction,
                point=(12.0 * fraction, 5.0),
            )
            for fraction in (0.0, 0.5, 1.0)
        ),
        _mask(),
    )
    pairs = [
        _StationPair(0.0, 0.0),
        _StationPair(4.0, 2.0),
        _StationPair(8.0, 10.0),
        _StationPair(12.0, 12.0),
    ]
    return pairs, upper, lower, field


def test_spacing_insertion_rejects_ray_at_previous_downstream_station() -> None:
    pairs, upper, lower, field = _spacing_refinement_fixture((-4.0, 10.0))

    result = _insert_for_spacing(pairs, upper, lower, field, 4.0)

    assert "no strictly interior" in result.error
    assert result.insertions == 0
    assert len(pairs) == 4


def test_spacing_insertion_rejects_ray_at_next_downstream_station() -> None:
    pairs, upper, lower, field = _spacing_refinement_fixture((4.0, 10.0))

    result = _insert_for_spacing(pairs, upper, lower, field, 4.0)

    assert "no strictly interior" in result.error
    assert result.insertions == 0
    assert len(pairs) == 4


def test_spacing_refinement_has_bounded_near_zero_progress(
    monkeypatch,
) -> None:
    pairs, upper, lower, field = _spacing_refinement_fixture((0.0, 1.0))

    def barely_inside(*_args, **kwargs):
        return kwargs["lower_bound_m"] + 2.0e-8

    monkeypatch.setattr(
        placement_module,
        "_preferred_ray_chainage",
        barely_inside,
    )
    result = _insert_for_spacing(
        pairs,
        upper,
        lower,
        field,
        4.0,
        max_insertions=3,
    )

    assert "deterministic insertion bound" in result.error
    assert result.insertions == result.insertion_limit == 3
    assert len(pairs) == 7
