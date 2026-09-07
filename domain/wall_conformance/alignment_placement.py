"""Wall Conformance placement from an explicit longitudinal Wall Alignment.

This path deliberately bypasses automatic ``WallSector`` discovery.  The
alignment supplies chainage and approximate locality only.  Semantic Design
Face triangles supply the physical downwall azimuth, and the Design TIN
intersection supplies the final Upper Crest anchor after the vertical search
plane has been placed.

Assessment geometry is used only as a spatial support mask.  Actual geometry
is accepted only by section assembly, after every placement decision exists.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil, fsum, hypot, isfinite, sqrt

from domain.geometry.operations import (
    point_in_polygon,
    segment_intersection,
    validate_simple_polygon,
)
from domain.geometry.surfaces import SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance.invariants import profile_vertical_order_issue
from domain.wall_conformance.measurement_context import build_measurement_context
from domain.wall_conformance.models import (
    DesignSection,
    DesignSectionElement,
    DesignVariant,
    SectionPoint,
    SectionSegment,
    SurfaceRoleMapping,
    TransverseProfile,
    WallAlignmentSample,
)
from domain.wall_conformance.profile_placement import (
    FaceDirectionSample,
    aggregate_face_direction,
    direction_sample_from_triangle,
)
from domain.wall_conformance.sections import (
    clip_section_segments_to_u_interval,
    clip_section_segments_to_z_range,
    connected_section_segments,
    intersect_surface_with_profile,
)
from domain.wall_conformance.semantic_sections import (
    build_design_section,
    build_design_variants,
)


_GEOMETRY_TOLERANCE = 1e-9
_SECTION_TOLERANCE = 1e-6


def _distance(first: PlanPoint, second: PlanPoint) -> float:
    return hypot(second.x - first.x, second.y - first.y)


def _unit(vector: tuple[float, float]) -> tuple[float, float]:
    length = hypot(*vector)
    if length <= _GEOMETRY_TOLERANCE:
        raise ValueError("Direction must have non-zero plan length")
    return vector[0] / length, vector[1] / length


def _dot(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[0] + first[1] * second[1]


def _point_key(point: PlanPoint) -> tuple[float, float]:
    return point.x, point.y


def _signed_area(points: tuple[PlanPoint, ...]) -> float:
    return fsum(
        first.x * second.y - second.x * first.y
        for first, second in zip(points, points[1:] + points[:1])
    ) / 2.0


def _area(points: tuple[PlanPoint, ...]) -> float:
    return abs(_signed_area(points))


def _canonical_ccw(points: tuple[PlanPoint, ...]) -> tuple[PlanPoint, ...]:
    ordered = tuple(reversed(points)) if _signed_area(points) < 0.0 else points
    seam = min(range(len(ordered)), key=lambda index: _point_key(ordered[index]))
    return ordered[seam:] + ordered[:seam]


def _cross(first: PlanPoint, second: PlanPoint, point: PlanPoint) -> float:
    return (
        (second.x - first.x) * (point.y - first.y)
        - (second.y - first.y) * (point.x - first.x)
    )


def _point_in_triangle(
    point: PlanPoint,
    triangle: tuple[PlanPoint, PlanPoint, PlanPoint],
) -> bool:
    return all(
        _cross(first, second, point) >= -_GEOMETRY_TOLERANCE
        for first, second in zip(triangle, triangle[1:] + triangle[:1])
    )


def _assessment_triangles(
    polygon: PlanPolygon,
) -> tuple[tuple[PlanPoint, PlanPoint, PlanPoint], ...]:
    """Triangulate a simple Assessment polygon deterministically."""
    validate_simple_polygon(polygon)
    remaining = list(_canonical_ccw(polygon.ring[:-1]))
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
            adjacent = {
                (index - 1) % len(remaining),
                index,
                (index + 1) % len(remaining),
            }
            if any(
                _point_in_triangle(candidate, triangle)
                for candidate_index, candidate in enumerate(remaining)
                if candidate_index not in adjacent
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
    triangles.append(_canonical_ccw(tuple(remaining)))
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
        if not result or _distance(result[-1], point) > _GEOMETRY_TOLERANCE:
            result.append(point)
    if len(result) > 1 and _distance(result[0], result[-1]) <= _GEOMETRY_TOLERANCE:
        result.pop()
    return tuple(result)


def _clip_to_triangle(
    subject: tuple[PlanPoint, ...],
    clip_triangle: tuple[PlanPoint, PlanPoint, PlanPoint],
) -> tuple[PlanPoint, ...]:
    output = subject
    for clip_start, clip_end in zip(
        clip_triangle, clip_triangle[1:] + clip_triangle[:1]
    ):
        if not output:
            break
        clipped: list[PlanPoint] = []
        previous = output[-1]
        previous_inside = (
            _cross(clip_start, clip_end, previous) >= -_GEOMETRY_TOLERANCE
        )
        for current in output:
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


@dataclass(frozen=True)
class WallAlignment:
    """A user-supplied longitudinal plan polyline, with no side authority."""

    points: tuple[PlanPoint, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("Wall Alignment requires at least two points")
        if not all(isfinite(point.x) and isfinite(point.y) for point in self.points):
            raise ValueError("Wall Alignment coordinates must be finite")
        cleaned: list[PlanPoint] = []
        for point in self.points:
            if not cleaned or _distance(cleaned[-1], point) > _GEOMETRY_TOLERANCE:
                cleaned.append(point)
        if len(cleaned) < 2:
            raise ValueError("Wall Alignment must have non-zero plan length")
        object.__setattr__(self, "points", tuple(cleaned))

    @property
    def cumulative_chainages_m(self) -> tuple[float, ...]:
        values = [0.0]
        for first, second in zip(self.points, self.points[1:]):
            values.append(values[-1] + _distance(first, second))
        return tuple(values)

    @property
    def length_m(self) -> float:
        return self.cumulative_chainages_m[-1]

    def point_and_tangent_at(
        self, chainage_m: float
    ) -> tuple[PlanPoint, tuple[float, float]]:
        chainage = max(0.0, min(self.length_m, chainage_m))
        cumulative = self.cumulative_chainages_m
        for index, vertex_chainage in enumerate(cumulative):
            if abs(chainage - vertex_chainage) > _GEOMETRY_TOLERANCE:
                continue
            point = self.points[index]
            if index == 0:
                following = self.points[1]
                return point, _unit((following.x - point.x, following.y - point.y))
            if index == len(self.points) - 1:
                previous = self.points[index - 1]
                return point, _unit((point.x - previous.x, point.y - previous.y))
            previous = self.points[index - 1]
            following = self.points[index + 1]
            incoming = _unit((point.x - previous.x, point.y - previous.y))
            outgoing = _unit((following.x - point.x, following.y - point.y))
            try:
                tangent = _unit((
                    incoming[0] + outgoing[0],
                    incoming[1] + outgoing[1],
                ))
            except ValueError as exc:
                raise ValueError(
                    "Wall Alignment reverses direction at a sampled vertex"
                ) from exc
            return point, tangent

        for index, (start, end) in enumerate(zip(cumulative, cumulative[1:])):
            if chainage < end + _GEOMETRY_TOLERANCE:
                span = end - start
                fraction = (chainage - start) / span
                first, second = self.points[index], self.points[index + 1]
                return (
                    PlanPoint(
                        first.x + (second.x - first.x) * fraction,
                        first.y + (second.y - first.y) * fraction,
                    ),
                    _unit((second.x - first.x, second.y - first.y)),
                )
        raise AssertionError("Clamped Wall Alignment chainage was not located")

    def nearest_chainage_to(self, point: PlanPoint) -> float:
        """Project a plan point to deterministic nearest Alignment chainage."""
        cumulative = self.cumulative_chainages_m
        candidates = []
        for index, (first, second) in enumerate(zip(self.points, self.points[1:])):
            dx, dy = second.x - first.x, second.y - first.y
            length_squared = dx * dx + dy * dy
            fraction = (
                ((point.x - first.x) * dx + (point.y - first.y) * dy)
                / length_squared
            )
            fraction = max(0.0, min(1.0, fraction))
            projected_x = first.x + dx * fraction
            projected_y = first.y + dy * fraction
            distance_squared = (
                (point.x - projected_x) ** 2
                + (point.y - projected_y) ** 2
            )
            chainage = cumulative[index] + sqrt(length_squared) * fraction
            candidates.append((distance_squared, chainage, index))
        return min(candidates)[1]


@dataclass(frozen=True)
class AlignmentPlacementDiagnostic:
    code: str
    message: str
    station_index: int | None = None
    chainage_m: float | None = None
    source: str = "alignment_placement"


@dataclass(frozen=True)
class AlignmentProfilePlacement:
    """One straight, unanchored vertical plane placed from Alignment + Face."""

    station_index: int
    chainage_m: float
    alignment_point: PlanPoint
    guide_tangent_xy: tuple[float, float]
    wall_strike_xy: tuple[float, float]
    downwall_xy: tuple[float, float]
    face_downwall_xy: tuple[float, float]
    face_agreement: float
    face_angular_dispersion_degrees: float
    face_angular_support_degrees: float
    face_sample_count: int
    face_supporting_weight: float
    plan_start: PlanPoint
    plan_end: PlanPoint
    supporting_face_triangle_indices: tuple[int, ...]


@dataclass(frozen=True)
class AlignmentProfilePlacementResult:
    placements: tuple[AlignmentProfilePlacement, ...]
    diagnostics: tuple[AlignmentPlacementDiagnostic, ...]
    requested_spacing_m: float
    station_chainages_m: tuple[float, ...]

    @property
    def supported(self) -> bool:
        return bool(self.placements) and len(self.placements) == len(
            self.station_chainages_m
        )


@dataclass(frozen=True)
class AlignmentProfileSectionResult:
    profiles: tuple[TransverseProfile, ...]
    design_variants: tuple[DesignVariant, ...]
    placement_result: AlignmentProfilePlacementResult
    diagnostics: tuple[AlignmentPlacementDiagnostic, ...]


@dataclass(frozen=True)
class _FaceFragment:
    triangle_index: int
    points: tuple[PlanPoint, ...]
    representative_point: PlanPoint
    downwall_xy: tuple[float, float]
    weight: float


def _polygon_centroid(points: tuple[PlanPoint, ...]) -> PlanPoint:
    """Return the deterministic area centroid of a positive-area polygon."""
    cross_terms = tuple(
        first.x * second.y - second.x * first.y
        for first, second in zip(points, points[1:] + points[:1])
    )
    scale = fsum(cross_terms)
    if abs(scale) <= _GEOMETRY_TOLERANCE:
        raise ValueError("Face fragment has no stable plan centroid")
    return PlanPoint(
        fsum(
            (first.x + second.x) * cross
            for first, second, cross in zip(
                points, points[1:] + points[:1], cross_terms
            )
        ) / (3.0 * scale),
        fsum(
            (first.y + second.y) * cross
            for first, second, cross in zip(
                points, points[1:] + points[:1], cross_terms
            )
        ) / (3.0 * scale),
    )


def _face_fragments(
    design_surface: TriangleSurface,
    assessment_polygon: PlanPolygon,
    role_mapping: SurfaceRoleMapping,
) -> tuple[_FaceFragment, ...]:
    assessment_triangles = _assessment_triangles(assessment_polygon)
    fragments: list[_FaceFragment] = []
    for triangle_index, triangle in enumerate(design_surface.triangles):
        if role_mapping.resolve(triangle.source_attributes) != "face":
            continue
        vertices = tuple(
            design_surface.vertices[index] for index in triangle.vertex_indices
        )
        plan_triangle = _canonical_ccw(tuple(
            PlanPoint(vertex.x, vertex.y) for vertex in vertices
        ))
        plan_area = _area(plan_triangle)
        if plan_area <= _GEOMETRY_TOLERANCE:
            continue
        try:
            direction = direction_sample_from_triangle(
                vertices,
                station_fraction=0.0,
                source_id=triangle.source_id or f"triangle:{triangle_index}",
            )
        except ValueError:
            continue
        for assessment_triangle in assessment_triangles:
            clipped = _clip_to_triangle(plan_triangle, assessment_triangle)
            if len(clipped) < 3:
                continue
            overlap_area = _area(clipped)
            area_tolerance = max(1.0, plan_area) * 1e-10
            if overlap_area <= area_tolerance:
                continue
            canonical_clipped = _canonical_ccw(clipped)
            fragments.append(_FaceFragment(
                triangle_index,
                canonical_clipped,
                _polygon_centroid(canonical_clipped),
                direction.downwall_xy,  # type: ignore[arg-type]
                direction.geometric_weight * overlap_area / plan_area,
            ))
    return tuple(sorted(
        fragments,
        key=lambda item: (
            item.triangle_index,
            tuple(_point_key(point) for point in item.points),
        ),
    ))


def _crossing_fragments(
    fragments: tuple[_FaceFragment, ...],
    point: PlanPoint,
    tangent: tuple[float, float],
) -> tuple[_FaceFragment, ...]:
    selected = []
    for fragment in fragments:
        offsets = tuple(
            (vertex.x - point.x) * tangent[0]
            + (vertex.y - point.y) * tangent[1]
            for vertex in fragment.points
        )
        if min(offsets) <= _GEOMETRY_TOLERANCE and max(offsets) >= -_GEOMETRY_TOLERANCE:
            selected.append(fragment)
    return tuple(selected)


def _face_direction_samples(
    fragments: tuple[_FaceFragment, ...],
    alignment: WallAlignment,
    station_chainages_m: tuple[float, ...],
) -> tuple[FaceDirectionSample, ...]:
    """Map clipped Face evidence into deterministic requested-station bins."""
    samples = []
    for fragment in fragments:
        projected_chainage = alignment.nearest_chainage_to(
            fragment.representative_point
        )
        _, station_chainage = min(
            enumerate(station_chainages_m),
            key=lambda item: (
                abs(item[1] - projected_chainage),
                item[1],
                item[0],
            ),
        )
        samples.append(FaceDirectionSample(
            fragment.representative_point,
            max(0.0, min(1.0, station_chainage / alignment.length_m)),
            fragment.downwall_xy,
            fragment.weight,
            source_id=f"triangle:{fragment.triangle_index}",
        ))
    return tuple(samples)


def _wall_strike(
    downwall_xy: tuple[float, float],
    guide_tangent_xy: tuple[float, float],
) -> tuple[float, float]:
    """Derive physical strike; guide direction selects its sign only."""
    candidate = downwall_xy[1], -downwall_xy[0]
    if _dot(candidate, guide_tangent_xy) < 0.0:
        return -candidate[0], -candidate[1]
    return candidate


def _search_extent(
    point: PlanPoint,
    normal: tuple[float, float],
    design_surface: TriangleSurface,
    assessment_polygon: PlanPolygon,
) -> float:
    projections = tuple(
        abs((candidate.x - point.x) * normal[0] + (candidate.y - point.y) * normal[1])
        for candidate in (
            *(PlanPoint(vertex.x, vertex.y) for vertex in design_surface.vertices),
            *assessment_polygon.ring[:-1],
        )
    )
    extent = max(projections, default=0.0)
    return extent + max(1.0, extent * 0.02)


def _proper_crossing_in_assessment(
    first: AlignmentProfilePlacement,
    second: AlignmentProfilePlacement,
    assessment_polygon: PlanPolygon,
) -> bool:
    intersection = segment_intersection(
        first.plan_start,
        first.plan_end,
        second.plan_start,
        second.plan_end,
    )
    if intersection is None or not point_in_polygon(intersection, assessment_polygon):
        return False
    return all(
        _distance(intersection, endpoint) > _GEOMETRY_TOLERANCE
        for endpoint in (
            first.plan_start,
            first.plan_end,
            second.plan_start,
            second.plan_end,
        )
    )


def place_profiles_from_alignment(
    *,
    alignment: WallAlignment,
    design_surface: TriangleSurface,
    assessment_polygon: PlanPolygon,
    role_mapping: SurfaceRoleMapping,
    spacing_m: float,
) -> AlignmentProfilePlacementResult:
    """Place straight profile planes without automatic corridor discovery."""
    if not isfinite(spacing_m) or spacing_m <= 0.0:
        raise ValueError("Wall Alignment profile spacing must be positive")
    interval_count = max(1, ceil(alignment.length_m / spacing_m))
    chainages = tuple(
        alignment.length_m * index / interval_count
        for index in range(interval_count + 1)
    )
    fragments = _face_fragments(design_surface, assessment_polygon, role_mapping)
    direction_samples = _face_direction_samples(fragments, alignment, chainages)
    placements: list[AlignmentProfilePlacement] = []
    diagnostics: list[AlignmentPlacementDiagnostic] = []
    for station_index, chainage in enumerate(chainages):
        try:
            point, guide_tangent = alignment.point_and_tangent_at(chainage)
        except ValueError as exc:
            diagnostics.append(AlignmentPlacementDiagnostic(
                "unstable_alignment_tangent", str(exc), station_index, chainage
            ))
            continue
        if not direction_samples:
            diagnostics.append(AlignmentPlacementDiagnostic(
                "insufficient_face_support",
                "Assessment Area contains no positive-area Design Face support",
                station_index,
                chainage,
            ))
            continue
        try:
            aggregate = aggregate_face_direction(
                direction_samples,
                chainage / alignment.length_m,
                assessment_polygon,
            )
        except ValueError:
            diagnostics.append(AlignmentPlacementDiagnostic(
                "contradictory_face_direction",
                "Local Design Face evidence has no stable physical downwall direction",
                station_index,
                chainage,
            ))
            continue
        if aggregate.angular_support_degrees >= 90.0 - _GEOMETRY_TOLERANCE:
            diagnostics.append(AlignmentPlacementDiagnostic(
                "contradictory_face_direction",
                (
                    "Local Design Face evidence does not share a common physical "
                    "downwall hemisphere"
                ),
                station_index,
                chainage,
            ))
            continue
        downwall = aggregate.downwall_xy
        wall_strike = _wall_strike(downwall, guide_tangent)
        support = _crossing_fragments(fragments, point, wall_strike)
        if not support:
            diagnostics.append(AlignmentPlacementDiagnostic(
                "insufficient_final_face_support",
                (
                    "The Design-Face-derived profile line crosses no "
                    "Assessment-supported Design Face"
                ),
                station_index,
                chainage,
            ))
            continue
        extent = _search_extent(
            point, downwall, design_surface, assessment_polygon
        )
        placement = AlignmentProfilePlacement(
            station_index,
            chainage,
            point,
            guide_tangent,
            wall_strike,
            downwall,
            aggregate.downwall_xy,
            1.0,
            aggregate.angular_dispersion_degrees,
            aggregate.angular_support_degrees,
            aggregate.sample_count,
            aggregate.supporting_weight,
            PlanPoint(point.x - downwall[0] * extent, point.y - downwall[1] * extent),
            PlanPoint(point.x + downwall[0] * extent, point.y + downwall[1] * extent),
            tuple(sorted({fragment.triangle_index for fragment in support})),
        )
        crossing = next(
            (
                previous
                for previous in reversed(placements)
                if _proper_crossing_in_assessment(
                    previous, placement, assessment_polygon
                )
            ),
            None,
        )
        if crossing is not None:
            diagnostics.append(AlignmentPlacementDiagnostic(
                "profile_crossing_in_assessment",
                (
                    "Profile crosses the accepted plane at Alignment station "
                    f"{crossing.station_index} inside the Assessment Area"
                ),
                station_index,
                chainage,
            ))
            continue
        placements.append(placement)
    return AlignmentProfilePlacementResult(
        tuple(placements), tuple(diagnostics), spacing_m, chainages
    )


def _section_components(
    segments: tuple[SectionSegment, ...],
) -> tuple[tuple[SectionSegment, ...], ...]:
    remaining = list(segments)
    components = []
    while remaining:
        component = connected_section_segments(tuple(remaining), remaining[0].start)
        components.append(component)
        for segment in component:
            remaining.remove(segment)
    return tuple(components)


def _shift_point(point: SectionPoint, offset_u: float) -> SectionPoint:
    return SectionPoint(
        point.u - offset_u,
        point.z,
        point.x,
        point.y,
    )


def _shift_segments(
    segments: tuple[SectionSegment, ...], offset_u: float
) -> tuple[SectionSegment, ...]:
    return tuple(
        SectionSegment(
            _shift_point(segment.start, offset_u),
            _shift_point(segment.end, offset_u),
            segment.source_triangle_index,
            segment.semantic_role,
        )
        for segment in segments
    )


def _assessment_profile_intervals(
    alignment: WallAlignmentSample,
    assessment_polygon: PlanPolygon,
) -> tuple[tuple[float, float], ...]:
    """Return every finite Assessment interval on this final profile line.

    ``alignment`` is already crest-anchored, so each returned U value is in
    the coordinate system stored on ``TransverseProfile``.  Polygon vertices
    on the line are included to retain a deterministic boundary for collinear
    edges; only midpoint-inside spans become evaluated intervals.
    """
    origin = alignment.origin
    nx, ny = alignment.normal_xy
    intersections: list[float] = []
    for first, second in zip(assessment_polygon.ring, assessment_polygon.ring[1:]):
        ex, ey = second.x - first.x, second.y - first.y
        ox, oy = first.x - origin.x, first.y - origin.y
        denominator = nx * ey - ny * ex
        if abs(denominator) > _GEOMETRY_TOLERANCE:
            line_u = (ox * ey - oy * ex) / denominator
            edge_fraction = (ox * ny - oy * nx) / denominator
            if -_GEOMETRY_TOLERANCE <= edge_fraction <= 1.0 + _GEOMETRY_TOLERANCE:
                intersections.append(line_u)
            continue
        for point in (first, second):
            offset_x, offset_y = point.x - origin.x, point.y - origin.y
            if abs(offset_x * ny - offset_y * nx) <= _GEOMETRY_TOLERANCE:
                intersections.append(offset_x * nx + offset_y * ny)
    ordered = []
    for value in sorted(intersections):
        if not ordered or abs(value - ordered[-1]) > _SECTION_TOLERANCE:
            ordered.append(value)
    return tuple(
        (start, end)
        for start, end in zip(ordered, ordered[1:])
        if end - start > _SECTION_TOLERANCE
        and point_in_polygon(
            PlanPoint(
                origin.x + (start + end) * nx / 2.0,
                origin.y + (start + end) * ny / 2.0,
            ),
            assessment_polygon,
        )
    )


def _select_assessment_interval(
    intervals: tuple[tuple[float, float], ...],
    seed_segments: tuple[SectionSegment, ...],
) -> tuple[float, float]:
    """Choose the unique Assessment interval containing a Face support seed."""
    supported = tuple(
        interval
        for interval in intervals
        if any(
            min(segment.u_max, interval[1])
            - max(segment.u_min, interval[0]) > _SECTION_TOLERANCE
            for segment in seed_segments
        )
    )
    if not supported:
        raise ValueError(
            "Assessment Area does not contain the supported Design Face "
            "portion for this profile"
        )
    if len(supported) > 1:
        raise ValueError(
            "Assessment Area contains multiple supported Design Face intervals "
            "for this profile"
        )
    return supported[0]


def _points_are_contiguous(first: SectionPoint, second: SectionPoint) -> bool:
    return (
        abs(first.u - second.u) <= _SECTION_TOLERANCE
        and abs(first.z - second.z) <= _SECTION_TOLERANCE
        and hypot(first.x - second.x, first.y - second.y) <= _SECTION_TOLERANCE
    )


def _upstream_context_for_assessed_section(
    evaluated: DesignSection,
    local_run: tuple[SectionSegment, ...],
    interval: tuple[float, float],
) -> DesignSectionElement | None:
    """Return one contiguous Berm/Road immediately before the first Face.

    The returned element is presentation context only.  It is selected from
    the existing local physical run after the evaluated geometry is clipped,
    so it cannot cross a disconnected component or a reverse-face boundary.
    """
    first_face = next(
        (element for element in evaluated.elements if element.role == "face"),
        None,
    )
    if first_face is None:
        return None
    full = build_design_section(local_run)
    full_face_index = next(
        (
            index
            for index, element in enumerate(full.elements)
            if element.role == "face"
            and element.start.u - _SECTION_TOLERANCE
            <= first_face.start.u
            <= element.end.u + _SECTION_TOLERANCE
            and set(element.source_triangle_indices).intersection(
                first_face.source_triangle_indices
            )
        ),
        None,
    )
    if full_face_index is None:
        return None
    full_face = full.elements[full_face_index]
    # A boundary through a Face remains a partial assessed Face; it does not
    # create a fictional upper Berm/Road context.
    if abs(first_face.start.u - full_face.start.u) > _SECTION_TOLERANCE:
        return None
    preceding = (
        full.elements[full_face_index - 1]
        if full_face_index > 0
        else full.upstream_context
    )
    if preceding is None or preceding.role not in {"berm", "road"}:
        return None
    if not _points_are_contiguous(preceding.end, full_face.start):
        return None
    # If the Assessment already contains any part of this element, it is
    # evaluated geometry, not extra upstream context.
    if preceding.end.u > interval[0] + _SECTION_TOLERANCE:
        return None
    return preceding


def _local_design_run(
    component: tuple[SectionSegment, ...],
    supported_triangle_indices: set[int],
) -> tuple[SectionSegment, ...]:
    """Select the physical 1D wall run containing Assessment Face seeds.

    A site-wide Design TIN can remain endpoint-connected through unrelated pit
    walls.  In section space, a semantic Face that rises with physical ``+U``
    is the boundary between the assessed downwall run and such remote geometry.
    Berm and Road segments do not create boundaries of their own.
    """
    seed_segments = tuple(
        segment
        for segment in component
        if segment.semantic_role == "face"
        and segment.source_triangle_index in supported_triangle_indices
    )
    if not seed_segments:
        raise ValueError("Connected Design section contains no supporting Face")

    minimum_u = min(segment.u_min for segment in component)
    semantic = build_design_section(_shift_segments(component, minimum_u))
    rising_face_triangle_indices = {
        triangle_index
        for element in semantic.elements
        if element.role == "face"
        and element.vertical_change > _SECTION_TOLERANCE
        for triangle_index in element.source_triangle_indices
    }
    if any(
        segment.source_triangle_index in rising_face_triangle_indices
        for segment in seed_segments
    ):
        raise ValueError("Assessment-supported Design Face rises with +U")

    traversable = tuple(
        segment
        for segment in component
        if not (
            segment.semantic_role == "face"
            and segment.source_triangle_index in rising_face_triangle_indices
        )
    )
    seed_set = set(seed_segments)
    candidates = tuple(
        local_component
        for local_component in _section_components(traversable)
        if seed_set.intersection(local_component)
    )
    if not candidates:
        raise ValueError("No local Design wall run contains the supporting Face")
    if len(candidates) > 1 or not seed_set.issubset(candidates[0]):
        raise ValueError(
            "Supporting Face evidence crosses a reverse-slope Design Face"
        )
    return candidates[0]


def _profile_from_placement(
    placement: AlignmentProfilePlacement,
    design_surface: TriangleSurface,
    actual_surface: TriangleSurface | None,
    role_mapping: SurfaceRoleMapping,
    assessment_polygon: PlanPolygon,
) -> TransverseProfile:
    provisional = WallAlignmentSample(
        placement.chainage_m,
        SurfaceVertex(placement.alignment_point.x, placement.alignment_point.y, 0.0),
        placement.wall_strike_xy,
        placement.downwall_xy,
    )
    all_segments = intersect_surface_with_profile(
        design_surface,
        provisional,
        role_mapping=role_mapping,
        half_width_m=_distance(
            placement.alignment_point, placement.plan_end
        ),
    )
    supported = set(placement.supporting_face_triangle_indices)
    candidates = tuple(
        component
        for component in _section_components(all_segments)
        if any(
            segment.semantic_role == "face"
            and segment.source_triangle_index in supported
            for segment in component
        )
    )
    if not candidates:
        raise ValueError("No connected Design section contains the supporting Face")
    if len(candidates) > 1:
        raise ValueError("Supporting Face evidence resolves to multiple Design sections")
    component = _local_design_run(candidates[0], supported)
    face_segments = tuple(
        segment for segment in component if segment.semantic_role == "face"
    )
    if not face_segments:
        raise ValueError("Connected Design section contains no Face")
    crest_point = min(
        (
            point
            for segment in face_segments
            for point in (segment.start, segment.end)
        ),
        key=lambda point: (point.u, -point.z, point.x, point.y),
    )
    alignment = WallAlignmentSample(
        placement.chainage_m,
        SurfaceVertex(crest_point.x, crest_point.y, crest_point.z),
        placement.wall_strike_xy,
        placement.downwall_xy,
    )
    shifted = _shift_segments(component, crest_point.u)
    seed_segments = tuple(
        segment
        for segment in shifted
        if segment.semantic_role == "face"
        and segment.source_triangle_index in supported
    )
    interval = _select_assessment_interval(
        _assessment_profile_intervals(alignment, assessment_polygon),
        seed_segments,
    )
    evaluated_design_segments = clip_section_segments_to_u_interval(
        shifted, *interval
    )
    evaluated = build_design_section(evaluated_design_segments)
    if not evaluated.elements:
        raise ValueError("Assessment Area clips away the Design wall section")
    actual_segments: tuple[SectionSegment, ...] = ()
    raw_actual_segments: tuple[SectionSegment, ...] = ()
    if actual_surface is not None:
        raw_actual_segments = intersect_surface_with_profile(actual_surface, alignment)
        actual_segments = clip_section_segments_to_u_interval(
            raw_actual_segments,
            *interval,
        )
        design_points = tuple(
            point
            for element in evaluated.elements
            for point in (element.start, element.end)
        )
        actual_segments = clip_section_segments_to_z_range(
            actual_segments,
            min(point.z for point in design_points),
            max(point.z for point in design_points),
        )
    profile = TransverseProfile(
        alignment,
        evaluated_design_segments,
        actual_segments,
        DesignSection(
            evaluated.elements,
            _upstream_context_for_assessed_section(evaluated, shifted, interval),
        ),
        assessment_u_interval=interval,
    )
    issue = profile_vertical_order_issue(profile)
    if issue is not None:
        raise ValueError(issue)
    return replace(profile, measurement_context=build_measurement_context(
        profile, shifted, raw_actual_segments,
    ))


def build_alignment_profile_sections(
    *,
    alignment: WallAlignment,
    design_surface: TriangleSurface,
    assessment_polygon: PlanPolygon,
    role_mapping: SurfaceRoleMapping,
    spacing_m: float,
    actual_surface: TriangleSurface | None = None,
) -> AlignmentProfileSectionResult:
    """Place from Alignment + Design, then intersect Actual if supplied."""
    placement_result = place_profiles_from_alignment(
        alignment=alignment,
        design_surface=design_surface,
        assessment_polygon=assessment_polygon,
        role_mapping=role_mapping,
        spacing_m=spacing_m,
    )
    profiles: list[TransverseProfile] = []
    diagnostics = list(placement_result.diagnostics)
    for placement in placement_result.placements:
        try:
            profiles.append(_profile_from_placement(
                placement,
                design_surface,
                actual_surface,
                role_mapping,
                assessment_polygon,
            ))
        except ValueError as exc:
            diagnostics.append(AlignmentPlacementDiagnostic(
                "design_section_assembly_failed",
                str(exc),
                placement.station_index,
                placement.chainage_m,
                "alignment_sections",
            ))
    profiles_tuple = tuple(profiles)
    return AlignmentProfileSectionResult(
        profiles_tuple,
        build_design_variants(profiles_tuple),
        placement_result,
        tuple(diagnostics),
    )


__all__ = [
    "AlignmentPlacementDiagnostic",
    "AlignmentProfilePlacement",
    "AlignmentProfilePlacementResult",
    "AlignmentProfileSectionResult",
    "WallAlignment",
    "build_alignment_profile_sections",
    "place_profiles_from_alignment",
]
