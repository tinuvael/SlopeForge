"""Pure-domain transverse profile placement for a known Design wall corridor.

This prototype deliberately starts after wall-corridor discovery.  Callers
provide an ordered upper guide, an optional ordered lower guide, and Design
Face direction samples already located in the corridor's along-wall
coordinate.  The module does not select crests/toes, group Face patches, or
intersect Design/Actual surfaces.

For this Phase-1 prototype the Assessment mask tests sample centroids only.
Phase 2 must perform proper triangle/Assessment overlap before constructing
``FaceDirectionSample`` instances; centroid filtering is not corridor
extraction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import acos, ceil, degrees, fsum, hypot, isfinite, sqrt
from statistics import median

from domain.geometry.operations import point_in_polygon
from domain.geometry.surfaces import SurfaceVertex
from domain.geometry.types import PlanPoint, PlanPolygon


_GEOMETRY_TOLERANCE = 1e-9
_CHAINAGE_TOLERANCE = 1e-8
_ANGLE_NUMERICAL_TOLERANCE_DEGREES = 1e-7

# Five nearest distinct station coordinates provide compact, deterministic
# local support without a user-facing smoothing control.  Every sample at a
# selected coordinate participates, so multiple Face tiers down the wall are
# aggregated together.  The support is truncated at detected discontinuities.
_LOCAL_SUPPORT_STATION_COUNT = 5

# Phase-1 discontinuity rule.  A turn is a sector break when it is both large
# and concentrated relative to neighbouring turns.  This is deliberately an
# internal prototype rule, not a calibrated or user-facing acceptance angle.
_DISCONTINUITY_MIN_TURN_DEGREES = 45.0
_DISCONTINUITY_MIN_CONCENTRATION = 0.65
_DISCONTINUITY_WINDOW_EDGES = 2
_DISCONTINUITY_MIN_RELATIVE_WEIGHT = 0.10


def _unit(vector: tuple[float, float]) -> tuple[float, float]:
    length = hypot(*vector)
    if length <= _GEOMETRY_TOLERANCE:
        raise ValueError("Direction must have non-zero plan length")
    return vector[0] / length, vector[1] / length


def _dot(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[0] + first[1] * second[1]


def _cross(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _angle_degrees(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    return degrees(acos(max(-1.0, min(1.0, _dot(_unit(first), _unit(second))))))


def _distance(first: PlanPoint, second: PlanPoint) -> float:
    return hypot(second.x - first.x, second.y - first.y)


@dataclass(frozen=True)
class WallGuide:
    """One ordered plan-view guide of a known wall corridor."""

    points: tuple[PlanPoint, ...]
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in {"upper", "lower", "downstream_extent"}:
            raise ValueError(f"Unsupported wall guide kind: {self.kind!r}")
        if len(self.points) < 2:
            raise ValueError("Wall guide requires at least two points")
        if not all(
            isfinite(point.x) and isfinite(point.y) for point in self.points
        ):
            raise ValueError("Wall guide coordinates must be finite")
        if self.length_m <= _GEOMETRY_TOLERANCE:
            raise ValueError("Wall guide must have non-zero plan length")

    @property
    def cumulative_chainages_m(self) -> tuple[float, ...]:
        values = [0.0]
        for first, second in zip(self.points, self.points[1:]):
            values.append(values[-1] + _distance(first, second))
        return tuple(values)

    @property
    def length_m(self) -> float:
        return sum(_distance(first, second) for first, second in zip(
            self.points, self.points[1:]
        ))

    def point_at(self, chainage_m: float) -> PlanPoint:
        chainage = max(0.0, min(self.length_m, chainage_m))
        cumulative = self.cumulative_chainages_m
        for index, (start, end) in enumerate(zip(cumulative, cumulative[1:])):
            if chainage <= end + _CHAINAGE_TOLERANCE:
                span = end - start
                fraction = 0.0 if span <= _GEOMETRY_TOLERANCE else (
                    chainage - start
                ) / span
                first, second = self.points[index], self.points[index + 1]
                return PlanPoint(
                    first.x + (second.x - first.x) * fraction,
                    first.y + (second.y - first.y) * fraction,
                )
        return self.points[-1]


@dataclass(frozen=True)
class FaceDirectionSample:
    """One explicit directional contribution in a known wall coordinate.

    ``geometric_weight`` is deliberately supplied or derived independently of
    gradient magnitude.  ``station_fraction`` is the known corridor's ordered
    along-wall coordinate, in the inclusive range 0..1.
    """

    point: PlanPoint
    station_fraction: float
    downwall_xy: tuple[float, float] | None
    geometric_weight: float = 1.0
    semantic_role: str = "face"
    source_id: str = ""

    def __post_init__(self) -> None:
        if not isfinite(self.point.x) or not isfinite(self.point.y):
            raise ValueError("Face sample coordinates must be finite")
        if not 0.0 <= self.station_fraction <= 1.0:
            raise ValueError("Face sample station fraction must be between 0 and 1")
        if not isfinite(self.geometric_weight) or self.geometric_weight <= 0.0:
            raise ValueError("Face sample geometric weight must be positive")
        if self.semantic_role == "face":
            if self.downwall_xy is None:
                raise ValueError("Face sample requires a downslope direction")
            object.__setattr__(self, "downwall_xy", _unit(self.downwall_xy))
        else:
            # Non-Face semantics retain location/provenance for diagnostics but
            # can never carry directional authority into the field.
            object.__setattr__(self, "downwall_xy", None)


@dataclass(frozen=True)
class AggregatedFaceDirection:
    downwall_xy: tuple[float, float]
    supporting_weight: float
    angular_dispersion_degrees: float
    angular_support_degrees: float
    sample_count: int
    station_span_fraction: float


@dataclass(frozen=True)
class ProfileTrace:
    station_index: int
    upper_chainage_m: float
    upper_point: PlanPoint
    lower_chainage_m: float | None
    lower_point: PlanPoint | None
    downstream_chainage_m: float
    plan_start: PlanPoint
    plan_end: PlanPoint
    downwall_xy: tuple[float, float]
    face_downwall_xy: tuple[float, float]
    face_alignment_residual_degrees: float
    face_alignment_allowance_degrees: float
    transversality_valid: bool
    lower_guide_constrained: bool


@dataclass(frozen=True)
class PlacementDiagnostics:
    supported: bool
    unsupported_reason: str | None
    sector_break_indices: tuple[int, ...]
    sector_break_station_fractions: tuple[float, ...]
    structural_mapping_valid: bool
    transversality_valid: bool
    order_preserved: bool
    non_crossing: bool
    spacing_within_bound: bool
    max_upper_spacing_m: float
    max_lower_spacing_m: float | None
    max_downstream_spacing_m: float
    max_alignment_residual_degrees: float
    max_alignment_allowance_degrees: float
    max_alignment_excess_degrees: float
    max_neighbour_azimuth_change_degrees: float
    ignored_non_face_samples: int
    rejected_correspondence_corrections: int
    omitted_zero_width_stations: int
    spacing_refinement_insertions: int
    spacing_refinement_limit: int


@dataclass(frozen=True)
class ProfilePlacementSet:
    traces: tuple[ProfileTrace, ...]
    diagnostics: PlacementDiagnostics


@dataclass(frozen=True)
class _DirectionNode:
    station_fraction: float
    samples: tuple[FaceDirectionSample, ...]
    aggregate: AggregatedFaceDirection


@dataclass(frozen=True)
class _StationPair:
    upper_chainage_m: float
    downstream_chainage_m: float


@dataclass(frozen=True)
class _SpacingRefinementResult:
    error: str | None
    insertions: int
    insertion_limit: int


def direction_sample_from_triangle(
    vertices: tuple[SurfaceVertex, SurfaceVertex, SurfaceVertex],
    *,
    station_fraction: float,
    semantic_role: str = "face",
    geometric_weight: float | None = None,
    source_id: str = "",
) -> FaceDirectionSample:
    """Create a normalized horizontal Face direction from one triangle.

    The default geometric weight is the triangle's physical 3-D area.  This
    avoids the collapse of plan-area weighting on steep Faces.  Gradient
    magnitude is never used as an additional direction weight, and callers may
    replace the area with an explicit geometric weight for controlled studies.
    Non-Face roles retain provenance but contribute no direction.
    """
    first, second, third = vertices
    ab = (second.x - first.x, second.y - first.y, second.z - first.z)
    ac = (third.x - first.x, third.y - first.y, third.z - first.z)
    cross_xyz = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    surface_area = sqrt(fsum(value * value for value in cross_xyz)) / 2.0
    weight = surface_area if geometric_weight is None else geometric_weight
    point = PlanPoint(
        (first.x + second.x + third.x) / 3.0,
        (first.y + second.y + third.y) / 3.0,
    )
    if semantic_role != "face":
        return FaceDirectionSample(
            point,
            station_fraction,
            None,
            weight,
            semantic_role,
            source_id,
        )

    ux, uy = second.x - first.x, second.y - first.y
    vx, vy = third.x - first.x, third.y - first.y
    determinant = ux * vy - uy * vx
    if abs(determinant) <= _GEOMETRY_TOLERANCE:
        raise ValueError("Face triangle has no stable horizontal gradient")
    uz, vz = second.z - first.z, third.z - first.z
    gradient_x = (uz * vy - uy * vz) / determinant
    gradient_y = (ux * vz - uz * vx) / determinant
    return FaceDirectionSample(
        point,
        station_fraction,
        _unit((-gradient_x, -gradient_y)),
        weight,
        semantic_role,
        source_id,
    )


def _sample_sort_key(sample: FaceDirectionSample) -> tuple[object, ...]:
    return (
        sample.station_fraction,
        sample.point.x,
        sample.point.y,
        sample.downwall_xy,
        sample.geometric_weight,
        sample.source_id,
    )


def _aggregate_weighted_samples(
    weighted_samples: tuple[tuple[FaceDirectionSample, float], ...],
    *,
    station_span_fraction: float,
) -> AggregatedFaceDirection:
    ordered = tuple(sorted(weighted_samples, key=lambda item: _sample_sort_key(item[0])))
    effective = tuple(
        (sample, sample.geometric_weight * kernel_weight)
        for sample, kernel_weight in ordered
        if kernel_weight > 0.0
    )
    total_weight = fsum(weight for _, weight in effective)
    x = fsum(sample.downwall_xy[0] * weight for sample, weight in effective)
    y = fsum(sample.downwall_xy[1] * weight for sample, weight in effective)
    aggregate = _unit((x, y))
    angular = tuple(
        (_angle_degrees(sample.downwall_xy, aggregate), weight)
        for sample, weight in effective
    )
    dispersion = sqrt(
        fsum(weight * angle * angle for angle, weight in angular) / total_weight
    )

    # The 95% weighted support envelope is the prototype's data-derived
    # transversality allowance.  Negligible noisy triangles cannot widen it,
    # while real local Face rotation remains represented.  No absolute
    # engineering acceptance angle is encoded here.
    cutoff = total_weight * 0.95
    cumulative = 0.0
    support = 0.0
    for angle, weight in sorted(angular, key=lambda item: item[0]):
        cumulative += weight
        support = angle
        if cumulative >= cutoff:
            break
    return AggregatedFaceDirection(
        aggregate,
        total_weight,
        dispersion,
        support,
        len(effective),
        station_span_fraction,
    )


def _direction_nodes(
    ordered: tuple[FaceDirectionSample, ...],
) -> tuple[_DirectionNode, ...]:
    groups: list[list[FaceDirectionSample]] = []
    for sample in ordered:
        if (
            not groups
            or abs(sample.station_fraction - groups[-1][0].station_fraction)
            > _CHAINAGE_TOLERANCE
        ):
            groups.append([sample])
        else:
            groups[-1].append(sample)
    return tuple(
        _DirectionNode(
            group[0].station_fraction,
            tuple(group),
            _aggregate_weighted_samples(
                tuple((sample, 1.0) for sample in group),
                station_span_fraction=0.0,
            ),
        )
        for group in groups
    )


def _discontinuity_fractions(
    nodes: tuple[_DirectionNode, ...],
) -> tuple[float, ...]:
    if len(nodes) < 2:
        return ()
    typical_weight = median(node.aggregate.supporting_weight for node in nodes)
    significant = tuple(
        node
        for node in nodes
        if node.aggregate.supporting_weight
        >= typical_weight * _DISCONTINUITY_MIN_RELATIVE_WEIGHT
    )
    if len(significant) < 2:
        return ()
    turns = tuple(
        _angle_degrees(first.aggregate.downwall_xy, second.aggregate.downwall_xy)
        for first, second in zip(significant, significant[1:])
    )
    breaks = []
    for index, turn in enumerate(turns):
        start = max(0, index - _DISCONTINUITY_WINDOW_EDGES)
        end = min(len(turns), index + _DISCONTINUITY_WINDOW_EDGES + 1)
        local_rotation = fsum(turns[start:end])
        concentration = 0.0 if local_rotation <= 1e-12 else turn / local_rotation
        if (
            turn >= _DISCONTINUITY_MIN_TURN_DEGREES
            and concentration >= _DISCONTINUITY_MIN_CONCENTRATION
        ):
            breaks.append(
                (
                    significant[index].station_fraction
                    + significant[index + 1].station_fraction
                )
                / 2.0
            )
    return tuple(breaks)


class _FaceDirectionField:
    def __init__(
        self,
        samples: tuple[FaceDirectionSample, ...],
        assessment_mask: PlanPolygon,
    ) -> None:
        face_samples = tuple(
            sample
            for sample in samples
            if sample.semantic_role == "face"
            and point_in_polygon(sample.point, assessment_mask)
        )
        if not face_samples:
            raise ValueError("Assessment mask selects no Design Face direction samples")
        self.samples = tuple(sorted(face_samples, key=_sample_sort_key))
        self.nodes = _direction_nodes(self.samples)
        self.break_fractions = _discontinuity_fractions(self.nodes)

    def at(self, station_fraction: float) -> AggregatedFaceDirection:
        fraction = max(0.0, min(1.0, station_fraction))
        lower_bound, upper_bound = 0.0, 1.0
        for break_fraction in self.break_fractions:
            if fraction <= break_fraction:
                upper_bound = break_fraction
                break
            lower_bound = break_fraction
        sector_nodes = tuple(
            node
            for node in self.nodes
            if lower_bound - _CHAINAGE_TOLERANCE
            <= node.station_fraction
            <= upper_bound + _CHAINAGE_TOLERANCE
        )
        selected = tuple(sorted(
            sorted(
                sector_nodes,
                key=lambda node: (
                    abs(node.station_fraction - fraction),
                    node.station_fraction,
                ),
            )[:_LOCAL_SUPPORT_STATION_COUNT],
            key=lambda node: node.station_fraction,
        ))
        station_values = tuple(node.station_fraction for node in selected)
        positive_gaps = tuple(
            second - first
            for first, second in zip(station_values, station_values[1:])
            if second - first > _CHAINAGE_TOLERANCE
        )
        local_step = median(positive_gaps) if positive_gaps else 1.0
        farthest = max(
            (abs(value - fraction) for value in station_values),
            default=0.0,
        )
        support_radius = max(local_step, farthest + local_step)
        weighted = tuple(
            (
                sample,
                (
                    1.0
                    - min(
                        1.0,
                        abs(node.station_fraction - fraction) / support_radius,
                    )
                )
                ** 2,
            )
            for node in selected
            for sample in node.samples
        )
        return _aggregate_weighted_samples(
            weighted,
            station_span_fraction=(
                max(station_values) - min(station_values)
                if station_values
                else 0.0
            ),
        )


def aggregate_face_direction(
    samples: tuple[FaceDirectionSample, ...],
    station_fraction: float,
    assessment_mask: PlanPolygon,
) -> AggregatedFaceDirection:
    """Inspect the deterministic Design-Face aggregate at one wall station."""
    return _FaceDirectionField(samples, assessment_mask).at(station_fraction)


def _ray_guide_intersections(
    origin: PlanPoint,
    direction: tuple[float, float],
    guide: WallGuide,
) -> tuple[tuple[float, float], ...]:
    cumulative = guide.cumulative_chainages_m
    candidates = []
    ray = _unit(direction)
    for index, (first, second) in enumerate(zip(guide.points, guide.points[1:])):
        segment = (second.x - first.x, second.y - first.y)
        offset = (first.x - origin.x, first.y - origin.y)
        denominator = _cross(ray, segment)
        if abs(denominator) <= _GEOMETRY_TOLERANCE:
            continue
        ray_distance = _cross(offset, segment) / denominator
        segment_fraction = _cross(offset, ray) / denominator
        if ray_distance < -_GEOMETRY_TOLERANCE:
            continue
        if not -_GEOMETRY_TOLERANCE <= segment_fraction <= 1.0 + _GEOMETRY_TOLERANCE:
            continue
        segment_fraction = max(0.0, min(1.0, segment_fraction))
        chainage = cumulative[index] + hypot(*segment) * segment_fraction
        candidates.append((ray_distance, chainage))
    deduplicated = []
    for candidate in sorted(candidates, key=lambda value: (value[0], value[1])):
        if not any(abs(candidate[1] - current[1]) <= _CHAINAGE_TOLERANCE for current in deduplicated):
            deduplicated.append(candidate)
    return tuple(deduplicated)


def _preferred_ray_chainage(
    origin: PlanPoint,
    direction: tuple[float, float],
    guide: WallGuide,
    expected_chainage_m: float,
    *,
    lower_bound_m: float = 0.0,
    upper_bound_m: float | None = None,
) -> float | None:
    upper_bound = guide.length_m if upper_bound_m is None else upper_bound_m
    candidates = tuple(
        (ray_distance, chainage)
        for ray_distance, chainage in _ray_guide_intersections(
            origin, direction, guide
        )
        if lower_bound_m + _CHAINAGE_TOLERANCE < chainage
        < upper_bound - _CHAINAGE_TOLERANCE
        or (
            lower_bound_m <= chainage <= upper_bound
            and (
                abs(chainage - lower_bound_m) <= _CHAINAGE_TOLERANCE
                or abs(chainage - upper_bound) <= _CHAINAGE_TOLERANCE
            )
        )
    )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda value: (
            abs(value[1] - expected_chainage_m),
            value[0],
            value[1],
        ),
    )[1]


def _trace_points(
    pair: _StationPair, upper: WallGuide, downstream: WallGuide
) -> tuple[PlanPoint, PlanPoint]:
    return (
        upper.point_at(pair.upper_chainage_m),
        downstream.point_at(pair.downstream_chainage_m),
    )


def _properly_cross(
    first: tuple[PlanPoint, PlanPoint],
    second: tuple[PlanPoint, PlanPoint],
) -> bool:
    a, b = first
    c, d = second
    r = (b.x - a.x, b.y - a.y)
    s = (d.x - c.x, d.y - c.y)
    denominator = _cross(r, s)
    if abs(denominator) <= _GEOMETRY_TOLERANCE:
        return False
    offset = (c.x - a.x, c.y - a.y)
    first_fraction = _cross(offset, s) / denominator
    second_fraction = _cross(offset, r) / denominator
    return (
        _GEOMETRY_TOLERANCE < first_fraction < 1.0 - _GEOMETRY_TOLERANCE
        and _GEOMETRY_TOLERANCE
        < second_fraction
        < 1.0 - _GEOMETRY_TOLERANCE
    )


def _candidate_crosses_neighbours(
    pairs: list[_StationPair],
    index: int,
    candidate: _StationPair,
    upper: WallGuide,
    downstream: WallGuide,
) -> bool:
    candidate_trace = _trace_points(candidate, upper, downstream)
    return any(
        _properly_cross(
            candidate_trace,
            _trace_points(pairs[neighbour], upper, downstream),
        )
        for neighbour in (index - 1, index + 1)
        if 0 <= neighbour < len(pairs)
    )


def _pair_residual(
    pair: _StationPair,
    upper: WallGuide,
    downstream: WallGuide,
    field: _FaceDirectionField,
) -> float:
    start, end = _trace_points(pair, upper, downstream)
    trace_direction = _unit((end.x - start.x, end.y - start.y))
    station_fraction = pair.upper_chainage_m / upper.length_m
    return _angle_degrees(trace_direction, field.at(station_fraction).downwall_xy)


def _optimise_ordered_correspondence(
    pairs: list[_StationPair],
    upper: WallGuide,
    lower: WallGuide,
    field: _FaceDirectionField,
) -> int:
    rejected = 0
    if len(pairs) <= 2:
        return rejected
    # Alternating sweeps let an accepted local correction open room for its
    # neighbour without introducing a global optimisation framework.
    for indices in (range(1, len(pairs) - 1), range(len(pairs) - 2, 0, -1)):
        for index in indices:
            pair = pairs[index]
            origin = upper.point_at(pair.upper_chainage_m)
            direction = field.at(pair.upper_chainage_m / upper.length_m).downwall_xy
            candidate_chainage = _preferred_ray_chainage(
                origin,
                direction,
                lower,
                pair.downstream_chainage_m,
                lower_bound_m=pairs[index - 1].downstream_chainage_m,
                upper_bound_m=pairs[index + 1].downstream_chainage_m,
            )
            if candidate_chainage is None:
                continue
            if not (
                pairs[index - 1].downstream_chainage_m + _CHAINAGE_TOLERANCE
                < candidate_chainage
                < pairs[index + 1].downstream_chainage_m - _CHAINAGE_TOLERANCE
            ):
                rejected += 1
                continue
            candidate = replace(pair, downstream_chainage_m=candidate_chainage)
            if _pair_residual(candidate, upper, lower, field) >= (
                _pair_residual(pair, upper, lower, field) - 1e-10
            ):
                continue
            if _candidate_crosses_neighbours(
                pairs, index, candidate, upper, lower
            ):
                rejected += 1
                continue
            pairs[index] = candidate
    return rejected


def _insert_for_spacing(
    pairs: list[_StationPair],
    upper: WallGuide,
    downstream: WallGuide,
    field: _FaceDirectionField,
    requested_spacing_m: float,
    *,
    max_insertions: int | None = None,
) -> _SpacingRefinementResult:
    calculated_limit = max(
        16,
        4
        * (
            ceil(upper.length_m / requested_spacing_m)
            + ceil(downstream.length_m / requested_spacing_m)
            + len(pairs)
        ),
    )
    insertion_limit = calculated_limit if max_insertions is None else max_insertions
    insertions = 0
    while True:
        violation_index = next((
            index
            for index, (first, second) in enumerate(zip(pairs, pairs[1:]))
            if (
                second.upper_chainage_m - first.upper_chainage_m
                > requested_spacing_m + _CHAINAGE_TOLERANCE
                or second.downstream_chainage_m - first.downstream_chainage_m
                > requested_spacing_m + _CHAINAGE_TOLERANCE
            )
        ), None)
        if violation_index is None:
            return _SpacingRefinementResult(None, insertions, insertion_limit)
        if insertions >= insertion_limit:
            return _SpacingRefinementResult(
                "Spacing refinement exceeded its deterministic insertion bound",
                insertions,
                insertion_limit,
            )
        first, second = pairs[violation_index], pairs[violation_index + 1]
        upper_chainage = (
            first.upper_chainage_m + second.upper_chainage_m
        ) / 2.0
        expected_downstream = (
            first.downstream_chainage_m + second.downstream_chainage_m
        ) / 2.0
        origin = upper.point_at(upper_chainage)
        direction = field.at(upper_chainage / upper.length_m).downwall_xy
        ray_chainage = _preferred_ray_chainage(
            origin,
            direction,
            downstream,
            expected_downstream,
            lower_bound_m=first.downstream_chainage_m,
            upper_bound_m=second.downstream_chainage_m,
        )
        if ray_chainage is None or not (
            first.downstream_chainage_m + _CHAINAGE_TOLERANCE
            < ray_chainage
            < second.downstream_chainage_m - _CHAINAGE_TOLERANCE
        ):
            return _SpacingRefinementResult(
                "Spacing refinement found no strictly interior "
                "Face-compatible downstream station",
                insertions,
                insertion_limit,
            )
        downstream_chainage = ray_chainage
        old_downstream_gap = (
            second.downstream_chainage_m - first.downstream_chainage_m
        )
        refined_max_gap = max(
            downstream_chainage - first.downstream_chainage_m,
            second.downstream_chainage_m - downstream_chainage,
        )
        if refined_max_gap >= old_downstream_gap - _CHAINAGE_TOLERANCE:
            return _SpacingRefinementResult(
                "Spacing refinement cannot strictly reduce the violating "
                "downstream interval",
                insertions,
                insertion_limit,
            )
        candidate = _StationPair(upper_chainage, downstream_chainage)
        proposed = [*pairs[: violation_index + 1], candidate, *pairs[violation_index + 1 :]]
        if _candidate_crosses_neighbours(
            proposed, violation_index + 1, candidate, upper, downstream
        ):
            return _SpacingRefinementResult(
                "Face-authoritative spacing refinement would cross "
                "neighbouring traces",
                insertions,
                insertion_limit,
            )
        pairs[:] = proposed
        insertions += 1


def _strictly_increasing(values: tuple[float, ...]) -> bool:
    return all(
        second - first > _CHAINAGE_TOLERANCE
        for first, second in zip(values, values[1:])
    )


def _max_gap(values: tuple[float, ...]) -> float:
    return max((second - first for first, second in zip(values, values[1:])), default=0.0)


def _empty_unsupported(
    reason: str,
    *,
    ignored_non_face_samples: int,
    sector_break_indices: tuple[int, ...] = (),
    sector_break_station_fractions: tuple[float, ...] = (),
    omitted_zero_width_stations: int = 0,
    spacing_refinement_insertions: int = 0,
    spacing_refinement_limit: int = 0,
) -> ProfilePlacementSet:
    return ProfilePlacementSet((), PlacementDiagnostics(
        supported=False,
        unsupported_reason=reason,
        sector_break_indices=sector_break_indices,
        sector_break_station_fractions=sector_break_station_fractions,
        structural_mapping_valid=False,
        transversality_valid=False,
        order_preserved=False,
        non_crossing=False,
        spacing_within_bound=False,
        max_upper_spacing_m=0.0,
        max_lower_spacing_m=None,
        max_downstream_spacing_m=0.0,
        max_alignment_residual_degrees=0.0,
        max_alignment_allowance_degrees=0.0,
        max_alignment_excess_degrees=0.0,
        max_neighbour_azimuth_change_degrees=0.0,
        ignored_non_face_samples=ignored_non_face_samples,
        rejected_correspondence_corrections=0,
        omitted_zero_width_stations=omitted_zero_width_stations,
        spacing_refinement_insertions=spacing_refinement_insertions,
        spacing_refinement_limit=spacing_refinement_limit,
    ))


def place_profile_traces(
    face_samples: tuple[FaceDirectionSample, ...],
    upper_guide: WallGuide,
    assessment_mask: PlanPolygon,
    *,
    requested_spacing_m: float,
    lower_guide: WallGuide | None = None,
    downstream_extent: WallGuide | None = None,
) -> ProfilePlacementSet:
    """Place straight transverse traces in an explicitly known wall corridor.

    A Lower guide constrains correspondence but never supplies direction.
    Without one, every trace follows the Design Face direction exactly until it
    intersects the supplied downstream extent.
    """
    if upper_guide.kind != "upper":
        raise ValueError("Profile placement requires an upper wall guide")
    if requested_spacing_m <= 0.0 or not isfinite(requested_spacing_m):
        raise ValueError("Requested profile spacing must be positive")
    if lower_guide is not None and lower_guide.kind != "lower":
        raise ValueError("Lower guide must have kind='lower'")
    if downstream_extent is not None and downstream_extent.kind != "downstream_extent":
        raise ValueError("Downstream extent must have kind='downstream_extent'")
    if lower_guide is None and downstream_extent is None:
        raise ValueError("Missing Lower guide requires a downstream extent")

    downstream = lower_guide or downstream_extent
    ignored_non_face = sum(sample.semantic_role != "face" for sample in face_samples)
    field = _FaceDirectionField(face_samples, assessment_mask)
    initial_intervals = max(
        1,
        ceil(max(upper_guide.length_m, downstream.length_m) / requested_spacing_m),
    )
    if field.break_fractions:
        break_indices = tuple(sorted({
            max(1, min(initial_intervals - 1, round(value * initial_intervals)))
            for value in field.break_fractions
        }))
        return _empty_unsupported(
            "Localized Design Face direction discontinuity requires a wall-sector split",
            ignored_non_face_samples=ignored_non_face,
            sector_break_indices=break_indices,
            sector_break_station_fractions=field.break_fractions,
        )
    pairs = [
        _StationPair(
            upper_guide.length_m * index / initial_intervals,
            downstream.length_m * index / initial_intervals,
        )
        for index in range(initial_intervals + 1)
    ]

    rejected_corrections = 0
    if lower_guide is not None:
        rejected_corrections = _optimise_ordered_correspondence(
            pairs, upper_guide, lower_guide, field
        )
    else:
        directed_pairs = []
        for pair in pairs:
            origin = upper_guide.point_at(pair.upper_chainage_m)
            direction = field.at(
                pair.upper_chainage_m / upper_guide.length_m
            ).downwall_xy
            chainage = _preferred_ray_chainage(
                origin,
                direction,
                downstream,
                pair.downstream_chainage_m,
            )
            if chainage is None:
                return _empty_unsupported(
                    "Design Face direction does not reach the supplied downstream extent",
                    ignored_non_face_samples=ignored_non_face,
                )
            directed_pairs.append(replace(pair, downstream_chainage_m=chainage))
        pairs = directed_pairs

    upper_values = tuple(pair.upper_chainage_m for pair in pairs)
    downstream_values = tuple(pair.downstream_chainage_m for pair in pairs)
    if not _strictly_increasing(upper_values) or not _strictly_increasing(
        downstream_values
    ):
        breaks = tuple(
            index
            for index, (first, second) in enumerate(
                zip(downstream_values, downstream_values[1:]), start=1
            )
            if second - first <= _CHAINAGE_TOLERANCE
        )
        result = _empty_unsupported(
            "Design Face mapping does not preserve downstream order; split the wall sector",
            ignored_non_face_samples=ignored_non_face,
        )
        return replace(
            result,
            diagnostics=replace(result.diagnostics, sector_break_indices=breaks),
        )

    spacing_error = _insert_for_spacing(
        pairs,
        upper_guide,
        downstream,
        field,
        requested_spacing_m,
    )
    if spacing_error.error is not None:
        return _empty_unsupported(
            spacing_error.error,
            ignored_non_face_samples=ignored_non_face,
            spacing_refinement_insertions=spacing_error.insertions,
            spacing_refinement_limit=spacing_error.insertion_limit,
        )

    nonzero_pairs = []
    omitted_zero_width = 0
    for pair in pairs:
        start, end = _trace_points(pair, upper_guide, downstream)
        if _distance(start, end) <= _GEOMETRY_TOLERANCE:
            omitted_zero_width += 1
            continue
        nonzero_pairs.append(pair)
    if not nonzero_pairs:
        return _empty_unsupported(
            "Known wall corridor has no nonzero-width transverse station",
            ignored_non_face_samples=ignored_non_face,
            omitted_zero_width_stations=omitted_zero_width,
            spacing_refinement_insertions=spacing_error.insertions,
            spacing_refinement_limit=spacing_error.insertion_limit,
        )

    traces = []
    for index, pair in enumerate(nonzero_pairs):
        start, end = _trace_points(pair, upper_guide, downstream)
        direction = _unit((end.x - start.x, end.y - start.y))
        aggregate = field.at(pair.upper_chainage_m / upper_guide.length_m)
        residual = _angle_degrees(direction, aggregate.downwall_xy)
        allowance = (
            aggregate.angular_support_degrees
            + _ANGLE_NUMERICAL_TOLERANCE_DEGREES
        )
        traces.append(ProfileTrace(
            station_index=index,
            upper_chainage_m=pair.upper_chainage_m,
            upper_point=start,
            lower_chainage_m=(
                pair.downstream_chainage_m if lower_guide is not None else None
            ),
            lower_point=end if lower_guide is not None else None,
            downstream_chainage_m=pair.downstream_chainage_m,
            plan_start=start,
            plan_end=end,
            downwall_xy=direction,
            face_downwall_xy=aggregate.downwall_xy,
            face_alignment_residual_degrees=residual,
            face_alignment_allowance_degrees=allowance,
            transversality_valid=residual <= allowance,
            lower_guide_constrained=lower_guide is not None,
        ))
    traces_tuple = tuple(traces)

    crossing_breaks = tuple(sorted({
        second_index
        for first_index, first in enumerate(traces_tuple)
        for second_index, second in enumerate(
            traces_tuple[first_index + 1 :], start=first_index + 1
        )
        if _properly_cross(
            (first.plan_start, first.plan_end),
            (second.plan_start, second.plan_end),
        )
    }))
    upper_values = tuple(trace.upper_chainage_m for trace in traces_tuple)
    downstream_values = tuple(trace.downstream_chainage_m for trace in traces_tuple)
    lower_values = (
        tuple(trace.lower_chainage_m for trace in traces_tuple)
        if lower_guide is not None
        else None
    )
    order_preserved = _strictly_increasing(upper_values) and _strictly_increasing(
        downstream_values
    )
    max_upper_spacing = _max_gap(upper_values)
    max_downstream_spacing = _max_gap(downstream_values)
    max_lower_spacing = _max_gap(lower_values) if lower_values is not None else None
    spacing_within_bound = (
        max_upper_spacing <= requested_spacing_m + _CHAINAGE_TOLERANCE
        and max_downstream_spacing <= requested_spacing_m + _CHAINAGE_TOLERANCE
    )
    non_crossing = not crossing_breaks
    structural_mapping_valid = order_preserved and non_crossing and spacing_within_bound
    transversality_valid = all(trace.transversality_valid for trace in traces_tuple)
    supported = structural_mapping_valid and transversality_valid
    reason = None
    if not order_preserved:
        reason = "Profile mapping does not preserve guide order"
    elif not non_crossing:
        reason = "Transverse traces cross; split the wall sector at reported indices"
    elif not spacing_within_bound:
        reason = "Profile placement exceeds the requested local spacing"
    elif not transversality_valid:
        reason = (
            "Structurally valid guide mapping is not transverse to the local "
            "Design Face direction support"
        )

    diagnostics = PlacementDiagnostics(
        supported=supported,
        unsupported_reason=reason,
        sector_break_indices=crossing_breaks,
        sector_break_station_fractions=(),
        structural_mapping_valid=structural_mapping_valid,
        transversality_valid=transversality_valid,
        order_preserved=order_preserved,
        non_crossing=non_crossing,
        spacing_within_bound=spacing_within_bound,
        max_upper_spacing_m=max_upper_spacing,
        max_lower_spacing_m=max_lower_spacing,
        max_downstream_spacing_m=max_downstream_spacing,
        max_alignment_residual_degrees=max(
            (trace.face_alignment_residual_degrees for trace in traces_tuple),
            default=0.0,
        ),
        max_alignment_allowance_degrees=max(
            (trace.face_alignment_allowance_degrees for trace in traces_tuple),
            default=0.0,
        ),
        max_alignment_excess_degrees=max(
            (
                max(
                    0.0,
                    trace.face_alignment_residual_degrees
                    - trace.face_alignment_allowance_degrees,
                )
                for trace in traces_tuple
            ),
            default=0.0,
        ),
        max_neighbour_azimuth_change_degrees=max((
            _angle_degrees(first.downwall_xy, second.downwall_xy)
            for first, second in zip(traces_tuple, traces_tuple[1:])
        ), default=0.0),
        ignored_non_face_samples=ignored_non_face,
        rejected_correspondence_corrections=rejected_corrections,
        omitted_zero_width_stations=omitted_zero_width,
        spacing_refinement_insertions=spacing_error.insertions,
        spacing_refinement_limit=spacing_error.insertion_limit,
    )
    return ProfilePlacementSet(traces_tuple, diagnostics)


__all__ = [
    "AggregatedFaceDirection",
    "FaceDirectionSample",
    "PlacementDiagnostics",
    "ProfilePlacementSet",
    "ProfileTrace",
    "WallGuide",
    "aggregate_face_direction",
    "direction_sample_from_triangle",
    "place_profile_traces",
]
