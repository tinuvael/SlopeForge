"""Pure-domain semantic topology for Wall Conformance v2 Design surfaces.

Phase 2A stops at reusable TIN topology and local Face/Platform/Face
continuation candidates.  Components in this module are topology containers,
not assessed walls.  Assessment selection, guide ownership, along-strike
coordinates, and transverse trace placement belong to later phases.

Connectivity is exact: only triangles sharing the same vertex-index edge are
adjacent.  Coincident coordinates are diagnosed but never welded.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from heapq import heappop, heappush
from itertools import combinations
from math import acos, degrees, fsum, hypot, sqrt
from typing import Literal, TypeAlias

from domain.geometry.surfaces import TriangleSurface, SurfaceVertex
from domain.geometry.types import PlanPoint
from domain.wall_conformance.models import SurfaceRoleMapping


_GEOMETRY_TOLERANCE = 1e-9
_PLAN_DETERMINANT_TOLERANCE = 1e-12
_PLATFORM_ROLES = frozenset({"berm", "road"})

VertexKey: TypeAlias = tuple[float, float, float]
EdgeGeometryKey: TypeAlias = tuple[VertexKey, VertexKey]
TriangleGeometryKey: TypeAlias = tuple[VertexKey, VertexKey, VertexKey]
PortalSide: TypeAlias = Literal[
    "upstream", "downstream", "lateral", "ambiguous"
]
ConnectionStatus: TypeAlias = Literal["compatible", "ambiguous", "rejected"]


def _vertex_key(vertex: SurfaceVertex) -> VertexKey:
    return vertex.x, vertex.y, vertex.z


def _edge_geometry_key(
    surface: TriangleSurface, edge: tuple[int, int]
) -> EdgeGeometryKey:
    return tuple(sorted((
        _vertex_key(surface.vertices[edge[0]]),
        _vertex_key(surface.vertices[edge[1]]),
    )))


def _triangle_geometry_key(
    surface: TriangleSurface, triangle_index: int
) -> TriangleGeometryKey:
    return tuple(sorted(
        _vertex_key(surface.vertices[index])
        for index in surface.triangles[triangle_index].vertex_indices
    ))


def _triangle_sort_key(
    surface: TriangleSurface, roles: tuple[str, ...], triangle_index: int
) -> tuple[object, ...]:
    triangle = surface.triangles[triangle_index]
    return (
        _triangle_geometry_key(surface, triangle_index),
        roles[triangle_index],
        triangle.source_id or "",
        tuple(sorted(
            (str(key).casefold(), str(value))
            for key, value in triangle.source_attributes.items()
        )),
        triangle_index,  # total-order fallback for materially identical entries
    )


def _shared_edges(
    surface: TriangleSurface,
) -> dict[tuple[int, int], tuple[int, ...]]:
    incident: dict[tuple[int, int], list[int]] = defaultdict(list)
    for triangle_index, triangle in enumerate(surface.triangles):
        first, second, third = triangle.vertex_indices
        for edge in ((first, second), (second, third), (third, first)):
            incident[tuple(sorted(edge))].append(triangle_index)
    return {
        edge: tuple(triangle_indices)
        for edge, triangle_indices in incident.items()
    }


def _triangle_components(
    surface: TriangleSurface,
    shared_edges: dict[tuple[int, int], tuple[int, ...]],
    roles: tuple[str, ...],
    wanted: frozenset[str],
) -> tuple[tuple[int, ...], ...]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    for triangle_indices in shared_edges.values():
        if len(triangle_indices) != 2:
            continue
        first, second = triangle_indices
        if roles[first] in wanted and roles[second] in wanted:
            adjacency[first].add(second)
            adjacency[second].add(first)

    unused = {
        index for index, role in enumerate(roles) if role in wanted
    }
    components: list[tuple[int, ...]] = []
    while unused:
        seed = min(
            unused,
            key=lambda index: _triangle_sort_key(surface, roles, index),
        )
        pending = [seed]
        component: list[int] = []
        while pending:
            current = pending.pop()
            if current not in unused:
                continue
            unused.remove(current)
            component.append(current)
            pending.extend(sorted(
                adjacency[current],
                key=lambda index: _triangle_sort_key(surface, roles, index),
                reverse=True,
            ))
        components.append(tuple(sorted(
            component,
            key=lambda index: _triangle_sort_key(surface, roles, index),
        )))
    return tuple(sorted(
        components,
        key=lambda component: tuple(
            _triangle_sort_key(surface, roles, index)[:-1]
            for index in component
        ),
    ))


def _third_vertex_index(
    surface: TriangleSurface, edge: tuple[int, int], triangle_index: int
) -> int:
    return next(
        index
        for index in surface.triangles[triangle_index].vertex_indices
        if index not in edge
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


@dataclass(frozen=True)
class FaceDirectionEvidence:
    """Normalized horizontal steepest descent from one Design Face triangle."""

    triangle_index: int
    triangle_key: TriangleGeometryKey
    point: PlanPoint
    downwall_xy: tuple[float, float] | None
    geometric_weight: float
    source_id: str


@dataclass(frozen=True)
class FaceDirectionSupport:
    """Normalized, explicitly geometry-weighted local Face direction support.

    ``angular_dispersion_degrees`` is weighted RMS residual to the aggregate.
    ``angular_support_degrees`` is its weighted 95% residual envelope.  Both
    are diagnostics for later phases, not Phase-2A acceptance thresholds.
    """

    downwall_xy: tuple[float, float] | None
    supporting_weight: float
    angular_dispersion_degrees: float
    angular_support_degrees: float
    sample_count: int
    triangle_keys: tuple[TriangleGeometryKey, ...]


@dataclass(frozen=True)
class PortalEdgeProvenance:
    """One raw portal-chain edge and its chain-local directional evidence."""

    edge_key: EdgeGeometryKey
    face_triangle_key: TriangleGeometryKey
    platform_triangle_key: TriangleGeometryKey | None
    source_kind: str
    provisional_side: PortalSide


@dataclass(frozen=True)
class FaceComponent:
    """One exact shared-edge Face container; never a wall-sector identity."""

    component_id: str
    canonical_index: int
    triangle_indices: tuple[int, ...]
    triangle_keys: tuple[TriangleGeometryKey, ...]
    portal_ids: tuple[str, ...] = ()
    outer_rim_portal_ids: tuple[str, ...] = ()
    direction_samples: tuple[FaceDirectionEvidence, ...] = ()
    topology_issue_codes: tuple[str, ...] = ()
    topology_issue_edge_keys: tuple[EdgeGeometryKey, ...] = ()


@dataclass(frozen=True)
class PlatformComponent:
    """One exact shared-edge Berm/Road container, potentially very large."""

    component_id: str
    canonical_index: int
    triangle_indices: tuple[int, ...]
    triangle_keys: tuple[TriangleGeometryKey, ...]
    semantic_roles: tuple[str, ...]
    portal_ids: tuple[str, ...] = ()
    topology_issue_codes: tuple[str, ...] = ()
    topology_issue_edge_keys: tuple[EdgeGeometryKey, ...] = ()


@dataclass(frozen=True)
class TransitionPortal:
    """A deterministic local boundary chain incident to one Face component."""

    portal_id: str
    points: tuple[SurfaceVertex, ...]
    face_component_index: int
    platform_component_index: int | None
    provisional_side: PortalSide
    source_kind: str
    edge_provenance: tuple[PortalEdgeProvenance, ...]
    adjacent_face_triangle_indices: tuple[int, ...]
    adjacent_platform_triangle_indices: tuple[int, ...]
    direction_support: FaceDirectionSupport

    @property
    def geometry_key(self) -> tuple[VertexKey, ...]:
        return tuple(_vertex_key(point) for point in self.points)


@dataclass(frozen=True)
class LocalPortalRunPair:
    """Exact local edge spans supporting one parent-portal connection."""

    source_edge_keys: tuple[EdgeGeometryKey, ...]
    target_edge_keys: tuple[EdgeGeometryKey, ...]


@dataclass(frozen=True)
class CorridorConnection:
    """One plausible local continuation candidate, not a validated wall."""

    connection_id: str
    source_portal_id: str
    target_portal_id: str
    platform_component_index: int
    status: ConnectionStatus
    reason: str
    displacement_xy: tuple[float, float]
    source_advance_m: float
    target_advance_m: float
    source_alignment_degrees: float
    target_alignment_degrees: float
    direction_mismatch_degrees: float
    source_direction_dispersion_degrees: float
    target_direction_dispersion_degrees: float
    strike_overlap_m: float
    order_compatible: bool
    validated_wall_continuation: bool = False
    local_run_pairs: tuple[LocalPortalRunPair, ...] = ()


@dataclass(frozen=True)
class TopologyDiagnostic:
    code: str
    message: str
    geometry_keys: tuple[tuple[VertexKey, ...], ...] = ()
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DesignTopologyDiagnostics:
    items: tuple[TopologyDiagnostic, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.items)


@dataclass(frozen=True)
class DesignTopologyIndex:
    """Reusable Design semantic topology; contains no assessed wall sectors."""

    triangle_roles: tuple[str, ...]
    face_components: tuple[FaceComponent, ...]
    platform_components: tuple[PlatformComponent, ...]
    portals: tuple[TransitionPortal, ...]
    face_direction_samples: tuple[FaceDirectionEvidence, ...]
    corridor_connections: tuple[CorridorConnection, ...]
    diagnostics: DesignTopologyDiagnostics

    @property
    def compatible_corridor_connections(self) -> tuple[CorridorConnection, ...]:
        return tuple(
            connection
            for connection in self.corridor_connections
            if connection.status == "compatible"
        )

    @property
    def ambiguous_corridor_connections(self) -> tuple[CorridorConnection, ...]:
        return tuple(
            connection
            for connection in self.corridor_connections
            if connection.status == "ambiguous"
        )

    @property
    def canonical_signature(self) -> tuple[object, ...]:
        """Material result signature independent of TIN storage ordering."""
        return (
            tuple(sorted(
                (sample.triangle_key, sample.downwall_xy, sample.geometric_weight)
                for sample in self.face_direction_samples
            )),
            tuple(
                (
                    component.triangle_keys,
                    component.portal_ids,
                    component.topology_issue_codes,
                    component.topology_issue_edge_keys,
                )
                for component in self.face_components
            ),
            tuple(
                (
                    component.triangle_keys,
                    component.semantic_roles,
                    component.portal_ids,
                    component.topology_issue_codes,
                    component.topology_issue_edge_keys,
                )
                for component in self.platform_components
            ),
            tuple(
                (
                    portal.portal_id,
                    portal.geometry_key,
                    portal.face_component_index,
                    portal.platform_component_index,
                    portal.provisional_side,
                    portal.source_kind,
                    portal.direction_support.downwall_xy,
                    portal.direction_support.angular_dispersion_degrees,
                    portal.direction_support.angular_support_degrees,
                    tuple(
                        (
                            edge.edge_key,
                            edge.face_triangle_key,
                            edge.platform_triangle_key,
                            edge.source_kind,
                            edge.provisional_side,
                        )
                        for edge in portal.edge_provenance
                    ),
                )
                for portal in self.portals
            ),
            tuple(
                (
                    connection.source_portal_id,
                    connection.target_portal_id,
                    connection.platform_component_index,
                    connection.status,
                    connection.reason,
                    connection.displacement_xy,
                    connection.source_advance_m,
                    connection.target_advance_m,
                    connection.source_alignment_degrees,
                    connection.target_alignment_degrees,
                    connection.direction_mismatch_degrees,
                    connection.source_direction_dispersion_degrees,
                    connection.target_direction_dispersion_degrees,
                    connection.strike_overlap_m,
                    connection.order_compatible,
                    connection.validated_wall_continuation,
                    tuple(
                        (
                            pair.source_edge_keys,
                            pair.target_edge_keys,
                        )
                        for pair in connection.local_run_pairs
                    ),
                )
                for connection in self.corridor_connections
            ),
            tuple(
                (
                    item.code,
                    item.geometry_keys,
                    item.related_ids,
                )
                for item in self.diagnostics.items
            ),
        )


@dataclass(frozen=True)
class _BoundaryEdge:
    edge: tuple[int, int]
    face_triangle_index: int
    platform_triangle_index: int | None
    face_component_index: int
    platform_component_index: int | None
    source_kind: str


def _face_direction_evidence(
    surface: TriangleSurface, triangle_index: int
) -> FaceDirectionEvidence:
    triangle = surface.triangles[triangle_index]
    first, second, third = (
        surface.vertices[index] for index in triangle.vertex_indices
    )
    ab = (second.x - first.x, second.y - first.y, second.z - first.z)
    ac = (third.x - first.x, third.y - first.y, third.z - first.z)
    cross_xyz = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    area = sqrt(fsum(value * value for value in cross_xyz)) / 2.0
    ux, uy = second.x - first.x, second.y - first.y
    vx, vy = third.x - first.x, third.y - first.y
    determinant = ux * vy - uy * vx
    downwall: tuple[float, float] | None = None
    if abs(determinant) > _PLAN_DETERMINANT_TOLERANCE:
        uz, vz = second.z - first.z, third.z - first.z
        gradient_x = (uz * vy - uy * vz) / determinant
        gradient_y = (ux * vz - uz * vx) / determinant
        downwall = _unit((-gradient_x, -gradient_y))
    return FaceDirectionEvidence(
        triangle_index,
        _triangle_geometry_key(surface, triangle_index),
        PlanPoint(
            (first.x + second.x + third.x) / 3.0,
            (first.y + second.y + third.y) / 3.0,
        ),
        downwall,
        area,
        triangle.source_id or "",
    )


def _aggregate_direction_support(
    samples: tuple[FaceDirectionEvidence, ...]
) -> FaceDirectionSupport:
    ordered = tuple(sorted(
        (sample for sample in samples if sample.downwall_xy is not None),
        key=lambda sample: (
            sample.triangle_key,
            sample.source_id,
            sample.geometric_weight,
        ),
    ))
    total_weight = fsum(sample.geometric_weight for sample in ordered)
    if total_weight <= _GEOMETRY_TOLERANCE:
        return FaceDirectionSupport(None, 0.0, 0.0, 0.0, 0, ())
    sum_x = fsum(
        sample.downwall_xy[0] * sample.geometric_weight  # type: ignore[index]
        for sample in ordered
    )
    sum_y = fsum(
        sample.downwall_xy[1] * sample.geometric_weight  # type: ignore[index]
        for sample in ordered
    )
    aggregate = _unit((sum_x, sum_y))
    if aggregate is None:
        return FaceDirectionSupport(
            None,
            total_weight,
            180.0,
            180.0,
            len(ordered),
            tuple(sample.triangle_key for sample in ordered),
        )
    angular = tuple(
        (_angle_degrees(sample.downwall_xy, aggregate), sample.geometric_weight)
        for sample in ordered  # type: ignore[arg-type]
    )
    # Preserve Phase 1's weight-aware directional-support semantics.  Each
    # triangle has already supplied a unit horizontal direction; only its
    # explicit geometric weight participates here, never gradient magnitude.
    dispersion = sqrt(
        fsum(weight * angle * angle for angle, weight in angular) / total_weight
    )
    cutoff = total_weight * 0.95
    cumulative = 0.0
    support = 0.0
    for angle, weight in sorted(angular, key=lambda item: item[0]):
        cumulative += weight
        support = angle
        if cumulative >= cutoff:
            break
    return FaceDirectionSupport(
        aggregate,
        total_weight,
        dispersion,
        support,
        len(ordered),
        tuple(sample.triangle_key for sample in ordered),
    )


def _face_adjacency(
    shared_edges: dict[tuple[int, int], tuple[int, ...]],
    roles: tuple[str, ...],
) -> dict[int, set[int]]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    for triangle_indices in shared_edges.values():
        if len(triangle_indices) != 2:
            continue
        first, second = triangle_indices
        if roles[first] == roles[second] == "face":
            adjacency[first].add(second)
            adjacency[second].add(first)
    return adjacency


def _platform_adjacency(
    shared_edges: dict[tuple[int, int], tuple[int, ...]],
    roles: tuple[str, ...],
) -> dict[int, set[int]]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    for triangle_indices in shared_edges.values():
        if len(triangle_indices) != 2:
            continue
        first, second = triangle_indices
        if roles[first] in _PLATFORM_ROLES and roles[second] in _PLATFORM_ROLES:
            adjacency[first].add(second)
            adjacency[second].add(first)
    return adjacency


def _local_support_for_face_triangles(
    adjacent_triangles: set[int],
    face_adjacency: dict[int, set[int]],
    evidence_by_triangle: dict[int, FaceDirectionEvidence],
) -> FaceDirectionSupport:
    support_indices = set(adjacent_triangles)
    for triangle_index in tuple(adjacent_triangles):
        support_indices.update(face_adjacency[triangle_index])
    return _aggregate_direction_support(tuple(
        evidence_by_triangle[index]
        for index in support_indices
        if index in evidence_by_triangle
    ))


def _classify_boundary_edge_side(
    surface: TriangleSurface,
    edge: tuple[int, int],
    face_triangle_index: int,
    support: FaceDirectionSupport,
) -> PortalSide:
    if support.downwall_xy is None:
        return "ambiguous"
    first, second = (surface.vertices[index] for index in edge)
    third = surface.vertices[_third_vertex_index(surface, edge, face_triangle_index)]
    edge_unit = _unit((second.x - first.x, second.y - first.y))
    midpoint = ((first.x + second.x) / 2.0, (first.y + second.y) / 2.0)
    interior_unit = _unit((third.x - midpoint[0], third.y - midpoint[1]))
    if edge_unit is None or interior_unit is None:
        return "ambiguous"
    edge_projection = abs(_dot(edge_unit, support.downwall_xy))
    interior_projection = _dot(interior_unit, support.downwall_xy)
    margin = abs(interior_projection) - edge_projection
    if abs(margin) <= _GEOMETRY_TOLERANCE:
        return "ambiguous"
    if margin < 0.0:
        return "lateral"
    return "upstream" if interior_projection > 0.0 else "downstream"


def _classify_portal_side(
    surface: TriangleSurface,
    chain_records: tuple[_BoundaryEdge, ...],
    edge_sides: tuple[PortalSide, ...],
) -> PortalSide:
    """Classify one topology-established chain from its aggregate Face support."""
    scores: dict[PortalSide, float] = defaultdict(float)
    for record, side in zip(chain_records, edge_sides, strict=True):
        first, second = (surface.vertices[index] for index in record.edge)
        edge_length = hypot(second.x - first.x, second.y - first.y)
        scores[side] += edge_length
    if not scores:
        return "ambiguous"
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if (
        len(ordered) > 1
        and abs(ordered[0][1] - ordered[1][1]) <= _GEOMETRY_TOLERANCE
    ):
        return "ambiguous"
    return ordered[0][0]


def _canonical_chain(
    points: tuple[SurfaceVertex, ...],
    edges: tuple[_BoundaryEdge, ...],
) -> tuple[tuple[SurfaceVertex, ...], tuple[_BoundaryEdge, ...]]:
    forward = tuple(_vertex_key(point) for point in points)
    reverse = tuple(reversed(forward))
    if reverse < forward:
        return tuple(reversed(points)), tuple(reversed(edges))
    return points, edges


def _boundary_chains(
    surface: TriangleSurface,
    boundary_edges: tuple[_BoundaryEdge, ...],
) -> tuple[
    tuple[tuple[SurfaceVertex, ...], tuple[_BoundaryEdge, ...]], ...
]:
    adjacency: dict[int, list[int]] = defaultdict(list)
    for index, record in enumerate(boundary_edges):
        adjacency[record.edge[0]].append(index)
        adjacency[record.edge[1]].append(index)
    for incident in adjacency.values():
        incident.sort(key=lambda index: _edge_geometry_key(
            surface, boundary_edges[index].edge
        ))

    unused = set(range(len(boundary_edges)))
    chains: list[tuple[tuple[SurfaceVertex, ...], tuple[_BoundaryEdge, ...]]] = []

    def walk(start_vertex: int, edge_index: int):
        vertex_indices = [start_vertex]
        records: list[_BoundaryEdge] = []
        current_vertex = start_vertex
        current_edge = edge_index
        while current_edge in unused:
            unused.remove(current_edge)
            record = boundary_edges[current_edge]
            other = (
                record.edge[1]
                if record.edge[0] == current_vertex
                else record.edge[0]
            )
            vertex_indices.append(other)
            records.append(record)
            if len(adjacency[other]) != 2:
                break
            remaining = [index for index in adjacency[other] if index in unused]
            if not remaining:
                break
            current_vertex, current_edge = other, remaining[0]
        return _canonical_chain(
            tuple(surface.vertices[index] for index in vertex_indices),
            tuple(records),
        )

    for vertex_index in sorted(
        adjacency,
        key=lambda index: (_vertex_key(surface.vertices[index]), index),
    ):
        if len(adjacency[vertex_index]) == 2:
            continue
        for edge_index in adjacency[vertex_index]:
            if edge_index in unused:
                chains.append(walk(vertex_index, edge_index))

    while unused:
        edge_index = min(
            unused,
            key=lambda index: _edge_geometry_key(
                surface, boundary_edges[index].edge
            ),
        )
        edge = boundary_edges[edge_index].edge
        start_vertex = min(
            edge,
            key=lambda index: (_vertex_key(surface.vertices[index]), index),
        )
        chains.append(walk(start_vertex, edge_index))

    return tuple(sorted(
        chains,
        key=lambda chain: tuple(_vertex_key(point) for point in chain[0]),
    ))


def _length_weighted_midpoint(portal: TransitionPortal) -> PlanPoint:
    segments = tuple(zip(portal.points, portal.points[1:]))
    lengths = tuple(
        hypot(second.x - first.x, second.y - first.y)
        for first, second in segments
    )
    total = fsum(lengths)
    if total <= _GEOMETRY_TOLERANCE:
        first = portal.points[0]
        return PlanPoint(first.x, first.y)
    return PlanPoint(
        fsum(
            ((first.x + second.x) / 2.0) * length
            for (first, second), length in zip(segments, lengths)
        ) / total,
        fsum(
            ((first.y + second.y) / 2.0) * length
            for (first, second), length in zip(segments, lengths)
        ) / total,
    )


def _platform_triangle_centroid(
    surface: TriangleSurface, triangle_index: int
) -> PlanPoint:
    vertices = tuple(
        surface.vertices[index]
        for index in surface.triangles[triangle_index].vertex_indices
    )
    return PlanPoint(
        fsum(vertex.x for vertex in vertices) / 3.0,
        fsum(vertex.y for vertex in vertices) / 3.0,
    )


def _point_segment_distance(
    point: PlanPoint, first: SurfaceVertex, second: SurfaceVertex
) -> float:
    dx, dy = second.x - first.x, second.y - first.y
    length_squared = dx * dx + dy * dy
    if length_squared <= _GEOMETRY_TOLERANCE * _GEOMETRY_TOLERANCE:
        return hypot(point.x - first.x, point.y - first.y)
    fraction = max(0.0, min(1.0, (
        (point.x - first.x) * dx + (point.y - first.y) * dy
    ) / length_squared))
    return hypot(
        point.x - (first.x + fraction * dx),
        point.y - (first.y + fraction * dy),
    )


def _point_portal_distance(point: PlanPoint, portal: TransitionPortal) -> float:
    return min(
        _point_segment_distance(point, first, second)
        for first, second in zip(portal.points, portal.points[1:])
    )


def _projection_values(
    portal: TransitionPortal, axis: tuple[float, float]
) -> tuple[float, ...]:
    return tuple(point.x * axis[0] + point.y * axis[1] for point in portal.points)


def _monotone_projection_ranges(
    values: tuple[float, ...],
) -> tuple[tuple[float, float], ...]:
    """Return maximal locally monotone scalar ranges without choosing a band."""
    if len(values) < 2:
        return ()
    spans: list[tuple[int, int]] = []
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
            spans.append((start, edge_index + 1))
            start = edge_index
            direction = edge_direction
    if direction == 0:
        return ()
    spans.append((start, len(values)))
    return tuple(
        (min(values[start:end]), max(values[start:end]))
        for start, end in spans
        if max(values[start:end]) - min(values[start:end])
        > _GEOMETRY_TOLERANCE
    )


def _locally_orderable_projection_overlap(
    source_values: tuple[float, ...],
    target_values: tuple[float, ...],
) -> float:
    """Return coarse overlap evidence; Phase 2B owns exact correspondence."""
    source_ranges = _monotone_projection_ranges(source_values)
    target_ranges = _monotone_projection_ranges(target_values)
    return max((
        min(source_end, target_end) - max(source_start, target_start)
        for source_start, source_end in source_ranges
        for target_start, target_end in target_ranges
    ), default=0.0)


def _platform_portal_neighbours(
    surface: TriangleSurface,
    component: PlatformComponent,
    portals: tuple[TransitionPortal, ...],
    platform_adjacency: dict[int, set[int]],
    roles: tuple[str, ...],
) -> set[tuple[str, str]]:
    """Return local portal neighbours using geometric-cost TIN Dijkstra."""
    component_triangles = set(component.triangle_indices)
    adjacency = {
        triangle_index: {
            neighbour
            for neighbour in platform_adjacency[triangle_index]
            if neighbour in component_triangles
        }
        for triangle_index in component.triangle_indices
    }

    centroids = {
        triangle_index: _platform_triangle_centroid(surface, triangle_index)
        for triangle_index in component.triangle_indices
    }
    best_distance: dict[int, float] = {}
    owners: dict[int, set[str]] = defaultdict(set)
    processed: set[tuple[int, str]] = set()
    queue: list[tuple[float, tuple[object, ...], int, str]] = []
    portal_by_id = {portal.portal_id: portal for portal in portals}
    for portal in portals:
        for triangle_index in portal.adjacent_platform_triangle_indices:
            if triangle_index not in component_triangles:
                continue
            initial_distance = _point_portal_distance(
                centroids[triangle_index], portal
            )
            heappush(queue, (
                initial_distance,
                _triangle_sort_key(surface, roles, triangle_index),
                triangle_index,
                portal.portal_id,
            ))

    while queue:
        distance, _sort_key, triangle_index, owner = heappop(queue)
        previous = best_distance.get(triangle_index)
        if (
            previous is not None
            and distance > previous + _GEOMETRY_TOLERANCE
        ):
            continue
        if (
            previous is None
            or distance < previous - _GEOMETRY_TOLERANCE
        ):
            best_distance[triangle_index] = distance
            owners[triangle_index] = {owner}
        else:
            owners[triangle_index].add(owner)
        state = (triangle_index, owner)
        if state in processed:
            continue
        processed.add(state)
        for neighbour in sorted(
            adjacency[triangle_index],
            key=lambda index: _triangle_sort_key(surface, roles, index),
        ):
            step_cost = hypot(
                centroids[neighbour].x - centroids[triangle_index].x,
                centroids[neighbour].y - centroids[triangle_index].y,
            )
            heappush(queue, (
                distance + step_cost,
                _triangle_sort_key(surface, roles, neighbour),
                neighbour,
                owner,
            ))

    pairs: set[tuple[str, str]] = set()
    for triangle_index, triangle_owners in owners.items():
        for first, second in combinations(sorted(triangle_owners), 2):
            pairs.add((first, second))
        for neighbour in adjacency[triangle_index]:
            for first in triangle_owners:
                for second in owners.get(neighbour, set()):
                    if first != second:
                        pairs.add(tuple(sorted((first, second))))
    return {
        pair
        for pair in pairs
        if pair[0] in portal_by_id and pair[1] in portal_by_id
    }


def _local_side_runs(
    surface: TriangleSurface,
    portal: TransitionPortal,
    wanted_side: PortalSide,
) -> tuple[TransitionPortal, ...]:
    """Return maximal positive-length runs from stored edge-side evidence.

    The returned portals are immutable, calculation-local views.  Their
    synthetic ids exist only to keep separate runs distinct while platform
    neighbourhood is evaluated; corridor provenance continues to use the
    parent portal ids.
    """
    count = len(portal.edge_provenance)
    if count == 0:
        return ()
    closed = portal.points[0] == portal.points[-1]
    wanted = tuple(
        edge.provisional_side == wanted_side
        for edge in portal.edge_provenance
    )
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

    runs: list[TransitionPortal] = []
    for start in starts:
        indices: list[int] = []
        index = start
        while 0 <= index < count and wanted[index] and index not in indices:
            indices.append(index)
            index = (index + 1) % count if closed else index + 1
        points = [portal.points[indices[0]]]
        points.extend(portal.points[edge_index + 1] for edge_index in indices)
        length = fsum(
            hypot(second.x - first.x, second.y - first.y)
            for first, second in zip(points, points[1:])
        )
        if length <= _GEOMETRY_TOLERANCE:
            continue
        provenance = tuple(
            portal.edge_provenance[edge_index] for edge_index in indices
        )
        face_keys = {edge.face_triangle_key for edge in provenance}
        platform_keys = {
            edge.platform_triangle_key
            for edge in provenance
            if edge.platform_triangle_key is not None
        }
        runs.append(TransitionPortal(
            f"{portal.portal_id}/local:{wanted_side}:{start}",
            tuple(points),
            portal.face_component_index,
            portal.platform_component_index,
            wanted_side,
            portal.source_kind,
            provenance,
            tuple(
                triangle_index
                for triangle_index in portal.adjacent_face_triangle_indices
                if _triangle_geometry_key(surface, triangle_index) in face_keys
            ),
            tuple(
                triangle_index
                for triangle_index
                in portal.adjacent_platform_triangle_indices
                if _triangle_geometry_key(surface, triangle_index)
                in platform_keys
            ),
            portal.direction_support,
        ))
    return tuple(runs)


def _platform_local_connection_candidates(
    surface: TriangleSurface,
    component: PlatformComponent,
    portals: tuple[TransitionPortal, ...],
    platform_adjacency: dict[int, set[int]],
    roles: tuple[str, ...],
) -> tuple[CorridorConnection, ...]:
    """Evaluate all structural local-run neighbours in one platform pass."""
    parent_by_local_id: dict[str, TransitionPortal] = {}
    local_runs: list[TransitionPortal] = []
    for portal in portals:
        for side in ("downstream", "upstream"):
            for run in _local_side_runs(surface, portal, side):
                local_runs.append(run)
                parent_by_local_id[run.portal_id] = portal
    if not local_runs:
        return ()
    local_by_id = {run.portal_id: run for run in local_runs}
    parent_neighbour_pairs = _platform_portal_neighbours(
        surface,
        component,
        portals,
        platform_adjacency,
        roles,
    )
    parent_by_id = {portal.portal_id: portal for portal in portals}
    whole_side_candidates: list[CorridorConnection] = []
    whole_side_pairs: set[tuple[str, str]] = set()
    for first_id, second_id in sorted(parent_neighbour_pairs):
        first = parent_by_id[first_id]
        second = parent_by_id[second_id]
        if first.provisional_side == "downstream" and (
            second.provisional_side == "upstream"
        ):
            source, target = first, second
        elif second.provisional_side == "downstream" and (
            first.provisional_side == "upstream"
        ):
            source, target = second, first
        else:
            continue
        if source.face_component_index == target.face_component_index:
            continue
        whole_side_pairs.add((first_id, second_id))
        whole_side_candidates.append(_connection_candidate(
            source, target, component.canonical_index
        ))
    neighbour_pairs = _platform_portal_neighbours(
        surface,
        component,
        tuple(local_runs),
        platform_adjacency,
        roles,
    )
    grouped: dict[tuple[str, str], list[CorridorConnection]] = defaultdict(list)
    for first_id, second_id in sorted(neighbour_pairs):
        first = local_by_id[first_id]
        second = local_by_id[second_id]
        if first.provisional_side == "downstream" and (
            second.provisional_side == "upstream"
        ):
            source, target = first, second
        elif second.provisional_side == "downstream" and (
            first.provisional_side == "upstream"
        ):
            source, target = second, first
        else:
            continue
        source_parent = parent_by_local_id[source.portal_id]
        target_parent = parent_by_local_id[target.portal_id]
        if source_parent.face_component_index == target_parent.face_component_index:
            continue
        if (
            source_parent.provisional_side not in {"downstream", "lateral"}
            or target_parent.provisional_side not in {"upstream", "lateral"}
            or "lateral" not in {
                source_parent.provisional_side,
                target_parent.provisional_side,
            }
        ):
            continue
        parent_pair = tuple(sorted((
            source_parent.portal_id,
            target_parent.portal_id,
        )))
        if (
            parent_pair not in parent_neighbour_pairs
            or parent_pair in whole_side_pairs
        ):
            continue
        grouped[(source_parent.portal_id, target_parent.portal_id)].append(
            replace(
                _connection_candidate(
                    source, target, component.canonical_index
                ),
                source_portal_id=source_parent.portal_id,
                target_portal_id=target_parent.portal_id,
                local_run_pairs=(LocalPortalRunPair(
                    tuple(edge.edge_key for edge in source.edge_provenance),
                    tuple(edge.edge_key for edge in target.edge_provenance),
                ),),
            )
        )
    local_parent_candidates = tuple(
        candidate
        for parent_ids in sorted(grouped)
        for candidate in (
            _parent_connection_candidate(tuple(grouped[parent_ids])),
        )
        if candidate is not None
    )
    return tuple(whole_side_candidates) + local_parent_candidates


def _parent_connection_candidate(
    local_candidates: tuple[CorridorConnection, ...],
) -> CorridorConnection | None:
    """Collapse local evidence without selecting a final correspondence."""
    if not local_candidates:
        return None
    compatible = tuple(
        candidate
        for candidate in local_candidates
        if candidate.status == "compatible"
    )
    if not compatible:
        return local_candidates[0]
    witness = compatible[0]
    if len(compatible) == 1:
        return witness
    return replace(
        witness,
        status="ambiguous",
        reason=(
            f"{len(compatible)} competing locally compatible run pairs; "
            "Phase 2B correspondence required"
        ),
        local_run_pairs=tuple(
            pair
            for candidate in compatible
            for pair in candidate.local_run_pairs
        ),
    )


def _connection_candidate(
    source: TransitionPortal,
    target: TransitionPortal,
    platform_component_index: int,
) -> CorridorConnection:
    source_direction = source.direction_support.downwall_xy
    target_direction = target.direction_support.downwall_xy
    source_midpoint = _length_weighted_midpoint(source)
    target_midpoint = _length_weighted_midpoint(target)
    displacement = (
        target_midpoint.x - source_midpoint.x,
        target_midpoint.y - source_midpoint.y,
    )
    displacement_unit = _unit(displacement)
    rejected_reason = ""
    order_compatible = False
    strike_overlap = 0.0
    source_advance = target_advance = 0.0
    source_alignment = target_alignment = 180.0
    direction_mismatch = 180.0

    if source_direction is None or target_direction is None:
        rejected_reason = "local Face direction support is unavailable"
    else:
        direction_mismatch = _angle_degrees(source_direction, target_direction)
        if _dot(source_direction, target_direction) <= 0.0:
            rejected_reason = (
                "adjacent Face directions do not share a downwall sense"
            )
        elif displacement_unit is None:
            rejected_reason = "portal representatives have zero plan displacement"
        else:
            source_advance = _dot(displacement, source_direction)
            target_advance = _dot(displacement, target_direction)
            source_alignment = _angle_degrees(
                displacement_unit, source_direction
            )
            target_alignment = _angle_degrees(
                displacement_unit, target_direction
            )
            combined = _unit((
                source_direction[0] + target_direction[0],
                source_direction[1] + target_direction[1],
            ))
            if combined is None:
                rejected_reason = "adjacent Face directions cancel"
            elif (
                source_advance <= _GEOMETRY_TOLERANCE
                or target_advance <= _GEOMETRY_TOLERANCE
            ):
                rejected_reason = (
                    "platform displacement does not advance downwall"
                )
            else:
                strike = (-combined[1], combined[0])
                source_values = _projection_values(source, strike)
                target_values = _projection_values(target, strike)
                strike_overlap = _locally_orderable_projection_overlap(
                    source_values, target_values
                )
                order_compatible = strike_overlap > _GEOMETRY_TOLERANCE
                if not order_compatible:
                    rejected_reason = (
                        "portal chains do not define an overlapping locally "
                        "monotone band"
                    )

    return CorridorConnection(
        "",
        source.portal_id,
        target.portal_id,
        platform_component_index,
        "rejected" if rejected_reason else "compatible",
        rejected_reason or (
            "locally compatible candidate; Phase 2B validation required"
        ),
        displacement,
        source_advance,
        target_advance,
        source_alignment,
        target_alignment,
        direction_mismatch,
        source.direction_support.angular_dispersion_degrees,
        target.direction_support.angular_dispersion_degrees,
        max(0.0, strike_overlap),
        order_compatible,
    )


def _diagnostic_sort_key(item: TopologyDiagnostic) -> tuple[object, ...]:
    return item.code, item.geometry_keys, item.related_ids, item.message


def build_design_topology_index(
    surface: TriangleSurface,
    role_mapping: SurfaceRoleMapping,
) -> DesignTopologyIndex:
    """Build exact semantic TIN topology and local corridor candidates."""
    roles = tuple(
        role_mapping.resolve(triangle.source_attributes)
        for triangle in surface.triangles
    )
    shared_edges = _shared_edges(surface)
    diagnostics: list[TopologyDiagnostic] = []
    non_manifold_edges = tuple(sorted(
        (
            (edge, triangle_indices)
            for edge, triangle_indices in shared_edges.items()
            if len(triangle_indices) > 2
        ),
        key=lambda item: _edge_geometry_key(surface, item[0]),
    ))

    duplicate_triangles: dict[TriangleGeometryKey, list[int]] = defaultdict(list)
    for triangle_index in range(len(surface.triangles)):
        duplicate_triangles[_triangle_geometry_key(surface, triangle_index)].append(
            triangle_index
        )
    for geometry_key, triangle_indices in duplicate_triangles.items():
        if len(triangle_indices) > 1:
            diagnostics.append(TopologyDiagnostic(
                "duplicate_triangle_geometry",
                "Multiple stored triangles have identical XYZ geometry",
                (geometry_key,),
            ))

    coincident_vertices: dict[VertexKey, list[int]] = defaultdict(list)
    for vertex_index, vertex in enumerate(surface.vertices):
        coincident_vertices[_vertex_key(vertex)].append(vertex_index)
    for vertex_key, vertex_indices in coincident_vertices.items():
        if len(vertex_indices) > 1:
            diagnostics.append(TopologyDiagnostic(
                "coincident_distinct_vertices",
                "Distinct TIN vertices share exact XYZ and were not welded",
                ((vertex_key,),),
            ))

    face_groups = _triangle_components(
        surface, shared_edges, roles, frozenset({"face"})
    )
    platform_groups = _triangle_components(
        surface, shared_edges, roles, _PLATFORM_ROLES
    )
    face_component_by_triangle = {
        triangle_index: component_index
        for component_index, group in enumerate(face_groups)
        for triangle_index in group
    }
    platform_component_by_triangle = {
        triangle_index: component_index
        for component_index, group in enumerate(platform_groups)
        for triangle_index in group
    }
    face_issue_edges: dict[int, set[EdgeGeometryKey]] = defaultdict(set)
    platform_issue_edges: dict[int, set[EdgeGeometryKey]] = defaultdict(set)
    for edge, triangle_indices in non_manifold_edges:
        edge_key = _edge_geometry_key(surface, edge)
        related_ids: set[str] = set()
        for triangle_index in triangle_indices:
            if triangle_index in face_component_by_triangle:
                component_index = face_component_by_triangle[triangle_index]
                face_issue_edges[component_index].add(edge_key)
                related_ids.add(f"face:{component_index}")
            if triangle_index in platform_component_by_triangle:
                component_index = platform_component_by_triangle[triangle_index]
                platform_issue_edges[component_index].add(edge_key)
                related_ids.add(f"platform:{component_index}")
        diagnostics.append(TopologyDiagnostic(
            "non_manifold_edge",
            "TIN edge has more than two incident triangles",
            (edge_key,),
            tuple(sorted(related_ids)),
        ))

    face_direction_samples = tuple(sorted(
        (
            _face_direction_evidence(surface, triangle_index)
            for triangle_index, role in enumerate(roles)
            if role == "face"
        ),
        key=lambda sample: (
            sample.triangle_key,
            sample.source_id,
            sample.geometric_weight,
        ),
    ))
    evidence_by_triangle = {
        sample.triangle_index: sample for sample in face_direction_samples
    }
    for sample in face_direction_samples:
        if sample.downwall_xy is None:
            diagnostics.append(TopologyDiagnostic(
                "degenerate_face_direction",
                "Face triangle has no stable horizontal steepest-descent direction",
                (sample.triangle_key,),
            ))

    face_components = tuple(
        FaceComponent(
            f"face:{component_index}",
            component_index,
            group,
            tuple(_triangle_geometry_key(surface, index) for index in group),
            direction_samples=tuple(
                evidence_by_triangle[index]
                for index in group
                if index in evidence_by_triangle
            ),
            topology_issue_codes=(
                ("non_manifold_edge",)
                if face_issue_edges[component_index]
                else ()
            ),
            topology_issue_edge_keys=tuple(sorted(
                face_issue_edges[component_index]
            )),
        )
        for component_index, group in enumerate(face_groups)
    )
    platform_components = tuple(
        PlatformComponent(
            f"platform:{component_index}",
            component_index,
            group,
            tuple(_triangle_geometry_key(surface, index) for index in group),
            tuple(sorted({roles[index] for index in group})),
            topology_issue_codes=(
                ("non_manifold_edge",)
                if platform_issue_edges[component_index]
                else ()
            ),
            topology_issue_edge_keys=tuple(sorted(
                platform_issue_edges[component_index]
            )),
        )
        for component_index, group in enumerate(platform_groups)
    )

    face_adjacency = _face_adjacency(shared_edges, roles)
    platform_adjacency = _platform_adjacency(shared_edges, roles)
    boundary_records: list[_BoundaryEdge] = []
    for edge, triangle_indices in shared_edges.items():
        if len(triangle_indices) > 2:
            continue
        face_indices = [
            index for index in triangle_indices if roles[index] == "face"
        ]
        if len(face_indices) != 1:
            continue
        face_triangle_index = face_indices[0]
        other_indices = [
            index for index in triangle_indices if index != face_triangle_index
        ]
        platform_triangle_index = next(
            (
                index for index in other_indices
                if roles[index] in _PLATFORM_ROLES
            ),
            None,
        )
        if len(triangle_indices) == 2 and platform_triangle_index is None:
            source_kind = "face_semantic_rim"
        elif platform_triangle_index is None:
            source_kind = "surface_outer_rim"
        else:
            source_kind = "face_platform"

        boundary_records.append(_BoundaryEdge(
            edge,
            face_triangle_index,
            platform_triangle_index,
            face_component_by_triangle[face_triangle_index],
            (
                platform_component_by_triangle[platform_triangle_index]
                if platform_triangle_index is not None
                else None
            ),
            source_kind,
        ))

    grouped_records: dict[
        tuple[int, int | None, str], list[_BoundaryEdge]
    ] = defaultdict(list)
    for record in boundary_records:
        grouped_records[(
            record.face_component_index,
            record.platform_component_index,
            record.source_kind,
        )].append(record)

    portal_specs: list[TransitionPortal] = []
    for group_key in sorted(
        grouped_records,
        key=lambda key: (
            key[0],
            -1 if key[1] is None else key[1],
            key[2],
        ),
    ):
        records = tuple(sorted(
            grouped_records[group_key],
            key=lambda record: _edge_geometry_key(surface, record.edge),
        ))
        vertex_degree: dict[int, int] = defaultdict(int)
        for record in records:
            vertex_degree[record.edge[0]] += 1
            vertex_degree[record.edge[1]] += 1
        branch_vertices = tuple(sorted(
            (
                _vertex_key(surface.vertices[index])
                for index, degree in vertex_degree.items()
                if degree > 2
            )
        ))
        if branch_vertices:
            diagnostics.append(TopologyDiagnostic(
                "ambiguous_transition_portal_branch",
                "Transition boundary contains a topological branch",
                tuple((key,) for key in branch_vertices),
            ))
        for points, chain_records in _boundary_chains(surface, records):
            face_triangles = {
                record.face_triangle_index for record in chain_records
            }
            support = _local_support_for_face_triangles(
                face_triangles, face_adjacency, evidence_by_triangle
            )
            edge_sides = tuple(
                _classify_boundary_edge_side(
                    surface,
                    record.edge,
                    record.face_triangle_index,
                    support,
                )
                for record in chain_records
            )
            provisional_side = _classify_portal_side(
                surface, chain_records, edge_sides
            )
            portal_specs.append(TransitionPortal(
                "",
                points,
                group_key[0],
                group_key[1],
                provisional_side,
                group_key[2],
                tuple(PortalEdgeProvenance(
                    _edge_geometry_key(surface, record.edge),
                    _triangle_geometry_key(surface, record.face_triangle_index),
                    (
                        _triangle_geometry_key(
                            surface, record.platform_triangle_index
                        )
                        if record.platform_triangle_index is not None
                        else None
                    ),
                    record.source_kind,
                    edge_side,
                )
                for record, edge_side in zip(
                    chain_records, edge_sides, strict=True
                )),
                tuple(sorted(
                    face_triangles,
                    key=lambda index: _triangle_sort_key(surface, roles, index),
                )),
                tuple(sorted(
                    {
                        record.platform_triangle_index
                        for record in chain_records
                        if record.platform_triangle_index is not None
                    },
                    key=lambda index: _triangle_sort_key(surface, roles, index),
                )),
                support,
            ))

    portal_specs.sort(key=lambda portal: (
        portal.geometry_key,
        portal.face_component_index,
        -1 if portal.platform_component_index is None else portal.platform_component_index,
        portal.provisional_side,
        portal.source_kind,
        tuple(
            (edge.edge_key, edge.provisional_side)
            for edge in portal.edge_provenance
        ),
    ))
    portals = tuple(
        replace(portal, portal_id=f"portal:{index}")
        for index, portal in enumerate(portal_specs)
    )

    face_components = tuple(replace(
        component,
        portal_ids=tuple(
            portal.portal_id
            for portal in portals
            if portal.face_component_index == component.canonical_index
        ),
        outer_rim_portal_ids=tuple(
            portal.portal_id
            for portal in portals
            if portal.face_component_index == component.canonical_index
            and portal.platform_component_index is None
        ),
    ) for component in face_components)
    platform_components = tuple(replace(
        component,
        portal_ids=tuple(
            portal.portal_id
            for portal in portals
            if portal.platform_component_index == component.canonical_index
        ),
    ) for component in platform_components)

    # A shared endpoint is provenance only.  It never creates component edges.
    triangles_by_vertex: dict[int, list[int]] = defaultdict(list)
    edges_by_vertex: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for edge in shared_edges:
        edges_by_vertex[edge[0]].append(edge)
        edges_by_vertex[edge[1]].append(edge)
    for triangle_index, triangle in enumerate(surface.triangles):
        for vertex_index in triangle.vertex_indices:
            triangles_by_vertex[vertex_index].append(triangle_index)
    endpoint_diagnostics: set[tuple[VertexKey, tuple[str, str]]] = set()
    for vertex_index, triangle_indices in triangles_by_vertex.items():
        memberships: set[str] = set()
        for triangle_index in triangle_indices:
            if triangle_index in face_component_by_triangle:
                memberships.add(
                    f"face:{face_component_by_triangle[triangle_index]}"
                )
            elif triangle_index in platform_component_by_triangle:
                memberships.add(
                    f"platform:{platform_component_by_triangle[triangle_index]}"
                )
        for first, second in combinations(sorted(memberships), 2):
            has_shared_edge = any(
                len(indices) == 2
                and {
                    (
                        f"face:{face_component_by_triangle[index]}"
                        if index in face_component_by_triangle
                        else f"platform:{platform_component_by_triangle[index]}"
                    )
                    for index in indices
                    if index in face_component_by_triangle
                    or index in platform_component_by_triangle
                } == {first, second}
                for edge in edges_by_vertex[vertex_index]
                for indices in (shared_edges[edge],)
            )
            if not has_shared_edge:
                endpoint_diagnostics.add((
                    _vertex_key(surface.vertices[vertex_index]),
                    (first, second),
                ))
    for vertex_key, related_ids in sorted(endpoint_diagnostics):
        diagnostics.append(TopologyDiagnostic(
            "shared_endpoint_without_shared_edge",
            "Components meet at one stored vertex but are not edge-connected",
            ((vertex_key,),),
            related_ids,
        ))

    portal_by_id = {portal.portal_id: portal for portal in portals}
    connections: list[CorridorConnection] = []
    for platform_component in platform_components:
        component_portals = tuple(
            portal_by_id[portal_id]
            for portal_id in platform_component.portal_ids
        )
        local_connections = _platform_local_connection_candidates(
            surface,
            platform_component,
            component_portals,
            platform_adjacency,
            roles,
        )
        connections.extend(local_connections)
        diagnostics.extend(
            TopologyDiagnostic(
                "ambiguous_local_run_correspondence",
                "Parent portals contain competing compatible local run pairs",
                related_ids=(
                    connection.source_portal_id,
                    connection.target_portal_id,
                ),
            )
            for connection in local_connections
            if connection.status == "ambiguous"
        )

    connections.sort(key=lambda connection: (
        connection.platform_component_index,
        connection.source_portal_id,
        connection.target_portal_id,
    ))
    connections = [
        replace(connection, connection_id=f"connection:{index}")
        for index, connection in enumerate(connections)
    ]

    plausible = [
        connection for connection in connections
        if connection.status in {"compatible", "ambiguous"}
    ]
    successors: dict[str, list[str]] = defaultdict(list)
    predecessors: dict[str, list[str]] = defaultdict(list)
    for connection in plausible:
        successors[connection.source_portal_id].append(connection.connection_id)
        predecessors[connection.target_portal_id].append(connection.connection_id)
    ambiguous_ids = {
        connection_id
        for connection_ids in (*successors.values(), *predecessors.values())
        if len(connection_ids) > 1
        for connection_id in connection_ids
    }
    if ambiguous_ids:
        connections = [
            replace(
                connection,
                status="ambiguous",
                reason="competing locally compatible portal continuation",
            )
            if connection.connection_id in ambiguous_ids
            else connection
            for connection in connections
        ]
        for portal_id, connection_ids in sorted(successors.items()):
            if len(connection_ids) > 1:
                diagnostics.append(TopologyDiagnostic(
                    "ambiguous_corridor_successors",
                    "Downstream Face portal has competing compatible successors",
                    related_ids=(portal_id, *tuple(sorted(connection_ids))),
                ))
        for portal_id, connection_ids in sorted(predecessors.items()):
            if len(connection_ids) > 1:
                diagnostics.append(TopologyDiagnostic(
                    "ambiguous_corridor_predecessors",
                    "Upstream Face portal has competing compatible predecessors",
                    related_ids=(portal_id, *tuple(sorted(connection_ids))),
                ))

    return DesignTopologyIndex(
        roles,
        face_components,
        platform_components,
        portals,
        face_direction_samples,
        tuple(connections),
        DesignTopologyDiagnostics(tuple(sorted(
            diagnostics, key=_diagnostic_sort_key
        ))),
    )


__all__ = [
    "ConnectionStatus",
    "CorridorConnection",
    "DesignTopologyDiagnostics",
    "DesignTopologyIndex",
    "FaceComponent",
    "FaceDirectionEvidence",
    "FaceDirectionSupport",
    "LocalPortalRunPair",
    "PlatformComponent",
    "PortalEdgeProvenance",
    "PortalSide",
    "TopologyDiagnostic",
    "TransitionPortal",
    "build_design_topology_index",
]
