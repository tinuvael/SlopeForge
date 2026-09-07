"""Pure geometry primitives for design-vs-actual wall conformance."""

from .design import (
    extract_design_transition_lines,
    extract_design_wall_topology,
    sample_wall_alignment,
    select_design_alignment,
    select_primary_crest_line,
    transition_length_in_area,
)
from .engine import build_transverse_profiles as _build_transverse_profiles
from .invariants import (
    enforce_profile_engineering_invariants,
    profile_vertical_order_issue,
)
from .models import (
    PROTOTYPE_DESIGN_ROLE_MAPPING,
    DesignAlignmentBoundary,
    DesignBoundaryEdge,
    ExternalWallBoundary,
    DesignWallTopology,
    SectionPoint,
    SectionSegment,
    SurfaceRoleMapping,
    TransverseProfile,
    UpperCrestStationEvaluation,
    WallAlignmentSample,
    WallProfileSet,
    WallTransitionLine,
    semantic_value_token,
)
from .sections import clip_section_segments_to_z_range, intersect_surface_with_profile
from .semantic_sections import build_design_section, build_design_variants
from .measurements import (
    BreakpointCandidateDiagnostic,
    KpiAggregate,
    LandmarkDetectionDiagnostic,
    LandmarkSource,
    MeasurementDetection,
    ProfileLandmarkDiagnostics,
    ProfileLandmark,
    WallAdditionalGeometrySummary,
    WallMeasurementSummary,
    WallMeasurementTolerances,
    WallProfileLandmarks,
    WallProfileMeasurements,
    aggregate_measurements,
    detect_actual_landmarks,
    diagnose_profile_landmark_set,
    diagnose_profile_landmarks,
    extract_design_landmarks,
    has_compatible_actual_wall_section,
    measure_profile,
    measure_profiles,
)
from .profile_sections import (
    ProfileSectionAssemblyError,
    ProfileSectionAssemblyResult,
    ProfileSectionDiagnostic,
    build_v2_profile_sections,
    profile_trace_to_alignment,
)
from .alignment_placement import (
    AlignmentPlacementDiagnostic,
    AlignmentProfilePlacement,
    AlignmentProfilePlacementResult,
    AlignmentProfileSectionResult,
    WallAlignment,
    build_alignment_profile_sections,
    place_profiles_from_alignment,
)


def build_transverse_profiles(*args, **kwargs) -> WallProfileSet:
    """Build profiles and enforce non-negotiable open-pit wall geometry."""
    return enforce_profile_engineering_invariants(
        _build_transverse_profiles(*args, **kwargs)
    )


__all__ = [
    "SectionPoint",
    "SectionSegment",
    "SurfaceRoleMapping",
    "PROTOTYPE_DESIGN_ROLE_MAPPING",
    "DesignAlignmentBoundary",
    "DesignBoundaryEdge",
    "ExternalWallBoundary",
    "DesignWallTopology",
    "TransverseProfile",
    "UpperCrestStationEvaluation",
    "WallAlignmentSample",
    "WallProfileSet",
    "WallTransitionLine",
    "build_transverse_profiles",
    "extract_design_transition_lines",
    "extract_design_wall_topology",
    "intersect_surface_with_profile",
    "clip_section_segments_to_z_range",
    "build_v2_profile_sections",
    "profile_trace_to_alignment",
    "ProfileSectionAssemblyError",
    "ProfileSectionAssemblyResult",
    "ProfileSectionDiagnostic",
    "sample_wall_alignment",
    "select_design_alignment",
    "select_primary_crest_line",
    "transition_length_in_area",
    "semantic_value_token",
    "build_design_section",
    "build_design_variants",
    "KpiAggregate",
    "BreakpointCandidateDiagnostic",
    "LandmarkDetectionDiagnostic",
    "LandmarkSource",
    "MeasurementDetection",
    "ProfileLandmarkDiagnostics",
    "ProfileLandmark",
    "WallAdditionalGeometrySummary",
    "WallMeasurementSummary",
    "WallMeasurementTolerances",
    "WallProfileLandmarks",
    "WallProfileMeasurements",
    "aggregate_measurements",
    "detect_actual_landmarks",
    "diagnose_profile_landmark_set",
    "diagnose_profile_landmarks",
    "extract_design_landmarks",
    "has_compatible_actual_wall_section",
    "measure_profile",
    "measure_profiles",
    "enforce_profile_engineering_invariants",
    "profile_vertical_order_issue",
    "AlignmentPlacementDiagnostic",
    "AlignmentProfilePlacement",
    "AlignmentProfilePlacementResult",
    "AlignmentProfileSectionResult",
    "WallAlignment",
    "build_alignment_profile_sections",
    "place_profiles_from_alignment",
]
