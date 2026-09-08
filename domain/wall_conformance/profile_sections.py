"""Production-compatible vertical sections over the Wall Conformance v2 pipeline.

Design topology and Face direction place every profile.  This module only
adapts an accepted Phase-3 ``ProfileTrace`` to the existing vertical-section
and presentation contracts; Assessment and Actual geometry never participate
in placement.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from domain.geometry.surfaces import SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance.design_topology import (
    DesignTopologyIndex,
    TransitionPortal,
    build_design_topology_index,
)
from domain.wall_conformance.invariants import profile_vertical_order_issue
from domain.wall_conformance.models import (
    DesignSection,
    SectionPoint,
    SectionSegment,
    SurfaceRoleMapping,
    TransverseProfile,
    WallAlignmentSample,
    WallProfileSet,
    WallTransitionLine,
)
from domain.wall_conformance.profile_pipeline import (
    WallProfilePlacementResult,
    place_wall_sector_extraction_profiles,
)
from domain.wall_conformance.profile_placement import ProfileTrace, WallGuide
from domain.wall_conformance.sections import (
    clip_section_segments_to_u_interval,
    clip_section_segments_to_z_range,
    connected_section_segments,
    intersect_surface_with_profile,
    section_points_close,
)
from domain.wall_conformance.semantic_sections import (
    build_design_section,
    build_design_variants,
)
from domain.wall_conformance.wall_sectors import (
    WallSector,
    extract_wall_sectors,
)


_PLAN_TOLERANCE = 1e-7
_SECTION_TOLERANCE = 1e-5


@dataclass(frozen=True)
class ProfileSectionDiagnostic:
    """One Phase-4A diagnostic with retained lower-phase provenance."""

    code: str
    message: str
    source: str = "profile_sections"
    sector_id: str | None = None
    station_index: int | None = None
    interval_index: int | None = None


@dataclass(frozen=True)
class ProfileSectionAssemblyResult:
    """Production output plus the accepted placement result and diagnostics."""

    profile_set: WallProfileSet
    placement_result: WallProfilePlacementResult
    diagnostics: tuple[ProfileSectionDiagnostic, ...] = ()


class ProfileSectionAssemblyError(ValueError):
    """All v2 sectors or profile traces were unusable for section assembly."""

    def __init__(
        self,
        message: str,
        *,
        placement_result: WallProfilePlacementResult,
        diagnostics: tuple[ProfileSectionDiagnostic, ...],
    ) -> None:
        super().__init__(message)
        self.placement_result = placement_result
        self.diagnostics = diagnostics


class _ProvenanceIssue(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def profile_trace_to_alignment(
    trace: ProfileTrace,
    upper_origin: SurfaceVertex,
    *,
    boundary_component_index: int,
) -> WallAlignmentSample:
    """Adapt one Phase-3 trace to the legacy plane representation.

    ``normal_xy`` is exactly the trace's Design-Face-authoritative ``+U``.
    The perpendicular tangent exists only to represent the vertical plane.
    """
    if hypot(
        upper_origin.x - trace.plan_start.x,
        upper_origin.y - trace.plan_start.y,
    ) > _PLAN_TOLERANCE:
        raise _ProvenanceIssue(
            "upper_origin_does_not_match_trace",
            "Exact Design Upper origin does not match ProfileTrace start",
        )
    nx, ny = trace.downwall_xy
    return WallAlignmentSample(
        chainage_m=trace.upper_chainage_m,
        origin=upper_origin,
        tangent_xy=(-ny, nx),
        normal_xy=(nx, ny),
        boundary_component_index=boundary_component_index,
    )


def _vertex_close(first: SurfaceVertex, second: SurfaceVertex) -> bool:
    return max(
        abs(first.x - second.x),
        abs(first.y - second.y),
        abs(first.z - second.z),
    ) <= _PLAN_TOLERANCE


def _candidate_on_segment(
    point: PlanPoint,
    first: SurfaceVertex,
    second: SurfaceVertex,
) -> SurfaceVertex | None:
    dx, dy = second.x - first.x, second.y - first.y
    length_sq = dx * dx + dy * dy
    if length_sq <= _PLAN_TOLERANCE * _PLAN_TOLERANCE:
        if hypot(point.x - first.x, point.y - first.y) <= _PLAN_TOLERANCE:
            if abs(second.z - first.z) > _PLAN_TOLERANCE:
                raise _ProvenanceIssue(
                    "ambiguous_design_guide_xyz",
                    "A Design guide contains multiple physical elevations at one plan position",
                )
            return first
        return None
    fraction = (
        (point.x - first.x) * dx + (point.y - first.y) * dy
    ) / length_sq
    if not -_PLAN_TOLERANCE <= fraction <= 1.0 + _PLAN_TOLERANCE:
        return None
    fraction = max(0.0, min(1.0, fraction))
    x = first.x + fraction * dx
    y = first.y + fraction * dy
    if hypot(point.x - x, point.y - y) > _PLAN_TOLERANCE:
        return None
    return SurfaceVertex(
        point.x,
        point.y,
        first.z + fraction * (second.z - first.z),
    )


def _resolve_exact_guide_vertex(
    point: PlanPoint,
    portal: TransitionPortal,
    *,
    guide_name: str,
) -> SurfaceVertex:
    candidates: list[SurfaceVertex] = []
    for first, second in zip(portal.points, portal.points[1:]):
        candidate = _candidate_on_segment(point, first, second)
        if candidate is not None and not any(
            _vertex_close(candidate, existing) for existing in candidates
        ):
            candidates.append(candidate)
    if not candidates:
        raise _ProvenanceIssue(
            f"missing_exact_{guide_name}_xyz",
            f"The {guide_name} guide point is absent from its Design portal provenance",
        )
    if len(candidates) > 1:
        raise _ProvenanceIssue(
            f"ambiguous_exact_{guide_name}_xyz",
            f"The {guide_name} guide point resolves to multiple physical Design points",
        )
    return candidates[0]


def _portal_for(
    topology: DesignTopologyIndex,
    portal_id: str | None,
    *,
    guide_name: str,
) -> TransitionPortal:
    if portal_id is None:
        raise _ProvenanceIssue(
            f"missing_{guide_name}_portal_provenance",
            f"WallSector has no exact Design {guide_name} portal provenance",
        )
    matches = tuple(
        portal for portal in topology.portals if portal.portal_id == portal_id
    )
    if len(matches) != 1:
        raise _ProvenanceIssue(
            f"invalid_{guide_name}_portal_provenance",
            f"WallSector Design {guide_name} portal provenance is not unique",
        )
    return matches[0]


def _transition_from_guide(
    guide: WallGuide,
    portal: TransitionPortal,
    *,
    kind: str,
) -> WallTransitionLine:
    vertices = tuple(
        _resolve_exact_guide_vertex(point, portal, guide_name=guide.kind)
        for point in guide.points
    )
    return WallTransitionLine(kind, vertices)


def _downstream_u(trace: ProfileTrace) -> float:
    nx, ny = trace.downwall_xy
    dx = trace.plan_end.x - trace.plan_start.x
    dy = trace.plan_end.y - trace.plan_start.y
    projected = dx * nx + dy * ny
    lateral = abs(dx * -ny + dy * nx)
    if projected <= _PLAN_TOLERANCE or lateral > _PLAN_TOLERANCE:
        raise _ProvenanceIssue(
            "invalid_profile_trace_extent",
            "ProfileTrace is not a positive straight +U corridor",
        )
    return projected


def _origin_point(alignment: WallAlignmentSample) -> SectionPoint:
    return SectionPoint(
        0.0,
        alignment.origin.z,
        alignment.origin.x,
        alignment.origin.y,
    )


def _with_upstream_context(
    evaluated: DesignSection,
    connected: tuple[SectionSegment, ...],
) -> tuple[DesignSection, tuple[SectionSegment, ...]]:
    full = build_design_section(connected)
    section = DesignSection(evaluated.elements, full.upstream_context)
    if full.upstream_context is None:
        return section, ()
    indices = set(full.upstream_context.source_triangle_indices)
    context = tuple(
        segment
        for segment in connected
        if segment.source_triangle_index in indices
        and segment.u_min < 0.0
        and segment.u_max <= _PLAN_TOLERANCE
    )
    return section, context


def _design_section_for_trace(
    design_surface: TriangleSurface,
    role_mapping: SurfaceRoleMapping,
    alignment: WallAlignmentSample,
    downstream_u: float,
) -> tuple[tuple[SectionSegment, ...], DesignSection]:
    origin = _origin_point(alignment)
    connected = connected_section_segments(
        intersect_surface_with_profile(
            design_surface,
            alignment,
            role_mapping=role_mapping,
        ),
        origin,
    )
    if not connected:
        raise _ProvenanceIssue(
            "design_section_not_incident_to_upper",
            "No connected Design section is incident to the exact Upper origin",
        )
    corridor = clip_section_segments_to_u_interval(
        connected, 0.0, downstream_u
    )
    evaluated = build_design_section(corridor)
    if not evaluated.elements:
        raise _ProvenanceIssue(
            "empty_design_section",
            "The accepted profile trace produced no Design section",
        )
    first = evaluated.elements[0].start
    if not section_points_close(first, origin, tolerance=_SECTION_TOLERANCE):
        raise _ProvenanceIssue(
            "design_section_does_not_start_at_upper",
            "Design section does not start at the exact Design Upper origin",
        )
    section, context = _with_upstream_context(evaluated, connected)
    display = tuple(sorted(
        (*context, *corridor),
        key=lambda item: (
            item.u_min,
            item.u_max,
            item.start.z,
            item.end.z,
            item.source_triangle_index,
        ),
    ))
    return display, section


def _exact_terminal_point(
    trace: ProfileTrace,
    sector: WallSector,
    topology: DesignTopologyIndex,
    downstream_u: float,
    design_segments: tuple[SectionSegment, ...],
) -> SectionPoint:
    if sector.lower_guide is not None:
        if trace.lower_point is None:
            raise _ProvenanceIssue(
                "missing_profile_lower_point",
                "A Lower-constrained ProfileTrace has no exact Lower point",
            )
        plan_point = trace.lower_point
        portal_id = sector.lower_portal_id
        guide_name = "lower"
    else:
        if sector.downstream_extent is None:
            raise _ProvenanceIssue(
                "missing_downstream_extent",
                "ProfileTrace has no physical Design terminal guide",
            )
        plan_point = trace.plan_end
        portal_id = sector.downstream_portal_id
        guide_name = "downstream_extent"
    portal = _portal_for(
        topology, portal_id, guide_name=guide_name
    )
    vertex = _resolve_exact_guide_vertex(
        plan_point, portal, guide_name=guide_name
    )
    terminal = SectionPoint(downstream_u, vertex.z, vertex.x, vertex.y)
    if not any(
        section_points_close(endpoint, terminal, tolerance=_SECTION_TOLERANCE)
        for segment in design_segments
        for endpoint in (segment.start, segment.end)
    ):
        raise _ProvenanceIssue(
            f"design_section_not_incident_to_{guide_name}",
            f"Exact Design {guide_name} is not incident to the connected Design section",
        )
    return terminal


def _actual_segments(
    actual_surface: TriangleSurface,
    alignment: WallAlignmentSample,
    downstream_u: float,
    design_section: DesignSection,
) -> tuple[SectionSegment, ...]:
    clipped = clip_section_segments_to_u_interval(
        intersect_surface_with_profile(actual_surface, alignment),
        0.0,
        downstream_u,
    )
    design_points = tuple(
        point
        for element in design_section.elements
        for point in (element.start, element.end)
    )
    if not design_points:
        return ()
    return clip_section_segments_to_z_range(
        clipped,
        min(point.z for point in design_points),
        max(point.z for point in design_points),
    )


def _pipeline_diagnostics(
    placement: WallProfilePlacementResult,
) -> list[ProfileSectionDiagnostic]:
    diagnostics = [
        ProfileSectionDiagnostic(item.code, item.message, item.source)
        for item in placement.diagnostics
    ]
    diagnostics.extend(
        ProfileSectionDiagnostic(
            item.code,
            item.message,
            item.source,
            sector_result.sector_id,
        )
        for sector_result in placement.sector_results
        for item in sector_result.diagnostics
    )
    return diagnostics


def _diagnostic_from_issue(
    issue: _ProvenanceIssue,
    sector_id: str,
    trace: ProfileTrace | None = None,
    interval_index: int | None = None,
) -> ProfileSectionDiagnostic:
    return ProfileSectionDiagnostic(
        issue.code,
        str(issue),
        "profile_sections",
        sector_id,
        trace.station_index if trace is not None else None,
        interval_index,
    )


def build_v2_profile_sections(
    design_surface: TriangleSurface,
    actual_surface: TriangleSurface,
    assessment_polygon: PlanPolygon,
    role_mapping: SurfaceRoleMapping,
    *,
    requested_spacing_m: float = 3.0,
) -> ProfileSectionAssemblyResult:
    """Assemble production-compatible sections over accepted v2 placements."""
    topology = build_design_topology_index(design_surface, role_mapping)
    extraction = extract_wall_sectors(
        design_surface, topology, assessment_polygon
    )
    placement = place_wall_sector_extraction_profiles(
        extraction,
        assessment_polygon,
        requested_spacing_m=requested_spacing_m,
    )
    diagnostics = _pipeline_diagnostics(placement)
    sectors = {sector.sector_id: sector for sector in extraction.sectors}
    crest_lines: list[WallTransitionLine] = []
    toe_lines: list[WallTransitionLine] = []
    profiles: list[TransverseProfile] = []

    for sector_result in placement.sector_results:
        if (
            sector_result.invariants is not None
            and not sector_result.invariants.all_valid
        ):
            diagnostics.append(ProfileSectionDiagnostic(
                "sector_horizontal_invariants_failed",
                "Phase-3 horizontal invariants prevent production section assembly",
                "horizontal_invariants",
                sector_result.sector_id,
            ))
            continue
        sector = sectors[sector_result.sector_id]
        try:
            upper_portal = _portal_for(
                topology, sector.upper_portal_id, guide_name="upper"
            )
            crest_line = _transition_from_guide(
                sector.upper_guide, upper_portal, kind="crest"
            )
            toe_line = None
            if sector.lower_guide is not None:
                lower_portal = _portal_for(
                    topology, sector.lower_portal_id, guide_name="lower"
                )
                toe_line = _transition_from_guide(
                    sector.lower_guide, lower_portal, kind="toe"
                )
        except _ProvenanceIssue as issue:
            diagnostics.append(_diagnostic_from_issue(issue, sector.sector_id))
            continue

        component_index = len(crest_lines)
        sector_profiles: list[TransverseProfile] = []
        for interval_result in sector_result.interval_results:
            if not interval_result.supported:
                continue
            interval_profiles: list[TransverseProfile] = []
            interval_failure: tuple[ProfileTrace, str] | None = None
            for trace in interval_result.profiles:
                try:
                    upper = _resolve_exact_guide_vertex(
                        trace.plan_start, upper_portal, guide_name="upper"
                    )
                    alignment = profile_trace_to_alignment(
                        trace,
                        upper,
                        boundary_component_index=component_index,
                    )
                    downstream_u = _downstream_u(trace)
                    design_segments, design_section = _design_section_for_trace(
                        design_surface,
                        role_mapping,
                        alignment,
                        downstream_u,
                    )
                    terminal = _exact_terminal_point(
                        trace,
                        sector,
                        topology,
                        downstream_u,
                        design_segments,
                    )
                    profile = TransverseProfile(
                        alignment=alignment,
                        design_segments=design_segments,
                        actual_segments=_actual_segments(
                            actual_surface,
                            alignment,
                            downstream_u,
                            design_section,
                        ),
                        design_section=design_section,
                        assessment_u_interval=(0.0, downstream_u),
                        external_toe=(
                            terminal if sector.lower_guide is not None else None
                        ),
                    )
                    vertical_issue = profile_vertical_order_issue(profile)
                    if vertical_issue is not None:
                        diagnostics.append(ProfileSectionDiagnostic(
                            "vertical_profile_invariant_failed",
                            vertical_issue,
                            "vertical_invariants",
                            sector.sector_id,
                            trace.station_index,
                            interval_result.interval_index,
                        ))
                        interval_failure = trace, vertical_issue
                        break
                    interval_profiles.append(profile)
                except _ProvenanceIssue as issue:
                    diagnostics.append(_diagnostic_from_issue(
                        issue,
                        sector.sector_id,
                        trace,
                        interval_result.interval_index,
                    ))
                    interval_failure = trace, str(issue)
                    break
                except ValueError as issue:
                    diagnostics.append(ProfileSectionDiagnostic(
                        "profile_section_assembly_failed",
                        str(issue),
                        "profile_sections",
                        sector.sector_id,
                        trace.station_index,
                        interval_result.interval_index,
                    ))
                    interval_failure = trace, str(issue)
                    break
            if interval_failure is not None or not interval_profiles:
                failed_trace, reason = interval_failure or (
                    None,
                    "Supported interval contained no ProfileTrace",
                )
                diagnostics.append(ProfileSectionDiagnostic(
                    "interval_section_assembly_failed",
                    (
                        f"Supported interval {interval_result.interval_index} was "
                        f"discarded atomically: {reason}"
                    ),
                    "profile_sections",
                    sector.sector_id,
                    (
                        failed_trace.station_index
                        if failed_trace is not None else None
                    ),
                    interval_result.interval_index,
                ))
                continue
            sector_profiles.extend(interval_profiles)
        if not sector_profiles:
            continue
        crest_lines.append(crest_line)
        if toe_line is not None:
            toe_lines.append(toe_line)
        profiles.extend(sector_profiles)

    if not profiles:
        raise ProfileSectionAssemblyError(
            "No usable v2 Design wall profiles could be assembled",
            placement_result=placement,
            diagnostics=tuple(diagnostics),
        )
    profiles_tuple = tuple(profiles)
    profile_set = WallProfileSet(
        crest_lines=tuple(crest_lines),
        toe_lines=tuple(toe_lines),
        profiles=profiles_tuple,
        design_variants=build_design_variants(profiles_tuple),
    )
    return ProfileSectionAssemblyResult(
        profile_set,
        placement,
        tuple(diagnostics),
    )


__all__ = [
    "ProfileSectionAssemblyError",
    "ProfileSectionAssemblyResult",
    "ProfileSectionDiagnostic",
    "build_v2_profile_sections",
    "profile_trace_to_alignment",
]
