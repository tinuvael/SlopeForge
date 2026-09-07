"""Derived engineering measurements for transverse Wall Conformance profiles.

Design semantics define the expected physical landmarks.  Actual geometry is
then searched only near those landmarks; it never contributes to profile
placement, orientation, clipping, or Design landmark selection.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import atan2, degrees, hypot, isfinite, sqrt
from statistics import mean, median
from typing import Literal

from .models import DesignSectionElement, SectionPoint, SectionSegment, TransverseProfile
from .sections import clip_section_segments_to_u_interval


DetectionState = Literal["detected", "low_confidence", "not_detected"]
LandmarkSource = Literal[
    "physical_breakpoint",
    "boundary_face_run_onset",
    "boundary_design_elevation_fallback",
]


@dataclass(frozen=True)
class MeasurementDetection:
    state: DetectionState
    reason_code: str
    message: str

    def __post_init__(self) -> None:
        if self.state not in {"detected", "low_confidence", "not_detected"}:
            raise ValueError(f"Unsupported measurement detection state: {self.state!r}")
        if not self.reason_code.strip() or not self.message.strip():
            raise ValueError("Measurement detection reason and message must be non-empty")

    @property
    def reliable(self) -> bool:
        return self.state == "detected"


@dataclass(frozen=True)
class ProfileLandmark:
    point: SectionPoint | None
    detection: MeasurementDetection
    source: LandmarkSource = "physical_breakpoint"

    def __post_init__(self) -> None:
        if self.point is not None and not all(
            isfinite(value) for value in (self.point.u, self.point.z, self.point.x, self.point.y)
        ):
            raise ValueError("Landmark coordinates must be finite")
        if self.detection.state == "detected" and self.point is None:
            raise ValueError("A detected landmark requires a point")
        if self.source not in {
            "physical_breakpoint",
            "boundary_face_run_onset",
            "boundary_design_elevation_fallback",
        }:
            raise ValueError(f"Unsupported landmark source: {self.source!r}")


@dataclass(frozen=True)
class WallProfileLandmarks:
    upper_berm_start: ProfileLandmark
    upper_crest: ProfileLandmark
    lower_toe: ProfileLandmark


@dataclass(frozen=True)
class WallMeasurementTolerances:
    """Named local breakpoint-fit and rejection tolerances.

    The defaults are metre-scale survey tolerances, not scoring thresholds.
    They deliberately permit ordinary triangulation noise while rejecting a
    disconnected or geometrically incompatible transition.
    """

    design_connection_tolerance_m: float = 1e-4
    actual_connection_tolerance_m: float = 0.15
    geometry_epsilon_m: float = 1e-9
    collinear_angle_tolerance_deg: float = 1e-5
    minimum_actual_component_length_m: float = 0.05
    search_half_window_m: float = 5.0
    side_fit_window_m: float = 2.5
    minimum_side_support_m: float = 0.60
    maximum_fit_residual_m: float = 0.45
    detected_fit_residual_m: float = 0.20
    minimum_orientation_change_deg: float = 7.0
    minimum_actual_face_descent_deg: float = 25.0
    maximum_actual_platform_angle_deg: float = 20.0
    minimum_upper_platform_width_m: float = 1.0
    design_distance_preference_m: float = 3.0
    design_orientation_preference_deg: float = 28.0
    expected_change_fraction: float = 0.30
    ambiguity_score_margin: float = 0.20
    ambiguity_separation_m: float = 0.30
    ambiguity_fit_residual_m: float = 0.01

    def __post_init__(self) -> None:
        values = tuple(self.__dict__.values())
        if not all(isfinite(value) and value > 0 for value in values):
            raise ValueError("Wall measurement tolerances must be finite and positive")
        if self.detected_fit_residual_m > self.maximum_fit_residual_m:
            raise ValueError("Detected residual tolerance must not exceed rejection tolerance")
        if self.expected_change_fraction > 1:
            raise ValueError("Expected orientation change fraction must not exceed one")
        if self.minimum_actual_face_descent_deg <= self.maximum_actual_platform_angle_deg:
            raise ValueError("Actual Face threshold must be steeper than the platform threshold")

    @property
    def candidate_half_window_m(self) -> float:
        return (
            self.search_half_window_m
            + self.side_fit_window_m
            + self.actual_connection_tolerance_m
        )

    @property
    def context_half_window_m(self) -> float:
        return (
            self.candidate_half_window_m
            + self.side_fit_window_m
            + self.actual_connection_tolerance_m
        )


@dataclass(frozen=True)
class WallProfileMeasurements:
    chainage_m: float
    design_landmarks: WallProfileLandmarks
    actual_landmarks: WallProfileLandmarks
    design_overall_angle_deg: float | None
    actual_overall_angle_deg: float | None
    angle_shortfall_deg: float | None
    design_upper_berm_width_m: float | None
    actual_upper_berm_width_m: float | None
    upper_berm_deficit_m: float | None
    toe_signed_offset_u_m: float | None
    toe_deviation_m: float | None
    angle_status: MeasurementDetection
    berm_status: MeasurementDetection
    toe_status: MeasurementDetection

    @property
    def angle_deviation_deg(self) -> float | None:
        """Signed Actual-minus-Design overall-angle deviation for presentation."""
        if self.actual_overall_angle_deg is None or self.design_overall_angle_deg is None:
            return None
        return self.actual_overall_angle_deg - self.design_overall_angle_deg

    @property
    def upper_berm_width_deviation_m(self) -> float | None:
        """Signed Actual-minus-Design upper-berm width deviation."""
        if self.actual_upper_berm_width_m is None or self.design_upper_berm_width_m is None:
            return None
        return self.actual_upper_berm_width_m - self.design_upper_berm_width_m


@dataclass(frozen=True)
class KpiAggregate:
    valid_count: int
    total_count: int
    median: float | None
    mean: float | None
    minimum: float | None
    maximum: float | None

    @property
    def primary_value(self) -> float | None:
        return self.median


@dataclass(frozen=True)
class WallMeasurementSummary:
    angle_shortfall_deg: KpiAggregate
    upper_berm_deficit_m: KpiAggregate
    toe_deviation_m: KpiAggregate
    angle_deviation_deg: KpiAggregate
    upper_berm_width_deviation_m: KpiAggregate
    toe_signed_offset_u_m: KpiAggregate


@dataclass(frozen=True)
class BreakpointCandidateDiagnostic:
    """Read-only evidence from one Actual vertex considered as a breakpoint."""

    point: SectionPoint
    expected_point: SectionPoint
    delta_u_m: float
    delta_z_m: float
    spatial_offset_m: float
    left_support_m: float | None
    right_support_m: float | None
    left_angle_deg: float | None
    right_angle_deg: float | None
    expected_left_angle_deg: float
    expected_right_angle_deg: float
    left_orientation_error_deg: float | None
    right_orientation_error_deg: float | None
    left_residual_m: float | None
    right_residual_m: float | None
    local_gap_m: float | None
    score: float | None
    rejection_gate: str | None
    component_index: int | None = None
    component_continuous: bool | None = None
    physical_topology: str | None = None
    hard_physical_gate: str | None = None
    design_orientation_preference_error_deg: float | None = None
    transition_onset_penalty: float | None = None
    immediate_left_angle_deg: float | None = None
    immediate_right_angle_deg: float | None = None
    left_transition_progress_fraction: float | None = None
    right_transition_progress_fraction: float | None = None
    selection_rank: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class LandmarkDetectionDiagnostic:
    """Read-only trace of one Design-guided Actual landmark search."""

    name: str
    expected_point: SectionPoint | None
    final: ProfileLandmark
    local_actual_segment_count: int
    local_gap_m: float | None
    candidates: tuple[BreakpointCandidateDiagnostic, ...]
    best_score: float | None
    runner_up_score: float | None
    ambiguity_margin: float | None


@dataclass(frozen=True)
class ProfileLandmarkDiagnostics:
    """All three landmark traces for one generated transverse profile."""

    chainage_m: float
    upper_berm_start: LandmarkDetectionDiagnostic
    upper_crest: LandmarkDetectionDiagnostic
    lower_toe: LandmarkDetectionDiagnostic


@dataclass(frozen=True)
class _ExpectedTransition:
    name: str
    point: SectionPoint
    left_angle_deg: float
    right_angle_deg: float


@dataclass(frozen=True)
class _DesignLandmarkGuide:
    landmarks: WallProfileLandmarks
    upper_berm_start: _ExpectedTransition | None
    upper_crest: _ExpectedTransition | None
    lower_toe: _ExpectedTransition | None
    no_upstream_berm_or_road_context: bool


@dataclass(frozen=True)
class _LineFit:
    angle_deg: float
    rms_residual_m: float
    support_m: float


@dataclass(frozen=True)
class _BreakpointCandidate:
    point: SectionPoint
    expected_point: SectionPoint
    left_fit: _LineFit
    right_fit: _LineFit
    score: float
    maximum_design_orientation_error_deg: float
    maximum_fit_residual_m: float
    physical_topology: str
    transition_onset_penalty: float


@dataclass(frozen=True)
class _TransitionOnsetEvidence:
    penalty: float
    immediate_left_angle_deg: float
    immediate_right_angle_deg: float
    left_progress_fraction: float
    right_progress_fraction: float


_DETECTED = MeasurementDetection("detected", "detected", "Landmark detected")
_BOUNDARY_FACE_RUN_ONSET = MeasurementDetection(
    "detected",
    "boundary_face_run_onset",
    "Upper crest is the upstream onset of the connected sustained Actual Face",
)


def _failure(reason_code: str, message: str) -> ProfileLandmark:
    return ProfileLandmark(None, MeasurementDetection("not_detected", reason_code, message))


def _point_distance(first: SectionPoint, second: SectionPoint) -> float:
    return hypot(second.u - first.u, second.z - first.z)


def _points_close(first: SectionPoint, second: SectionPoint, tolerance: float) -> bool:
    return _point_distance(first, second) <= tolerance


def _element_angle(element: DesignSectionElement) -> float:
    return degrees(atan2(element.end.z - element.start.z, element.end.u - element.start.u))


def _segment_angle(segment: SectionSegment) -> float:
    start, end = _ordered_segment_points(segment)
    return degrees(atan2(end.z - start.z, end.u - start.u))


def _ordered_segment_points(segment: SectionSegment) -> tuple[SectionPoint, SectionPoint]:
    if (segment.end.u, segment.end.z, segment.end.x, segment.end.y) < (
        segment.start.u, segment.start.z, segment.start.x, segment.start.y
    ):
        return segment.end, segment.start
    return segment.start, segment.end


def _platform_component_at(
    segments: tuple[SectionSegment, ...],
    crest: SectionPoint,
    tolerance: float,
) -> tuple[SectionSegment, ...]:
    candidates = tuple(
        segment for segment in segments if segment.semantic_role in {"berm", "road"}
    )
    seeds = tuple(
        segment
        for segment in candidates
        if _points_close(segment.start, crest, tolerance)
        or _points_close(segment.end, crest, tolerance)
    )
    if not seeds:
        return ()
    role = seeds[0].semantic_role
    connected = list(seeds)
    remaining = [segment for segment in candidates if segment not in connected]
    changed = True
    while changed:
        changed = False
        endpoints = tuple(
            point for segment in connected for point in (segment.start, segment.end)
        )
        for segment in tuple(remaining):
            if segment.semantic_role == role and any(
                _points_close(point, endpoint, tolerance)
                for point in (segment.start, segment.end)
                for endpoint in endpoints
            ):
                connected.append(segment)
                remaining.remove(segment)
                changed = True
    return tuple(connected)


def _component_endpoints(
    segments: tuple[SectionSegment, ...],
) -> tuple[SectionPoint, SectionPoint]:
    points = tuple(point for segment in segments for point in (segment.start, segment.end))
    return min(points, key=lambda point: (point.u, point.z)), max(
        points, key=lambda point: (point.u, point.z)
    )


def _preceding_face_angle(
    profile: TransverseProfile,
    platform_start: SectionPoint,
    tolerance: float,
) -> float | None:
    touching = tuple(
        segment
        for segment in profile.design_segments
        if segment.semantic_role == "face"
        and _points_close(_ordered_segment_points(segment)[1], platform_start, tolerance)
        and segment.u_min < platform_start.u - tolerance
    )
    return _segment_angle(touching[0]) if touching else None


def _explicit_lower_platform(
    profile: TransverseProfile,
    last_face: DesignSectionElement,
    tolerance: float,
) -> DesignSectionElement | None:
    section = profile.design_section
    assert section is not None
    face_index = max(
        index for index, element in enumerate(section.elements) if element.role == "face"
    )
    if face_index + 1 >= len(section.elements):
        return None
    following = section.elements[face_index + 1]
    if following.role not in {"berm", "road"}:
        return None
    if not _points_close(last_face.end, following.start, tolerance):
        return None
    return following


def _design_guide(
    profile: TransverseProfile,
    tolerances: WallMeasurementTolerances,
) -> _DesignLandmarkGuide:
    if profile.measurement_context is not None:
        context = profile.measurement_context
        profile = replace(
            profile, design_section=context.design_section,
            design_segments=context.design_segments, measurement_context=None,
        )
    unsupported = _failure(
        "design_landmark_unsupported",
        "Design section does not provide a supported physical transition",
    )
    section = profile.design_section
    if section is None or not section.elements:
        landmarks = WallProfileLandmarks(unsupported, unsupported, unsupported)
        return _DesignLandmarkGuide(landmarks, None, None, None, False)
    faces = tuple(element for element in section.elements if element.role == "face")
    if not faces:
        landmarks = WallProfileLandmarks(unsupported, unsupported, unsupported)
        return _DesignLandmarkGuide(landmarks, None, None, None, False)

    tolerance = tolerances.design_connection_tolerance_m
    first_face = faces[0]
    crest = first_face.start
    context = section.upstream_context
    platform_segments = _platform_component_at(profile.design_segments, crest, tolerance)
    platform_start: SectionPoint | None = None
    platform_angle: float | None = None
    preceding_face_angle: float | None = None
    if context is not None and context.role in {"berm", "road"} and _points_close(
        context.end, crest, tolerance
    ):
        platform_angle = _element_angle(context)
        preceding_face_angle = _preceding_face_angle(profile, context.start, tolerance)
        # Upstream context is presentation geometry, not endpoint provenance.
        # A clip or dataset end does not prove the physical start of the berm.
        if preceding_face_angle is not None:
            platform_start = context.start
    elif platform_segments:
        candidate_start, candidate_end = _component_endpoints(platform_segments)
        if _points_close(candidate_end, crest, tolerance):
            platform_angle = degrees(atan2(
                candidate_end.z - candidate_start.z,
                candidate_end.u - candidate_start.u,
            ))
            candidate_preceding_angle = _preceding_face_angle(
                profile, candidate_start, tolerance
            )
            if candidate_preceding_angle is not None:
                platform_start = candidate_start
                platform_angle = degrees(atan2(
                    candidate_end.z - candidate_start.z,
                    candidate_end.u - candidate_start.u,
                ))
                preceding_face_angle = candidate_preceding_angle

    face_angle = _element_angle(first_face)
    upper_crest = ProfileLandmark(crest, _DETECTED)
    if platform_angle is None:
        # The upper endpoint of a semantically supported Design Face remains
        # the Design crest even when no Design upper Berm is available.  Zero
        # is only an orientation preference for the missing upstream side;
        # Actual physical topology still decides whether a crest exists.
        upper_start = unsupported
        start_guide = None
        crest_guide = _ExpectedTransition(
            "upper_crest", crest, 0.0, face_angle
        )
    else:
        upper_start = (
            ProfileLandmark(platform_start, _DETECTED)
            if platform_start is not None else unsupported
        )
        start_guide = _ExpectedTransition(
            "upper_berm_start",
            platform_start,
            preceding_face_angle,
            platform_angle,
        ) if platform_start is not None else None
        crest_guide = _ExpectedTransition(
            "upper_crest", crest, platform_angle, face_angle
        )

    last_face = faces[-1]
    following = _explicit_lower_platform(profile, last_face, tolerance)
    toe_point: SectionPoint | None = None
    toe_right_angle: float | None = None
    if following is not None:
        toe_point = last_face.end
        toe_right_angle = _element_angle(following)
    if toe_point is None or toe_right_angle is None:
        lower_toe = unsupported
        toe_guide = None
    else:
        lower_toe = ProfileLandmark(toe_point, _DETECTED)
        toe_guide = _ExpectedTransition(
            "lower_toe", toe_point, _element_angle(last_face), toe_right_angle
        )

    return _DesignLandmarkGuide(
        WallProfileLandmarks(upper_start, upper_crest, lower_toe),
        start_guide,
        crest_guide,
        toe_guide,
        platform_angle is None,
    )


def extract_design_landmarks(
    profile: TransverseProfile,
    tolerances: WallMeasurementTolerances | None = None,
) -> WallProfileLandmarks:
    """Extract only semantically supported physical Design landmarks."""
    return _design_guide(profile, tolerances or WallMeasurementTolerances()).landmarks


def _normalized_segments(
    segments: tuple[SectionSegment, ...],
    tolerances: WallMeasurementTolerances,
) -> tuple[SectionSegment, ...]:
    output = []
    seen: set[tuple[SectionPoint, SectionPoint]] = set()
    for segment in segments:
        start, end = _ordered_segment_points(segment)
        if _point_distance(start, end) <= tolerances.geometry_epsilon_m:
            continue
        if (start, end) in seen:
            continue
        seen.add((start, end))
        output.append(SectionSegment(start, end, segment.source_triangle_index, None))
    return tuple(sorted(output, key=lambda item: (
        item.u_min, item.u_max, item.start.z, item.end.z, item.source_triangle_index
    )))


def _segment_components(
    segments: tuple[SectionSegment, ...], tolerance: float
) -> tuple[tuple[SectionSegment, ...], ...]:
    remaining = list(segments)
    components: list[tuple[SectionSegment, ...]] = []
    while remaining:
        connected = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            points = tuple(
                point for segment in connected for point in (segment.start, segment.end)
            )
            for segment in tuple(remaining):
                if any(
                    _points_close(point, candidate, tolerance)
                    for point in (segment.start, segment.end)
                    for candidate in points
                ):
                    connected.append(segment)
                    remaining.remove(segment)
                    changed = True
        components.append(tuple(connected))
    return tuple(components)


def _unique_points(
    segments: tuple[SectionSegment, ...], tolerance: float
) -> tuple[SectionPoint, ...]:
    points: list[SectionPoint] = []
    for point in sorted(
        (point for segment in segments for point in (segment.start, segment.end)),
        key=lambda item: (item.u, item.z, item.x, item.y),
    ):
        if not any(_points_close(point, existing, tolerance) for existing in points):
            points.append(point)
    return tuple(points)


def _angle_difference(first: float, second: float) -> float:
    # Both sides point along +U. Rising and descending near-vertical Faces
    # are different engineering orientations, not equivalent undirected lines.
    difference = abs(first - second) % 360.0
    return min(difference, 360.0 - difference)


def _is_transition_vertex(
    point: SectionPoint,
    component: tuple[SectionSegment, ...],
    tolerances: WallMeasurementTolerances,
) -> bool:
    """Collinear tessellation vertices are not alternative breakpoints.

    This removes only numerically straight continuations. Engineering side
    compatibility and required orientation change are checked by the fits.
    """
    left = tuple(s for s in component
                 if s.u_min < point.u and _points_close(
                     s.end, point, tolerances.actual_connection_tolerance_m))
    right = tuple(s for s in component
                  if s.u_max > point.u and _points_close(
                      s.start, point, tolerances.actual_connection_tolerance_m))
    return any(_angle_difference(_segment_angle(a), _segment_angle(b))
               > tolerances.collinear_angle_tolerance_deg
               for a in left for b in right)


def _fit_line(segments: tuple[SectionSegment, ...]) -> _LineFit | None:
    """Orthogonal least squares integrated over the piecewise linear survey.

    Length-weighted exact segment moments give the same fit after subdivision
    and keep a cluster of tiny triangles from outweighing metres of support.
    Coordinates are centred before accumulating moments for survey elevations.
    """
    if not segments:
        return None
    lengths = tuple(_point_distance(s.start, s.end) for s in segments)
    total_length = sum(lengths)
    if total_length <= 0:
        return None
    mean_u = sum(length * (s.start.u + s.end.u) / 2 for s, length in zip(segments, lengths)) / total_length
    mean_z = sum(length * (s.start.z + s.end.z) / 2 for s, length in zip(segments, lengths)) / total_length
    variance_u = variance_z = covariance = 0.0
    for segment, length in zip(segments, lengths):
        a, b = segment.start.u - mean_u, segment.end.u - mean_u
        c, d = segment.start.z - mean_z, segment.end.z - mean_z
        weight = length / total_length
        variance_u += weight * (a * a + a * b + b * b) / 3
        variance_z += weight * (c * c + c * d + d * d) / 3
        covariance += weight * (2 * a * c + a * d + b * c + 2 * b * d) / 6
    points = tuple(point for segment in segments for point in (segment.start, segment.end))
    if variance_u + variance_z <= 1e-12:
        return None
    trace = variance_u + variance_z
    root = sqrt(max((variance_u - variance_z) ** 2 + 4.0 * covariance ** 2, 0.0))
    eigenvalue = (trace + root) / 2.0
    if abs(covariance) > 1e-12:
        direction_u, direction_z = eigenvalue - variance_z, covariance
    elif variance_u >= variance_z:
        direction_u, direction_z = 1.0, 0.0
    else:
        direction_u = 1e-12
        direction_z = 1.0 if points[-1].z >= points[0].z else -1.0
    length = hypot(direction_u, direction_z)
    direction_u, direction_z = direction_u / length, direction_z / length
    if direction_u < 0:
        direction_u, direction_z = -direction_u, -direction_z
    projections = tuple(
        (point.u - mean_u) * direction_u + (point.z - mean_z) * direction_z
        for point in points
    )
    return _LineFit(
        degrees(atan2(direction_z, direction_u)),
        sqrt(max((trace - root) / 2, 0.0)),
        max(projections) - min(projections),
    )


def _candidate_at(
    point: SectionPoint,
    component: tuple[SectionSegment, ...],
    expected: _ExpectedTransition,
    tolerances: WallMeasurementTolerances,
) -> _BreakpointCandidate | None:
    candidate, _diagnostic = _candidate_evaluation(
        point, component, expected, tolerances, local_gap_m=None,
    )
    return candidate


def _actual_transition_topology(
    name: str,
    left_angle_deg: float,
    right_angle_deg: float,
    tolerances: WallMeasurementTolerances,
) -> str | None:
    """Classify the physical transition without requiring Design agreement.

    All profile axes point in the Design-derived +U downwall direction.  A
    physical Face must therefore descend materially as U increases, while a
    Berm/Road/floor is materially flatter.  This intentionally tests Actual
    geometry itself; the Design angles remain a selection preference only.
    """
    face_left = left_angle_deg <= -tolerances.minimum_actual_face_descent_deg
    face_right = right_angle_deg <= -tolerances.minimum_actual_face_descent_deg
    platform_left = abs(left_angle_deg) <= tolerances.maximum_actual_platform_angle_deg
    platform_right = abs(right_angle_deg) <= tolerances.maximum_actual_platform_angle_deg
    if (
        _angle_difference(left_angle_deg, right_angle_deg)
        < tolerances.minimum_orientation_change_deg
    ):
        return None
    if name == "outer_upper_crest" and face_right:
        # At an outer pit boundary the upstream surface is topography, not a
        # safety Berm.  Its slope may descend, be flat, or rise into a local
        # high point; only the physically sustained downwall side is typed.
        return "topography_to_face"
    if name == "upper_crest" and platform_left and face_right:
        return "platform_to_face"
    if name in {"upper_berm_start", "lower_toe"} and face_left and platform_right:
        return "face_to_platform"
    return None


def _transition_onset_evidence(
    point: SectionPoint,
    component: tuple[SectionSegment, ...],
    left_fit: _LineFit,
    right_fit: _LineFit,
    tolerances: WallMeasurementTolerances,
) -> _TransitionOnsetEvidence:
    """Return a continuous onset preference relative to stable fitted runs.

    A side-fit window can make several vertices around one physical transition
    look like Face-to-platform candidates.  The immediate facets identify the
    onset within that transition zone, but their absolute angles must not veto
    the stable fitted topology: one short TIN facet may be transitional, and
    harmless subdivision or survey noise can move it across a Face/platform
    class threshold.

    Progress is measured from the fitted left-run orientation toward the fitted
    right-run orientation.  Zero penalty means that an upstream facet still
    belongs predominantly to the left run while a downstream facet has begun
    the transition.  The penalty changes continuously with facet orientation
    and is used only for physical-first candidate ranking.
    """
    left = tuple(
        segment for segment in component
        if segment.u_min < point.u
        and _points_close(
            _ordered_segment_points(segment)[1], point,
            tolerances.actual_connection_tolerance_m,
        )
    )
    right = tuple(
        segment for segment in component
        if segment.u_max > point.u
        and _points_close(
            _ordered_segment_points(segment)[0], point,
            tolerances.actual_connection_tolerance_m,
        )
    )
    orientation_change = _angle_difference(left_fit.angle_deg, right_fit.angle_deg)
    candidates: list[tuple[float, float, float, float, float]] = []
    for upstream in left:
        upstream_angle = _segment_angle(upstream)
        left_progress = (
            _angle_difference(upstream_angle, left_fit.angle_deg)
            / orientation_change
        )
        for downstream in right:
            downstream_angle = _segment_angle(downstream)
            right_progress = (
                _angle_difference(downstream_angle, left_fit.angle_deg)
                / orientation_change
            )
            penalty = max(
                left_progress - (1.0 - tolerances.expected_change_fraction),
                tolerances.expected_change_fraction - right_progress,
                0.0,
            )
            candidates.append((
                penalty,
                left_progress,
                -right_progress,
                upstream_angle,
                downstream_angle,
            ))
    penalty, left_progress, negative_right_progress, left_angle, right_angle = min(
        candidates
    )
    return _TransitionOnsetEvidence(
        penalty,
        left_angle,
        right_angle,
        left_progress,
        -negative_right_progress,
    )


def _face_resumes_downstream(
    point: SectionPoint,
    component: tuple[SectionSegment, ...],
    maximum_candidate_u: float,
    tolerances: WallMeasurementTolerances,
) -> bool:
    """Whether a supported platform-to-Face run follows this candidate.

    A Face-to-platform transition followed by another supported Face inside
    its downstream run-fit window is an internal bench toe, not the lower toe
    of the assessed wall. A Face beyond a sustained floor does not move the
    floor onset. This Actual topology check becomes essential when a displaced
    physical transition is allowed to compete without a hard Design-distance
    gate.
    """
    for candidate_point in _unique_points(component, tolerances.geometry_epsilon_m):
        if candidate_point.u <= point.u or candidate_point.u > maximum_candidate_u:
            continue
        if not _is_transition_vertex(candidate_point, component, tolerances):
            continue
        left = _fit_line(clip_section_segments_to_u_interval(
            component,
            candidate_point.u - tolerances.side_fit_window_m,
            candidate_point.u,
        ))
        right = _fit_line(clip_section_segments_to_u_interval(
            component,
            candidate_point.u,
            candidate_point.u + tolerances.side_fit_window_m,
        ))
        if left is None or right is None:
            continue
        if (
            left.support_m < tolerances.minimum_side_support_m
            or right.support_m < tolerances.minimum_side_support_m
            or max(left.rms_residual_m, right.rms_residual_m)
            > tolerances.maximum_fit_residual_m
        ):
            continue
        if _actual_transition_topology(
            "upper_crest", left.angle_deg, right.angle_deg, tolerances
        ) is not None:
            return True
    return False


def _candidate_rank(candidate: _BreakpointCandidate) -> tuple[float, float, float, float, float]:
    """Deterministic physical-first selection among already valid candidates."""
    return (
        candidate.transition_onset_penalty,
        candidate.maximum_fit_residual_m,
        candidate.maximum_design_orientation_error_deg,
        _point_distance(candidate.point, candidate.expected_point),
        candidate.point.u,
    )


def _candidate_evaluation(
    point: SectionPoint,
    component: tuple[SectionSegment, ...],
    expected: _ExpectedTransition,
    tolerances: WallMeasurementTolerances,
    *,
    local_gap_m: float | None,
    transition_name: str | None = None,
) -> tuple[_BreakpointCandidate | None, BreakpointCandidateDiagnostic]:
    """Evaluate hard Actual topology, then retain soft Design preferences."""
    left_segments = clip_section_segments_to_u_interval(
        component, point.u - tolerances.side_fit_window_m, point.u,
    )
    right_segments = clip_section_segments_to_u_interval(
        component, point.u, point.u + tolerances.side_fit_window_m,
    )
    left = _fit_line(left_segments)
    right = _fit_line(right_segments)
    common = dict(
        point=point,
        expected_point=expected.point,
        delta_u_m=point.u - expected.point.u,
        delta_z_m=point.z - expected.point.z,
        spatial_offset_m=_point_distance(point, expected.point),
        left_support_m=None if left is None else left.support_m,
        right_support_m=None if right is None else right.support_m,
        left_angle_deg=None if left is None else left.angle_deg,
        right_angle_deg=None if right is None else right.angle_deg,
        expected_left_angle_deg=expected.left_angle_deg,
        expected_right_angle_deg=expected.right_angle_deg,
        left_orientation_error_deg=None,
        right_orientation_error_deg=None,
        left_residual_m=None if left is None else left.rms_residual_m,
        right_residual_m=None if right is None else right.rms_residual_m,
        local_gap_m=local_gap_m,
        score=None,
        rejection_gate=None,
        hard_physical_gate=None,
        transition_onset_penalty=None,
        immediate_left_angle_deg=None,
        immediate_right_angle_deg=None,
        left_transition_progress_fraction=None,
        right_transition_progress_fraction=None,
    )
    if left is None or right is None:
        return None, BreakpointCandidateDiagnostic(
            **(common | {
                "rejection_gate": "line_fit_unavailable",
                "hard_physical_gate": "line_fit_unavailable",
            }),
        )
    if (
        left.support_m < tolerances.minimum_side_support_m
        or right.support_m < tolerances.minimum_side_support_m
    ):
        return None, BreakpointCandidateDiagnostic(
            **(common | {
                "rejection_gate": (
                    "insufficient_left_support"
                    if left.support_m < tolerances.minimum_side_support_m
                    else "insufficient_right_support"
                ),
                "hard_physical_gate": (
                    "insufficient_left_support"
                    if left.support_m < tolerances.minimum_side_support_m
                    else "insufficient_right_support"
                ),
            }),
        )
    left_error = _angle_difference(left.angle_deg, expected.left_angle_deg)
    right_error = _angle_difference(right.angle_deg, expected.right_angle_deg)
    common["left_orientation_error_deg"] = left_error
    common["right_orientation_error_deg"] = right_error
    if max(left.rms_residual_m, right.rms_residual_m) > tolerances.maximum_fit_residual_m:
        return None, BreakpointCandidateDiagnostic(
            **(common | {
                "rejection_gate": "fit_residual",
                "hard_physical_gate": "fit_residual",
            }),
        )
    name = transition_name or expected.name
    topology = _actual_transition_topology(
        name, left.angle_deg, right.angle_deg, tolerances
    )
    if topology is None:
        return None, BreakpointCandidateDiagnostic(
            **(common | {
                "rejection_gate": "transition_topology",
                "hard_physical_gate": "transition_topology",
                "physical_topology": "incompatible",
                "design_orientation_preference_error_deg": max(left_error, right_error),
            }),
        )
    if name == "lower_toe" and _face_resumes_downstream(
        point,
        component,
        point.u + tolerances.side_fit_window_m,
        tolerances,
    ):
        return None, BreakpointCandidateDiagnostic(
            **(common | {
                "rejection_gate": "face_resumes_downstream",
                "hard_physical_gate": "face_resumes_downstream",
                "physical_topology": "internal_face_to_platform",
                "design_orientation_preference_error_deg": max(
                    left_error, right_error
                ),
            }),
        )
    onset = (
        _transition_onset_evidence(point, component, left, right, tolerances)
        if name == "lower_toe"
        else None
    )
    onset_penalty = 0.0 if onset is None else onset.penalty
    maximum_error = max(left_error, right_error)
    maximum_residual = max(left.rms_residual_m, right.rms_residual_m)
    # Retain a readable Design-relative preference value for diagnostics.  It
    # never controls validity; selection uses the lexicographic rank below.
    score = (
        maximum_error / tolerances.design_orientation_preference_deg
        + _point_distance(point, expected.point) / tolerances.design_distance_preference_m
    )
    candidate = _BreakpointCandidate(
        point,
        expected.point,
        left,
        right,
        score,
        maximum_error,
        maximum_residual,
        topology,
        onset_penalty,
    )
    return candidate, BreakpointCandidateDiagnostic(
        **(common | {
            "score": score,
            "physical_topology": topology,
            "design_orientation_preference_error_deg": maximum_error,
            "transition_onset_penalty": onset_penalty,
            "immediate_left_angle_deg": (
                None if onset is None else onset.immediate_left_angle_deg
            ),
            "immediate_right_angle_deg": (
                None if onset is None else onset.immediate_right_angle_deg
            ),
            "left_transition_progress_fraction": (
                None if onset is None else onset.left_progress_fraction
            ),
            "right_transition_progress_fraction": (
                None if onset is None else onset.right_progress_fraction
            ),
            "selection_rank": (
                onset_penalty,
                maximum_residual,
                maximum_error,
                _point_distance(point, expected.point),
            ),
        }),
    )


def _local_gap_at_u(
    segments: tuple[SectionSegment, ...], u: float, tolerance: float,
) -> float | None:
    """Return an observed disconnected gap across U, or zero for continuity."""
    if not segments:
        return None
    components = _segment_components(segments, tolerance)
    if any(
        min(segment.u_min for segment in component) <= u <= max(segment.u_max for segment in component)
        for component in components
    ):
        return 0.0
    left = [segment.u_max for segment in segments if segment.u_max <= u]
    right = [segment.u_min for segment in segments if segment.u_min >= u]
    return max(min(right) - max(left), 0.0) if left and right else None


def _coverage_reason(
    segments: tuple[SectionSegment, ...],
    expected: _ExpectedTransition,
    tolerances: WallMeasurementTolerances,
) -> tuple[str, str]:
    if not segments:
        return "no_actual_coverage", "Actual survey has no section coverage"
    points = tuple(
        point for segment in segments for point in (segment.start, segment.end)
    )
    left_points = tuple(point for point in points if point.u < expected.point.u)
    right_points = tuple(point for point in points if point.u > expected.point.u)
    if not left_points or max(
        _point_distance(point, expected.point) for point in left_points
    ) < tolerances.minimum_side_support_m:
        return "insufficient_left_support", "Actual survey has insufficient upstream support"
    if not right_points or max(
        _point_distance(point, expected.point) for point in right_points
    ) < tolerances.minimum_side_support_m:
        return "insufficient_right_support", "Actual survey has insufficient downstream support"
    components = _segment_components(
        segments, tolerances.actual_connection_tolerance_m
    )
    if not any(
        min(segment.u_min for segment in component) < expected.point.u
        and max(segment.u_max for segment in component) > expected.point.u
        for component in components
    ):
        return "data_gap", "Actual survey has a data gap across the expected transition"
    return "incompatible_geometry", "Actual geometry is incompatible with the expected transition"


def _detect_actual_transition(
    actual_segments: tuple[SectionSegment, ...],
    expected: _ExpectedTransition | None,
    tolerances: WallMeasurementTolerances,
    *,
    bounded_context: bool = False,
    minimum_candidate_u: float | None = None,
    candidate_z_interval: tuple[float, float] | None = None,
    transition_name: str | None = None,
    required_connected_to: SectionPoint | None = None,
) -> ProfileLandmark:
    if expected is None:
        return _failure(
            "design_landmark_unsupported",
            "Design landmark is unsupported, so Actual was not searched",
        )
    segments = _normalized_segments(actual_segments, tolerances)
    if not segments:
        return _failure("no_actual_coverage", "Actual survey has no section coverage")
    local = segments if bounded_context else clip_section_segments_to_u_interval(
        segments,
        expected.point.u - tolerances.context_half_window_m,
        expected.point.u + tolerances.context_half_window_m,
    )
    if not local:
        return _failure("no_actual_coverage", "Actual survey does not cover the expected landmark")
    candidates: list[_BreakpointCandidate] = []
    for component in _segment_components(local, tolerances.actual_connection_tolerance_m):
        if sum(_point_distance(s.start, s.end) for s in component) < tolerances.minimum_actual_component_length_m:
            continue
        if required_connected_to is not None and not any(
            _points_close(point, required_connected_to, tolerances.actual_connection_tolerance_m)
            for segment in component
            for point in (segment.start, segment.end)
        ):
            continue
        points = _unique_points(component, tolerances.geometry_epsilon_m)
        for point in points:
            if minimum_candidate_u is not None and point.u <= minimum_candidate_u:
                continue
            if abs(point.u - expected.point.u) > tolerances.candidate_half_window_m:
                continue
            if (
                candidate_z_interval is not None
                and not candidate_z_interval[0] <= point.z <= candidate_z_interval[1]
            ):
                continue
            if not _is_transition_vertex(point, component, tolerances):
                continue
            candidate, _diagnostic = _candidate_evaluation(
                point,
                component,
                expected,
                tolerances,
                local_gap_m=None,
                transition_name=transition_name,
            )
            if candidate is not None:
                candidates.append(candidate)
    if not candidates:
        reason_code, message = _coverage_reason(local, expected, tolerances)
        return _failure(reason_code, message)
    candidates.sort(key=_candidate_rank)
    best = candidates[0]
    if any(
        abs(candidate.transition_onset_penalty - best.transition_onset_penalty)
        <= 1e-12
        and abs(candidate.maximum_fit_residual_m - best.maximum_fit_residual_m)
        <= tolerances.ambiguity_fit_residual_m
        and _point_distance(candidate.point, best.point)
        >= tolerances.ambiguity_separation_m
        for candidate in candidates[1:]
    ):
        return ProfileLandmark(
            best.point,
            MeasurementDetection(
                "low_confidence",
                "ambiguous_breakpoint",
                "Multiple similarly supported Actual breakpoints were found",
            ),
        )
    residual = best.maximum_fit_residual_m
    if residual > tolerances.detected_fit_residual_m:
        return ProfileLandmark(
            best.point,
            MeasurementDetection(
                "low_confidence",
                "unstable_breakpoint",
                "Actual breakpoint fit is plausible but below reliable tolerance",
            ),
        )
    return ProfileLandmark(best.point, _DETECTED)


def _landmark_from_candidate(
    candidate: _BreakpointCandidate,
    tolerances: WallMeasurementTolerances,
) -> ProfileLandmark:
    if candidate.maximum_fit_residual_m > tolerances.detected_fit_residual_m:
        return ProfileLandmark(
            candidate.point,
            MeasurementDetection(
                "low_confidence",
                "unstable_breakpoint",
                "Actual breakpoint fit is physically plausible but below reliable tolerance",
            ),
        )
    return ProfileLandmark(candidate.point, _DETECTED)


def _upper_pair_candidates(
    actual_segments: tuple[SectionSegment, ...],
    start_expected: _ExpectedTransition,
    crest_expected: _ExpectedTransition,
    tolerances: WallMeasurementTolerances,
) -> tuple[tuple[_BreakpointCandidate, _BreakpointCandidate], ...]:
    """Find connected Face -> platform -> Face candidate pairs in local input."""
    normalized = _normalized_segments(actual_segments, tolerances)
    pairs: list[tuple[_BreakpointCandidate, _BreakpointCandidate]] = []
    for component in _segment_components(normalized, tolerances.actual_connection_tolerance_m):
        if sum(_point_distance(s.start, s.end) for s in component) < tolerances.minimum_actual_component_length_m:
            continue
        starts: list[_BreakpointCandidate] = []
        crests: list[_BreakpointCandidate] = []
        for point in _unique_points(component, tolerances.geometry_epsilon_m):
            if not _is_transition_vertex(point, component, tolerances):
                continue
            if abs(
                point.u - start_expected.point.u
            ) <= tolerances.candidate_half_window_m:
                candidate, _ = _candidate_evaluation(
                    point, component, start_expected, tolerances, local_gap_m=0.0,
                )
                if candidate is not None:
                    starts.append(candidate)
            if abs(
                point.u - crest_expected.point.u
            ) <= tolerances.candidate_half_window_m:
                candidate, _ = _candidate_evaluation(
                    point, component, crest_expected, tolerances, local_gap_m=0.0,
                )
                if candidate is not None:
                    crests.append(candidate)
        for start in starts:
            for crest in crests:
                if crest.point.u - start.point.u < tolerances.minimum_upper_platform_width_m:
                    continue
                platform = clip_section_segments_to_u_interval(
                    component, start.point.u, crest.point.u,
                )
                platform_fit = _fit_line(platform)
                if (
                    platform_fit is None
                    or platform_fit.support_m < tolerances.minimum_upper_platform_width_m
                    or platform_fit.rms_residual_m > tolerances.maximum_fit_residual_m
                    or abs(platform_fit.angle_deg) > tolerances.maximum_actual_platform_angle_deg
                ):
                    continue
                pairs.append((start, crest))
    return tuple(pairs)


def _upper_pair_rank(
    pair: tuple[_BreakpointCandidate, _BreakpointCandidate],
) -> tuple[float, float, float, float, float, float]:
    start, crest = pair
    return (
        max(start.maximum_fit_residual_m, crest.maximum_fit_residual_m),
        start.maximum_fit_residual_m + crest.maximum_fit_residual_m,
        max(
            start.maximum_design_orientation_error_deg,
            crest.maximum_design_orientation_error_deg,
        ),
        _point_distance(start.point, start.expected_point)
        + _point_distance(crest.point, crest.expected_point),
        start.point.u,
        crest.point.u,
    )


def _detect_actual_upper_pair(
    actual_segments: tuple[SectionSegment, ...],
    start_expected: _ExpectedTransition | None,
    crest_expected: _ExpectedTransition | None,
    tolerances: WallMeasurementTolerances,
) -> tuple[ProfileLandmark, ProfileLandmark] | None:
    """Select a coherent upper platform before falling back to lone transitions."""
    if start_expected is None or crest_expected is None:
        return None
    pairs = sorted(
        _upper_pair_candidates(
            actual_segments,
            start_expected,
            crest_expected,
            tolerances,
        ),
        key=_upper_pair_rank,
    )
    if not pairs:
        return None
    best = pairs[0]
    if any(
        abs(
            pair[0].maximum_fit_residual_m + pair[1].maximum_fit_residual_m
            - best[0].maximum_fit_residual_m - best[1].maximum_fit_residual_m
        )
        <= tolerances.ambiguity_fit_residual_m
        and max(
            _point_distance(pair[0].point, best[0].point),
            _point_distance(pair[1].point, best[1].point),
        ) >= tolerances.ambiguity_separation_m
        for pair in pairs[1:]
    ):
        return (
            ProfileLandmark(
                best[0].point,
                MeasurementDetection(
                    "low_confidence", "ambiguous_breakpoint",
                    "Multiple coherent Face-platform-Face pairs were found",
                ),
            ),
            ProfileLandmark(
                best[1].point,
                MeasurementDetection(
                    "low_confidence", "ambiguous_breakpoint",
                    "Multiple coherent Face-platform-Face pairs were found",
                ),
            ),
        )
    return (
        _landmark_from_candidate(best[0], tolerances),
        _landmark_from_candidate(best[1], tolerances),
    )


def _boundary_truncated_upper_berm_start(
    profile: TransverseProfile,
    actual_segments: tuple[SectionSegment, ...],
    upper_crest: ProfileLandmark,
    tolerances: WallMeasurementTolerances,
) -> ProfileLandmark | None:
    """Recognise a real survey edge that starts on the upper platform.

    This is deliberately a statement about the connected Actual section, not
    about a station, Assessment edge, or Design landmark position.  A clipped
    measurement-context edge and a disconnected upstream survey component are
    both insufficient: the former has no physical boundary evidence and the
    latter is a data gap rather than a pit boundary.
    """
    if not upper_crest.detection.reliable:
        return None
    crest = upper_crest.point
    assert crest is not None
    segments = _normalized_segments(actual_segments, tolerances)
    components = _segment_components(segments, tolerances.actual_connection_tolerance_m)
    component = next(
        (
            candidate
            for candidate in components
            if any(
                _points_close(point, crest, tolerances.actual_connection_tolerance_m)
                for segment in candidate
                for point in (segment.start, segment.end)
            )
        ),
        None,
    )
    if component is None:
        return None
    boundary, _ = _component_endpoints(component)
    if crest.u - boundary.u < tolerances.minimum_upper_platform_width_m:
        return None

    # ``actual_segments`` may be clipped at the measurement-context U bound.
    # Such an artificial endpoint cannot establish that the survey itself ends
    # on a platform.
    context = profile.measurement_context
    if context is not None and any(
        abs(boundary.u - lower) <= tolerances.actual_connection_tolerance_m
        for lower, _ in context.u_intervals
    ):
        return None

    # A preceding disconnected component is positive evidence of a survey gap,
    # not an upstream pit boundary.  Leave the ordinary missing-start reason in
    # place in that case.
    if any(
        max(segment.u_max for segment in candidate)
        < boundary.u - tolerances.actual_connection_tolerance_m
        for candidate in components
        if candidate is not component
    ):
        return None

    upstream_platform = clip_section_segments_to_u_interval(
        component, boundary.u, crest.u,
    )
    platform_fit = _fit_line(upstream_platform)
    if (
        platform_fit is None
        or platform_fit.support_m < tolerances.minimum_upper_platform_width_m
        or platform_fit.rms_residual_m > tolerances.maximum_fit_residual_m
        or abs(platform_fit.angle_deg) > tolerances.maximum_actual_platform_angle_deg
    ):
        return None
    initial = tuple(
        segment for segment in component
        if _points_close(
            _ordered_segment_points(segment)[0], boundary,
            tolerances.actual_connection_tolerance_m,
        )
    )
    if not initial or any(
        abs(_segment_angle(segment)) > tolerances.maximum_actual_platform_angle_deg
        for segment in initial
    ):
        return None
    return _failure(
        "boundary_truncated",
        "Actual connected survey begins on the upper platform at its upstream boundary",
    )


def _outer_boundary_crest_search_z_interval(
    profile: TransverseProfile,
    expected: _ExpectedTransition | None,
    tolerances: WallMeasurementTolerances,
) -> tuple[float, float] | None:
    """Return the Design-scale vertical locality for an outer crest search.

    One assessed Design Face height is the local engineering scale: it bounds
    the search to this wall's upper neighbourhood without asserting that the
    Actual crest has the Design elevation.  It is geometry-derived rather than
    a profile-, mine-, or coordinate-specific tolerance.
    """
    if expected is None:
        return None
    section = (
        profile.measurement_context.design_section
        if profile.measurement_context is not None
        else profile.design_section
    )
    if section is None:
        return None
    first_face = next((element for element in section.elements if element.role == "face"), None)
    if first_face is None:
        return None
    face_height = abs(first_face.end.z - first_face.start.z)
    if face_height <= tolerances.geometry_epsilon_m:
        return None
    return expected.point.z - face_height, expected.point.z + face_height


def _detect_outer_boundary_crest(
    profile: TransverseProfile,
    actual_segments: tuple[SectionSegment, ...],
    expected: _ExpectedTransition | None,
    lower_toe: ProfileLandmark,
    tolerances: WallMeasurementTolerances,
) -> ProfileLandmark:
    """Find a local Actual topography-to-Face breakpoint near Design crest Z."""
    if not lower_toe.detection.reliable or lower_toe.point is None:
        return _failure(
            "lower_toe_unsupported",
            "Outer boundary crest requires a connected physical lower toe",
        )
    normalized = _normalized_segments(actual_segments, tolerances)
    component = _component_containing_landmark(
        normalized, lower_toe.point, tolerances
    )
    if component is None:
        return _failure(
            "data_gap",
            "Outer boundary crest is not connected to the physical lower toe",
        )
    component_start, _component_end = _component_endpoints(component)
    if any(
        0.0 < component_start.u - max(segment.u_max for segment in candidate)
        <= tolerances.side_fit_window_m
        for candidate in _segment_components(
            normalized, tolerances.actual_connection_tolerance_m
        )
        if candidate is not component
        and max(segment.u_max for segment in candidate) < component_start.u
    ):
        return _failure(
            "data_gap",
            "A local Actual data gap precedes the toe-connected outer wall",
        )
    return _detect_actual_transition(
        actual_segments,
        expected,
        tolerances,
        bounded_context=profile.measurement_context is not None,
        candidate_z_interval=_outer_boundary_crest_search_z_interval(
            profile, expected, tolerances
        ),
        transition_name="outer_upper_crest",
        required_connected_to=lower_toe.point,
    )


def _component_containing_landmark(
    segments: tuple[SectionSegment, ...],
    landmark: SectionPoint,
    tolerances: WallMeasurementTolerances,
) -> tuple[SectionSegment, ...] | None:
    return next(
        (
            component
            for component in _segment_components(
                segments, tolerances.actual_connection_tolerance_m
            )
            if any(
                _points_close(point, landmark, tolerances.actual_connection_tolerance_m)
                for segment in component
                for point in (segment.start, segment.end)
            )
        ),
        None,
    )


def _boundary_face_crest(
    profile: TransverseProfile,
    actual_segments: tuple[SectionSegment, ...],
    ordinary_crest: ProfileLandmark,
    lower_toe: ProfileLandmark,
    tolerances: WallMeasurementTolerances,
) -> ProfileLandmark | None:
    """Detect a one-sided crest where the available survey starts on the Face.

    Unlike an ordinary crest, this endpoint has no upstream side to fit.  It is
    accepted only from authoritative raw measurement context and only when the
    same connected Actual component continues as a Face to a detected lower
    toe.  This keeps Assessment/display clipping and internal survey gaps from
    becoming fabricated crests.
    """
    if ordinary_crest.detection.reliable or not lower_toe.detection.reliable:
        return None
    context = profile.measurement_context
    if context is None:
        # Without raw measurement context, an endpoint may be an Assessment-U
        # or Design-Z presentation clip and is not authoritative boundary data.
        return None
    toe = lower_toe.point
    assert toe is not None
    segments = _normalized_segments(actual_segments, tolerances)
    components = _segment_components(segments, tolerances.actual_connection_tolerance_m)
    component = next(
        (
            candidate
            for candidate in components
            if any(
                _points_close(point, toe, tolerances.actual_connection_tolerance_m)
                for segment in candidate
                for point in (segment.start, segment.end)
            )
        ),
        None,
    )
    if component is None:
        return None
    boundary, _ = _component_endpoints(component)
    if (
        boundary.u >= toe.u - tolerances.minimum_side_support_m
        or boundary.z <= toe.z + tolerances.geometry_epsilon_m
    ):
        return None

    # A context-bound endpoint is a preprocessing artifact, not an Actual
    # surface boundary.
    if any(
        abs(boundary.u - lower) <= tolerances.actual_connection_tolerance_m
        for lower, _ in context.u_intervals
    ):
        return None

    # Any disconnected Actual evidence upstream makes this an internal data
    # gap rather than the upstream boundary of the available survey.
    if any(
        max(segment.u_max for segment in candidate)
        < boundary.u - tolerances.actual_connection_tolerance_m
        for candidate in components
        if candidate is not component
    ):
        return None

    immediate = clip_section_segments_to_u_interval(
        component,
        boundary.u,
        min(boundary.u + tolerances.side_fit_window_m, toe.u),
    )
    face_fit = _fit_line(immediate)
    if (
        face_fit is None
        or face_fit.support_m < tolerances.minimum_side_support_m
        or face_fit.rms_residual_m > tolerances.maximum_fit_residual_m
        or face_fit.angle_deg > -tolerances.minimum_actual_face_descent_deg
    ):
        return None
    initial = tuple(
        segment for segment in component
        if _points_close(
            _ordered_segment_points(segment)[0], boundary,
            tolerances.actual_connection_tolerance_m,
        )
    )
    if not initial or not any(
        _segment_angle(segment) <= -tolerances.minimum_actual_face_descent_deg
        for segment in initial
    ):
        return None
    return ProfileLandmark(boundary, _DETECTED)


def detect_actual_landmarks(
    profile: TransverseProfile,
    tolerances: WallMeasurementTolerances | None = None,
) -> WallProfileLandmarks:
    """Detect physical Actual breakpoints in local Design-guided windows."""
    settings = tolerances or WallMeasurementTolerances()
    guide = _design_guide(profile, settings)
    segments = _actual_measurement_segments(profile)
    bounded_context = profile.measurement_context is not None
    if guide.no_upstream_berm_or_road_context:
        # An outer pit boundary has no Design-supported upper Berm/Road.  The
        # Design crest elevation therefore anchors a *local search*, while the
        # accepted point remains an Actual physical breakpoint.  This restores
        # the stable local candidate path and prevents remote topography from
        # becoming the wall merely because it joins a long downwall run.
        lower_toe = _detect_actual_transition(
            segments,
            guide.lower_toe,
            settings,
            bounded_context=bounded_context,
        )
        upper_crest = _detect_outer_boundary_crest(
            profile, segments, guide.upper_crest, lower_toe, settings,
        )
        if not upper_crest.detection.reliable:
            # A survey which genuinely begins on the Face has no upstream
            # side to fit.  Retain the established measurement-context endpoint
            # rule as the only one-sided secondary path.
            boundary_crest = _boundary_face_crest(
                profile, segments, upper_crest, lower_toe, settings,
            )
            if boundary_crest is not None:
                upper_crest = boundary_crest
        upper_start = _failure(
            "boundary_truncated",
            "Outer Design boundary has no supported upstream Berm or Road context",
        )
        return WallProfileLandmarks(upper_start, upper_crest, lower_toe)

    upper_pair = _detect_actual_upper_pair(
        segments,
        guide.upper_berm_start,
        guide.upper_crest,
        settings,
    )
    upper_start = upper_pair[0] if upper_pair is not None else _detect_actual_transition(
        segments, guide.upper_berm_start, settings,
        bounded_context=bounded_context,
    )
    upper_crest = upper_pair[1] if upper_pair is not None else _detect_actual_transition(
        segments, guide.upper_crest, settings,
        bounded_context=bounded_context,
    )
    lower_toe = _detect_actual_transition(
        segments,
        guide.lower_toe,
        settings,
        bounded_context=bounded_context,
        minimum_candidate_u=(
            upper_crest.point.u
            if upper_crest.detection.reliable and upper_crest.point is not None
            else None
        ),
    )
    if not upper_start.detection.reliable and not upper_crest.detection.reliable:
        boundary_crest = _boundary_face_crest(
            profile, segments, upper_crest, lower_toe, settings,
        )
        if boundary_crest is not None:
            upper_start = _failure(
                "boundary_truncated",
                "Actual connected survey begins at the upper crest with no measurable upper Berm",
            )
            upper_crest = boundary_crest
    if not upper_start.detection.reliable:
        upper_start = _boundary_truncated_upper_berm_start(
            profile, segments, upper_crest, settings,
        ) or upper_start
    return WallProfileLandmarks(
        upper_start,
        upper_crest,
        lower_toe,
    )


def _diagnose_actual_transition(
    name: str,
    actual_segments: tuple[SectionSegment, ...],
    expected: _ExpectedTransition | None,
    tolerances: WallMeasurementTolerances,
    *,
    final_override: ProfileLandmark | None = None,
    bounded_context: bool = False,
    minimum_candidate_u: float | None = None,
    candidate_z_interval: tuple[float, float] | None = None,
    transition_name: str | None = None,
    required_connected_to: SectionPoint | None = None,
) -> LandmarkDetectionDiagnostic:
    """Capture the existing detector's decision inputs without affecting it."""
    final = final_override or _detect_actual_transition(
        actual_segments,
        expected,
        tolerances,
        bounded_context=bounded_context,
        minimum_candidate_u=minimum_candidate_u,
        candidate_z_interval=candidate_z_interval,
        transition_name=transition_name,
        required_connected_to=required_connected_to,
    )
    if expected is None:
        return LandmarkDetectionDiagnostic(
            name, None, final, 0, None, (), None, None,
            tolerances.ambiguity_fit_residual_m,
        )
    normalized = _normalized_segments(actual_segments, tolerances)
    local = normalized if bounded_context else clip_section_segments_to_u_interval(
        normalized,
        expected.point.u - tolerances.context_half_window_m,
        expected.point.u + tolerances.context_half_window_m,
    )
    gap = _local_gap_at_u(local, expected.point.u, tolerances.actual_connection_tolerance_m)
    diagnostics: list[BreakpointCandidateDiagnostic] = []
    accepted: list[_BreakpointCandidate] = []
    for component_index, component in enumerate(
        _segment_components(local, tolerances.actual_connection_tolerance_m)
    ):
        if sum(_point_distance(s.start, s.end) for s in component) < tolerances.minimum_actual_component_length_m:
            continue
        if required_connected_to is not None and not any(
            _points_close(point, required_connected_to, tolerances.actual_connection_tolerance_m)
            for segment in component
            for point in (segment.start, segment.end)
        ):
            continue
        for point in _unique_points(component, tolerances.geometry_epsilon_m):
            if minimum_candidate_u is not None and point.u <= minimum_candidate_u:
                continue
            if abs(point.u - expected.point.u) > tolerances.candidate_half_window_m:
                continue
            if (
                candidate_z_interval is not None
                and not candidate_z_interval[0] <= point.z <= candidate_z_interval[1]
            ):
                continue
            if not _is_transition_vertex(point, component, tolerances):
                continue
            candidate, diagnostic = _candidate_evaluation(
                point,
                component,
                expected,
                tolerances,
                local_gap_m=gap,
                transition_name=transition_name,
            )
            diagnostics.append(replace(
                diagnostic,
                component_index=component_index,
                component_continuous=True,
            ))
            if candidate is not None:
                accepted.append(candidate)
    accepted.sort(key=_candidate_rank)
    return LandmarkDetectionDiagnostic(
        name=name,
        expected_point=expected.point,
        final=final,
        local_actual_segment_count=len(local),
        local_gap_m=gap,
        candidates=tuple(diagnostics),
        best_score=accepted[0].score if accepted else None,
        runner_up_score=accepted[1].score if len(accepted) > 1 else None,
        ambiguity_margin=tolerances.ambiguity_fit_residual_m,
    )


def diagnose_profile_landmarks(
    profile: TransverseProfile,
    tolerances: WallMeasurementTolerances | None = None,
) -> ProfileLandmarkDiagnostics:
    """Return a read-only trace explaining all three landmark outcomes."""
    settings = tolerances or WallMeasurementTolerances()
    guide = _design_guide(profile, settings)
    segments = _actual_measurement_segments(profile)
    actual = detect_actual_landmarks(profile, settings)
    bounded_context = profile.measurement_context is not None
    outer_crest_kwargs = {}
    if guide.no_upstream_berm_or_road_context:
        outer_crest_kwargs = {
            "candidate_z_interval": _outer_boundary_crest_search_z_interval(
                profile, guide.upper_crest, settings
            ),
            "transition_name": "outer_upper_crest",
            "required_connected_to": (
                actual.lower_toe.point
                if actual.lower_toe.detection.reliable
                else None
            ),
        }
    return ProfileLandmarkDiagnostics(
        profile.alignment.chainage_m,
        _diagnose_actual_transition(
            "upper_berm_start", segments, guide.upper_berm_start, settings,
            final_override=actual.upper_berm_start,
            bounded_context=bounded_context,
        ),
        _diagnose_actual_transition(
            "upper_crest", segments, guide.upper_crest, settings,
            final_override=actual.upper_crest,
            bounded_context=bounded_context,
            **outer_crest_kwargs,
        ),
        _diagnose_actual_transition(
            "lower_toe", segments, guide.lower_toe, settings,
            final_override=actual.lower_toe,
            bounded_context=bounded_context,
            minimum_candidate_u=(
                actual.upper_crest.point.u
                if actual.upper_crest.detection.reliable
                and actual.upper_crest.point is not None
                else None
            ),
        ),
    )


def diagnose_profile_landmark_set(
    profiles: tuple[TransverseProfile, ...],
    tolerances: WallMeasurementTolerances | None = None,
) -> tuple[ProfileLandmarkDiagnostics, ...]:
    """Diagnose every already-generated profile in a finite validation set."""
    settings = tolerances or WallMeasurementTolerances()
    return tuple(diagnose_profile_landmarks(profile, settings) for profile in profiles)


def _actual_measurement_segments(profile: TransverseProfile) -> tuple[SectionSegment, ...]:
    if profile.measurement_context is not None:
        return profile.measurement_context.actual_segments
    return profile.actual_segments


def _overall_angle(upper_crest: SectionPoint, lower_toe: SectionPoint) -> float:
    return degrees(atan2(abs(lower_toe.z - upper_crest.z), abs(lower_toe.u - upper_crest.u)))


def _berm_is_connected(
    segments: tuple[SectionSegment, ...],
    start: SectionPoint,
    crest: SectionPoint,
    tolerances: WallMeasurementTolerances,
) -> bool:
    """Do not report a full berm from two disconnected partial platforms."""
    bounded = clip_section_segments_to_u_interval(segments, start.u, crest.u)
    for component in _segment_components(bounded, tolerances.actual_connection_tolerance_m):
        points = tuple(p for segment in component for p in (segment.start, segment.end))
        if all(any(_points_close(p, landmark, tolerances.actual_connection_tolerance_m)
                   for p in points) for landmark in (start, crest)):
            return True
    return False


def _measurement_status(
    design_landmarks: tuple[ProfileLandmark, ...],
    actual_landmarks: tuple[ProfileLandmark, ...],
) -> MeasurementDetection:
    for landmark in design_landmarks:
        if not landmark.detection.reliable:
            return MeasurementDetection(
                "not_detected",
                "design_landmark_unsupported",
                "Required Design landmark is unsupported",
            )
    for landmark in actual_landmarks:
        if landmark.detection.state == "not_detected":
            return landmark.detection
    for landmark in actual_landmarks:
        if landmark.detection.state == "low_confidence":
            return MeasurementDetection(
                "low_confidence",
                landmark.detection.reason_code,
                landmark.detection.message,
            )
    return MeasurementDetection("detected", "detected", "Measurement detected")


def measure_profile(
    profile: TransverseProfile,
    tolerances: WallMeasurementTolerances | None = None,
) -> WallProfileMeasurements:
    """Measure the three Assessment-Area KPIs on one generated profile."""
    settings = tolerances or WallMeasurementTolerances()
    guide = _design_guide(profile, settings)
    design = guide.landmarks
    segments = _actual_measurement_segments(profile)
    actual = detect_actual_landmarks(profile, settings)

    angle_status = _measurement_status(
        (design.upper_crest, design.lower_toe),
        (actual.upper_crest, actual.lower_toe),
    )
    berm_status = _measurement_status(
        (design.upper_berm_start, design.upper_crest),
        (actual.upper_berm_start, actual.upper_crest),
    )
    toe_status = _measurement_status((design.lower_toe,), (actual.lower_toe,))
    if angle_status.reliable and (
        actual.lower_toe.point.u < actual.upper_crest.point.u - settings.geometry_epsilon_m
        or actual.lower_toe.point.z >= actual.upper_crest.point.z
    ):
        angle_status = MeasurementDetection(
            "low_confidence", "incompatible_landmark_order",
            "Actual lower toe must lie downwall and below the upper crest",
        )
    if berm_status.reliable:
        if actual.upper_berm_start.point.u >= actual.upper_crest.point.u:
            berm_status = MeasurementDetection(
                "low_confidence", "incompatible_landmark_order",
                "Actual upper berm start must precede the upper crest",
            )
        elif not _berm_is_connected(
            segments, actual.upper_berm_start.point, actual.upper_crest.point, settings,
        ):
            berm_status = MeasurementDetection(
                "not_detected", "data_gap",
                "Actual upper berm has a data gap between its detected boundaries",
            )

    design_angle = (
        _overall_angle(design.upper_crest.point, design.lower_toe.point)
        if design.upper_crest.detection.reliable and design.lower_toe.detection.reliable
        else None
    )
    actual_angle = (
        _overall_angle(actual.upper_crest.point, actual.lower_toe.point)
        if angle_status.reliable
        else None
    )
    design_berm = (
        abs(design.upper_crest.point.u - design.upper_berm_start.point.u)
        if design.upper_crest.detection.reliable
        and design.upper_berm_start.detection.reliable
        else None
    )
    actual_berm = (
        abs(actual.upper_crest.point.u - actual.upper_berm_start.point.u)
        if berm_status.reliable
        else None
    )
    signed_toe = (
        actual.lower_toe.point.u - design.lower_toe.point.u
        if toe_status.reliable
        else None
    )
    return WallProfileMeasurements(
        chainage_m=profile.alignment.chainage_m,
        design_landmarks=design,
        actual_landmarks=actual,
        design_overall_angle_deg=design_angle,
        actual_overall_angle_deg=actual_angle,
        angle_shortfall_deg=(
            max(design_angle - actual_angle, 0.0)
            if design_angle is not None and actual_angle is not None
            else None
        ),
        design_upper_berm_width_m=design_berm,
        actual_upper_berm_width_m=actual_berm,
        upper_berm_deficit_m=(
            max(design_berm - actual_berm, 0.0)
            if design_berm is not None and actual_berm is not None
            else None
        ),
        toe_signed_offset_u_m=signed_toe,
        toe_deviation_m=abs(signed_toe) if signed_toe is not None else None,
        angle_status=angle_status,
        berm_status=berm_status,
        toe_status=toe_status,
    )


def measure_profiles(
    profiles: tuple[TransverseProfile, ...],
    tolerances: WallMeasurementTolerances | None = None,
) -> tuple[WallProfileMeasurements, ...]:
    settings = tolerances or WallMeasurementTolerances()
    return tuple(measure_profile(profile, settings) for profile in profiles)


def _aggregate(values: tuple[float, ...], total_count: int) -> KpiAggregate:
    return KpiAggregate(
        valid_count=len(values),
        total_count=total_count,
        median=median(values) if values else None,
        mean=mean(values) if values else None,
        minimum=min(values) if values else None,
        maximum=max(values) if values else None,
    )


def aggregate_measurements(
    measurements: tuple[WallProfileMeasurements, ...],
) -> WallMeasurementSummary:
    """Aggregate paired per-profile deviations independently by KPI."""
    return WallMeasurementSummary(
        angle_shortfall_deg=_aggregate(
            tuple(
                item.angle_shortfall_deg
                for item in measurements
                if item.angle_status.reliable and item.angle_shortfall_deg is not None
            ),
            len(measurements),
        ),
        upper_berm_deficit_m=_aggregate(
            tuple(
                item.upper_berm_deficit_m
                for item in measurements
                if item.berm_status.reliable and item.upper_berm_deficit_m is not None
            ),
            len(measurements),
        ),
        toe_deviation_m=_aggregate(
            tuple(
                item.toe_deviation_m
                for item in measurements
                if item.toe_status.reliable and item.toe_deviation_m is not None
            ),
            len(measurements),
        ),
        angle_deviation_deg=_aggregate(
            tuple(
                item.angle_deviation_deg
                for item in measurements
                if item.angle_status.reliable and item.angle_deviation_deg is not None
            ),
            len(measurements),
        ),
        upper_berm_width_deviation_m=_aggregate(
            tuple(
                item.upper_berm_width_deviation_m
                for item in measurements
                if item.berm_status.reliable
                and item.upper_berm_width_deviation_m is not None
            ),
            len(measurements),
        ),
        toe_signed_offset_u_m=_aggregate(
            tuple(
                item.toe_signed_offset_u_m
                for item in measurements
                if item.toe_status.reliable and item.toe_signed_offset_u_m is not None
            ),
            len(measurements),
        ),
    )


__all__ = [
    "BreakpointCandidateDiagnostic",
    "KpiAggregate",
    "LandmarkDetectionDiagnostic",
    "MeasurementDetection",
    "ProfileLandmarkDiagnostics",
    "ProfileLandmark",
    "WallMeasurementSummary",
    "WallMeasurementTolerances",
    "WallProfileLandmarks",
    "WallProfileMeasurements",
    "aggregate_measurements",
    "detect_actual_landmarks",
    "diagnose_profile_landmark_set",
    "diagnose_profile_landmarks",
    "extract_design_landmarks",
    "measure_profile",
    "measure_profiles",
]
