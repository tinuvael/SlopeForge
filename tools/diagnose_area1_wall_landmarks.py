"""Read-only runtime landmark report for the currently selected Area 1 case.

This development utility deliberately follows the Wall Conformance tab's
calculation path: it resolves the active Assessment geometry, loads the
persisted Wall Alignment through ``SqlAlchemyAssessmentWrites``, then invokes
``WallConformanceDiagnosticService.calculate_current``.  It writes no database
state and the JSON report is not consumed by production code.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, replace
from math import hypot
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy import select

from app.connection_settings import ConnectionSettingsStore
from application.services.project_surfaces import ProjectSurfaceDatasetService
from application.services.wall_conformance import (
    WallConformanceDiagnosticService,
    WallConformanceDiagnosticSettings,
)
from database import assessment_models as assessment_orm
from database.connection import create_database_engine, create_session_factory
from database.models import Domain
from domain.geometry.types import PlanPolygon
from domain.wall_conformance import (
    WallMeasurementTolerances,
    clip_section_segments_to_z_range,
    diagnose_profile_landmark_set,
    diagnose_profile_landmarks,
    intersect_surface_with_profile,
)
from domain.wall_conformance.measurement_context import ACTUAL_DESIGN_Z_MARGIN_M
from domain.wall_conformance.measurements import (
    _component_containing_landmark,
    _design_guide,
    _fit_line,
    _normalized_segments,
    _outer_boundary_crest_search_z_interval,
    _segment_components,
)
from domain.wall_conformance.sections import clip_section_segments_to_u_interval
from infrastructure.db.assessment_writes import SqlAlchemyAssessmentWrites
from infrastructure.files.project_geometry import ProjectGeometryFileStorage
from infrastructure.geometry_import.surfaces import import_surface_geometry
from repositories.project_surface_repository import ProjectSurfaceDatasetRepository


AREA_LOGICAL_ID = "AA-9FA43299"
SPACING_M = 3.0
EXPECTED_ACCEPTED_PROFILE_COUNT = 37
TARGET_PROFILE_NUMBERS = (6, 15, 22, 24, 25, 34, 36)


def _point(point) -> dict[str, float] | None:
    if point is None:
        return None
    return {"u": point.u, "z": point.z, "x": point.x, "y": point.y}


def _landmark(landmark) -> dict[str, Any]:
    return {
        "status": landmark.detection.state,
        "reason": landmark.detection.reason_code,
        "source": landmark.source,
        "message": landmark.detection.message,
        "point": _point(landmark.point),
    }


def _bounds(segments) -> dict[str, float] | None:
    points = [point for segment in segments for point in (segment.start, segment.end)]
    if not points:
        return None
    return {
        "u_min": min(point.u for point in points),
        "u_max": max(point.u for point in points),
        "z_min": min(point.z for point in points),
        "z_max": max(point.z for point in points),
    }


def _segments_payload(segments) -> dict[str, Any]:
    return {"count": len(segments), "bounds": _bounds(segments)}


def _candidate(candidate, expected, tolerances) -> dict[str, Any]:
    payload = asdict(candidate)
    payload["point"] = _point(candidate.point)
    payload["expected_point"] = _point(candidate.expected_point)
    payload["search_window_included"] = (
        abs(candidate.delta_u_m) <= tolerances.search_half_window_m
    )
    payload["candidate_envelope_included"] = (
        abs(candidate.delta_u_m) <= tolerances.candidate_half_window_m
    )
    payload["nominal_search_is_hard_gate"] = False
    payload["ambiguity_margin"] = tolerances.ambiguity_score_margin
    return payload


def _candidate_summary(candidates, expected, tolerances) -> dict[str, Any] | None:
    if not candidates:
        return None
    # A failed candidate may be closer than an accepted one.  The report retains
    # all candidates and highlights the closest physical alternative as the
    # requested "best candidate evidence" for a missing landmark.
    chosen = min(
        candidates,
        key=lambda item: (
            item.spatial_offset_m,
            item.rejection_gate is not None,
            float("inf") if item.score is None else item.score,
            item.point.u,
            item.point.z,
        ),
    )
    return _candidate(chosen, expected, tolerances)


def _stage_payload(profile, actual_surface) -> tuple[dict[str, Any], tuple, tuple, tuple]:
    """Expose raw, display, and detector-context section representations."""
    raw = intersect_surface_with_profile(actual_surface, profile.alignment)
    visible = profile.actual_segments
    context = profile.measurement_context
    if context is None:
        return {
            "raw_actual": _segments_payload(raw),
            "measurement_context_actual": _segments_payload(visible),
            "detector_input_actual": _segments_payload(visible),
            "visible_actual": _segments_payload(visible),
            "measurement_context": None,
        }, raw, raw, visible

    after_u = raw
    for u_min, u_max in context.u_intervals:
        # Context currently has one combined interval.  Preserve this loop if
        # the domain later represents more than one interval.
        after_u = clip_section_segments_to_u_interval(after_u, u_min, u_max)
    after_z = after_u
    if context.design_z_interval is not None:
        z_min, z_max = context.design_z_interval
        after_z = clip_section_segments_to_z_range(
            after_u,
            z_min - ACTUAL_DESIGN_Z_MARGIN_M,
            z_max + ACTUAL_DESIGN_Z_MARGIN_M,
        )
    return {
        "raw_actual": _segments_payload(raw),
        "after_measurement_context_u": _segments_payload(after_u),
        "design_z_display_fallback": _segments_payload(after_z),
        "measurement_context_actual": _segments_payload(context.actual_segments),
        "detector_input_actual": _segments_payload(context.actual_segments),
        "visible_actual": _segments_payload(visible),
        "measurement_context": {
            "u_intervals": list(context.u_intervals),
            "design_z_interval": context.design_z_interval,
            "actual_design_z_margin_m": ACTUAL_DESIGN_Z_MARGIN_M,
        },
    }, raw, after_u, context.actual_segments


def _profile_with_detector_actual(profile, segments):
    """Use a shadow Actual representation only for diagnostic comparison."""
    if profile.measurement_context is None:
        return replace(profile, actual_segments=segments)
    return replace(
        profile,
        measurement_context=replace(profile.measurement_context, actual_segments=segments),
    )


def _pre_scoring_stage(diagnostic, stages) -> tuple[bool, str | None]:
    """Identify the first stage which removed Actual support for this search."""
    if diagnostic.expected_point is None:
        return False, "design_landmark_unsupported"
    if stages["raw_actual"]["count"] == 0:
        return False, "raw_intersection"
    if stages.get("after_measurement_context_u", stages["raw_actual"])["count"] == 0:
        return False, "measurement_context_u_bounds"
    if stages["detector_input_actual"]["count"] == 0:
        return False, "detector_input_construction"
    if diagnostic.local_actual_segment_count == 0:
        return False, "detector_locality_window"
    return True, None


def _side_visibility(diagnostic) -> dict[str, Any]:
    """Numeric support evidence for the two expected sides at one transition."""
    if not diagnostic.candidates:
        return {
            "left_support_present": False,
            "right_support_present": False,
            "basis": "no_transition_vertex_candidate",
        }
    candidate = min(
        diagnostic.candidates,
        key=lambda item: (item.spatial_offset_m, item.point.u, item.point.z),
    )
    return {
        "left_support_present": bool(candidate.left_support_m and candidate.left_support_m > 0),
        "right_support_present": bool(candidate.right_support_m and candidate.right_support_m > 0),
        "left_support_m": candidate.left_support_m,
        "right_support_m": candidate.right_support_m,
        "candidate_point": _point(candidate.point),
        "basis": "nearest_transition_vertex_side_fit",
    }


def _expected_side_coverage(segments, expected, tolerances) -> dict[str, Any]:
    if expected is None:
        return {"left_visible": False, "right_visible": False, "reason": "design_landmark_unsupported"}
    local = clip_section_segments_to_u_interval(
        segments,
        expected.u - tolerances.context_half_window_m,
        expected.u + tolerances.context_half_window_m,
    )
    points = [point for segment in local for point in (segment.start, segment.end)]
    left = [hypot(point.u - expected.u, point.z - expected.z) for point in points if point.u < expected.u]
    right = [hypot(point.u - expected.u, point.z - expected.z) for point in points if point.u > expected.u]
    left_extent = max(left) if left else 0.0
    right_extent = max(right) if right else 0.0
    return {
        "left_visible": left_extent >= tolerances.minimum_side_support_m,
        "right_visible": right_extent >= tolerances.minimum_side_support_m,
        "left_extent_m": left_extent,
        "right_extent_m": right_extent,
        "basis": "detector_input_points_relative_to_expected_design_transition",
    }


def _transition_payload(name, diagnostic, tolerances, stages, raw_shadow, u_shadow, detector_segments) -> dict[str, Any]:
    final = diagnostic.final
    present, removed_stage = _pre_scoring_stage(diagnostic, stages)
    candidates = tuple(diagnostic.candidates)
    return {
        "expected_design": _point(diagnostic.expected_point),
        "actual": _landmark(final),
        "local_actual_segment_count": diagnostic.local_actual_segment_count,
        "local_gap_m": diagnostic.local_gap_m,
        "best_score": diagnostic.best_score,
        "runner_up_score": diagnostic.runner_up_score,
        "ambiguity_margin": diagnostic.ambiguity_margin,
        "geometry_present_before_detector_scoring": present,
        "first_missing_geometry_stage": removed_stage,
        "expected_side_coverage": _expected_side_coverage(
            detector_segments, diagnostic.expected_point, tolerances
        ),
        "preprocessing_shadow": {
            "raw_intersection": _landmark(getattr(raw_shadow, name).final),
            "after_measurement_context_u": _landmark(getattr(u_shadow, name).final),
            "u_context_removed_detectable_candidate": (
                getattr(raw_shadow, name).final.detection.reliable
                and not final.detection.reliable
                and not getattr(u_shadow, name).final.detection.reliable
            ),
            "design_z_filter_is_not_detector_input": True,
        },
        "best_candidate": _candidate_summary(candidates, diagnostic.expected_point, tolerances),
        "candidates": [_candidate(item, diagnostic.expected_point, tolerances) for item in candidates],
        "side_visibility": _side_visibility(diagnostic),
    }


def _overview_actual_payload(profile, diagnostic) -> dict[str, Any]:
    """Mirror the Overview's landmark-bounded Actual source and extent."""
    upper_start = diagnostic.upper_berm_start.final
    lower_toe = diagnostic.lower_toe.final
    if (
        not upper_start.detection.reliable
        or not lower_toe.detection.reliable
        or upper_start.point is None
        or lower_toe.point is None
        or lower_toe.point.u < upper_start.point.u
    ):
        return {
            "included": False,
            "reason": "physical_interval_unavailable",
            "physical_interval_u_m": None,
            "rendered_actual": _segments_payload(()),
        }
    context = profile.measurement_context
    source = context.actual_segments if context is not None else profile.actual_segments
    interval = (upper_start.point.u, lower_toe.point.u)
    return {
        "included": True,
        "reason": "landmark_bounded_measurement_context",
        "physical_interval_u_m": {"u_min": interval[0], "u_max": interval[1]},
        "rendered_actual": _segments_payload(
            clip_section_segments_to_u_interval(source, *interval)
        ),
    }


def _outer_boundary_crest_search_payload(profile, diagnostic, tolerances) -> dict[str, Any] | None:
    """Expose the Design-Z anchored, toe-connected outer crest search.

    This is diagnostic-only: it mirrors the detector's local candidate scope
    without influencing measurement output.
    """
    crest = diagnostic.upper_crest.final
    toe = diagnostic.lower_toe.final
    if (
        not crest.detection.reliable
        or not toe.detection.reliable
        or crest.point is None
        or toe.point is None
        or profile.measurement_context is None
    ):
        return None
    source = profile.measurement_context.actual_segments
    segments = _normalized_segments(source, tolerances)
    components = _segment_components(segments, tolerances.actual_connection_tolerance_m)
    component = _component_containing_landmark(segments, toe.point, tolerances)
    if component is None:
        return {"selected_component_index": None, "reason": "toe_not_in_component"}
    component_index = next(
        (index for index, candidate in enumerate(components) if candidate == component), None
    )
    guide = _design_guide(profile, tolerances)
    expected = guide.upper_crest
    z_interval = _outer_boundary_crest_search_z_interval(
        profile, expected, tolerances
    )
    raw_in_band = () if z_interval is None else clip_section_segments_to_z_range(
        source, *z_interval
    )
    face = clip_section_segments_to_u_interval(component, crest.point.u, toe.point.u)
    face_fit = _fit_line(face)
    return {
        "design_crest_z": None if expected is None else expected.point.z,
        "crest_search_z_interval": z_interval,
        "raw_actual_inside_crest_search_band": _segments_payload(raw_in_band),
        "selected_component_index": component_index,
        "selected_component_segment_count": len(component),
        "face_extent": {"start": _point(crest.point), "end": _point(toe.point)},
        "face_length_m": sum(hypot(
            segment.end.u - segment.start.u,
            segment.end.z - segment.start.z,
        ) for segment in face),
        "fitted_face": None if face_fit is None else {
            "angle_deg": face_fit.angle_deg,
            "support_m": face_fit.support_m,
            "rms_residual_m": face_fit.rms_residual_m,
        },
        "physical_crest_candidates": [
            _candidate(candidate, expected, tolerances)
            for candidate in diagnostic.upper_crest.candidates
        ],
        "crest_source": crest.source,
    }


def _profile_payload(index, profile, diagnostic, actual_surface, tolerances) -> dict[str, Any]:
    stages, raw, after_u, detector_actual = _stage_payload(profile, actual_surface)
    raw_shadow = diagnose_profile_landmarks(
        _profile_with_detector_actual(profile, raw), tolerances
    )
    u_shadow = diagnose_profile_landmarks(
        _profile_with_detector_actual(profile, after_u), tolerances
    )
    transitions = {
        "upper_berm_start": _transition_payload(
            "upper_berm_start", diagnostic.upper_berm_start, tolerances, stages,
            raw_shadow, u_shadow, detector_actual,
        ),
        "upper_crest": _transition_payload(
            "upper_crest", diagnostic.upper_crest, tolerances, stages,
            raw_shadow, u_shadow, detector_actual,
        ),
        "lower_toe": _transition_payload(
            "lower_toe", diagnostic.lower_toe, tolerances, stages,
            raw_shadow, u_shadow, detector_actual,
        ),
    }
    upper_start_sides = transitions["upper_berm_start"]["expected_side_coverage"]
    crest_sides = transitions["upper_crest"]["expected_side_coverage"]
    toe_sides = transitions["lower_toe"]["expected_side_coverage"]
    return {
        "profile_number": index + 1,
        "profile_index_zero_based": index,
        "chainage_m": profile.alignment.chainage_m,
        "actual_segment_representations": stages,
        "overview_actual": _overview_actual_payload(profile, diagnostic),
        "outer_boundary_crest_search": _outer_boundary_crest_search_payload(
            profile, diagnostic, tolerances
        ),
        "landmarks": transitions,
        "expected_physical_side_visibility": {
            "previous_face": upper_start_sides["left_visible"],
            "upper_berm": upper_start_sides["right_visible"] and crest_sides["left_visible"],
            "assessed_face": crest_sides["right_visible"] and toe_sides["left_visible"],
            "lower_platform_or_berm": toe_sides["right_visible"],
            "basis": "expected-side coverage in detector input; Actual has no trusted semantic labels",
        },
    }


def _histogram(profiles, landmark_name: str) -> dict[str, int]:
    return dict(sorted(Counter(
        profile["landmarks"][landmark_name]["actual"]["reason"]
        for profile in profiles
    ).items()))


def _distribution(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "minimum": min(values),
        "median": median(values),
        "mean": mean(values),
        "maximum": max(values),
    }


def _candidate_gate_histograms(profiles) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for name in ("upper_berm_start", "upper_crest", "lower_toe"):
        gates = Counter(
            candidate["rejection_gate"] or "accepted_candidate"
            for profile in profiles
            for candidate in profile["landmarks"][name]["candidates"]
        )
        result[name] = dict(sorted(gates.items()))
    return result


def _missing_candidate_evidence(profiles) -> dict[str, Any]:
    result = {}
    for name in ("upper_berm_start", "upper_crest", "lower_toe"):
        rejected = [
            candidate
            for profile in profiles
            if profile["landmarks"][name]["actual"]["status"] != "detected"
            for candidate in profile["landmarks"][name]["candidates"]
        ]
        result[name] = {
            "candidate_count": len(rejected),
            "spatial_offset_m": _distribution([
                candidate["spatial_offset_m"] for candidate in rejected
            ]),
            "left_orientation_error_deg": _distribution([
                candidate["left_orientation_error_deg"] for candidate in rejected
                if candidate["left_orientation_error_deg"] is not None
            ]),
            "right_orientation_error_deg": _distribution([
                candidate["right_orientation_error_deg"] for candidate in rejected
                if candidate["right_orientation_error_deg"] is not None
            ]),
        }
    return result


def _load_runtime_context(profile_id: str | None) -> dict[str, Any]:
    store = ConnectionSettingsStore()
    selected_id = profile_id or store.last_profile_id()
    if not selected_id:
        raise RuntimeError("No last-used saved SlopeForge connection exists")
    connection_profile = store.runtime_profile(selected_id)
    settings = connection_profile.to_settings()
    if settings.storage_root is None:
        raise RuntimeError("The selected connection has no Project file storage")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        row = session.execute(
            select(
                assessment_orm.AssessmentArea,
                assessment_orm.AssessmentAreaGeometryRevision,
                Domain,
            )
            .join(Domain, Domain.id == assessment_orm.AssessmentArea.domain_id)
            .join(
                assessment_orm.AssessmentAreaGeometryRevision,
                assessment_orm.AssessmentAreaGeometryRevision.assessment_area_id
                == assessment_orm.AssessmentArea.id,
            )
            .where(
                assessment_orm.AssessmentArea.logical_id == AREA_LOGICAL_ID,
                assessment_orm.AssessmentArea.is_archived.is_(False),
                assessment_orm.AssessmentAreaGeometryRevision.is_active.is_(True),
            )
        ).one_or_none()
    if row is None:
        raise RuntimeError(f"Active Assessment Area {AREA_LOGICAL_ID} was not found")
    area, geometry_revision, domain = row
    alignment = SqlAlchemyAssessmentWrites(session_factory).load_wall_alignment(
        area.domain_id, area.logical_id, geometry_revision.logical_id
    )
    if alignment is None:
        raise RuntimeError("The active Assessment geometry has no persisted Wall Alignment")
    surface_service = ProjectSurfaceDatasetService(
        ProjectSurfaceDatasetRepository(session_factory),
        ProjectGeometryFileStorage(settings.storage_root),
        import_surface_geometry,
    )
    return {
        "connection_profile_id": selected_id,
        "session_factory": session_factory,
        "area": area,
        "geometry_revision": geometry_revision,
        "domain": domain,
        "alignment": alignment,
        "surface_service": surface_service,
    }


def run(profile_id: str | None, output: Path) -> dict[str, Any]:
    context = _load_runtime_context(profile_id)
    area = context["area"]
    revision = context["geometry_revision"]
    domain = context["domain"]
    service = WallConformanceDiagnosticService(context["surface_service"])
    result = service.calculate_current(
        domain.site_id,
        PlanPolygon.from_dict(revision.final_geometry_json),
        context["alignment"],
        WallConformanceDiagnosticSettings(spacing_m=SPACING_M),
    )
    profile_count = len(result.profile_sections.profiles)
    reproduction = {
        "assessment_area": {"logical_id": area.logical_id, "name": area.name},
        "geometry_revision": {
            "logical_id": revision.logical_id,
            "revision_number": revision.revision_number,
            "min_elevation_m": None if revision.min_elevation_m is None else float(revision.min_elevation_m),
            "max_elevation_m": None if revision.max_elevation_m is None else float(revision.max_elevation_m),
        },
        "project_domain": {"site_id": domain.site_id, "domain_id": domain.id, "domain_name": domain.name},
        "design": {"logical_id": result.design_dataset.logical_id, "revision": result.design_dataset.revision_number, "triangles": result.design_dataset.triangle_count},
        "actual": {"logical_id": result.actual_dataset.logical_id, "revision": result.actual_dataset.revision_number, "triangles": result.actual_dataset.triangle_count},
        "wall_alignment": {"vertex_count": len(result.wall_alignment.points), "length_m": result.wall_alignment.length_m},
        "spacing_m": result.settings.spacing_m,
        "accepted_profile_count": profile_count,
        "placement_diagnostic_count": len(result.diagnostics),
        "expected_accepted_profile_count": EXPECTED_ACCEPTED_PROFILE_COUNT,
    }
    if profile_count != EXPECTED_ACCEPTED_PROFILE_COUNT:
        raise RuntimeError(
            "Runtime reproduction did not match the UI case: "
            + json.dumps(reproduction, ensure_ascii=False, default=str)
        )

    _design_row, design_import = context["surface_service"].load_dataset(
        domain.site_id, result.design_dataset.logical_id
    )
    _actual_row, actual_import = context["surface_service"].load_dataset(
        domain.site_id, result.actual_dataset.logical_id
    )
    design_surface = design_import.surface
    actual_surface = actual_import.surface
    if len(design_surface.triangles) != result.design_dataset.triangle_count:
        raise RuntimeError("Loaded Design geometry does not match the active Design dataset")
    if len(actual_surface.triangles) != result.actual_dataset.triangle_count:
        raise RuntimeError("Loaded Actual geometry does not match the active Actual dataset")

    tolerances = WallMeasurementTolerances()
    diagnostics = diagnose_profile_landmark_set(result.profile_sections.profiles, tolerances)
    profiles = [
        _profile_payload(index, profile, diagnostic, actual_surface, tolerances)
        for index, (profile, diagnostic) in enumerate(
            zip(result.profile_sections.profiles, diagnostics, strict=True)
        )
    ]
    detected_counts = Counter(
        sum(
            profile["landmarks"][name]["actual"]["status"] == "detected"
            for name in ("upper_berm_start", "upper_crest", "lower_toe")
        )
        for profile in profiles
    )
    target_profiles = {
        str(number): profiles[number - 1]
        for number in TARGET_PROFILE_NUMBERS
    }
    report = {
        "purpose": "read-only exact runtime landmark diagnostic",
        "reproduction": reproduction,
        "measurement_tolerances": asdict(tolerances),
        "profiles": profiles,
        "target_profiles": target_profiles,
        "summary": {
            "landmark_reason_histograms": {
                name: _histogram(profiles, name)
                for name in ("upper_berm_start", "upper_crest", "lower_toe")
            },
            "candidate_gate_histograms": _candidate_gate_histograms(profiles),
            "profiles_by_detected_landmark_count": {
                "all_3": detected_counts[3],
                "exactly_2": detected_counts[2],
                "exactly_1": detected_counts[1],
                "none": detected_counts[0],
            },
            "missing_candidate_evidence": _missing_candidate_evidence(profiles),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-profile-id")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/validation/area1_wall_landmarks.json"),
    )
    args = parser.parse_args()
    report = run(args.connection_profile_id, args.output)
    print(json.dumps({
        "output": str(args.output),
        "accepted_profile_count": report["reproduction"]["accepted_profile_count"],
        "landmark_reason_histograms": report["summary"]["landmark_reason_histograms"],
        "profiles_by_detected_landmark_count": report["summary"]["profiles_by_detected_landmark_count"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
