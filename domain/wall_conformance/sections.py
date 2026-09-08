from __future__ import annotations

from math import hypot

from domain.geometry.surfaces import SurfaceVertex, TriangleSurface
from domain.wall_conformance.models import (
    SectionPoint,
    SectionSegment,
    SurfaceRoleMapping,
    WallAlignmentSample,
)


def section_points_close(
    first: SectionPoint, second: SectionPoint, *, tolerance: float = 1e-5
) -> bool:
    """Return whether points coincide in the physical ``(U, Z)`` section."""
    return hypot(first.u - second.u, first.z - second.z) <= tolerance


def connected_section_segments(
    segments: tuple[SectionSegment, ...],
    origin: SectionPoint,
    *,
    tolerance: float = 1e-5,
) -> tuple[SectionSegment, ...]:
    """Return the endpoint-connected component incident to ``origin``."""
    remaining = list(segments)
    connected = []
    frontier = [origin]
    while frontier:
        point = frontier.pop()
        touching = [
            segment for segment in remaining
            if section_points_close(segment.start, point, tolerance=tolerance)
            or section_points_close(segment.end, point, tolerance=tolerance)
        ]
        for segment in touching:
            remaining.remove(segment)
            connected.append(segment)
            frontier.extend((segment.start, segment.end))
    return tuple(connected)


def _same_point(a: SurfaceVertex, b: SurfaceVertex, tolerance: float) -> bool:
    return max(abs(a.x - b.x), abs(a.y - b.y), abs(a.z - b.z)) <= tolerance


def _append_unique(points: list[SurfaceVertex], point: SurfaceVertex, tolerance: float) -> None:
    if not any(_same_point(point, existing, tolerance) for existing in points):
        points.append(point)


def _plane_distance(vertex: SurfaceVertex, alignment: WallAlignmentSample) -> float:
    tx, ty = alignment.tangent_xy
    return (vertex.x - alignment.origin.x) * tx + (vertex.y - alignment.origin.y) * ty


def _interpolate(first: SurfaceVertex, second: SurfaceVertex, fraction: float) -> SurfaceVertex:
    return SurfaceVertex(
        first.x + (second.x - first.x) * fraction,
        first.y + (second.y - first.y) * fraction,
        first.z + (second.z - first.z) * fraction,
    )


def _triangle_plane_points(
    vertices: tuple[SurfaceVertex, SurfaceVertex, SurfaceVertex],
    alignment: WallAlignmentSample,
    tolerance: float,
) -> tuple[SurfaceVertex, ...]:
    distances = tuple(_plane_distance(vertex, alignment) for vertex in vertices)
    if all(distance > tolerance for distance in distances) or all(
        distance < -tolerance for distance in distances
    ):
        return ()

    points: list[SurfaceVertex] = []
    for vertex, distance in zip(vertices, distances):
        if abs(distance) <= tolerance:
            _append_unique(points, vertex, tolerance)

    for first_index, second_index in ((0, 1), (1, 2), (2, 0)):
        first_distance = distances[first_index]
        second_distance = distances[second_index]
        if first_distance * second_distance >= 0:
            continue
        fraction = first_distance / (first_distance - second_distance)
        _append_unique(
            points,
            _interpolate(vertices[first_index], vertices[second_index], fraction),
            tolerance,
        )
    return tuple(points)


def _section_point(vertex: SurfaceVertex, alignment: WallAlignmentSample) -> SectionPoint:
    nx, ny = alignment.normal_xy
    u = (vertex.x - alignment.origin.x) * nx + (vertex.y - alignment.origin.y) * ny
    return SectionPoint(u=u, z=vertex.z, x=vertex.x, y=vertex.y)


def _farthest_pair(
    points: tuple[SurfaceVertex, ...],
    alignment: WallAlignmentSample,
) -> tuple[SectionPoint, SectionPoint] | None:
    section_points = tuple(_section_point(point, alignment) for point in points)
    if len(section_points) < 2:
        return None
    best = None
    best_distance_sq = -1.0
    for index, first in enumerate(section_points):
        for second in section_points[index + 1 :]:
            distance_sq = (second.u - first.u) ** 2 + (second.z - first.z) ** 2
            if distance_sq > best_distance_sq:
                best_distance_sq = distance_sq
                best = (first, second)
    return best


def _clip_to_half_width(
    first: SectionPoint,
    second: SectionPoint,
    half_width_m: float,
) -> tuple[SectionPoint, SectionPoint] | None:
    lower, upper = -half_width_m, half_width_m
    if first.u < lower and second.u < lower:
        return None
    if first.u > upper and second.u > upper:
        return None

    source_start, source_end = first, second
    du = source_end.u - source_start.u
    if abs(du) <= 1e-12:
        return (
            (source_start, source_end)
            if lower <= source_start.u <= upper
            else None
        )

    def at_u(target: float) -> SectionPoint:
        fraction = (target - source_start.u) / du
        return SectionPoint(
            u=target,
            z=source_start.z + (source_end.z - source_start.z) * fraction,
            x=source_start.x + (source_end.x - source_start.x) * fraction,
            y=source_start.y + (source_end.y - source_start.y) * fraction,
        )

    start, end = source_start, source_end
    if start.u < lower:
        start = at_u(lower)
    elif start.u > upper:
        start = at_u(upper)
    if end.u < lower:
        end = at_u(lower)
    elif end.u > upper:
        end = at_u(upper)
    return start, end


def intersect_surface_with_profile(
    surface: TriangleSurface,
    alignment: WallAlignmentSample,
    *,
    role_mapping: SurfaceRoleMapping | None = None,
    half_width_m: float | None = None,
    tolerance: float = 1e-8,
) -> tuple[SectionSegment, ...]:
    """Intersect a vertical plane normal to the local design wall with a mesh."""
    if half_width_m is not None and half_width_m <= 0:
        raise ValueError("Profile half width must be positive")

    segments: list[SectionSegment] = []
    for triangle_index, triangle in enumerate(surface.triangles):
        vertices = tuple(surface.vertices[index] for index in triangle.vertex_indices)
        intersection = _triangle_plane_points(vertices, alignment, tolerance)
        pair = _farthest_pair(intersection, alignment)
        if pair is None:
            continue
        first, second = pair
        if hypot(second.u - first.u, second.z - first.z) <= tolerance:
            continue
        if half_width_m is not None:
            clipped = _clip_to_half_width(first, second, half_width_m)
            if clipped is None:
                continue
            first, second = clipped
        if (second.u, second.z, second.x, second.y) < (
            first.u,
            first.z,
            first.x,
            first.y,
        ):
            first, second = second, first
        role = role_mapping.resolve(triangle.source_attributes) if role_mapping else None
        segments.append(
            SectionSegment(
                start=first,
                end=second,
                source_triangle_index=triangle_index,
                semantic_role=role,
            )
        )
    return tuple(
        sorted(
            segments,
            key=lambda segment: (
                segment.u_min,
                segment.u_max,
                segment.start.z,
                segment.end.z,
                segment.source_triangle_index,
            ),
        )
    )


def _point_at_u(
    first: SectionPoint,
    second: SectionPoint,
    u: float,
) -> SectionPoint:
    span = second.u - first.u
    fraction = 0.0 if abs(span) <= 1e-12 else (u - first.u) / span
    return SectionPoint(
        u,
        first.z + (second.z - first.z) * fraction,
        first.x + (second.x - first.x) * fraction,
        first.y + (second.y - first.y) * fraction,
    )


def clip_section_segments_to_u_interval(
    segments: tuple[SectionSegment, ...],
    u_min: float,
    u_max: float,
    *,
    tolerance: float = 1e-9,
) -> tuple[SectionSegment, ...]:
    """Clip finite section segments to an evaluated transverse corridor."""
    if u_max < u_min:
        raise ValueError("Section U maximum must be greater than its minimum")
    clipped = []
    for segment in segments:
        if (
            segment.u_max < u_min - tolerance
            or segment.u_min > u_max + tolerance
        ):
            continue
        start, end = segment.start, segment.end
        if start.u > end.u:
            start, end = end, start
        source_start, source_end = start, end
        if source_start.u < u_min:
            start = _point_at_u(source_start, source_end, u_min)
        if source_end.u > u_max:
            end = _point_at_u(source_start, source_end, u_max)
        if hypot(end.u - start.u, end.z - start.z) <= tolerance:
            continue
        clipped.append(SectionSegment(
            start,
            end,
            segment.source_triangle_index,
            segment.semantic_role,
        ))
    return tuple(clipped)


def clip_section_segments_to_z_range(
    segments: tuple[SectionSegment, ...],
    z_min: float,
    z_max: float,
    *,
    tolerance: float = 1e-7,
) -> tuple[SectionSegment, ...]:
    """Clip section segments to the exact local Design elevation envelope."""
    if z_max < z_min:
        raise ValueError("Section Z maximum must be greater than its minimum")
    clipped = []
    lower, upper = z_min - tolerance, z_max + tolerance
    for segment in segments:
        source_first, source_second = segment.start, segment.end
        if max(source_first.z, source_second.z) < lower or min(
            source_first.z, source_second.z
        ) > upper:
            continue
        dz = source_second.z - source_first.z

        def at_z(z):
            fraction = (z - source_first.z) / dz
            return SectionPoint(
                source_first.u + (source_second.u - source_first.u) * fraction,
                z,
                source_first.x + (source_second.x - source_first.x) * fraction,
                source_first.y + (source_second.y - source_first.y) * fraction,
            )

        first, second = source_first, source_second
        if abs(dz) > 1e-12:
            if first.z < lower:
                first = at_z(z_min)
            elif first.z > upper:
                first = at_z(z_max)
            if second.z < lower:
                second = at_z(z_min)
            elif second.z > upper:
                second = at_z(z_max)
        clipped.append(
            SectionSegment(
                first, second, segment.source_triangle_index, segment.semantic_role
            )
        )
    return tuple(clipped)
