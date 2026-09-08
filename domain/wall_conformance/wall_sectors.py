"""Assessment-local WallSector extraction for Wall Conformance v2 Phase 2B.

This pure-domain layer consumes the exact semantic topology built by Phase 2A.
It does not infer topology, place profiles, or use Assessment boundary geometry
as wall-direction authority.  Assessment geometry only clips Design Face
triangles into positive-area local seeds.  Every output direction remains a
normalized Design Face steepest-descent direction.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from heapq import heappop, heappush
from math import acos, degrees, fsum, hypot, isfinite

from domain.geometry.operations import (
    clip_datamine_line_by_polygon,
    validate_simple_polygon,
)
from domain.geometry.surfaces import SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanLineString, PlanPoint, PlanPolygon
from domain.wall_conformance.design_topology import (
    CorridorConnection,
    DesignTopologyIndex,
    EdgeGeometryKey,
    FaceDirectionEvidence,
    LocalPortalRunPair,
    PortalSide,
    TransitionPortal,
    TriangleGeometryKey,
)
from domain.wall_conformance.profile_placement import (
    FaceDirectionSample,
    WallGuide,
    aggregate_face_direction,
)


_GEOMETRY_TOLERANCE = 1e-9
_AREA_RELATIVE_TOLERANCE = 1e-12

# This is a localized-break detector, not a calibrated production acceptance
# angle.  A large turn is a break only when it is concentrated at one station
# edge rather than distributed along a smooth curve.  Phase 1 uses the same
# prototype distinction between distributed curvature and a sharp corner.
_LOCAL_BREAK_MIN_TURN_DEGREES = 45.0
_LOCAL_BREAK_MIN_CONCENTRATION = 0.65


@dataclass(frozen=True, order=True)
class StationInterval:
    start_fraction: float
    end_fraction: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.start_fraction < self.end_fraction <= 1.0:
            raise ValueError("Station interval must be an increasing subset of 0..1")


@dataclass(frozen=True)
class AssessmentFaceFragment:
    """One positive-plan-area Face/Assessment overlap fragment."""

    fragment_id: str
    triangle_index: int
    triangle_key: TriangleGeometryKey
    face_component_index: int
    points: tuple[PlanPoint, ...]
    overlap_area: float
    representative_point: PlanPoint
    downwall_xy: tuple[float, float]
    geometric_weight: float
    source_id: str


@dataclass(frozen=True)
class WallSectorDiagnostics:
    codes: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuideStationMapping:
    """Monotone corridor-station coordinates along one terminal guide."""

    chainages_m: tuple[float, ...]
    station_fractions: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.chainages_m) != len(self.station_fractions):
            raise ValueError("Guide station mapping arrays must align")
        if len(self.chainages_m) < 2:
            raise ValueError("Guide station mapping requires at least two nodes")
        if not all(isfinite(value) for value in (
            *self.chainages_m, *self.station_fractions,
        )):
            raise ValueError("Guide station mapping values must be finite")
        if abs(self.chainages_m[0]) > _GEOMETRY_TOLERANCE:
            raise ValueError("Guide station mapping must start at zero chainage")
        if any(
            second - first <= _GEOMETRY_TOLERANCE
            for first, second in zip(self.chainages_m, self.chainages_m[1:])
        ):
            raise ValueError("Guide station mapping chainages must increase")
        if any(
            second < first - _GEOMETRY_TOLERANCE
            for first, second in zip(
                self.station_fractions, self.station_fractions[1:]
            )
        ):
            raise ValueError("Guide station mapping stations must be monotone")
        if (
            self.station_fractions[0] < -_GEOMETRY_TOLERANCE
            or self.station_fractions[-1] > 1.0 + _GEOMETRY_TOLERANCE
            or self.station_fractions[-1] - self.station_fractions[0]
            <= _GEOMETRY_TOLERANCE
        ):
            raise ValueError(
                "Guide station mapping must occupy an increasing subset of 0..1"
            )

    def chainage_at_station(self, station_fraction: float) -> float:
        """Interpolate terminal chainage without geometric projection."""
        if not (
            self.station_fractions[0] - _GEOMETRY_TOLERANCE
            <= station_fraction
            <= self.station_fractions[-1] + _GEOMETRY_TOLERANCE
        ):
            raise ValueError("Station lies outside terminal guide coverage")
        for index, (first, second) in enumerate(zip(
            self.station_fractions, self.station_fractions[1:]
        )):
            if (
                first - _GEOMETRY_TOLERANCE
                <= station_fraction
                <= second + _GEOMETRY_TOLERANCE
            ):
                fraction = (
                    0.0
                    if second - first <= _GEOMETRY_TOLERANCE
                    else (station_fraction - first) / (second - first)
                )
                return self.chainages_m[index] + fraction * (
                    self.chainages_m[index + 1] - self.chainages_m[index]
                )
        return self.chainages_m[-1]


@dataclass(frozen=True)
class WallSector:
    sector_id: str
    upper_guide: WallGuide
    lower_guide: WallGuide | None
    downstream_extent: WallGuide | None
    face_direction_samples: tuple[FaceDirectionSample, ...]
    assessed_station_intervals: tuple[StationInterval, ...]
    closed_along_strike: bool
    seam_point: PlanPoint | None
    supported: bool
    face_component_ids: tuple[str, ...]
    portal_ids: tuple[str, ...]
    connection_ids: tuple[str, ...]
    fragment_ids: tuple[str, ...]
    span_states: tuple[CorridorSpanState, ...]
    portal_correspondences: tuple[PortalSpanCorrespondence, ...]
    diagnostics: WallSectorDiagnostics
    lower_station_mapping: GuideStationMapping | None = None
    downstream_station_mapping: GuideStationMapping | None = None
    upper_portal_id: str | None = None
    lower_portal_id: str | None = None
    downstream_portal_id: str | None = None


@dataclass(frozen=True)
class WallSectorExtractionResult:
    sectors: tuple[WallSector, ...]
    overlap_fragments: tuple[AssessmentFaceFragment, ...]
    diagnostics: WallSectorDiagnostics

    @property
    def supported_sectors(self) -> tuple[WallSector, ...]:
        return tuple(sector for sector in self.sectors if sector.supported)

    @property
    def canonical_signature(self) -> tuple[object, ...]:
        def q(value: float) -> float:
            return round(value, 10)

        return tuple(
            (
                tuple((q(point.x), q(point.y)) for point in sector.upper_guide.points),
                (
                    tuple((q(point.x), q(point.y)) for point in sector.lower_guide.points)
                    if sector.lower_guide is not None
                    else None
                ),
                (
                    tuple((q(point.x), q(point.y)) for point in sector.downstream_extent.points)
                    if sector.downstream_extent is not None
                    else None
                ),
                (
                    (
                        tuple(q(value) for value in sector.lower_station_mapping.chainages_m),
                        tuple(
                            q(value)
                            for value in sector.lower_station_mapping.station_fractions
                        ),
                    )
                    if sector.lower_station_mapping is not None
                    else None
                ),
                (
                    (
                        tuple(q(value) for value in sector.downstream_station_mapping.chainages_m),
                        tuple(
                            q(value)
                            for value in sector.downstream_station_mapping.station_fractions
                        ),
                    )
                    if sector.downstream_station_mapping is not None
                    else None
                ),
                sector.upper_portal_id,
                sector.lower_portal_id,
                sector.downstream_portal_id,
                tuple(
                    (
                        sample.source_id,
                        q(sample.point.x),
                        q(sample.point.y),
                        q(sample.station_fraction),
                        tuple(q(value) for value in sample.downwall_xy),
                        q(sample.geometric_weight),
                    )
                    for sample in sector.face_direction_samples
                ),
                tuple(
                    (q(interval.start_fraction), q(interval.end_fraction))
                    for interval in sector.assessed_station_intervals
                ),
                sector.closed_along_strike,
                (
                    (q(sector.seam_point.x), q(sector.seam_point.y))
                    if sector.seam_point is not None
                    else None
                ),
                sector.supported,
                tuple(
                    (
                        state.face_component_index,
                        state.incoming_portal_id,
                        state.outgoing_portal_id,
                        q(state.station_start),
                        q(state.station_end),
                        state.active_triangle_keys,
                    )
                    for state in sector.span_states
                ),
                tuple(
                    (
                        item.connection_id,
                        item.source_portal_id,
                        item.target_portal_id,
                        q(item.source_station_start),
                        q(item.source_station_end),
                        q(item.source_chainage_start_fraction),
                        q(item.source_chainage_end_fraction),
                        q(item.target_chainage_start_fraction),
                        q(item.target_chainage_end_fraction),
                        item.target_reversed,
                    )
                    for item in sector.portal_correspondences
                ),
                sector.diagnostics.codes,
            )
            for sector in self.sectors
        )


@dataclass(frozen=True)
class _GuideRun:
    portal_id: str
    face_component_index: int
    side: PortalSide
    source_kind: str
    points: tuple[PlanPoint, ...]
    face_triangle_indices: tuple[int, ...]
    closed: bool
    station_support_points: tuple[PlanPoint, ...] = ()
    station_support_triangle_indices: tuple[int, ...] = ()
    station_support_start: float | None = None
    station_support_end: float | None = None


@dataclass(frozen=True)
class _RunStationCandidate:
    points: tuple[PlanPoint, ...]
    stations: tuple[float, ...]
    canonical_local_points: tuple[PlanPoint, ...]


@dataclass(frozen=True)
class _TerminalRunSelection:
    run: _GuideRun | None
    values: tuple[tuple[PlanPoint, ...], tuple[float, ...]] | None
    ambiguous: bool
    non_injective: bool


@dataclass(frozen=True)
class _TerminalGuideCorrespondence:
    result: tuple[WallGuide, GuideStationMapping] | None
    ambiguous: bool


@dataclass(frozen=True)
class _LocalPortalRunChain:
    source_run: _GuideRun
    source_values: tuple[tuple[PlanPoint, ...], tuple[float, ...]]
    target_run: _GuideRun


@dataclass(frozen=True)
class CorridorSpanState:
    """One locally active Face-layer span in an assembled wall corridor."""

    face_component_index: int
    incoming_portal_id: str | None
    outgoing_portal_id: str | None
    station_start: float
    station_end: float
    active_triangle_keys: tuple[TriangleGeometryKey, ...]


@dataclass(frozen=True)
class PortalSpanCorrespondence:
    """Monotone transport of one local subspan across a Phase-2A connection.

    Chainage fractions use the ordered source run and the correspondingly
    oriented target run.  ``target_reversed`` records whether that target
    order reverses the portal's stored topology-chain order.
    """

    connection_id: str
    source_portal_id: str
    target_portal_id: str
    source_station_start: float
    source_station_end: float
    source_chainage_start_fraction: float
    source_chainage_end_fraction: float
    target_chainage_start_fraction: float
    target_chainage_end_fraction: float
    target_reversed: bool


@dataclass(frozen=True)
class _DiscoveredCorridor:
    component_indices: tuple[int, ...]
    connections: tuple[CorridorConnection, ...]
    seed_component_index: int
    seed_triangle_indices: tuple[int, ...]
    discovery_codes: tuple[str, ...]


@dataclass(frozen=True)
class _AssessmentSeedGroup:
    """One topology-connected, spatially local Assessment/Face seed set."""

    face_component_index: int
    fragments: tuple[AssessmentFaceFragment, ...]


def _point_key(point: PlanPoint) -> tuple[float, float]:
    return point.x, point.y


def _vertex_key(vertex: SurfaceVertex) -> tuple[float, float, float]:
    return vertex.x, vertex.y, vertex.z


def _signed_area(points: tuple[PlanPoint, ...]) -> float:
    return fsum(
        first.x * second.y - second.x * first.y
        for first, second in zip(points, points[1:] + points[:1])
    ) / 2.0


def _polygon_area(points: tuple[PlanPoint, ...]) -> float:
    return abs(_signed_area(points))


def _cross(first: PlanPoint, second: PlanPoint, third: PlanPoint) -> float:
    return (
        (second.x - first.x) * (third.y - first.y)
        - (second.y - first.y) * (third.x - first.x)
    )


def _unit(vector: tuple[float, float]) -> tuple[float, float] | None:
    length = hypot(*vector)
    if length <= _GEOMETRY_TOLERANCE:
        return None
    return vector[0] / length, vector[1] / length


def _dot(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[0] + first[1] * second[1]


def _angle_degrees(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    return degrees(acos(max(-1.0, min(1.0, _dot(first, second)))))


def _canonical_polygon_vertices(polygon: PlanPolygon) -> tuple[PlanPoint, ...]:
    points = polygon.ring[:-1]
    if _signed_area(points) < 0.0:
        points = tuple(reversed(points))
    seam = min(range(len(points)), key=lambda index: _point_key(points[index]))
    return points[seam:] + points[:seam]


def _canonical_plan_polygon(points: tuple[PlanPoint, ...]) -> tuple[PlanPoint, ...]:
    if _signed_area(points) < 0.0:
        points = tuple(reversed(points))
    seam = min(range(len(points)), key=lambda index: _point_key(points[index]))
    return points[seam:] + points[:seam]


def _point_in_triangle(
    point: PlanPoint,
    triangle: tuple[PlanPoint, PlanPoint, PlanPoint],
) -> bool:
    return all(
        _cross(first, second, point) >= -_GEOMETRY_TOLERANCE
        for first, second in zip(triangle, triangle[1:] + triangle[:1])
    )


def _triangulate_assessment(
    polygon: PlanPolygon,
) -> tuple[tuple[PlanPoint, PlanPoint, PlanPoint], ...]:
    """Deterministic ear clipping of a validated simple polygon."""
    validate_simple_polygon(polygon)
    remaining = list(_canonical_polygon_vertices(polygon))
    triangles: list[tuple[PlanPoint, PlanPoint, PlanPoint]] = []
    while len(remaining) > 3:
        ears: list[
            tuple[
                tuple[tuple[float, float], ...],
                int,
                tuple[PlanPoint, PlanPoint, PlanPoint],
            ]
        ] = []
        for index, point in enumerate(remaining):
            previous = remaining[index - 1]
            following = remaining[(index + 1) % len(remaining)]
            triangle = (previous, point, following)
            if _cross(*triangle) <= _GEOMETRY_TOLERANCE:
                continue
            if any(
                _point_in_triangle(candidate, triangle)
                for candidate_index, candidate in enumerate(remaining)
                if candidate_index not in {
                    index - 1 if index > 0 else len(remaining) - 1,
                    index,
                    (index + 1) % len(remaining),
                }
            ):
                continue
            ears.append((
                tuple(sorted(_point_key(vertex) for vertex in triangle)),
                index,
                triangle,
            ))
        if not ears:
            raise ValueError("Assessment polygon cannot be triangulated deterministically")
        _, index, triangle = min(ears, key=lambda item: item[0])
        triangles.append(triangle)
        del remaining[index]
    final = tuple(remaining)
    if _cross(*final) < 0.0:
        final = (final[0], final[2], final[1])
    triangles.append(final)
    return tuple(sorted(
        triangles,
        key=lambda triangle: tuple(sorted(_point_key(point) for point in triangle)),
    ))


def _line_intersection(
    start: PlanPoint,
    end: PlanPoint,
    clip_start: PlanPoint,
    clip_end: PlanPoint,
) -> PlanPoint:
    dx, dy = end.x - start.x, end.y - start.y
    ex, ey = clip_end.x - clip_start.x, clip_end.y - clip_start.y
    denominator = dx * ey - dy * ex
    if abs(denominator) <= _GEOMETRY_TOLERANCE:
        return end
    qx, qy = clip_start.x - start.x, clip_start.y - start.y
    fraction = (qx * ey - qy * ex) / denominator
    return PlanPoint(start.x + fraction * dx, start.y + fraction * dy)


def _deduplicate_polygon(points: list[PlanPoint]) -> tuple[PlanPoint, ...]:
    result: list[PlanPoint] = []
    for point in points:
        if not result or hypot(
            point.x - result[-1].x, point.y - result[-1].y
        ) > _GEOMETRY_TOLERANCE:
            result.append(point)
    if len(result) > 1 and hypot(
        result[0].x - result[-1].x, result[0].y - result[-1].y
    ) <= _GEOMETRY_TOLERANCE:
        result.pop()
    return tuple(result)


def _clip_polygon_to_triangle(
    subject: tuple[PlanPoint, ...],
    clip_triangle: tuple[PlanPoint, PlanPoint, PlanPoint],
) -> tuple[PlanPoint, ...]:
    output = subject
    for clip_start, clip_end in zip(
        clip_triangle, clip_triangle[1:] + clip_triangle[:1]
    ):
        if not output:
            break
        input_points = output
        clipped: list[PlanPoint] = []
        previous = input_points[-1]
        previous_inside = (
            _cross(clip_start, clip_end, previous) >= -_GEOMETRY_TOLERANCE
        )
        for current in input_points:
            current_inside = (
                _cross(clip_start, clip_end, current) >= -_GEOMETRY_TOLERANCE
            )
            if current_inside:
                if not previous_inside:
                    clipped.append(_line_intersection(
                        previous, current, clip_start, clip_end
                    ))
                clipped.append(current)
            elif previous_inside:
                clipped.append(_line_intersection(
                    previous, current, clip_start, clip_end
                ))
            previous = current
            previous_inside = current_inside
        output = _deduplicate_polygon(clipped)
    return output


def _aabb(points: tuple[PlanPoint, ...]) -> tuple[float, float, float, float]:
    return (
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )


def _aabb_intersects(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def _polygon_centroid(points: tuple[PlanPoint, ...]) -> PlanPoint:
    origin = points[0]
    local_points = tuple(
        PlanPoint(point.x - origin.x, point.y - origin.y)
        for point in points
    )
    cross_values = tuple(
        first.x * second.y - second.x * first.y
        for first, second in zip(
            local_points, local_points[1:] + local_points[:1]
        )
    )
    denominator = 3.0 * fsum(cross_values)
    if abs(denominator) <= _GEOMETRY_TOLERANCE:
        return PlanPoint(
            fsum(point.x for point in points) / len(points),
            fsum(point.y for point in points) / len(points),
        )
    return PlanPoint(
        origin.x + fsum(
            (first.x + second.x) * cross
            for first, second, cross in zip(
                local_points,
                local_points[1:] + local_points[:1],
                cross_values,
            )
        ) / denominator,
        origin.y + fsum(
            (first.y + second.y) * cross
            for first, second, cross in zip(
                local_points,
                local_points[1:] + local_points[:1],
                cross_values,
            )
        ) / denominator,
    )


def _assessment_fragments(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    assessment: PlanPolygon,
) -> tuple[AssessmentFaceFragment, ...]:
    ears = _triangulate_assessment(assessment)
    ear_aabbs = tuple(_aabb(ear) for ear in ears)
    assessment_aabb = _aabb(_canonical_polygon_vertices(assessment))
    component_by_triangle = {
        triangle_index: component.canonical_index
        for component in topology.face_components
        for triangle_index in component.triangle_indices
    }
    evidence_by_triangle = {
        evidence.triangle_index: evidence
        for evidence in topology.face_direction_samples
    }
    fragments: list[AssessmentFaceFragment] = []
    for triangle_index in sorted(
        component_by_triangle,
        key=lambda index: evidence_by_triangle[index].triangle_key,
    ):
        evidence = evidence_by_triangle[triangle_index]
        if evidence.downwall_xy is None:
            continue
        triangle = surface.triangles[triangle_index]
        plan_triangle = _canonical_plan_polygon(tuple(
            PlanPoint(surface.vertices[index].x, surface.vertices[index].y)
            for index in triangle.vertex_indices
        ))
        plan_area = _polygon_area(plan_triangle)
        triangle_aabb = _aabb(plan_triangle)
        if plan_area <= _GEOMETRY_TOLERANCE or not _aabb_intersects(
            triangle_aabb, assessment_aabb
        ):
            continue
        area_tolerance = max(
            1.0,
            (triangle_aabb[2] - triangle_aabb[0])
            * (triangle_aabb[3] - triangle_aabb[1]),
        ) * _AREA_RELATIVE_TOLERANCE
        for ear_index, (ear, ear_aabb) in enumerate(zip(ears, ear_aabbs)):
            if not _aabb_intersects(triangle_aabb, ear_aabb):
                continue
            clipped = _clip_polygon_to_triangle(plan_triangle, ear)
            if len(clipped) >= 3:
                clipped = _canonical_plan_polygon(clipped)
            overlap_area = _polygon_area(clipped) if len(clipped) >= 3 else 0.0
            if overlap_area <= area_tolerance:
                continue
            fragments.append(AssessmentFaceFragment(
                "",
                triangle_index,
                evidence.triangle_key,
                component_by_triangle[triangle_index],
                clipped,
                overlap_area,
                _polygon_centroid(clipped),
                evidence.downwall_xy,
                evidence.geometric_weight * overlap_area / plan_area,
                evidence.source_id or f"triangle:{evidence.triangle_key}:ear:{ear_index}",
            ))
    fragments.sort(key=lambda fragment: (
        fragment.triangle_key,
        tuple(_point_key(point) for point in fragment.points),
        fragment.source_id,
    ))
    return tuple(
        AssessmentFaceFragment(
            f"fragment:{index}",
            fragment.triangle_index,
            fragment.triangle_key,
            fragment.face_component_index,
            fragment.points,
            fragment.overlap_area,
            fragment.representative_point,
            fragment.downwall_xy,
            fragment.geometric_weight,
            fragment.source_id,
        )
        for index, fragment in enumerate(fragments)
    )


def _surface_face_adjacency(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
) -> dict[int, set[int]]:
    by_edge: dict[tuple[int, int], list[int]] = defaultdict(list)
    for triangle_index, role in enumerate(topology.triangle_roles):
        if role != "face":
            continue
        indices = surface.triangles[triangle_index].vertex_indices
        for first, second in (
            (indices[0], indices[1]),
            (indices[1], indices[2]),
            (indices[2], indices[0]),
        ):
            by_edge[tuple(sorted((first, second)))].append(triangle_index)
    adjacency: dict[int, set[int]] = defaultdict(set)
    for triangle_indices in by_edge.values():
        if len(triangle_indices) == 2:
            first, second = triangle_indices
            adjacency[first].add(second)
            adjacency[second].add(first)
    return adjacency


def _segments_share_positive_length(
    first_start: PlanPoint,
    first_end: PlanPoint,
    second_start: PlanPoint,
    second_end: PlanPoint,
) -> bool:
    """Return whether two collinear plan segments overlap by positive length."""
    first_vector = (
        first_end.x - first_start.x,
        first_end.y - first_start.y,
    )
    first_length = hypot(*first_vector)
    second_length = hypot(
        second_end.x - second_start.x,
        second_end.y - second_start.y,
    )
    if (
        first_length <= _GEOMETRY_TOLERANCE
        or second_length <= _GEOMETRY_TOLERANCE
    ):
        return False
    scale = max(1.0, first_length, second_length)
    if (
        abs(_cross(first_start, first_end, second_start))
        > _GEOMETRY_TOLERANCE * scale
        or abs(_cross(first_start, first_end, second_end))
        > _GEOMETRY_TOLERANCE * scale
    ):
        return False
    if abs(first_vector[0]) >= abs(first_vector[1]):
        first_interval = sorted((first_start.x, first_end.x))
        second_interval = sorted((second_start.x, second_end.x))
    else:
        first_interval = sorted((first_start.y, first_end.y))
        second_interval = sorted((second_start.y, second_end.y))
    return (
        min(first_interval[1], second_interval[1])
        - max(first_interval[0], second_interval[0])
        > _GEOMETRY_TOLERANCE
    )


def _fragments_share_local_face_region(
    first: AssessmentFaceFragment,
    second: AssessmentFaceFragment,
    face_adjacency: dict[int, set[int]],
) -> bool:
    if (
        first.triangle_index != second.triangle_index
        and second.triangle_index not in face_adjacency[first.triangle_index]
    ):
        return False
    first_edges = tuple(zip(first.points, first.points[1:] + first.points[:1]))
    second_edges = tuple(zip(second.points, second.points[1:] + second.points[:1]))
    return any(
        _segments_share_positive_length(a, b, c, d)
        for a, b in first_edges
        for c, d in second_edges
    )


def _assessment_seed_groups(
    fragments: tuple[AssessmentFaceFragment, ...],
    face_adjacency: dict[int, set[int]],
) -> tuple[_AssessmentSeedGroup, ...]:
    """Split Assessment evidence into exact-topology, locally touching groups.

    Phase 2B intentionally keeps a pairwise fragment comparison inside each
    FaceComponent.  A triangle/adjacency-indexed candidate lookup remains a
    pre-production performance improvement for very large clipped components.
    """
    by_component: dict[int, list[AssessmentFaceFragment]] = defaultdict(list)
    for fragment in fragments:
        by_component[fragment.face_component_index].append(fragment)
    groups: list[_AssessmentSeedGroup] = []
    for component_index, component_fragments in sorted(by_component.items()):
        ordered = tuple(sorted(
            component_fragments,
            key=lambda fragment: (
                fragment.triangle_key,
                tuple(map(_point_key, fragment.points)),
                fragment.fragment_id,
            ),
        ))
        neighbours: dict[int, set[int]] = defaultdict(set)
        for first_index, first in enumerate(ordered):
            for second_index in range(first_index + 1, len(ordered)):
                second = ordered[second_index]
                if _fragments_share_local_face_region(
                    first, second, face_adjacency
                ):
                    neighbours[first_index].add(second_index)
                    neighbours[second_index].add(first_index)
        unused = set(range(len(ordered)))
        while unused:
            seed = min(unused)
            pending = [seed]
            member_indices: list[int] = []
            while pending:
                current = pending.pop()
                if current not in unused:
                    continue
                unused.remove(current)
                member_indices.append(current)
                pending.extend(sorted(neighbours[current], reverse=True))
            groups.append(_AssessmentSeedGroup(
                component_index,
                tuple(ordered[index] for index in sorted(member_indices)),
            ))
    return tuple(sorted(groups, key=lambda group: (
        group.face_component_index,
        tuple(fragment.fragment_id for fragment in group.fragments),
    )))


def _aggregate_direction(
    evidence: tuple[FaceDirectionEvidence, ...],
) -> tuple[float, float] | None:
    effective = tuple(item for item in evidence if item.downwall_xy is not None)
    x = fsum(item.downwall_xy[0] * item.geometric_weight for item in effective)
    y = fsum(item.downwall_xy[1] * item.geometric_weight for item in effective)
    return _unit((x, y))


def _locally_classify_portal_edges(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    portal: TransitionPortal,
    adjacency: dict[int, set[int]],
) -> tuple[tuple[PortalSide, ...], tuple[int | None, ...], bool]:
    evidence_by_triangle = {
        evidence.triangle_index: evidence
        for evidence in topology.face_direction_samples
    }
    result: list[PortalSide] = []
    resolved_indices: list[int | None] = []
    ambiguous_provenance = False
    for provenance in portal.edge_provenance:
        candidates = tuple(
            triangle_index
            for triangle_index in portal.adjacent_face_triangle_indices
            if evidence_by_triangle[triangle_index].triangle_key
            == provenance.face_triangle_key
            and set(provenance.edge_key).issubset({
                _vertex_key(surface.vertices[vertex_index])
                for vertex_index in surface.triangles[
                    triangle_index
                ].vertex_indices
            })
        )
        if len(candidates) != 1:
            result.append("ambiguous")
            resolved_indices.append(None)
            ambiguous_provenance = True
            continue
        triangle_index = candidates[0]
        resolved_indices.append(triangle_index)
        support_indices = {triangle_index, *adjacency[triangle_index]}
        direction = _aggregate_direction(tuple(
            evidence_by_triangle[index]
            for index in sorted(support_indices)
            if index in evidence_by_triangle
        ))
        if direction is None:
            result.append("ambiguous")
            continue
        first_key, second_key = provenance.edge_key
        first = PlanPoint(first_key[0], first_key[1])
        second = PlanPoint(second_key[0], second_key[1])
        edge_unit = _unit((second.x - first.x, second.y - first.y))
        triangle = surface.triangles[triangle_index]
        triangle_points = tuple(surface.vertices[index] for index in triangle.vertex_indices)
        third = next(
            point
            for point in triangle_points
            if _vertex_key(point) not in provenance.edge_key
        )
        midpoint = PlanPoint((first.x + second.x) / 2.0, (first.y + second.y) / 2.0)
        interior = _unit((third.x - midpoint.x, third.y - midpoint.y))
        if edge_unit is None or interior is None:
            result.append("ambiguous")
            continue
        edge_projection = abs(_dot(edge_unit, direction))
        interior_projection = _dot(interior, direction)
        margin = abs(interior_projection) - edge_projection
        if abs(margin) <= _GEOMETRY_TOLERANCE:
            result.append("ambiguous")
        elif margin < 0.0:
            result.append("lateral")
        else:
            result.append("upstream" if interior_projection > 0.0 else "downstream")
    return tuple(result), tuple(resolved_indices), ambiguous_provenance


def _canonical_open_points(points: tuple[PlanPoint, ...]) -> tuple[PlanPoint, ...]:
    reversed_points = tuple(reversed(points))
    return min(points, reversed_points, key=lambda value: tuple(map(_point_key, value)))


def _canonical_closed_points(points: tuple[PlanPoint, ...]) -> tuple[PlanPoint, ...]:
    ring = points[:-1] if points[0] == points[-1] else points
    candidates: list[tuple[PlanPoint, ...]] = []
    for oriented in (ring, tuple(reversed(ring))):
        seam = min(range(len(oriented)), key=lambda index: _point_key(oriented[index]))
        rotated = oriented[seam:] + oriented[:seam]
        candidates.append(rotated + (rotated[0],))
    return min(candidates, key=lambda value: tuple(map(_point_key, value)))


def _portal_runs(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    portal: TransitionPortal,
    wanted_side: PortalSide,
    adjacency: dict[int, set[int]],
) -> tuple[_GuideRun, ...]:
    sides, resolved_indices, _ambiguous = _locally_classify_portal_edges(
        surface, topology, portal, adjacency
    )
    count = len(sides)
    if count == 0:
        return ()
    closed = portal.points[0] == portal.points[-1]
    wanted = tuple(side == wanted_side for side in sides)
    starts = [
        index
        for index, selected in enumerate(wanted)
        if selected and (
            (not closed and index == 0)
            or not wanted[(index - 1) % count]
        )
    ]
    if closed and all(wanted):
        starts = [0]
    runs: list[_GuideRun] = []
    for start in starts:
        indices: list[int] = []
        index = start
        while 0 <= index < count and wanted[index] and index not in indices:
            indices.append(index)
            index = (index + 1) % count if closed else index + 1
        points = [PlanPoint(portal.points[indices[0]].x, portal.points[indices[0]].y)]
        for edge_index in indices:
            endpoint = portal.points[edge_index + 1]
            points.append(PlanPoint(endpoint.x, endpoint.y))
        run_closed = closed and len(indices) == count
        ordered_points = (
            _canonical_closed_points(tuple(points))
            if run_closed
            else _canonical_open_points(tuple(points))
        )
        runs.append(_GuideRun(
            portal.portal_id,
            portal.face_component_index,
            wanted_side,
            portal.source_kind,
            ordered_points,
            tuple(sorted({
                resolved_indices[index]
                for index in indices
                if resolved_indices[index] is not None
            })),
            run_closed,
        ))
    return tuple(sorted(runs, key=lambda run: (
        tuple(map(_point_key, run.points)), run.portal_id
    )))


def _triangle_centroid(surface: TriangleSurface, triangle_index: int) -> PlanPoint:
    points = tuple(
        surface.vertices[index]
        for index in surface.triangles[triangle_index].vertex_indices
    )
    return PlanPoint(
        fsum(point.x for point in points) / 3.0,
        fsum(point.y for point in points) / 3.0,
    )


def _triangle_sort_key(
    surface: TriangleSurface, triangle_index: int
) -> tuple[object, ...]:
    triangle = surface.triangles[triangle_index]
    return (
        tuple(sorted(
            _vertex_key(surface.vertices[index])
            for index in triangle.vertex_indices
        )),
        triangle.source_id or "",
    )


def _geometric_triangle_paths(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    starts: set[int],
    adjacency: dict[int, set[int]],
) -> tuple[dict[int, float], dict[int, int]]:
    """Exact-adjacency Dijkstra with physical plan centroid cost."""
    centroids = {
        index: _triangle_centroid(surface, index)
        for index in adjacency.keys() | starts
    }
    distances = {index: 0.0 for index in starts}
    predecessors: dict[int, int] = {}
    queue: list[tuple[float, tuple[object, ...], int]] = []
    for index in starts:
        heappush(queue, (0.0, _triangle_sort_key(surface, index), index))
    while queue:
        distance, _key, current = heappop(queue)
        if distance > distances[current] + _GEOMETRY_TOLERANCE:
            continue
        for neighbour in sorted(
            adjacency[current], key=lambda item: _triangle_sort_key(surface, item)
        ):
            first, second = centroids[current], centroids[neighbour]
            candidate = distance + hypot(second.x - first.x, second.y - first.y)
            previous = distances.get(neighbour)
            if previous is None or candidate < previous - _GEOMETRY_TOLERANCE:
                distances[neighbour] = candidate
                predecessors[neighbour] = current
                heappush(queue, (
                    candidate, _triangle_sort_key(surface, neighbour), neighbour
                ))
            elif previous is not None and abs(candidate - previous) <= _GEOMETRY_TOLERANCE:
                old_predecessor = predecessors.get(neighbour)
                if old_predecessor is None or _triangle_sort_key(
                    surface, current
                ) < _triangle_sort_key(surface, old_predecessor):
                    predecessors[neighbour] = current
    return distances, predecessors


def _select_local_run(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    runs: tuple[_GuideRun, ...],
    anchor_triangles: set[int],
    adjacency: dict[int, set[int]],
) -> tuple[_GuideRun | None, bool]:
    if not runs:
        return None, False
    distances, _predecessors = _geometric_triangle_paths(
        surface, topology, anchor_triangles, adjacency
    )
    scored = tuple(
        (min((distances.get(index, 10**9) for index in run.face_triangle_indices)), run)
        for run in runs
    )
    best_distance = min(score for score, _ in scored)
    best = tuple(run for score, run in scored if score == best_distance)
    return (best[0], len(best) > 1)


def _guide_chainages(points: tuple[PlanPoint, ...]) -> tuple[float, ...]:
    values = [0.0]
    for first, second in zip(points, points[1:]):
        values.append(values[-1] + hypot(second.x - first.x, second.y - first.y))
    return tuple(values)


def _point_at_chainage(points: tuple[PlanPoint, ...], chainage: float) -> PlanPoint:
    values = _guide_chainages(points)
    target = max(0.0, min(values[-1], chainage))
    if target <= _GEOMETRY_TOLERANCE:
        return points[0]
    if target >= values[-1] - _GEOMETRY_TOLERANCE:
        return points[-1]
    for index, (start, end) in enumerate(zip(values, values[1:])):
        if target <= end + _GEOMETRY_TOLERANCE:
            fraction = 0.0 if end - start <= _GEOMETRY_TOLERANCE else (
                target - start
            ) / (end - start)
            first, second = points[index], points[index + 1]
            return PlanPoint(
                first.x + fraction * (second.x - first.x),
                first.y + fraction * (second.y - first.y),
            )
    return points[-1]


def _crop_points(
    points: tuple[PlanPoint, ...], start: float, end: float
) -> tuple[PlanPoint, ...] | None:
    if end - start <= _GEOMETRY_TOLERANCE:
        return None
    chainages = _guide_chainages(points)
    result = [_point_at_chainage(points, start)]
    result.extend(
        point
        for point, chainage in zip(points[1:-1], chainages[1:-1])
        if start + _GEOMETRY_TOLERANCE < chainage < end - _GEOMETRY_TOLERANCE
    )
    result.append(_point_at_chainage(points, end))
    deduplicated = tuple(
        point
        for index, point in enumerate(result)
        if index == 0 or hypot(
            point.x - result[index - 1].x,
            point.y - result[index - 1].y,
        ) > _GEOMETRY_TOLERANCE
    )
    return deduplicated if len(deduplicated) >= 2 else None


def _merge_intervals(
    intervals: tuple[tuple[float, float], ...]
) -> tuple[tuple[float, float], ...]:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if end - start <= _GEOMETRY_TOLERANCE:
            continue
        if not merged or start > merged[-1][1] + _GEOMETRY_TOLERANCE:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def _connection_is_structurally_plausible(connection: CorridorConnection) -> bool:
    """Dispersion is evidence quality, never an allowed wall-turn angle."""
    return (
        connection.status in {"compatible", "ambiguous"}
        and connection.order_compatible
        and connection.source_advance_m > _GEOMETRY_TOLERANCE
        and connection.target_advance_m > _GEOMETRY_TOLERANCE
    )


def _connection_face_component(
    connection: CorridorConnection,
    portal_by_id: dict[str, TransitionPortal],
    *,
    source: bool,
) -> int:
    portal_id = (
        connection.source_portal_id if source else connection.target_portal_id
    )
    return portal_by_id[portal_id].face_component_index


def _candidate_has_local_strike_overlap(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    portal: TransitionPortal,
    active_triangles: set[int],
    local_edge_runs: tuple[tuple[EdgeGeometryKey, ...], ...] = (),
) -> bool:
    """Prune only candidates outside the active Face-derived strike slab."""
    evidence_by_triangle = {
        evidence.triangle_index: evidence
        for evidence in topology.face_direction_samples
    }
    direction = _aggregate_direction(tuple(
        evidence_by_triangle[index]
        for index in sorted(active_triangles)
        if index in evidence_by_triangle
    ))
    if direction is None:
        return True
    strike = (-direction[1], direction[0])
    active_values = tuple(
        surface.vertices[vertex_index].x * strike[0]
        + surface.vertices[vertex_index].y * strike[1]
        for triangle_index in active_triangles
        for vertex_index in surface.triangles[triangle_index].vertex_indices
    )
    if not active_values:
        return False
    candidate_values = (
        tuple(
            vertex[0] * strike[0] + vertex[1] * strike[1]
            for edge_key in edge_keys
            for vertex in edge_key
        )
        for edge_keys in local_edge_runs
    ) if local_edge_runs else (tuple(
        point.x * strike[0] + point.y * strike[1]
        for point in portal.points
    ),)
    return any(
        values
        and min(max(active_values), max(values))
        - max(min(active_values), min(values))
        > _GEOMETRY_TOLERANCE
        for values in candidate_values
    )


def _ordered_local_connection_candidates(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    face_adjacency: dict[int, set[int]],
    portal_by_id: dict[str, TransitionPortal],
    component_index: int,
    active_triangles: set[int],
    *,
    downstream: bool,
    excluded_connection_ids: set[str],
) -> tuple[CorridorConnection, ...]:
    candidates = tuple(
        connection
        for connection in topology.corridor_connections
        if connection.connection_id not in excluded_connection_ids
        and _connection_is_structurally_plausible(connection)
        and _connection_face_component(
            connection, portal_by_id, source=downstream
        ) == component_index
    )
    if not candidates:
        return ()
    distances, _predecessors = _geometric_triangle_paths(
        surface, topology, active_triangles, face_adjacency
    )
    scored: list[tuple[float, CorridorConnection]] = []
    for connection in candidates:
        portal_id = (
            connection.source_portal_id
            if downstream
            else connection.target_portal_id
        )
        portal = portal_by_id[portal_id]
        local_edge_runs = tuple(
            (
                pair.source_edge_keys
                if downstream
                else pair.target_edge_keys
            )
            for pair in connection.local_run_pairs
        )
        if not _candidate_has_local_strike_overlap(
            surface,
            topology,
            portal,
            active_triangles,
            local_edge_runs,
        ):
            continue
        distance = min(
            (distances.get(index, float("inf"))
             for index in portal.adjacent_face_triangle_indices),
            default=float("inf"),
        )
        if distance < float("inf"):
            scored.append((distance, connection))
    return tuple(
        connection
        for _distance, connection in sorted(
            scored,
            key=lambda item: (item[0], item[1].connection_id),
        )
    )


def _walk_connection_hypotheses(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    face_adjacency: dict[int, set[int]],
    portal_by_id: dict[str, TransitionPortal],
    component_index: int,
    active_triangles: set[int],
    *,
    downstream: bool,
    used_connection_ids: frozenset[str],
    visited_components: frozenset[int],
) -> tuple[tuple[CorridorConnection, ...], ...]:
    candidates = _ordered_local_connection_candidates(
        surface, topology, face_adjacency, portal_by_id,
        component_index, active_triangles, downstream=downstream,
        excluded_connection_ids=set(used_connection_ids),
    )
    hypotheses: list[tuple[CorridorConnection, ...]] = []
    for connection in candidates:
        next_component = _connection_face_component(
            connection, portal_by_id, source=not downstream
        )
        if next_component in visited_components:
            continue
        next_portal_id = (
            connection.target_portal_id
            if downstream else connection.source_portal_id
        )
        tails = _walk_connection_hypotheses(
            surface, topology, face_adjacency, portal_by_id,
            next_component,
            set(portal_by_id[next_portal_id].adjacent_face_triangle_indices),
            downstream=downstream,
            used_connection_ids=used_connection_ids | {connection.connection_id},
            visited_components=visited_components | {next_component},
        )
        hypotheses.extend((connection, *tail) for tail in tails)
    return tuple(hypotheses) if hypotheses else ((),)


def _discover_corridor_hypotheses(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    face_adjacency: dict[int, set[int]],
    portal_by_id: dict[str, TransitionPortal],
    seed_component_index: int,
    seed_triangles: set[int],
) -> tuple[_DiscoveredCorridor, ...]:
    upstream_hypotheses = _walk_connection_hypotheses(
        surface, topology, face_adjacency, portal_by_id,
        seed_component_index, set(seed_triangles), downstream=False,
        used_connection_ids=frozenset(),
        visited_components=frozenset({seed_component_index}),
    )
    corridors: list[_DiscoveredCorridor] = []
    for upstream in upstream_hypotheses:
        downstream_hypotheses = _walk_connection_hypotheses(
            surface, topology, face_adjacency, portal_by_id,
            seed_component_index, set(seed_triangles), downstream=True,
            used_connection_ids=frozenset(
                connection.connection_id for connection in upstream
            ),
            visited_components=frozenset({seed_component_index}),
        )
        for downstream in downstream_hypotheses:
            connections = tuple(reversed(upstream)) + downstream
            if connections:
                root = _connection_face_component(
                    connections[0], portal_by_id, source=True
                )
            else:
                root = seed_component_index
            components = [root]
            for connection in connections:
                target = _connection_face_component(
                    connection, portal_by_id, source=False
                )
                if target != components[-1]:
                    components.append(target)
            corridors.append(_DiscoveredCorridor(
                tuple(components), connections, seed_component_index,
                tuple(sorted(seed_triangles)), (),
            ))
    unique = {
        (
            corridor.component_indices,
            tuple(connection.connection_id for connection in corridor.connections),
        ): corridor
        for corridor in corridors
    }
    return tuple(unique[key] for key in sorted(unique))


def _component_vertex_graph(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    component_index: int,
) -> dict[int, dict[int, float]]:
    graph: dict[int, dict[int, float]] = defaultdict(dict)
    component = topology.face_components[component_index]
    for triangle_index in component.triangle_indices:
        indices = surface.triangles[triangle_index].vertex_indices
        for first, second in (
            (indices[0], indices[1]),
            (indices[1], indices[2]),
            (indices[2], indices[0]),
        ):
            a, b = surface.vertices[first], surface.vertices[second]
            cost = hypot(b.x - a.x, b.y - a.y)
            if cost <= _GEOMETRY_TOLERANCE:
                continue
            graph[first][second] = min(graph[first].get(second, cost), cost)
            graph[second][first] = min(graph[second].get(first, cost), cost)
    return graph


def _resolve_run_vertices(
    surface: TriangleSurface,
    run: _GuideRun,
) -> tuple[int, ...] | None:
    """Resolve prototype run points through portal-local plan-XY provenance.

    Explicit retained vertex indices would be cleaner before production where
    distinct vertices may share plan XY at different elevations.
    """
    local_vertices = {
        vertex_index
        for triangle_index in run.face_triangle_indices
        for vertex_index in surface.triangles[triangle_index].vertex_indices
    }
    resolved: list[int] = []
    for point in run.points:
        matches = tuple(
            index for index in local_vertices
            if abs(surface.vertices[index].x - point.x) <= _GEOMETRY_TOLERANCE
            and abs(surface.vertices[index].y - point.y) <= _GEOMETRY_TOLERANCE
        )
        if len(matches) != 1:
            return None
        resolved.append(matches[0])
    return tuple(resolved)


def _run_edge_keys(
    surface: TriangleSurface,
    run: _GuideRun,
) -> tuple[EdgeGeometryKey, ...]:
    vertices = _resolve_run_vertices(surface, run)
    if vertices is None:
        return ()
    return tuple(
        tuple(sorted((
            (
                surface.vertices[first].x,
                surface.vertices[first].y,
                surface.vertices[first].z,
            ),
            (
                surface.vertices[second].x,
                surface.vertices[second].y,
                surface.vertices[second].z,
            ),
        )))
        for first, second in zip(vertices, vertices[1:])
    )


def _run_subspan_for_edge_keys(
    surface: TriangleSurface,
    run: _GuideRun,
    edge_keys: frozenset[EdgeGeometryKey],
) -> _GuideRun | None:
    """Return the continuous parent-run envelope carrying exact edge keys."""
    run_edges = _run_edge_keys(surface, run)
    matched = tuple(
        index for index, edge_key in enumerate(run_edges)
        if edge_key in edge_keys
    )
    if not matched:
        return None
    first_edge = min(matched)
    last_edge = max(matched)
    points = run.points[first_edge:last_edge + 2]
    vertices = _resolve_run_vertices(surface, run)
    if vertices is None:
        return None
    active_triangles: set[int] = set()
    for edge_index in range(first_edge, last_edge + 1):
        edge_vertices = {vertices[edge_index], vertices[edge_index + 1]}
        active_triangles.update(
            triangle_index
            for triangle_index in run.face_triangle_indices
            if edge_vertices.issubset(
                set(surface.triangles[triangle_index].vertex_indices)
            )
        )
    if not active_triangles:
        return None
    return _GuideRun(
        run.portal_id,
        run.face_component_index,
        run.side,
        run.source_kind,
        points,
        tuple(sorted(active_triangles)),
        False,
    )


def _local_pair_order_compatible(
    surface: TriangleSurface,
    source_run: _GuideRun,
    target_run: _GuideRun,
    pairs: tuple[LocalPortalRunPair, ...],
) -> bool:
    """Require disjoint pair intervals to retain one portal-chain order."""
    source_edges = _run_edge_keys(surface, source_run)
    target_edges = _run_edge_keys(surface, target_run)
    records: list[tuple[int, int, int, int]] = []
    for pair in pairs:
        source_indices = tuple(
            index for index, edge_key in enumerate(source_edges)
            if edge_key in pair.source_edge_keys
        )
        target_indices = tuple(
            index for index, edge_key in enumerate(target_edges)
            if edge_key in pair.target_edge_keys
        )
        if not source_indices or not target_indices:
            return False
        records.append((
            min(source_indices), max(source_indices),
            min(target_indices), max(target_indices),
        ))
    records.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    if any(
        second[0] <= first[1]
        for first, second in zip(records, records[1:])
    ):
        return False
    target_increasing = all(
        second[2] > first[3]
        for first, second in zip(records, records[1:])
    )
    target_decreasing = all(
        second[3] < first[2]
        for first, second in zip(records, records[1:])
    )
    return len(records) == 1 or target_increasing or target_decreasing


def _source_portal_correspondence_chains(
    surface: TriangleSurface,
    connection: CorridorConnection,
    source_run: _GuideRun,
    target_runs: tuple[_GuideRun, ...],
    station_by_vertex: dict[int, float],
    station_interval: tuple[float, float],
    assessment: PlanPolygon,
) -> tuple[_LocalPortalRunChain, ...]:
    """Resolve provenance-local source/target chains before injectivity."""
    if not connection.local_run_pairs:
        return ()
    source_vertices = _resolve_run_vertices(surface, source_run)
    if source_vertices is None or any(
        vertex not in station_by_vertex for vertex in source_vertices
    ):
        return ()
    source_values = tuple(
        station_by_vertex[vertex] for vertex in source_vertices
    )
    monotone_candidates = _monotone_station_candidates(
        source_run.points, source_values, station_interval
    )
    chains: dict[
        tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]],
        _LocalPortalRunChain,
    ] = {}
    for candidate in monotone_candidates:
        monotone_run = _target_correspondence_run(
            source_run, candidate.points
        )
        monotone_edge_keys = frozenset(_run_edge_keys(surface, monotone_run))
        candidate_pairs = tuple(
            pair
            for pair in connection.local_run_pairs
            if set(pair.source_edge_keys).issubset(monotone_edge_keys)
        )
        if not candidate_pairs:
            continue
        assessment_local_pairs = tuple(
            pair
            for pair in candidate_pairs
            if (
                pair_run := _run_subspan_for_edge_keys(
                    surface,
                    monotone_run,
                    frozenset(pair.source_edge_keys),
                )
            ) is not None
            and clip_datamine_line_by_polygon(
                PlanLineString(pair_run.points),
                assessment,
                _GEOMETRY_TOLERANCE,
            )
        )
        candidate_pairs = assessment_local_pairs or candidate_pairs
        for target_run in target_runs:
            target_edge_keys = frozenset(_run_edge_keys(surface, target_run))
            local_pairs = tuple(
                pair for pair in candidate_pairs
                if set(pair.target_edge_keys).issubset(target_edge_keys)
            )
            if not local_pairs or not _local_pair_order_compatible(
                surface, monotone_run, target_run, local_pairs
            ):
                continue
            local_source = _run_subspan_for_edge_keys(
                surface,
                monotone_run,
                frozenset(
                    edge_key
                    for pair in local_pairs
                    for edge_key in pair.source_edge_keys
                ),
            )
            local_target = _run_subspan_for_edge_keys(
                surface,
                target_run,
                frozenset(
                    edge_key
                    for pair in local_pairs
                    for edge_key in pair.target_edge_keys
                ),
            )
            if (
                local_source is None
                or local_target is None
            ):
                continue
            values = _run_station_values(
                surface,
                local_source,
                station_by_vertex,
                station_interval,
            )
            if values is None or (
                min(values[1][-1], station_interval[1])
                - max(values[1][0], station_interval[0])
                <= _GEOMETRY_TOLERANCE
            ):
                continue
            chain = _LocalPortalRunChain(
                local_source, values, local_target
            )
            key = (
                tuple(map(_point_key, local_source.points)),
                tuple(map(_point_key, local_target.points)),
            )
            chains.setdefault(key, chain)
    resolved = tuple(chains[key] for key in sorted(chains))
    if len(resolved) <= 1:
        return resolved
    assessment_local = tuple(
        chain
        for chain in resolved
        if clip_datamine_line_by_polygon(
            PlanLineString(chain.source_run.points),
            assessment,
            _GEOMETRY_TOLERANCE,
        )
    )
    return assessment_local or resolved


def _provenance_compatible_target_runs(
    surface: TriangleSurface,
    connection: CorridorConnection,
    source_run: _GuideRun,
    source_fraction_start: float,
    source_fraction_end: float,
    target_runs: tuple[_GuideRun, ...],
) -> tuple[_GuideRun, ...]:
    """Keep only target runs paired with the active exact source edges."""
    if not connection.local_run_pairs:
        return target_runs

    source_edge_keys = _run_edge_keys(surface, source_run)
    source_chainages = _guide_chainages(source_run.points)
    if not source_edge_keys or source_chainages[-1] <= _GEOMETRY_TOLERANCE:
        return ()
    active_start = source_fraction_start * source_chainages[-1]
    active_end = source_fraction_end * source_chainages[-1]
    active_source_edge_keys = frozenset(
        edge_key
        for edge_key, edge_start, edge_end in zip(
            source_edge_keys,
            source_chainages,
            source_chainages[1:],
        )
        if min(edge_end, active_end) - max(edge_start, active_start)
        > _GEOMETRY_TOLERANCE
    )
    compatible_pairs = tuple(
        pair
        for pair in connection.local_run_pairs
        if active_source_edge_keys.intersection(pair.source_edge_keys)
    )
    if not compatible_pairs:
        return ()
    compatible_target_edge_keys = frozenset(
        edge_key
        for pair in compatible_pairs
        for edge_key in pair.target_edge_keys
    )
    return tuple(
        run
        for run in target_runs
        if compatible_target_edge_keys.intersection(_run_edge_keys(surface, run))
    )


def _vertex_distances_from_set(
    graph: dict[int, dict[int, float]], starts: set[int]
) -> dict[int, float]:
    distances = {start: 0.0 for start in starts}
    queue: list[tuple[float, int]] = []
    for start in sorted(starts):
        heappush(queue, (0.0, start))
    while queue:
        distance, current = heappop(queue)
        if distance > distances[current] + _GEOMETRY_TOLERANCE:
            continue
        for neighbour, cost in sorted(graph[current].items()):
            candidate = distance + cost
            if candidate < distances.get(neighbour, float("inf")) - _GEOMETRY_TOLERANCE:
                distances[neighbour] = candidate
                heappush(queue, (candidate, neighbour))
    return distances


def _lateral_anchor_vertices(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    component_index: int,
    incoming_run: _GuideRun,
    face_adjacency: dict[int, set[int]],
) -> tuple[set[int], set[int]]:
    incoming_vertices = _resolve_run_vertices(surface, incoming_run)
    if incoming_vertices is None:
        return set(), set()
    start_vertex, end_vertex = incoming_vertices[0], incoming_vertices[-1]
    start_anchors = {start_vertex}
    end_anchors = {end_vertex}
    lateral_runs = tuple(
        run
        for portal in topology.portals
        if portal.face_component_index == component_index
        for run in _portal_runs(
            surface, topology, portal, "lateral", face_adjacency
        )
    )
    for run in lateral_runs:
        vertices = _resolve_run_vertices(surface, run)
        if vertices is None:
            continue
        vertex_set = set(vertices)
        if start_vertex in vertex_set:
            start_anchors.update(vertex_set)
        if end_vertex in vertex_set:
            end_anchors.update(vertex_set)
    return start_anchors, end_anchors


def _transport_layer_station(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    component_index: int,
    incoming_run: _GuideRun,
    incoming_station_start: float,
    incoming_station_end: float,
    face_adjacency: dict[int, set[int]],
) -> tuple[dict[int, float] | None, str | None]:
    support_run = incoming_run
    support_start = incoming_station_start
    support_end = incoming_station_end
    if incoming_run.station_support_points:
        support_run = _GuideRun(
            incoming_run.portal_id,
            incoming_run.face_component_index,
            incoming_run.side,
            incoming_run.source_kind,
            incoming_run.station_support_points,
            incoming_run.station_support_triangle_indices,
            incoming_run.closed,
        )
        support_start = (
            incoming_run.station_support_start
            if incoming_run.station_support_start is not None
            else incoming_station_start
        )
        support_end = (
            incoming_run.station_support_end
            if incoming_run.station_support_end is not None
            else incoming_station_end
        )
    vertices = _resolve_run_vertices(surface, support_run)
    if vertices is None:
        return None, "ambiguous_portal_triangle_provenance"
    graph = _component_vertex_graph(surface, topology, component_index)
    if incoming_run.closed:
        # Closed sectors are retained for provenance but Phase 1 periodic
        # semantics remain deferred.  Multi-source propagation follows exact
        # mesh connectivity and never performs a spatial nearest-guide jump.
        chainages = _guide_chainages(support_run.points)
        total = chainages[-1]
        anchors = {
            vertex: support_start
            + (support_end - support_start)
            * chainage / total
            for vertex, chainage in zip(vertices, chainages)
        }
        values: dict[int, float] = {}
        best_costs: dict[int, float] = {}
        queue: list[tuple[float, float, int]] = []
        for vertex, station in anchors.items():
            heappush(queue, (0.0, station, vertex))
        while queue:
            distance, station, current = heappop(queue)
            if distance > best_costs.get(current, float("inf")) + _GEOMETRY_TOLERANCE:
                continue
            if current not in best_costs:
                best_costs[current] = distance
                values[current] = station
            for neighbour, cost in graph[current].items():
                candidate = distance + cost
                if candidate < best_costs.get(neighbour, float("inf")) - _GEOMETRY_TOLERANCE:
                    best_costs[neighbour] = candidate
                    values[neighbour] = station
                    heappush(queue, (candidate, station, neighbour))
        return values, None
    start_anchors, end_anchors = _lateral_anchor_vertices(
        surface, topology, component_index, support_run, face_adjacency
    )
    start_distances = _vertex_distances_from_set(graph, start_anchors)
    end_distances = _vertex_distances_from_set(graph, end_anchors)
    if not start_distances or not end_distances:
        return None, "non_injective_station_mapping"
    values = {}
    for vertex in graph:
        if vertex not in start_distances or vertex not in end_distances:
            continue
        total = start_distances[vertex] + end_distances[vertex]
        if total <= _GEOMETRY_TOLERANCE:
            fraction = 0.5
        else:
            fraction = start_distances[vertex] / total
        values[vertex] = support_start + (
            support_end - support_start
        ) * fraction
    support_chainages = _guide_chainages(support_run.points)
    for vertex, chainage in zip(vertices, support_chainages):
        values[vertex] = support_start + (
            support_end - support_start
        ) * chainage / support_chainages[-1]
    return values, None


def _station_at_point_in_triangle(
    surface: TriangleSurface,
    triangle_index: int,
    station_by_vertex: dict[int, float],
    point: PlanPoint,
) -> float | None:
    indices = surface.triangles[triangle_index].vertex_indices
    a, b, c = (surface.vertices[index] for index in indices)
    denominator = (b.y - c.y) * (a.x - c.x) + (
        c.x - b.x
    ) * (a.y - c.y)
    if abs(denominator) <= _GEOMETRY_TOLERANCE:
        return None
    first = ((b.y - c.y) * (point.x - c.x) + (
        c.x - b.x
    ) * (point.y - c.y)) / denominator
    second = ((c.y - a.y) * (point.x - c.x) + (
        a.x - c.x
    ) * (point.y - c.y)) / denominator
    third = 1.0 - first - second
    try:
        return (
            first * station_by_vertex[indices[0]]
            + second * station_by_vertex[indices[1]]
            + third * station_by_vertex[indices[2]]
        )
    except KeyError:
        return None


def _run_station_values(
    surface: TriangleSurface,
    run: _GuideRun,
    station_by_vertex: dict[int, float],
    station_interval: tuple[float, float] | None = None,
) -> tuple[tuple[PlanPoint, ...], tuple[float, ...]] | None:
    vertices = _resolve_run_vertices(surface, run)
    if vertices is None or any(vertex not in station_by_vertex for vertex in vertices):
        return None
    points = run.points
    values = tuple(station_by_vertex[vertex] for vertex in vertices)
    candidates = _monotone_station_candidates(
        points, values, station_interval
    )
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    return candidate.points, candidate.stations


def _monotone_station_candidates(
    points: tuple[PlanPoint, ...],
    values: tuple[float, ...],
    station_interval: tuple[float, float] | None,
) -> tuple[_RunStationCandidate, ...]:
    """Return every distinct maximal monotone subspan relevant to a station slab."""
    if len(points) != len(values) or len(points) < 2:
        return ()
    segments: list[tuple[int, int, int]] = []
    start = 0
    direction = 0
    for edge_index, (first, second) in enumerate(zip(values, values[1:])):
        delta = second - first
        edge_direction = (
            1 if delta > _GEOMETRY_TOLERANCE
            else -1 if delta < -_GEOMETRY_TOLERANCE
            else 0
        )
        if edge_direction == 0:
            continue
        if direction == 0:
            direction = edge_direction
            continue
        if edge_direction != direction:
            segments.append((start, edge_index + 1, direction))
            start = edge_index
            direction = edge_direction
    segments.append((start, len(points), direction or 1))

    canonical: dict[tuple[tuple[float, float], ...], _RunStationCandidate] = {}
    for start, end, direction in segments:
        local_points = points[start:end]
        local_values = values[start:end]
        if len(local_points) < 2:
            continue
        if direction < 0:
            local_points = tuple(reversed(local_points))
            local_values = tuple(reversed(local_values))
        if any(
            second < first - _GEOMETRY_TOLERANCE
            for first, second in zip(local_values, local_values[1:])
        ):
            continue
        relevant_start = local_values[0]
        relevant_end = local_values[-1]
        if station_interval is not None:
            relevant_start = max(relevant_start, station_interval[0])
            relevant_end = min(relevant_end, station_interval[1])
        if relevant_end - relevant_start <= _GEOMETRY_TOLERANCE:
            continue
        cropped_points = _crop_points_by_station(
            local_points, local_values, relevant_start, relevant_end,
        )
        if cropped_points is None:
            continue
        canonical_points = _canonical_open_points(cropped_points)
        key = tuple(map(_point_key, canonical_points))
        canonical.setdefault(key, _RunStationCandidate(
            local_points, local_values, canonical_points
        ))
    return tuple(canonical[key] for key in sorted(canonical))


def _select_terminal_run(
    surface: TriangleSurface,
    runs: tuple[_GuideRun, ...],
    station_by_vertex: dict[int, float],
    station_interval: tuple[float, float],
    active_triangles: set[int],
) -> _TerminalRunSelection:
    """Resolve terminal ownership only when one local physical subspan survives."""
    candidates: dict[
        tuple[tuple[float, float], ...],
        list[tuple[_GuideRun, _RunStationCandidate]],
    ] = defaultdict(list)
    folded_local_run = False
    unresolved_local_run = False
    for run in runs:
        if active_triangles and not (
            set(run.face_triangle_indices) & active_triangles
        ):
            continue
        vertices = _resolve_run_vertices(surface, run)
        if vertices is None or any(
            vertex not in station_by_vertex for vertex in vertices
        ):
            unresolved_local_run = True
            continue
        values = tuple(station_by_vertex[vertex] for vertex in vertices)
        local_candidates = _monotone_station_candidates(
            run.points, values, station_interval
        )
        if len(local_candidates) > 1:
            folded_local_run = True
            continue
        if not local_candidates:
            continue
        candidate = local_candidates[0]
        key = tuple(map(_point_key, candidate.canonical_local_points))
        candidates[key].append((run, candidate))

    if folded_local_run or len(candidates) > 1:
        return _TerminalRunSelection(None, None, True, folded_local_run)
    if not candidates:
        return _TerminalRunSelection(
            None, None, False, unresolved_local_run
        )
    representations = next(iter(candidates.values()))
    run, candidate = min(
        representations,
        key=lambda item: (
            tuple(map(_point_key, item[1].canonical_local_points)),
            item[0].portal_id,
        ),
    )
    return _TerminalRunSelection(
        run,
        (candidate.points, candidate.stations),
        False,
        False,
    )


def _orient_target_run(
    source_points: tuple[PlanPoint, ...], target: _GuideRun
) -> tuple[tuple[PlanPoint, ...], bool]:
    direct = (
        hypot(source_points[0].x - target.points[0].x,
              source_points[0].y - target.points[0].y)
        + hypot(source_points[-1].x - target.points[-1].x,
                source_points[-1].y - target.points[-1].y)
    )
    reverse = (
        hypot(source_points[0].x - target.points[-1].x,
              source_points[0].y - target.points[-1].y)
        + hypot(source_points[-1].x - target.points[0].x,
                source_points[-1].y - target.points[0].y)
    )
    if abs(direct - reverse) <= _GEOMETRY_TOLERANCE:
        return target.points, False
    return (target.points, False) if direct < reverse else (
        tuple(reversed(target.points)), True
    )


def _target_correspondence_run(
    target: _GuideRun,
    oriented_points: tuple[PlanPoint, ...],
) -> _GuideRun:
    return _GuideRun(
        target.portal_id, target.face_component_index, target.side,
        target.source_kind, oriented_points, target.face_triangle_indices,
        target.closed,
    )


def _run_chainage_fraction_at_station(
    points: tuple[PlanPoint, ...],
    stations: tuple[float, ...],
    station: float,
) -> float | None:
    if len(points) != len(stations):
        return None
    chainages = _guide_chainages(points)
    total = chainages[-1]
    if total <= _GEOMETRY_TOLERANCE:
        return None
    if station <= stations[0] + _GEOMETRY_TOLERANCE:
        return 0.0
    if station >= stations[-1] - _GEOMETRY_TOLERANCE:
        return 1.0
    for index, (first, second) in enumerate(zip(stations, stations[1:])):
        if first - _GEOMETRY_TOLERANCE <= station <= second + _GEOMETRY_TOLERANCE:
            if second - first <= _GEOMETRY_TOLERANCE:
                return chainages[index] / total
            local = (station - first) / (second - first)
            return (
                chainages[index]
                + local * (chainages[index + 1] - chainages[index])
            ) / total
    return None


def _crop_correspondence_run(
    surface: TriangleSurface,
    run: _GuideRun,
    start_fraction: float,
    end_fraction: float,
    support_station_start: float,
    support_station_end: float,
) -> _GuideRun | None:
    """Crop a portal run while retaining its full station-support provenance."""
    if end_fraction - start_fraction <= _GEOMETRY_TOLERANCE:
        return None
    chainages = _guide_chainages(run.points)
    total = chainages[-1]
    if total <= _GEOMETRY_TOLERANCE:
        return None
    cropped = _crop_points(
        run.points, start_fraction * total, end_fraction * total
    )
    vertices = _resolve_run_vertices(surface, run)
    if cropped is None or vertices is None:
        return None
    active_triangles: set[int] = set()
    for index, (start, end) in enumerate(zip(chainages, chainages[1:])):
        segment_start = start / total
        segment_end = end / total
        if (
            min(segment_end, end_fraction)
            - max(segment_start, start_fraction)
            <= _GEOMETRY_TOLERANCE
        ):
            continue
        edge_vertices = {vertices[index], vertices[index + 1]}
        active_triangles.update(
            triangle_index
            for triangle_index in run.face_triangle_indices
            if edge_vertices.issubset(
                set(surface.triangles[triangle_index].vertex_indices)
            )
        )
    if not active_triangles:
        return None
    return _GuideRun(
        run.portal_id,
        run.face_component_index,
        run.side,
        run.source_kind,
        cropped,
        tuple(sorted(active_triangles)),
        False,
        run.points,
        run.face_triangle_indices,
        support_station_start,
        support_station_end,
    )


def _crop_guide_by_station(
    points: tuple[PlanPoint, ...],
    stations: tuple[float, ...],
    start: float,
    end: float,
    kind: str,
) -> WallGuide | None:
    cropped = _crop_points_by_station(points, stations, start, end)
    return WallGuide(cropped, kind) if cropped is not None else None


def _chainage_at_station(
    chainages: tuple[float, ...],
    stations: tuple[float, ...],
    station: float,
) -> float:
    for index, (first, second) in enumerate(zip(stations, stations[1:])):
        if first - _GEOMETRY_TOLERANCE <= station <= second + _GEOMETRY_TOLERANCE:
            fraction = 0.0 if second - first <= _GEOMETRY_TOLERANCE else (
                station - first
            ) / (second - first)
            return chainages[index] + fraction * (
                chainages[index + 1] - chainages[index]
            )
    return chainages[0] if station <= stations[0] else chainages[-1]


def _station_at_chainage(
    chainages: tuple[float, ...],
    stations: tuple[float, ...],
    chainage: float,
) -> float:
    for index, (first, second) in enumerate(zip(chainages, chainages[1:])):
        if first - _GEOMETRY_TOLERANCE <= chainage <= second + _GEOMETRY_TOLERANCE:
            fraction = 0.0 if second - first <= _GEOMETRY_TOLERANCE else (
                chainage - first
            ) / (second - first)
            return stations[index] + fraction * (
                stations[index + 1] - stations[index]
            )
    return stations[0] if chainage <= chainages[0] else stations[-1]


def _crop_terminal_guide_by_station(
    points: tuple[PlanPoint, ...],
    stations: tuple[float, ...],
    start: float,
    end: float,
    kind: str,
) -> tuple[WallGuide, GuideStationMapping] | None:
    """Crop a terminal guide and retain its transported station mapping."""
    if end - start <= _GEOMETRY_TOLERANCE or any(
        second < first - _GEOMETRY_TOLERANCE
        for first, second in zip(stations, stations[1:])
    ):
        return None
    overlap_start = max(start, stations[0])
    overlap_end = min(end, stations[-1])
    if overlap_end - overlap_start <= _GEOMETRY_TOLERANCE:
        return None
    chainages = _guide_chainages(points)
    start_chainage = _chainage_at_station(
        chainages, stations, overlap_start
    )
    end_chainage = _chainage_at_station(chainages, stations, overlap_end)
    if end_chainage - start_chainage <= _GEOMETRY_TOLERANCE:
        return None

    nodes = [(_point_at_chainage(points, start_chainage), start_chainage)]
    nodes.extend(
        (point, chainage)
        for point, chainage in zip(points[1:-1], chainages[1:-1])
        if (
            start_chainage + _GEOMETRY_TOLERANCE
            < chainage
            < end_chainage - _GEOMETRY_TOLERANCE
        )
    )
    nodes.append((_point_at_chainage(points, end_chainage), end_chainage))
    deduplicated: list[tuple[PlanPoint, float]] = []
    for point, chainage in nodes:
        if not deduplicated or hypot(
            point.x - deduplicated[-1][0].x,
            point.y - deduplicated[-1][0].y,
        ) > _GEOMETRY_TOLERANCE:
            deduplicated.append((point, chainage))
    if len(deduplicated) < 2:
        return None

    guide = WallGuide(tuple(point for point, _ in deduplicated), kind)
    span = end - start
    normalized_stations = tuple(
        max(0.0, min(1.0, (
            _station_at_chainage(chainages, stations, chainage) - start
        ) / span))
        for _, chainage in deduplicated
    )
    normalized_stations = (
        (0.0 if abs(overlap_start - start) <= _GEOMETRY_TOLERANCE
         else normalized_stations[0]),
        *normalized_stations[1:-1],
        (1.0 if abs(overlap_end - end) <= _GEOMETRY_TOLERANCE
         else normalized_stations[-1]),
    )
    mapping = GuideStationMapping(
        guide.cumulative_chainages_m,
        normalized_stations,
    )
    return guide, mapping


def _ray_run_intersections(
    origin: PlanPoint,
    direction: tuple[float, float],
    points: tuple[PlanPoint, ...],
) -> tuple[tuple[tuple[float, PlanPoint], ...], bool]:
    """Return every forward ray hit on one ordered run without picking one."""
    chainages = _guide_chainages(points)
    hits: list[tuple[float, PlanPoint]] = []
    collinear = False
    for index, (first, second) in enumerate(zip(points, points[1:])):
        segment = (second.x - first.x, second.y - first.y)
        offset = (first.x - origin.x, first.y - origin.y)
        denominator = (
            direction[0] * segment[1] - direction[1] * segment[0]
        )
        if abs(denominator) <= _GEOMETRY_TOLERANCE:
            if abs(
                offset[0] * direction[1] - offset[1] * direction[0]
            ) <= _GEOMETRY_TOLERANCE:
                projections = tuple(
                    (point.x - origin.x) * direction[0]
                    + (point.y - origin.y) * direction[1]
                    for point in (first, second)
                )
                if max(projections) >= -_GEOMETRY_TOLERANCE:
                    collinear = True
            continue
        ray_distance = (
            offset[0] * segment[1] - offset[1] * segment[0]
        ) / denominator
        segment_fraction = (
            offset[0] * direction[1] - offset[1] * direction[0]
        ) / denominator
        if ray_distance < -_GEOMETRY_TOLERANCE or not (
            -_GEOMETRY_TOLERANCE
            <= segment_fraction
            <= 1.0 + _GEOMETRY_TOLERANCE
        ):
            continue
        segment_fraction = max(0.0, min(1.0, segment_fraction))
        chainage = chainages[index] + segment_fraction * (
            chainages[index + 1] - chainages[index]
        )
        point = PlanPoint(
            origin.x + max(0.0, ray_distance) * direction[0],
            origin.y + max(0.0, ray_distance) * direction[1],
        )
        if not any(
            abs(chainage - current_chainage) <= _GEOMETRY_TOLERANCE
            and hypot(
                point.x - current_point.x, point.y - current_point.y
            ) <= _GEOMETRY_TOLERANCE
            for current_chainage, current_point in hits
        ):
            hits.append((chainage, point))
    return tuple(sorted(hits, key=lambda item: item[0])), collinear


def _segments_properly_cross(
    first: tuple[PlanPoint, PlanPoint],
    second: tuple[PlanPoint, PlanPoint],
) -> bool:
    a, b = first
    c, d = second
    first_vector = (b.x - a.x, b.y - a.y)
    second_vector = (d.x - c.x, d.y - c.y)
    denominator = (
        first_vector[0] * second_vector[1]
        - first_vector[1] * second_vector[0]
    )
    if abs(denominator) <= _GEOMETRY_TOLERANCE:
        return False
    offset = (c.x - a.x, c.y - a.y)
    first_fraction = (
        offset[0] * second_vector[1] - offset[1] * second_vector[0]
    ) / denominator
    second_fraction = (
        offset[0] * first_vector[1] - offset[1] * first_vector[0]
    ) / denominator
    return (
        _GEOMETRY_TOLERANCE
        < first_fraction
        < 1.0 - _GEOMETRY_TOLERANCE
        and _GEOMETRY_TOLERANCE
        < second_fraction
        < 1.0 - _GEOMETRY_TOLERANCE
    )


def _face_ray_terminal_guide(
    upper: WallGuide,
    lower_points: tuple[PlanPoint, ...],
    samples: tuple[FaceDirectionSample, ...],
    assessment: PlanPolygon,
) -> _TerminalGuideCorrespondence:
    """Resolve one ordered Lower subspan using only local Design Face rays."""
    upper_chainages = upper.cumulative_chainages_m
    support_fractions = tuple(sorted({
        0.0,
        1.0,
        *(
            chainage / upper.length_m
            for chainage in upper_chainages[1:-1]
        ),
        *(sample.station_fraction for sample in samples),
    }))
    hits: list[tuple[float, PlanPoint, float]] = []
    for fraction in support_fractions:
        origin = upper.point_at(fraction * upper.length_m)
        try:
            direction = aggregate_face_direction(
                samples, fraction, assessment
            ).downwall_xy
        except ValueError:
            return _TerminalGuideCorrespondence(None, False)
        intersections, collinear = _ray_run_intersections(
            origin, direction, lower_points
        )
        if collinear or len(intersections) > 1:
            return _TerminalGuideCorrespondence(None, True)
        if not intersections:
            return _TerminalGuideCorrespondence(None, False)
        chainage, point = intersections[0]
        hits.append((chainage, point, fraction))

    deltas = tuple(
        second[0] - first[0] for first, second in zip(hits, hits[1:])
    )
    if not deltas or any(abs(delta) <= _GEOMETRY_TOLERANCE for delta in deltas):
        return _TerminalGuideCorrespondence(None, True)
    increasing = all(delta > _GEOMETRY_TOLERANCE for delta in deltas)
    decreasing = all(delta < -_GEOMETRY_TOLERANCE for delta in deltas)
    if not increasing and not decreasing:
        return _TerminalGuideCorrespondence(None, True)
    ray_segments = tuple(
        (
            upper.point_at(fraction * upper.length_m),
            point,
        )
        for _chainage, point, fraction in hits
    )
    if any(
        _segments_properly_cross(first, second)
        for index, first in enumerate(ray_segments)
        for second in ray_segments[index + 1:]
    ):
        return _TerminalGuideCorrespondence(None, True)

    oriented_points = lower_points
    oriented_hits = hits
    if decreasing:
        total = _guide_chainages(lower_points)[-1]
        oriented_points = tuple(reversed(lower_points))
        oriented_hits = [
            (total - chainage, point, fraction)
            for chainage, point, fraction in hits
        ]
    start_chainage = oriented_hits[0][0]
    end_chainage = oriented_hits[-1][0]
    parent_chainages = _guide_chainages(oriented_points)
    node_chainages = sorted({
        start_chainage,
        end_chainage,
        *(chainage for chainage in parent_chainages[1:-1]
          if start_chainage < chainage < end_chainage),
        *(chainage for chainage, _point, _fraction in oriented_hits[1:-1]),
    })
    if len(node_chainages) < 2:
        return _TerminalGuideCorrespondence(None, True)
    guide = WallGuide(
        tuple(_point_at_chainage(oriented_points, value) for value in node_chainages),
        "lower",
    )
    hit_chainages = tuple(item[0] for item in oriented_hits)
    hit_fractions = tuple(item[2] for item in oriented_hits)
    station_fractions = tuple(
        _station_at_chainage(hit_chainages, hit_fractions, chainage)
        for chainage in node_chainages
    )
    return _TerminalGuideCorrespondence(
        (guide, GuideStationMapping(
            guide.cumulative_chainages_m,
            station_fractions,
        )),
        False,
    )


def _crop_points_by_station(
    points: tuple[PlanPoint, ...],
    stations: tuple[float, ...],
    start: float,
    end: float,
) -> tuple[PlanPoint, ...] | None:
    if end - start <= _GEOMETRY_TOLERANCE:
        return None
    if any(
        second < first - _GEOMETRY_TOLERANCE
        for first, second in zip(stations, stations[1:])
    ):
        return None
    overlap_start = max(start, stations[0])
    overlap_end = min(end, stations[-1])
    if overlap_end - overlap_start <= _GEOMETRY_TOLERANCE:
        return None
    chainages = _guide_chainages(points)

    return _crop_points(
        points,
        _chainage_at_station(chainages, stations, overlap_start),
        _chainage_at_station(chainages, stations, overlap_end),
    )


def _correspondences_for_partition(
    correspondences: tuple[PortalSpanCorrespondence, ...],
    partition_start: float,
    partition_end: float,
) -> tuple[PortalSpanCorrespondence, ...]:
    span = partition_end - partition_start
    result: list[PortalSpanCorrespondence] = []
    for item in correspondences:
        overlap_start = max(partition_start, item.source_station_start)
        overlap_end = min(partition_end, item.source_station_end)
        if overlap_end - overlap_start <= _GEOMETRY_TOLERANCE:
            continue
        item_span = item.source_station_end - item.source_station_start
        if item_span <= _GEOMETRY_TOLERANCE:
            continue
        start_local = (
            overlap_start - item.source_station_start
        ) / item_span
        end_local = (
            overlap_end - item.source_station_start
        ) / item_span

        def interpolate(first: float, second: float, fraction: float) -> float:
            return first + fraction * (second - first)

        result.append(PortalSpanCorrespondence(
            item.connection_id,
            item.source_portal_id,
            item.target_portal_id,
            (overlap_start - partition_start) / span,
            (overlap_end - partition_start) / span,
            interpolate(
                item.source_chainage_start_fraction,
                item.source_chainage_end_fraction,
                start_local,
            ),
            interpolate(
                item.source_chainage_start_fraction,
                item.source_chainage_end_fraction,
                end_local,
            ),
            interpolate(
                item.target_chainage_start_fraction,
                item.target_chainage_end_fraction,
                start_local,
            ),
            interpolate(
                item.target_chainage_start_fraction,
                item.target_chainage_end_fraction,
                end_local,
            ),
            item.target_reversed,
        ))
    return tuple(result)


def _has_local_direction_break(
    samples: tuple[FaceDirectionSample, ...]
) -> bool:
    grouped: dict[float, list[FaceDirectionSample]] = defaultdict(list)
    for sample in samples:
        grouped[round(sample.station_fraction, 10)].append(sample)
    directions: list[tuple[float, tuple[float, float]]] = []
    for station, group in sorted(grouped.items()):
        x = fsum(sample.downwall_xy[0] * sample.geometric_weight for sample in group)
        y = fsum(sample.downwall_xy[1] * sample.geometric_weight for sample in group)
        direction = _unit((x, y))
        if direction is not None:
            directions.append((station, direction))
    turns = tuple(
        _angle_degrees(first[1], second[1])
        for first, second in zip(directions, directions[1:])
    )
    if not turns:
        return False
    total = fsum(turns)
    maximum = max(turns)
    return (
        maximum >= _LOCAL_BREAK_MIN_TURN_DEGREES
        and total > _GEOMETRY_TOLERANCE
        and maximum / total >= _LOCAL_BREAK_MIN_CONCENTRATION
    )


def _active_triangle_region(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    adjacency: dict[int, set[int]],
    anchor_sets: tuple[set[int], ...],
) -> set[int]:
    nonempty = tuple(anchor for anchor in anchor_sets if anchor)
    if not nonempty:
        return set()
    active = set(nonempty[0])
    starts = set(nonempty[0])
    distances, predecessors = _geometric_triangle_paths(
        surface, topology, starts, adjacency
    )
    for targets in nonempty[1:]:
        reachable = tuple(index for index in targets if index in distances)
        if not reachable:
            continue
        target = min(
            reachable,
            key=lambda index: (distances[index], _triangle_sort_key(surface, index)),
        )
        current = target
        active.add(current)
        while current not in starts and current in predecessors:
            current = predecessors[current]
            active.add(current)
        starts.update(targets)
        distances, predecessors = _geometric_triangle_paths(
            surface, topology, starts, adjacency
        )
    return active


def _active_triangle_band(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    adjacency: dict[int, set[int]],
    component_index: int,
    station_map: dict[int, float],
    station_start: float,
    station_end: float,
    anchor_sets: tuple[set[int], ...],
) -> set[int]:
    """Flood the exact-topology Face band inside one transported station slab."""
    component_triangles = set(
        topology.face_components[component_index].triangle_indices
    )
    eligible: set[int] = set()
    for triangle_index in component_triangles:
        values = tuple(
            station_map[vertex_index]
            for vertex_index in surface.triangles[
                triangle_index
            ].vertex_indices
            if vertex_index in station_map
        )
        if len(values) != 3:
            continue
        if (
            min(max(values), station_end)
            - max(min(values), station_start)
            > _GEOMETRY_TOLERANCE
        ):
            eligible.add(triangle_index)
    seeds = {
        triangle_index
        for anchors in anchor_sets
        for triangle_index in anchors
        if triangle_index in eligible
    }
    if not seeds:
        skeleton = _active_triangle_region(
            surface, topology, adjacency, anchor_sets
        )
        return skeleton & eligible
    active: set[int] = set()
    pending = sorted(
        seeds,
        key=lambda index: _triangle_sort_key(surface, index),
        reverse=True,
    )
    while pending:
        current = pending.pop()
        if current in active:
            continue
        active.add(current)
        pending.extend(sorted(
            (
                neighbour
                for neighbour in adjacency[current]
                if neighbour in eligible and neighbour not in active
            ),
            key=lambda index: _triangle_sort_key(surface, index),
            reverse=True,
        ))
    return active


def _local_taint_on_active_region(
    topology: DesignTopologyIndex,
    states: tuple[CorridorSpanState, ...],
) -> bool:
    for state in states:
        component = topology.face_components[state.face_component_index]
        active_keys = set(state.active_triangle_keys)
        for edge in component.topology_issue_edge_keys:
            if any(set(edge).issubset(set(key)) for key in active_keys):
                return True
    return False


def _assembled_direction_break(
    topology: DesignTopologyIndex,
    states: tuple[CorridorSpanState, ...],
) -> bool:
    evidence_by_key: dict[TriangleGeometryKey, list[FaceDirectionEvidence]] = defaultdict(list)
    for evidence in topology.face_direction_samples:
        evidence_by_key[evidence.triangle_key].append(evidence)
    directions: list[tuple[float, float]] = []
    for state in states:
        evidence = tuple(
            item
            for key in state.active_triangle_keys
            for item in evidence_by_key[key]
            if item.downwall_xy is not None
        )
        direction = _aggregate_direction(evidence)
        if direction is not None:
            directions.append(direction)
    turns = tuple(
        _angle_degrees(first, second)
        for first, second in zip(directions, directions[1:])
    )
    if not turns:
        return False
    for index, turn in enumerate(turns):
        start = max(0, index - 2)
        end = min(len(turns), index + 3)
        local_rotation = fsum(turns[start:end])
        concentration = (
            0.0 if local_rotation <= _GEOMETRY_TOLERANCE
            else turn / local_rotation
        )
        if (
            turn >= _LOCAL_BREAK_MIN_TURN_DEGREES
            and concentration >= _LOCAL_BREAK_MIN_CONCENTRATION
        ):
            return True
    return False


def _active_station_direction_break(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    face_adjacency: dict[int, set[int]],
    active_regions: tuple[set[int], ...],
    station_maps: tuple[dict[int, float], ...],
) -> bool:
    """Audit direction rotation inside each active Face layer by corridor s."""
    evidence_by_triangle = {
        evidence.triangle_index: evidence
        for evidence in topology.face_direction_samples
    }
    for active_triangles, station_map in zip(
        active_regions, station_maps, strict=True
    ):
        samples: list[FaceDirectionSample] = []
        records: dict[int, tuple[float, tuple[float, float]]] = {}
        for triangle_index in sorted(
            active_triangles,
            key=lambda index: _triangle_sort_key(surface, index),
        ):
            evidence = evidence_by_triangle.get(triangle_index)
            if evidence is None or evidence.downwall_xy is None:
                continue
            station_values = tuple(
                station_map[vertex_index]
                for vertex_index in surface.triangles[
                    triangle_index
                ].vertex_indices
                if vertex_index in station_map
            )
            if len(station_values) != 3:
                continue
            station = fsum(station_values) / 3.0
            records[triangle_index] = (station, evidence.downwall_xy)
            samples.append(FaceDirectionSample(
                evidence.point,
                station,
                evidence.downwall_xy,
                evidence.geometric_weight,
                "face",
                evidence.source_id,
            ))
        for first_index in sorted(
            records, key=lambda index: _triangle_sort_key(surface, index)
        ):
            for second_index in sorted(
                face_adjacency[first_index] & active_triangles,
                key=lambda index: _triangle_sort_key(surface, index),
            ):
                if _triangle_sort_key(surface, second_index) <= _triangle_sort_key(
                    surface, first_index
                ):
                    continue
                first_station, first_direction = records[first_index]
                second_station, second_direction = records[second_index]
                if second_station < first_station:
                    first_index_local, second_index_local = (
                        second_index, first_index
                    )
                    first_station, second_station = second_station, first_station
                    first_direction, second_direction = (
                        second_direction, first_direction
                    )
                else:
                    first_index_local, second_index_local = (
                        first_index, second_index
                    )
                if second_station - first_station <= _GEOMETRY_TOLERANCE:
                    continue
                turn = _angle_degrees(first_direction, second_direction)
                if turn < _LOCAL_BREAK_MIN_TURN_DEGREES:
                    continue
                neighbouring_turns = [turn]
                predecessors = tuple(
                    neighbour
                    for neighbour in face_adjacency[first_index_local] & active_triangles
                    if neighbour in records
                    and records[neighbour][0]
                    < first_station - _GEOMETRY_TOLERANCE
                )
                if predecessors:
                    predecessor = max(
                        predecessors,
                        key=lambda index: (
                            records[index][0],
                            _triangle_sort_key(surface, index),
                        ),
                    )
                    neighbouring_turns.append(_angle_degrees(
                        records[predecessor][1], first_direction
                    ))
                successors = tuple(
                    neighbour
                    for neighbour in face_adjacency[second_index_local] & active_triangles
                    if neighbour in records
                    and records[neighbour][0]
                    > second_station + _GEOMETRY_TOLERANCE
                )
                if successors:
                    successor = min(
                        successors,
                        key=lambda index: (
                            records[index][0],
                            _triangle_sort_key(surface, index),
                        ),
                    )
                    neighbouring_turns.append(_angle_degrees(
                        second_direction, records[successor][1]
                    ))
                local_rotation = fsum(neighbouring_turns)
                if (
                    local_rotation > _GEOMETRY_TOLERANCE
                    and turn / local_rotation
                    >= _LOCAL_BREAK_MIN_CONCENTRATION
                ):
                    return True
        if _has_local_direction_break(tuple(samples)):
            return True
    return False


def _zero_width_downstream_termination(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    component_index: int,
) -> bool:
    component = topology.face_components[component_index]
    direction = _aggregate_direction(component.direction_samples)
    if direction is None:
        return False
    vertices = {
        vertex_index
        for triangle_index in component.triangle_indices
        for vertex_index in surface.triangles[triangle_index].vertex_indices
    }
    projections = {
        vertex_index: (
            surface.vertices[vertex_index].x * direction[0]
            + surface.vertices[vertex_index].y * direction[1]
        )
        for vertex_index in vertices
    }
    downstream = max(projections.values())
    return sum(
        abs(value - downstream) <= _GEOMETRY_TOLERANCE
        for value in projections.values()
    ) == 1


def _extract_wall_sectors_propagated(
    surface: TriangleSurface,
    topology: DesignTopologyIndex,
    assessment: PlanPolygon,
) -> WallSectorExtractionResult:
    fragments = _assessment_fragments(surface, topology, assessment)
    if not fragments:
        return WallSectorExtractionResult(
            (), (), WallSectorDiagnostics(("no_positive_face_overlap",), (
                "Assessment has no positive-area overlap with Design Face triangles",
            ))
        )
    face_adjacency = _surface_face_adjacency(surface, topology)
    portal_by_id = {portal.portal_id: portal for portal in topology.portals}
    discovered: dict[tuple[object, ...], _DiscoveredCorridor] = {}
    discovered_fragments: dict[
        tuple[object, ...], list[AssessmentFaceFragment]
    ] = defaultdict(list)
    seed_hypotheses: dict[int, set[tuple[object, ...]]] = defaultdict(set)
    for seed_group_index, seed_group in enumerate(
        _assessment_seed_groups(fragments, face_adjacency)
    ):
        seed_triangles = {
            fragment.triangle_index for fragment in seed_group.fragments
        }
        for corridor in _discover_corridor_hypotheses(
            surface, topology, face_adjacency, portal_by_id,
            seed_group.face_component_index, seed_triangles,
        ):
            signature = (
                corridor.component_indices,
                tuple(
                    connection.connection_id
                    for connection in corridor.connections
                ),
            )
            discovered.setdefault(signature, corridor)
            discovered_fragments[signature].extend(seed_group.fragments)
            seed_hypotheses[seed_group_index].add(signature)

    evidence_by_triangle = {
        evidence.triangle_index: evidence
        for evidence in topology.face_direction_samples
    }
    sectors: list[WallSector] = []
    sector_hypotheses: list[tuple[object, ...]] = []
    extraction_codes: set[str] = set()

    for signature, corridor in sorted(discovered.items()):
        components = corridor.component_indices
        connections = corridor.connections
        group_fragments = tuple(sorted(
            {
                fragment.fragment_id: fragment
                for fragment in discovered_fragments[signature]
            }.values(),
            key=lambda fragment: fragment.fragment_id,
        ))
        codes = set(corridor.discovery_codes)
        extraction_codes.update(corridor.discovery_codes)
        root = components[0]
        leaf = components[-1]

        root_anchor = (
            set(portal_by_id[connections[0].source_portal_id].adjacent_face_triangle_indices)
            if connections
            else {
                fragment.triangle_index for fragment in group_fragments
                if fragment.face_component_index == root
            }
        )
        root_portals = tuple(
            portal for portal in topology.portals
            if portal.face_component_index == root
        )
        upper_runs = tuple(
            run for portal in root_portals
            for run in _portal_runs(
                surface, topology, portal, "upstream", face_adjacency
            )
        )
        upper_run, upper_ambiguous = _select_local_run(
            surface, topology, upper_runs, root_anchor, face_adjacency
        )
        if upper_run is None:
            extraction_codes.add("missing_external_upper_guide")
            continue
        if upper_ambiguous:
            codes.add("ambiguous_external_upper_guide")
        upper_chainages = _guide_chainages(upper_run.points)
        upper_stations = tuple(
            chainage / upper_chainages[-1] for chainage in upper_chainages
        )

        incoming_run = upper_run
        incoming_start = 0.0
        incoming_end = 1.0
        states: list[CorridorSpanState] = []
        state_active_indices: list[set[int]] = []
        state_station_maps: list[dict[int, float]] = []
        correspondences: list[PortalSpanCorrespondence] = []
        fragment_station_data: list[
            tuple[AssessmentFaceFragment, float, float, float]
        ] = []
        leaf_station_map: dict[int, float] | None = None
        mapping_failed = False
        stopped_span_correspondence = False

        for layer_index, component_index in enumerate(components):
            station_map, issue = _transport_layer_station(
                surface, topology, component_index, incoming_run,
                incoming_start, incoming_end, face_adjacency,
            )
            if station_map is None:
                codes.add(issue or "non_injective_station_mapping")
                mapping_failed = True
                break
            layer_fragments = tuple(
                fragment for fragment in group_fragments
                if fragment.face_component_index == component_index
            )
            seed_triangles = {fragment.triangle_index for fragment in layer_fragments}
            layer_station_ranges: list[tuple[float, float]] = []
            incoming_triangles = set(incoming_run.face_triangle_indices)
            outgoing_triangles: set[int] = set()
            outgoing_portal_id = None
            next_run = None
            next_start = next_end = 0.0

            for fragment in layer_fragments:
                station = _station_at_point_in_triangle(
                    surface, fragment.triangle_index, station_map,
                    fragment.representative_point,
                )
                vertex_stations = tuple(
                    value
                    for point in fragment.points
                    for value in (
                        _station_at_point_in_triangle(
                            surface, fragment.triangle_index, station_map, point
                        ),
                    )
                    if value is not None
                )
                if station is None or not vertex_stations:
                    codes.add("non_injective_station_mapping")
                    mapping_failed = True
                    break
                fragment_station_data.append((
                    fragment, station, min(vertex_stations), max(vertex_stations)
                ))
                layer_station_ranges.append((
                    min(vertex_stations), max(vertex_stations)
                ))
            if mapping_failed:
                break

            active_start = (
                min(start for start, _end in layer_station_ranges)
                if layer_station_ranges else incoming_start
            )
            active_end = (
                max(end for _start, end in layer_station_ranges)
                if layer_station_ranges else incoming_end
            )
            band_start, band_end = active_start, active_end

            if layer_index < len(connections):
                connection = connections[layer_index]
                source_portal = portal_by_id[connection.source_portal_id]
                target_portal = portal_by_id[connection.target_portal_id]
                source_runs = _portal_runs(
                    surface, topology, source_portal, "downstream", face_adjacency
                )
                source_run, source_ambiguous = _select_local_run(
                    surface, topology, source_runs,
                    seed_triangles or incoming_triangles, face_adjacency,
                )
                if source_run is None:
                    codes.add("ambiguous_portal_triangle_provenance")
                    mapping_failed = True
                    break
                target_parent_runs = _portal_runs(
                    surface,
                    topology,
                    target_portal,
                    "upstream",
                    face_adjacency,
                )
                target_run = None
                target_ambiguous = False
                if connection.local_run_pairs:
                    source_active_start = max(active_start, incoming_start)
                    source_active_end = min(active_end, incoming_end)
                    local_chains = _source_portal_correspondence_chains(
                        surface,
                        connection,
                        source_run,
                        target_parent_runs,
                        station_map,
                        (source_active_start, source_active_end),
                        assessment,
                    )
                    if source_ambiguous or len(local_chains) > 1:
                        codes.add("ambiguous_portal_span_correspondence")
                        stopped_span_correspondence = True
                        mapping_failed = True
                        break
                    if not local_chains:
                        codes.add("missing_portal_span_correspondence")
                        stopped_span_correspondence = True
                        mapping_failed = True
                        break
                    local_chain = local_chains[0]
                    source_run = local_chain.source_run
                    source_values = local_chain.source_values
                    target_run = local_chain.target_run
                else:
                    source_values = _run_station_values(
                        surface,
                        source_run,
                        station_map,
                        (active_start, active_end),
                    )
                if source_values is None:
                    codes.add("non_injective_portal_station_mapping")
                    mapping_failed = True
                    break
                source_points, source_stations = source_values
                if source_stations[-1] - source_stations[0] <= _GEOMETRY_TOLERANCE:
                    codes.add("remote_portal_outside_active_span")
                    mapping_failed = True
                    break
                transport_start = max(source_stations[0], active_start)
                transport_end = min(source_stations[-1], active_end)
                if transport_end - transport_start <= _GEOMETRY_TOLERANCE:
                    codes.add("remote_portal_outside_active_span")
                    mapping_failed = True
                    break
                source_fraction_start = _run_chainage_fraction_at_station(
                    source_points, source_stations, transport_start
                )
                source_fraction_end = _run_chainage_fraction_at_station(
                    source_points, source_stations, transport_end
                )
                if (
                    source_fraction_start is None
                    or source_fraction_end is None
                    or source_fraction_end - source_fraction_start
                    <= _GEOMETRY_TOLERANCE
                ):
                    codes.add("non_injective_portal_station_mapping")
                    mapping_failed = True
                    break
                source_ordered_run = _target_correspondence_run(
                    source_run, source_points
                )
                local_source_run = _crop_correspondence_run(
                    surface,
                    source_ordered_run,
                    source_fraction_start,
                    source_fraction_end,
                    source_stations[0],
                    source_stations[-1],
                )
                if local_source_run is None:
                    codes.add("non_injective_portal_station_mapping")
                    mapping_failed = True
                    break
                if target_run is None:
                    target_runs = _provenance_compatible_target_runs(
                        surface,
                        connection,
                        source_ordered_run,
                        source_fraction_start,
                        source_fraction_end,
                        target_parent_runs,
                    )
                    if not target_runs:
                        codes.add("missing_portal_span_correspondence")
                        stopped_span_correspondence = True
                        mapping_failed = True
                        break
                    target_run, target_ambiguous = _select_local_run(
                        surface, topology, target_runs,
                        set(target_portal.adjacent_face_triangle_indices),
                        face_adjacency,
                    )
                if source_ambiguous or target_ambiguous:
                    codes.add("ambiguous_portal_span_correspondence")
                if target_run is None:
                    codes.add("ambiguous_portal_triangle_provenance")
                    mapping_failed = True
                    break
                target_points, target_reversed = _orient_target_run(
                    source_points, target_run
                )
                target_ordered_run = _target_correspondence_run(
                    target_run, target_points
                )
                station_per_fraction = (
                    (transport_end - transport_start)
                    / (source_fraction_end - source_fraction_start)
                )
                target_support_start = (
                    transport_start
                    - station_per_fraction * source_fraction_start
                )
                target_support_end = (
                    transport_start
                    + station_per_fraction * (1.0 - source_fraction_start)
                )
                next_run = _crop_correspondence_run(
                    surface,
                    target_ordered_run,
                    source_fraction_start,
                    source_fraction_end,
                    target_support_start,
                    target_support_end,
                )
                if next_run is None:
                    codes.add("non_injective_portal_station_mapping")
                    mapping_failed = True
                    break
                next_start, next_end = transport_start, transport_end
                band_start, band_end = transport_start, transport_end
                outgoing_triangles = set(local_source_run.face_triangle_indices)
                outgoing_portal_id = source_portal.portal_id
                correspondences.append(PortalSpanCorrespondence(
                    connection.connection_id,
                    source_portal.portal_id,
                    target_portal.portal_id,
                    next_start,
                    next_end,
                    source_fraction_start,
                    source_fraction_end,
                    source_fraction_start,
                    source_fraction_end,
                    target_reversed,
                ))

            active_triangles = _active_triangle_band(
                surface, topology, face_adjacency, component_index,
                station_map, band_start, band_end,
                (incoming_triangles, seed_triangles, outgoing_triangles),
            )
            active_keys = tuple(sorted(
                evidence_by_triangle[index].triangle_key
                for index in active_triangles
                if index in evidence_by_triangle
            ))
            active_values = tuple(
                station_map[index]
                for triangle_index in active_triangles
                for index in surface.triangles[triangle_index].vertex_indices
                if index in station_map
            )
            states.append(CorridorSpanState(
                component_index,
                incoming_run.portal_id if layer_index > 0 else None,
                outgoing_portal_id,
                min(active_values, default=incoming_start),
                max(active_values, default=incoming_end),
                active_keys,
            ))
            state_active_indices.append(active_triangles)
            state_station_maps.append(station_map)
            if next_run is not None:
                incoming_run = next_run
                incoming_start, incoming_end = next_start, next_end
            else:
                leaf_station_map = station_map

        if mapping_failed:
            if stopped_span_correspondence and fragment_station_data:
                absolute_intervals = _merge_intervals(tuple(
                    (start, end) for _fragment, _station, start, end
                    in fragment_station_data
                ))
                if absolute_intervals:
                    assessed_start = min(start for start, _end in absolute_intervals)
                    assessed_end = max(end for _start, end in absolute_intervals)
                    span = assessed_end - assessed_start
                    upper_guide = _crop_guide_by_station(
                        upper_run.points,
                        upper_stations,
                        assessed_start,
                        assessed_end,
                        "upper",
                    )
                    if upper_guide is not None and span > _GEOMETRY_TOLERANCE:
                        intervals = tuple(StationInterval(
                            max(0.0, (start - assessed_start) / span),
                            min(1.0, (end - assessed_start) / span),
                        ) for start, end in absolute_intervals)
                        samples = tuple(sorted((
                            FaceDirectionSample(
                                fragment.representative_point,
                                max(0.0, min(
                                    1.0,
                                    (station - assessed_start) / span,
                                )),
                                fragment.downwall_xy,
                                fragment.geometric_weight,
                                "face",
                                fragment.source_id,
                            )
                            for fragment, station, _start, _end
                            in fragment_station_data
                        ), key=lambda sample: (
                            sample.station_fraction,
                            sample.point.x,
                            sample.point.y,
                            sample.source_id,
                        )))
                        normalized_states = tuple(CorridorSpanState(
                            state.face_component_index,
                            state.incoming_portal_id,
                            state.outgoing_portal_id,
                            (state.station_start - assessed_start) / span,
                            (state.station_end - assessed_start) / span,
                            state.active_triangle_keys,
                        ) for state in states)
                        sectors.append(WallSector(
                            "",
                            upper_guide,
                            None,
                            None,
                            samples,
                            intervals,
                            False,
                            None,
                            False,
                            tuple(
                                topology.face_components[index].component_id
                                for index in components
                            ),
                            tuple(sorted({
                                upper_run.portal_id,
                                *(
                                    portal_id
                                    for connection in connections
                                    for portal_id in (
                                        connection.source_portal_id,
                                        connection.target_portal_id,
                                    )
                                ),
                            })),
                            tuple(
                                connection.connection_id
                                for connection in connections
                            ),
                            tuple(sorted(
                                fragment.fragment_id
                                for fragment, _station, _start, _end
                                in fragment_station_data
                            )),
                            normalized_states,
                            tuple(correspondences),
                            WallSectorDiagnostics(tuple(sorted(codes))),
                            upper_portal_id=upper_run.portal_id,
                        ))
                        sector_hypotheses.append(signature)
            extraction_codes.update(codes)
            continue
        if leaf_station_map is None or not fragment_station_data:
            extraction_codes.update(codes)
            continue

        absolute_intervals = _merge_intervals(tuple(
            (start, end) for _fragment, _station, start, end
            in fragment_station_data
        ))
        if upper_run.closed and set(
            topology.face_components[root].triangle_indices
        ).issubset({fragment.triangle_index for fragment in group_fragments}):
            absolute_intervals = ((0.0, 1.0),)
        if not absolute_intervals:
            continue
        assessed_start = min(start for start, _end in absolute_intervals)
        assessed_end = max(end for _start, end in absolute_intervals)
        if assessed_end - assessed_start <= _GEOMETRY_TOLERANCE:
            codes.add("non_injective_station_mapping")
            extraction_codes.update(codes)
            continue

        leaf_anchor = (
            set(incoming_run.face_triangle_indices)
            if connections
            else {
                fragment.triangle_index for fragment in group_fragments
                if fragment.face_component_index == leaf
            }
        )
        leaf_portals = tuple(
            portal for portal in topology.portals
            if portal.face_component_index == leaf
        )
        lower_runs = tuple(
            run for portal in leaf_portals
            if portal.source_kind == "face_platform"
            for run in _portal_runs(
                surface, topology, portal, "downstream", face_adjacency
            )
        )
        extent_runs = tuple(
            run for portal in leaf_portals
            if portal.source_kind != "face_platform"
            for run in _portal_runs(
                surface, topology, portal, "downstream", face_adjacency
            )
        )
        local_terminal_triangles = (
            state_active_indices[-1] if state_active_indices else leaf_anchor
        )
        lower_selection = _select_terminal_run(
            surface, lower_runs, leaf_station_map,
            (assessed_start, assessed_end), local_terminal_triangles,
        )
        extent_selection = _select_terminal_run(
            surface, extent_runs, leaf_station_map,
            (assessed_start, assessed_end), local_terminal_triangles,
        )
        lower_run = lower_selection.run
        lower_values = lower_selection.values
        extent_run = extent_selection.run
        extent_values = extent_selection.values
        if lower_selection.ambiguous:
            codes.add("ambiguous_external_lower_guide")
        if extent_selection.ambiguous:
            codes.add("ambiguous_downstream_extent")
        if lower_selection.non_injective:
            codes.add("non_injective_lower_correspondence")
        if extent_selection.non_injective:
            codes.add("non_injective_downstream_extent")
        lower_coverage = (
            (lower_values[1][0], lower_values[1][-1])
            if lower_values is not None else None
        )
        leaf_terminal_triangles = set()
        if lower_run is not None:
            leaf_terminal_triangles.update(lower_run.face_triangle_indices)
        if extent_run is not None:
            leaf_terminal_triangles.update(extent_run.face_triangle_indices)
        if states and leaf_terminal_triangles:
            leaf_seed_triangles = {
                fragment.triangle_index for fragment in group_fragments
                if fragment.face_component_index == leaf
            }
            expanded_leaf = _active_triangle_band(
                surface, topology, face_adjacency, leaf,
                leaf_station_map, assessed_start, assessed_end,
                (
                    set(incoming_run.face_triangle_indices),
                    leaf_seed_triangles,
                    leaf_terminal_triangles,
                ),
            )
            state_active_indices[-1].update(expanded_leaf)
            active_keys = tuple(sorted(
                evidence_by_triangle[index].triangle_key
                for index in state_active_indices[-1]
                if index in evidence_by_triangle
            ))
            previous = states[-1]
            states[-1] = CorridorSpanState(
                previous.face_component_index,
                previous.incoming_portal_id,
                previous.outgoing_portal_id,
                previous.station_start,
                previous.station_end,
                active_keys,
            )

        if lower_coverage is not None and (
            lower_coverage[0] > assessed_start + _GEOMETRY_TOLERANCE
            or lower_coverage[1] < assessed_end - _GEOMETRY_TOLERANCE
        ):
            codes.add("partial_lower_coverage_split_required")
            overlap = (
                max(assessed_start, lower_coverage[0]),
                min(assessed_end, lower_coverage[1]),
            )
            partitions: list[tuple[float, float, bool]] = []
            if overlap[0] > assessed_start + _GEOMETRY_TOLERANCE:
                partitions.append((assessed_start, overlap[0], False))
            if overlap[1] > overlap[0] + _GEOMETRY_TOLERANCE:
                partitions.append((overlap[0], overlap[1], True))
            if overlap[1] < assessed_end - _GEOMETRY_TOLERANCE:
                partitions.append((overlap[1], assessed_end, False))
        else:
            partitions = [(assessed_start, assessed_end, lower_values is not None)]

        closed = upper_run.closed and assessed_start <= _GEOMETRY_TOLERANCE and (
            assessed_end >= 1.0 - _GEOMETRY_TOLERANCE
        )
        if _assembled_direction_break(topology, tuple(states)):
            codes.add("abrupt_local_direction_break")
        if _active_station_direction_break(
            surface,
            topology,
            face_adjacency,
            tuple(state_active_indices),
            tuple(state_station_maps),
        ):
            codes.add("abrupt_local_direction_break")
        raw_direction_samples = tuple(
            FaceDirectionSample(
                fragment.representative_point,
                max(0.0, min(1.0, (station - assessed_start)
                              / (assessed_end - assessed_start))),
                fragment.downwall_xy,
                fragment.geometric_weight,
                "face",
                fragment.source_id,
            )
            for fragment, station, _start, _end in fragment_station_data
        )
        if _has_local_direction_break(raw_direction_samples):
            codes.add("abrupt_local_direction_break")
        if _local_taint_on_active_region(topology, tuple(states)):
            codes.add("local_non_manifold_topology")
        if (
            lower_values is None
            and extent_values is None
            and not lower_selection.ambiguous
            and not extent_selection.ambiguous
        ):
            codes.add("missing_downstream_extent")
            if _zero_width_downstream_termination(surface, topology, leaf):
                codes.add("zero_width_convergence")
        if closed:
            codes.add("phase1_periodic_spacing_deferred")

        critical = {
            "ambiguous_corridor_branch",
            "ambiguous_external_upper_guide",
            "ambiguous_external_lower_guide",
            "ambiguous_downstream_extent",
            "ambiguous_portal_span_correspondence",
            "missing_portal_span_correspondence",
            "ambiguous_portal_triangle_provenance",
            "non_injective_station_mapping",
            "non_injective_portal_station_mapping",
            "non_injective_lower_correspondence",
            "non_injective_downstream_extent",
            "ambiguous_face_ray_lower_correspondence",
            "missing_face_ray_lower_correspondence",
            "local_non_manifold_topology",
            "abrupt_local_direction_break",
            "missing_downstream_extent",
            "phase1_periodic_spacing_deferred",
        }

        for partition_start, partition_end, has_lower in partitions:
            partition_codes = set(codes)
            span = partition_end - partition_start
            upper_guide = _crop_guide_by_station(
                upper_run.points, upper_stations,
                partition_start, partition_end, "upper",
            )
            if upper_guide is None:
                continue
            intervals_abs = _merge_intervals(tuple(
                (max(start, partition_start), min(end, partition_end))
                for start, end in absolute_intervals
                if min(end, partition_end) - max(start, partition_start)
                > _GEOMETRY_TOLERANCE
            ))
            if not intervals_abs:
                continue
            intervals = tuple(StationInterval(
                max(0.0, (start - partition_start) / span),
                min(1.0, (end - partition_start) / span),
            ) for start, end in intervals_abs)
            samples = tuple(sorted((
                FaceDirectionSample(
                    fragment.representative_point,
                    max(0.0, min(1.0, (station - partition_start) / span)),
                    fragment.downwall_xy,
                    fragment.geometric_weight,
                    "face",
                    fragment.source_id,
                )
                for fragment, station, _start, _end in fragment_station_data
                if partition_start - _GEOMETRY_TOLERANCE
                <= station <= partition_end + _GEOMETRY_TOLERANCE
            ), key=lambda sample: (
                sample.station_fraction, sample.point.x, sample.point.y,
                sample.source_id,
            )))
            station_lower_result = (
                _crop_terminal_guide_by_station(
                    lower_values[0], lower_values[1],
                    partition_start, partition_end, "lower",
                )
                if has_lower and lower_values is not None else None
            )
            lower_correspondence = (
                _face_ray_terminal_guide(
                    upper_guide,
                    lower_values[0],
                    samples,
                    assessment,
                )
                if station_lower_result is not None
                and lower_values is not None else None
            )
            lower_result = (
                lower_correspondence.result
                if lower_correspondence is not None else None
            )
            if lower_correspondence is not None and lower_result is None:
                partition_codes.add(
                    "ambiguous_face_ray_lower_correspondence"
                    if lower_correspondence.ambiguous
                    else "missing_face_ray_lower_correspondence"
                )
            extent_result = (
                _crop_terminal_guide_by_station(
                    extent_values[0], extent_values[1],
                    partition_start, partition_end, "downstream_extent",
                )
                if not has_lower and extent_values is not None else None
            )
            lower_guide = lower_result[0] if lower_result is not None else None
            lower_mapping = lower_result[1] if lower_result is not None else None
            downstream_extent = (
                extent_result[0] if extent_result is not None else None
            )
            downstream_mapping = (
                extent_result[1] if extent_result is not None else None
            )
            supported = bool(samples) and not (critical & partition_codes) and (
                lower_guide is not None or downstream_extent is not None
            )
            normalized_states = tuple(CorridorSpanState(
                state.face_component_index,
                state.incoming_portal_id,
                state.outgoing_portal_id,
                (state.station_start - partition_start) / span,
                (state.station_end - partition_start) / span,
                state.active_triangle_keys,
            ) for state in states)
            sectors.append(WallSector(
                "", upper_guide, lower_guide, downstream_extent,
                samples, intervals, closed,
                upper_guide.points[0] if closed else None,
                supported,
                tuple(topology.face_components[index].component_id for index in components),
                tuple(sorted({
                    upper_run.portal_id,
                    *(run.portal_id for run in (lower_run, extent_run) if run is not None),
                })),
                tuple(connection.connection_id for connection in connections),
                tuple(sorted(
                    fragment.fragment_id
                    for fragment, station, _start, _end in fragment_station_data
                    if partition_start - _GEOMETRY_TOLERANCE
                    <= station <= partition_end + _GEOMETRY_TOLERANCE
                )),
                normalized_states,
                _correspondences_for_partition(
                    tuple(correspondences), partition_start, partition_end
                ),
                WallSectorDiagnostics(tuple(sorted(partition_codes))),
                lower_mapping,
                downstream_mapping,
                upper_run.portal_id,
                lower_run.portal_id if lower_guide is not None else None,
                extent_run.portal_id if downstream_extent is not None else None,
            ))
            sector_hypotheses.append(signature)
            extraction_codes.update(partition_codes)
        extraction_codes.update(codes)

    produced_hypotheses = set(sector_hypotheses)
    ambiguous_hypotheses: set[tuple[object, ...]] = set()
    selected_hypotheses: set[tuple[object, ...]] = set()
    for signatures in seed_hypotheses.values():
        survivors = signatures & produced_hypotheses
        if len(survivors) > 1:
            ambiguous_hypotheses.update(survivors)
            extraction_codes.add("ambiguous_corridor_branch")
        elif len(survivors) == 1:
            survivor = next(iter(survivors))
            corridor = discovered[survivor]
            was_branch_candidate = len(signatures) > 1 or any(
                connection.status == "ambiguous"
                for connection in corridor.connections
            )
            if not was_branch_candidate:
                for component_index in corridor.component_indices:
                    downstream_count = sum(
                        _connection_is_structurally_plausible(connection)
                        and _connection_face_component(
                            connection, portal_by_id, source=True
                        ) == component_index
                        for connection in topology.corridor_connections
                    )
                    upstream_count = sum(
                        _connection_is_structurally_plausible(connection)
                        and _connection_face_component(
                            connection, portal_by_id, source=False
                        ) == component_index
                        for connection in topology.corridor_connections
                    )
                    if downstream_count > 1 or upstream_count > 1:
                        was_branch_candidate = True
                        break
            if was_branch_candidate:
                selected_hypotheses.add(survivor)
                extraction_codes.add("assessment_local_branch_selected")
        elif not survivors and signatures:
            extraction_codes.add("no_local_span_continuation")

    resolved_sectors: list[WallSector] = []
    for sector, signature in zip(sectors, sector_hypotheses, strict=True):
        codes = set(sector.diagnostics.codes)
        supported = sector.supported
        if signature in ambiguous_hypotheses:
            codes.add("ambiguous_corridor_branch")
            supported = False
        elif signature in selected_hypotheses:
            codes.add("assessment_local_branch_selected")
        resolved_sectors.append(replace(
            sector,
            supported=supported,
            diagnostics=WallSectorDiagnostics(tuple(sorted(codes))),
        ))
    sectors = resolved_sectors

    sectors.sort(key=lambda sector: (
        tuple(map(_point_key, sector.upper_guide.points)),
        sector.connection_ids,
        sector.fragment_ids,
    ))
    finalized = tuple(
        WallSector(
            f"wall-sector:{index}", sector.upper_guide,
            sector.lower_guide, sector.downstream_extent,
            sector.face_direction_samples, sector.assessed_station_intervals,
            sector.closed_along_strike, sector.seam_point, sector.supported,
            sector.face_component_ids, sector.portal_ids,
            sector.connection_ids, sector.fragment_ids,
            sector.span_states, sector.portal_correspondences,
            sector.diagnostics,
            sector.lower_station_mapping,
            sector.downstream_station_mapping,
            sector.upper_portal_id,
            sector.lower_portal_id,
            sector.downstream_portal_id,
        )
        for index, sector in enumerate(sectors)
    )
    return WallSectorExtractionResult(
        finalized, fragments,
        WallSectorDiagnostics(tuple(sorted(extraction_codes))),
    )


def extract_wall_sectors(
    surface: TriangleSurface,
    topology_index: DesignTopologyIndex,
    assessment_polygon: PlanPolygon,
) -> WallSectorExtractionResult:
    """Extract local spans using corridor-owned station propagation."""
    if len(surface.triangles) != len(topology_index.triangle_roles):
        raise ValueError("Design surface and topology index do not correspond")
    return _extract_wall_sectors_propagated(
        surface, topology_index, assessment_polygon
    )


__all__ = [
    "AssessmentFaceFragment",
    "CorridorSpanState",
    "GuideStationMapping",
    "PortalSpanCorrespondence",
    "StationInterval",
    "WallSector",
    "WallSectorDiagnostics",
    "WallSectorExtractionResult",
    "extract_wall_sectors",
]
