from __future__ import annotations

import logging
from math import hypot

from domain.geometry.operations import point_in_polygon, segment_intersection
from domain.geometry.surfaces import SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance.design import (
    extract_design_wall_topology,
    sample_wall_alignment,
    transition_length_in_area,
)
from domain.wall_conformance.models import (
    DesignAlignmentBoundary,
    ExternalWallBoundary,
    SectionPoint,
    SectionSegment,
    SurfaceRoleMapping,
    TransverseProfile,
    UpperCrestStationEvaluation,
    WallAlignmentSample,
    WallProfileSet,
    WallTransitionLine,
)
from domain.wall_conformance.sections import (
    clip_section_segments_to_z_range,
    connected_section_segments,
    intersect_surface_with_profile,
    section_points_close,
)
from domain.wall_conformance.semantic_sections import build_design_section, build_design_variants


logger = logging.getLogger(__name__)

_SECTION_TOLERANCE = 1e-5


def _assemble_alignment_boundaries(boundaries, tolerance: float = 0.02):
    """Join continuous crest fragments without bridging separate wall sectors."""
    remaining = list(boundaries)
    assembled = []
    while remaining:
        current = remaining.pop(0)
        points = list(current.line.points)
        interiors = list(current.interior_points)
        sources = {current.source}
        patch_indices = {current.face_patch_index}
        changed = True
        while changed:
            changed = False
            for index, candidate in enumerate(remaining):
                variants = (
                    (candidate.line.points, candidate.interior_points),
                    (
                        tuple(reversed(candidate.line.points)),
                        tuple(reversed(candidate.interior_points)),
                    ),
                )
                joined = None
                for candidate_points, candidate_interiors in variants:
                    append_distance = hypot(
                        points[-1].x - candidate_points[0].x,
                        points[-1].y - candidate_points[0].y,
                    )
                    prepend_distance = hypot(
                        candidate_points[-1].x - points[0].x,
                        candidate_points[-1].y - points[0].y,
                    )

                    def direction_ok(first_a, first_b, second_a, second_b):
                        first = (first_b.x - first_a.x, first_b.y - first_a.y)
                        second = (second_b.x - second_a.x, second_b.y - second_a.y)
                        lengths = hypot(*first) * hypot(*second)
                        return lengths > 1e-12 and (
                            first[0] * second[0] + first[1] * second[1]
                        ) / lengths >= 0.5

                    if append_distance <= tolerance and direction_ok(
                        points[-2], points[-1], candidate_points[0], candidate_points[1]
                    ):
                        joined = "append", candidate_points, candidate_interiors
                        break
                    if prepend_distance <= tolerance and direction_ok(
                        candidate_points[-2], candidate_points[-1], points[0], points[1]
                    ):
                        joined = "prepend", candidate_points, candidate_interiors
                        break
                if joined is None:
                    continue
                mode, candidate_points, candidate_interiors = joined
                if mode == "append" and points[-1] != candidate_points[0]:
                    bridge = SurfaceVertex(
                        (points[-1].x + candidate_points[0].x) / 2.0,
                        (points[-1].y + candidate_points[0].y) / 2.0,
                        (points[-1].z + candidate_points[0].z) / 2.0,
                    )
                    points[-1] = bridge
                    candidate_points = (bridge, *candidate_points[1:])
                elif mode == "prepend" and candidate_points[-1] != points[0]:
                    bridge = SurfaceVertex(
                        (candidate_points[-1].x + points[0].x) / 2.0,
                        (candidate_points[-1].y + points[0].y) / 2.0,
                        (candidate_points[-1].z + points[0].z) / 2.0,
                    )
                    candidate_points = (*candidate_points[:-1], bridge)
                    points[0] = bridge
                if mode == "append":
                    points.extend(candidate_points[1:])
                    interiors.extend(candidate_interiors)
                else:
                    points = [*candidate_points[:-1], *points]
                    interiors = [*candidate_interiors, *interiors]
                sources.add(candidate.source)
                patch_indices.add(candidate.face_patch_index)
                remaining.pop(index)
                changed = True
                break
        source = next(iter(sources)) if len(sources) == 1 else "mixed crest sources"
        patch_index = next(iter(patch_indices)) if len(patch_indices) == 1 else -1
        assembled.append(
            DesignAlignmentBoundary(
                type(current.line)("crest", tuple(points)),
                patch_index,
                tuple(interiors),
                source,
            )
        )
    return tuple(assembled)


def _segment_inside_area(segment: SectionSegment, area: PlanPolygon) -> bool:
    return point_in_polygon(
        PlanPoint(
            (segment.start.x + segment.end.x) / 2.0,
            (segment.start.y + segment.end.y) / 2.0,
        ),
        area,
    )


def _terminal_toe(section) -> SectionPoint | None:
    transitions = _face_platform_toes(section)
    return transitions[-1] if transitions else None


def _face_platform_toes(section) -> tuple[SectionPoint, ...]:
    return tuple(
        first.end
        for first, second in zip(section.elements, section.elements[1:])
        if first.role == "face" and second.role in {"berm", "road"}
    )


def _cross_xy(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _assessment_u_interval(
    sample, area: PlanPolygon
) -> tuple[float, float] | None:
    """Return the Assessment interval containing the profile origin.

    Intersections are converted into inside intervals along the positive-U ray.
    This handles an origin on/slightly outside the upper boundary and avoids
    selecting a later re-entry of a concave Assessment polygon.
    """
    origin = sample.origin
    direction = sample.normal_xy
    intersections = []
    for first, second in zip(area.ring, area.ring[1:]):
        edge = (second.x - first.x, second.y - first.y)
        offset = (first.x - origin.x, first.y - origin.y)
        denominator = _cross_xy(direction, edge)
        if abs(denominator) <= 1e-12:
            continue
        ray_u = _cross_xy(offset, edge) / denominator
        edge_fraction = _cross_xy(offset, direction) / denominator
        if -1e-8 <= edge_fraction <= 1.0 + 1e-8:
            intersections.append(ray_u)
    ordered = []
    for value in sorted(intersections):
        if not ordered or abs(value - ordered[-1]) > 1e-7:
            ordered.append(value)
    if not ordered:
        return None
    bounds = ordered
    for start, end in zip(bounds, bounds[1:]):
        if end - start <= 1e-8:
            continue
        midpoint = (start + end) / 2.0
        if point_in_polygon(
            PlanPoint(
                origin.x + midpoint * direction[0],
                origin.y + midpoint * direction[1],
            ),
            area,
        ):
            if start - 1e-7 <= 0.0 <= end + 1e-7:
                return start, end
    for start, end in zip(bounds, bounds[1:]):
        midpoint = (start + end) / 2.0
        if end > 0.0 and point_in_polygon(
            PlanPoint(
                origin.x + midpoint * direction[0],
                origin.y + midpoint * direction[1],
            ),
            area,
        ):
            return start, end
    return None


def _downstream_area_exit_u(sample, area: PlanPolygon) -> float | None:
    interval = _assessment_u_interval(sample, area)
    return None if interval is None else interval[1]


def _interpolate_section_point(first: SectionPoint, second: SectionPoint, u: float):
    span = second.u - first.u
    fraction = 0.0 if abs(span) <= 1e-12 else (u - first.u) / span
    return SectionPoint(
        u,
        first.z + (second.z - first.z) * fraction,
        first.x + (second.x - first.x) * fraction,
        first.y + (second.y - first.y) * fraction,
    )


def _clip_segments_to_interval(segments, interval):
    if interval is None:
        return ()
    lower, upper = interval
    clipped = []
    for segment in segments:
        if segment.u_max < lower - 1e-9 or segment.u_min > upper + 1e-9:
            continue
        start, end = segment.start, segment.end
        if start.u > end.u:
            start, end = end, start
        source_start, source_end = start, end
        if source_start.u < lower:
            start = _interpolate_section_point(source_start, source_end, lower)
        if source_end.u > upper:
            end = _interpolate_section_point(source_start, source_end, upper)
        clipped.append(
            SectionSegment(
                start,
                end,
                segment.source_triangle_index,
                segment.semantic_role,
            )
        )
    return tuple(clipped)


def _toe_near_area_exit(
    section, downstream_area_u: float | None
) -> tuple[SectionPoint | None, tuple[SectionPoint, ...]]:
    transitions = _face_platform_toes(section)
    if downstream_area_u is None or not transitions:
        return None, transitions
    return min(
        transitions, key=lambda point: abs(point.u - downstream_area_u)
    ), transitions


def _origin_section_point(sample: WallAlignmentSample) -> SectionPoint:
    return SectionPoint(0.0, sample.origin.z, sample.origin.x, sample.origin.y)


def _other_incident_endpoint(
    segment: SectionSegment,
    origin: SectionPoint,
) -> SectionPoint | None:
    start_is_origin = section_points_close(
        segment.start, origin, tolerance=_SECTION_TOLERANCE
    )
    end_is_origin = section_points_close(
        segment.end, origin, tolerance=_SECTION_TOLERANCE
    )
    if start_is_origin == end_is_origin:
        return None
    return segment.end if start_is_origin else segment.start


def _orient_sample_downwall(
    sample: WallAlignmentSample,
    design_surface: TriangleSurface,
    role_mapping: SurfaceRoleMapping,
) -> WallAlignmentSample | None:
    """Orient +U from the Upper Crest down the adjacent Design Face."""
    origin = _origin_section_point(sample)
    segments = connected_section_segments(
        intersect_surface_with_profile(
            design_surface, sample, role_mapping=role_mapping
        ),
        origin,
    )
    descent_signs: set[int] = set()
    for segment in segments:
        if segment.semantic_role != "face":
            continue
        other = _other_incident_endpoint(segment, origin)
        if other is None or abs(other.u) <= _SECTION_TOLERANCE:
            continue
        if other.z < origin.z - _SECTION_TOLERANCE:
            descent_signs.add(1 if other.u > 0.0 else -1)
    if descent_signs == {1}:
        return sample
    if descent_signs == {-1}:
        nx, ny = sample.normal_xy
        return WallAlignmentSample(
            sample.chainage_m,
            sample.origin,
            sample.tangent_xy,
            (-nx, -ny),
            sample.boundary_component_index,
        )
    if len(descent_signs) > 1:
        logger.debug(
            "Upper station skipped: conflicting descending Face sectors at "
            "origin=(%.3f, %.3f, %.3f)",
            sample.origin.x,
            sample.origin.y,
            sample.origin.z,
        )
        return None
    logger.debug(
        "Upper station skipped: no descending Design Face incident to "
        "origin=(%.3f, %.3f, %.3f)",
        sample.origin.x,
        sample.origin.y,
        sample.origin.z,
    )
    return None


def evaluate_upper_crest_station(
    sample: WallAlignmentSample,
    design_surface: TriangleSurface,
    assessment_polygon: PlanPolygon,
    role_mapping: SurfaceRoleMapping,
    toe_lines,
) -> UpperCrestStationEvaluation:
    """Validate one candidate origin using only its local wall geometry."""
    interval = _assessment_u_interval(sample, assessment_polygon)
    if interval is None:
        return UpperCrestStationEvaluation(False, "no local Assessment interval")
    width = interval[1] - interval[0]
    if width <= 1e-6 or interval[1] <= 1e-6:
        return UpperCrestStationEvaluation(False, "local Assessment width below tolerance")

    full_segments = intersect_surface_with_profile(
        design_surface, sample, role_mapping=role_mapping
    )
    origin = _origin_section_point(sample)
    local_segments = connected_section_segments(full_segments, origin)
    if not local_segments:
        return UpperCrestStationEvaluation(
            False, "no Design geometry incident to sampled crest", interval
        )

    if any(
        segment.semantic_role == "face"
        and segment.u_max < -_SECTION_TOLERANCE
        and segment.u_max >= interval[0] - _SECTION_TOLERANCE
        for segment in local_segments
    ):
        return UpperCrestStationEvaluation(
            False, "connected evaluated Face exists upstream", interval
        )

    section = build_design_section(local_segments)
    downstream_faces = tuple(
        segment
        for segment in local_segments
        if segment.semantic_role == "face"
        and segment.u_min <= _SECTION_TOLERANCE
        and segment.u_max > _SECTION_TOLERANCE
        and (
            section_points_close(
                segment.start, origin, tolerance=_SECTION_TOLERANCE
            )
            or section_points_close(
                segment.end, origin, tolerance=_SECTION_TOLERANCE
            )
        )
    )
    descending_faces = tuple(
        segment
        for segment in downstream_faces
        if (
            (other := _other_incident_endpoint(segment, origin)) is not None
            and other.u > _SECTION_TOLERANCE
            and other.z < origin.z - _SECTION_TOLERANCE
        )
    )
    if not descending_faces:
        return UpperCrestStationEvaluation(
            False, "adjacent downstream Design Face does not descend", interval
        )

    external_toe, _ = _toe_near_area_exit(section, interval[1])
    return UpperCrestStationEvaluation(
        True, "valid local wall section", interval, external_toe, local_segments
    )


def _crest_cumulative_lengths(line: WallTransitionLine) -> tuple[float, ...]:
    values = [0.0]
    for first, second in zip(line.points, line.points[1:]):
        values.append(values[-1] + hypot(second.x - first.x, second.y - first.y))
    return tuple(values)


def _crest_is_closed(line: WallTransitionLine, tolerance: float = 1e-8) -> bool:
    first, last = line.points[0], line.points[-1]
    return hypot(first.x - last.x, first.y - last.y) <= tolerance


def _interpolate_crest_chainage(
    line: WallTransitionLine,
    cumulative: tuple[float, ...],
    chainage: float,
) -> SurfaceVertex:
    total = cumulative[-1]
    if _crest_is_closed(line):
        chainage %= total
    else:
        chainage = max(0.0, min(total, chainage))
    for index, (start, end) in enumerate(zip(cumulative, cumulative[1:])):
        if chainage <= end + 1e-9:
            span = end - start
            fraction = 0.0 if span <= 1e-12 else (chainage - start) / span
            first, second = line.points[index], line.points[index + 1]
            return SurfaceVertex(
                first.x + (second.x - first.x) * fraction,
                first.y + (second.y - first.y) * fraction,
                first.z + (second.z - first.z) * fraction,
            )
    return line.points[-1]


def _sample_crest_chainage(
    line: WallTransitionLine,
    chainage: float,
    tangent_window_m: float,
) -> WallAlignmentSample | None:
    cumulative = _crest_cumulative_lengths(line)
    total = cumulative[-1]
    if total <= 1e-9:
        return None
    closed = _crest_is_closed(line)
    if closed and abs(chainage - total) <= 1e-8:
        chainage = 0.0
    origin = _interpolate_crest_chainage(line, cumulative, chainage)
    if closed:
        before_s = chainage - tangent_window_m
        after_s = chainage + tangent_window_m
    else:
        before_s = max(0.0, chainage - tangent_window_m)
        after_s = min(total, chainage + tangent_window_m)
    before = _interpolate_crest_chainage(line, cumulative, before_s)
    after = _interpolate_crest_chainage(line, cumulative, after_s)
    tx, ty = after.x - before.x, after.y - before.y
    tangent_length = hypot(tx, ty)
    if tangent_length <= 1e-9:
        return None
    tx, ty = tx / tangent_length, ty / tangent_length
    return WallAlignmentSample(
        chainage,
        origin,
        (tx, ty),
        (-ty, tx),
    )


def _crest_subline(line, start_chainage: float, end_chainage: float):
    """Return source-polyline geometry between confirmed sample chainages."""
    cumulative = list(_crest_cumulative_lengths(line))
    if (
        _crest_is_closed(line)
        and start_chainage <= 1e-8
        and end_chainage >= cumulative[-1] - 1e-8
    ):
        return line

    def interpolate(chainage):
        for index, (start, end) in enumerate(zip(cumulative, cumulative[1:])):
            if chainage <= end + 1e-9:
                span = end - start
                fraction = 0.0 if span <= 1e-12 else (chainage - start) / span
                first, second = line.points[index], line.points[index + 1]
                return SurfaceVertex(
                    first.x + (second.x - first.x) * fraction,
                    first.y + (second.y - first.y) * fraction,
                    first.z + (second.z - first.z) * fraction,
                ), index
        return line.points[-1], len(line.points) - 2

    first, first_index = interpolate(start_chainage)
    last, last_index = interpolate(end_chainage)
    middle = tuple(line.points[index] for index in range(first_index + 1, last_index + 1))
    points = (first, *middle, last)
    deduplicated = tuple(
        point for index, point in enumerate(points)
        if index == 0 or point != points[index - 1]
    )
    if len(deduplicated) < 2:
        raise ValueError("Confirmed crest span has zero plan length")
    return WallTransitionLine("crest", deduplicated)


def _crest_area_chainage_spans(
    line: WallTransitionLine, assessment_polygon: PlanPolygon
) -> tuple[tuple[float, float], ...]:
    """Return in-Area spans without projecting repeated/self-crossing XY points."""
    spans: list[tuple[float, float]] = []
    cumulative = 0.0
    for first, second in zip(line.points, line.points[1:]):
        start = PlanPoint(first.x, first.y)
        end = PlanPoint(second.x, second.y)
        dx, dy = end.x - start.x, end.y - start.y
        length_sq = dx * dx + dy * dy
        segment_length = hypot(dx, dy)
        if segment_length <= 1e-12:
            continue
        cuts = [0.0, 1.0]
        for edge_start, edge_end in zip(
            assessment_polygon.ring, assessment_polygon.ring[1:]
        ):
            intersection = segment_intersection(start, end, edge_start, edge_end)
            if intersection is not None:
                cuts.append(max(
                    0.0,
                    min(
                        1.0,
                        ((intersection.x - start.x) * dx
                         + (intersection.y - start.y) * dy) / length_sq,
                    ),
                ))
        cuts.sort()
        unique = [cuts[0]]
        for value in cuts[1:]:
            if abs(value - unique[-1]) > 1e-9:
                unique.append(value)
        for lower_fraction, upper_fraction in zip(unique, unique[1:]):
            if upper_fraction - lower_fraction <= 1e-9:
                continue
            midpoint_fraction = (lower_fraction + upper_fraction) / 2.0
            midpoint = PlanPoint(
                start.x + dx * midpoint_fraction,
                start.y + dy * midpoint_fraction,
            )
            if not point_in_polygon(midpoint, assessment_polygon):
                continue
            lower = cumulative + segment_length * lower_fraction
            upper = cumulative + segment_length * upper_fraction
            if spans and abs(spans[-1][1] - lower) <= 1e-8:
                spans[-1] = (spans[-1][0], upper)
            else:
                spans.append((lower, upper))
        cumulative += segment_length
    return tuple(spans)


def _samples_with_area_endpoints(
    line: WallTransitionLine,
    samples: tuple[WallAlignmentSample, ...],
    area_spans: tuple[tuple[float, float], ...],
    tangent_window_m: float,
) -> tuple[WallAlignmentSample, ...]:
    """Add exact crest/Assessment intersections as profile stations."""
    by_chainage = {round(sample.chainage_m, 8): sample for sample in samples}
    total = _crest_cumulative_lengths(line)[-1]
    closed = _crest_is_closed(line)
    for start, end in area_spans:
        for chainage in (start, end):
            if closed and abs(chainage - total) <= 1e-8:
                chainage = 0.0
            key = round(chainage, 8)
            if key in by_chainage:
                continue
            sample = _sample_crest_chainage(line, chainage, tangent_window_m)
            if sample is not None:
                by_chainage[key] = sample
    return tuple(sorted(by_chainage.values(), key=lambda sample: sample.chainage_m))


def _split_valid_runs(indexed_evaluated):
    runs = []
    current = []
    for index, sample, evaluation in indexed_evaluated:
        if evaluation.valid:
            current.append((index, sample, evaluation))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _collect_external_upper_stations(
    topology,
    design_surface,
    assessment_polygon,
    role_mapping,
    toe_lines,
    *,
    spacing_m,
    tangent_window_m,
):
    confirmed = []
    accepted = []
    candidates = tuple(
        boundary for boundary in topology.alignment_boundaries
        if transition_length_in_area(boundary.line, assessment_polygon) > 1e-9
    )
    for candidate_index, component in enumerate(candidates):
        area_spans = _crest_area_chainage_spans(
            component.line, assessment_polygon
        )
        if not area_spans:
            continue
        try:
            regular_samples = sample_wall_alignment(
                component.line,
                toe_lines,
                assessment_polygon,
                spacing_m=spacing_m,
                tangent_window_m=tangent_window_m,
                interior_points=component.interior_points,
            )
        except ValueError as error:
            logger.debug(
                "Upper component %d regular sampling unavailable: %s",
                candidate_index,
                error,
            )
            regular_samples = ()
        component_samples = _samples_with_area_endpoints(
            component.line,
            regular_samples,
            area_spans,
            tangent_window_m,
        )
        evaluated = []
        for raw_sample in component_samples:
            sample = _orient_sample_downwall(
                raw_sample,
                design_surface,
                role_mapping,
            )
            if sample is None:
                evaluation = UpperCrestStationEvaluation(
                    False, "unable to orient +U down adjacent Design Face"
                )
                evaluated.append((raw_sample, evaluation))
                continue
            evaluation = evaluate_upper_crest_station(
                sample,
                design_surface,
                assessment_polygon,
                role_mapping,
                toe_lines,
            )
            evaluated.append((sample, evaluation))
            if not evaluation.valid:
                logger.debug(
                    "Upper station skipped: origin=(%.3f, %.3f, %.3f) "
                    "chainage=%.3f reason=%s",
                    sample.origin.x, sample.origin.y, sample.origin.z,
                    sample.chainage_m, evaluation.reason,
                )
        if not any(evaluation.valid for _, evaluation in evaluated):
            continue

        indexed_evaluated = [
            (index, sample, evaluation)
            for index, (sample, evaluation) in enumerate(evaluated)
        ]
        for area_start, area_end in area_spans:
            span_evaluated = [
                item for item in indexed_evaluated
                if area_start - 1e-8 <= item[1].chainage_m <= area_end + 1e-8
            ]
            for run in _split_valid_runs(span_evaluated):
                first_index, first_sample, _ = run[0]
                last_index, last_sample, _ = run[-1]
                start_chainage, end_chainage = area_start, area_end
                if first_index > 0:
                    previous_sample, previous_evaluation = evaluated[first_index - 1]
                    if (
                        not previous_evaluation.valid
                        and previous_sample.chainage_m >= area_start - 1e-8
                    ):
                        start_chainage = max(
                            start_chainage,
                            (previous_sample.chainage_m + first_sample.chainage_m) / 2.0,
                        )
                if last_index + 1 < len(evaluated):
                    next_sample, next_evaluation = evaluated[last_index + 1]
                    if (
                        not next_evaluation.valid
                        and next_sample.chainage_m <= area_end + 1e-8
                    ):
                        end_chainage = min(
                            end_chainage,
                            (last_sample.chainage_m + next_sample.chainage_m) / 2.0,
                        )
                if end_chainage - start_chainage <= 1e-9:
                    logger.debug(
                        "Confirmed crest run collapsed below tolerance: component=%d",
                        candidate_index,
                    )
                    continue

                confirmed_line = _crest_subline(
                    component.line, start_chainage, end_chainage
                )
                component_index = len(confirmed)
                confirmed.append(DesignAlignmentBoundary(
                    confirmed_line,
                    component.face_patch_index,
                    (),
                    component.source,
                ))
                for _sample_index, sample, evaluation in run:
                    accepted.append((WallAlignmentSample(
                        sample.chainage_m,
                        sample.origin,
                        sample.tangent_xy,
                        sample.normal_xy,
                        component_index,
                    ), evaluation))

    if not accepted:
        raise ValueError("No design wall alignment samples fall inside the Assessment Area")
    logger.debug(
        "External wall boundary: semantic crest edges=%d accepted components=%d "
        "profiles=%d",
        len(tuple(edge for edge in topology.boundary_edges if edge.kind == "crest")),
        len(confirmed), len(accepted),
    )
    return ExternalWallBoundary(tuple(confirmed)), tuple(accepted)


def _point_line_distance(point: SectionPoint, line) -> float:
    best = float("inf")
    for first, second in zip(line.points, line.points[1:]):
        dx, dy = second.x - first.x, second.y - first.y
        length_sq = dx * dx + dy * dy
        fraction = 0.0 if length_sq <= 1e-18 else max(
            0.0,
            min(1.0, ((point.x - first.x) * dx + (point.y - first.y) * dy) / length_sq),
        )
        x, y = first.x + fraction * dx, first.y + fraction * dy
        best = min(best, hypot(point.x - x, point.y - y))
    return best


def _external_toe_lines(profiles, toe_lines):
    observations = [
        profile.external_toe
        for profile in profiles
        if profile.external_toe is not None
    ]
    if not observations or not toe_lines:
        return ()
    counts = {line: 0 for line in toe_lines}
    for observation in observations:
        closest = min(toe_lines, key=lambda line: _point_line_distance(observation, line))
        counts[closest] += 1
    retained = tuple(line for line in toe_lines if counts[line] > 0)
    logger.debug(
        "External Lower Toe: retained components=%d observations=%s",
        len(retained), tuple(counts[line] for line in retained),
    )
    return retained


def build_transverse_profiles(
    design_surface: TriangleSurface,
    actual_surface: TriangleSurface,
    assessment_polygon: PlanPolygon,
    role_mapping: SurfaceRoleMapping,
    *,
    spacing_m: float = 3.0,
    tangent_window_m: float = 6.0,
) -> WallProfileSet:
    """Build design-derived transverse sections through design and actual meshes."""
    topology = extract_design_wall_topology(design_surface, role_mapping)
    toe_lines = tuple(line for line in topology.transitions if line.kind == "toe")
    alignment, stations = _collect_external_upper_stations(
        topology,
        design_surface,
        assessment_polygon,
        role_mapping,
        toe_lines,
        spacing_m=spacing_m,
        tangent_window_m=tangent_window_m,
    )
    profiles = []
    for sample, evaluation in stations:
        interval = evaluation.assessment_u_interval
        local_design_segments = evaluation.local_design_segments
        external_toe = evaluation.external_toe
        design_segments = _clip_segments_to_interval(
            local_design_segments,
            interval,
        )
        evaluated_section = build_design_section(design_segments)
        full_section = build_design_section(local_design_segments)
        design_section = type(evaluated_section)(
            evaluated_section.elements,
            full_section.upstream_context,
        )
        context_triangle_indices = (
            set(full_section.upstream_context.source_triangle_indices)
            if full_section.upstream_context is not None
            else set()
        )
        display_design_segments = tuple(sorted(
            (
                *(
                    segment for segment in local_design_segments
                    if segment.source_triangle_index in context_triangle_indices
                    and segment.u_min < 0.0
                    and segment.u_max <= 1e-7
                ),
                *design_segments,
            ),
            key=lambda segment: (segment.u_min, segment.u_max),
        ))
        raw_actual_segments = intersect_surface_with_profile(actual_surface, sample)
        u_clipped_actual_segments = _clip_segments_to_interval(
            raw_actual_segments,
            interval,
        )
        actual_segments = u_clipped_actual_segments
        design_points = [
            point
            for element in design_section.elements
            for point in (element.start, element.end)
        ]
        if design_points:
            actual_segments = clip_section_segments_to_z_range(
                actual_segments,
                min(point.z for point in design_points),
                max(point.z for point in design_points),
            )
        else:
            actual_segments = ()
        logger.debug(
            "Wall profile chainage=%.3f U=(%.3f, %.3f) Design segments=%d "
            "Actual raw=%d after-U=%d after-Z=%d",
            sample.chainage_m,
            interval[0],
            interval[1],
            len(design_segments),
            len(raw_actual_segments),
            len(u_clipped_actual_segments),
            len(actual_segments),
        )
        profiles.append(TransverseProfile(
            alignment=sample,
            design_segments=display_design_segments,
            actual_segments=actual_segments,
            design_section=design_section,
            assessment_u_interval=interval,
            external_toe=external_toe,
        ))
    profiles = tuple(profiles)
    external_toes = _external_toe_lines(profiles, toe_lines)
    return WallProfileSet(
        crest_lines=alignment.upper_lines,
        toe_lines=external_toes,
        profiles=profiles,
        design_variants=build_design_variants(profiles),
    )
