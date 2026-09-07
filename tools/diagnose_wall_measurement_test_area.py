"""Read-only detector evidence for the existing clean Wall Conformance test area.

Run ``python -m tools.diagnose_wall_measurement_test_area``.  It uses the same
``assembled()`` fixture as the measurement-context tests, prints every generated
profile and writes nothing.  The fixture represents previous Face -> upper Berm
-> assessed Face -> lower Berm geometry on both Design and Actual surfaces.
"""
from __future__ import annotations

import json
from collections import Counter
from math import isfinite
from statistics import median

from domain.wall_conformance import (
    WallMeasurementTolerances,
    diagnose_profile_landmark_set,
)
from domain.wall_conformance.measurement_context import ACTUAL_DESIGN_Z_MARGIN_M
from domain.wall_conformance.sections import (
    clip_section_segments_to_u_interval,
    clip_section_segments_to_z_range,
    intersect_surface_with_profile,
)
from tests.test_wall_conformance_measurement_context import assembled


LANDMARKS = ("upper_berm_start", "upper_crest", "lower_toe")


def _point(point):
    return None if point is None else {"u": point.u, "z": point.z}


def _distribution(values):
    values = sorted(value for value in values if value is not None and isfinite(value))
    return None if not values else {
        "count": len(values), "minimum": values[0], "median": median(values), "maximum": values[-1],
    }


def _candidate_payload(candidate):
    return {
        "actual_u_z": _point(candidate.point),
        "expected_u_z": _point(candidate.expected_point),
        "delta_u_m": candidate.delta_u_m,
        "delta_z_m": candidate.delta_z_m,
        "spatial_offset_m": candidate.spatial_offset_m,
        "left_support_m": candidate.left_support_m,
        "right_support_m": candidate.right_support_m,
        "left_angle_deg": candidate.left_angle_deg,
        "right_angle_deg": candidate.right_angle_deg,
        "expected_left_angle_deg": candidate.expected_left_angle_deg,
        "expected_right_angle_deg": candidate.expected_right_angle_deg,
        "left_orientation_error_deg": candidate.left_orientation_error_deg,
        "right_orientation_error_deg": candidate.right_orientation_error_deg,
        "left_residual_m": candidate.left_residual_m,
        "right_residual_m": candidate.right_residual_m,
        "local_gap_m": candidate.local_gap_m,
        "score": candidate.score,
        "rejection_gate": candidate.rejection_gate,
    }


def _preprocessing(profile, raw_actual, landmark):
    context = profile.measurement_context
    local_count = landmark.local_actual_segment_count
    if local_count:
        return {"input_geometry_present_at_candidate_transition": True, "removed_by": None}
    if not raw_actual:
        removed_by = "surface_intersection"
    elif context is not None and not context.actual_segments:
        after_u = tuple(
            segment
            for interval in context.u_intervals
            for segment in clip_section_segments_to_u_interval(raw_actual, *interval)
        )
        if not after_u:
            removed_by = "measurement_context_u_bounds"
        elif context.design_z_interval is not None and not clip_section_segments_to_z_range(
            after_u,
            context.design_z_interval[0] - ACTUAL_DESIGN_Z_MARGIN_M,
            context.design_z_interval[1] + ACTUAL_DESIGN_Z_MARGIN_M,
        ):
            removed_by = "measurement_context_design_z_envelope"
        else:
            removed_by = "measurement_context"
    else:
        removed_by = "local_detector_window"
    return {
        "input_geometry_present_at_candidate_transition": False,
        "removed_by": removed_by,
    }


def _landmark_payload(profile, raw_actual, landmark):
    rejected = [candidate for candidate in landmark.candidates if candidate.rejection_gate]
    return {
        "final_status": landmark.final.detection.state,
        "reason": landmark.final.detection.reason_code,
        "message": landmark.final.detection.message,
        "final_actual_u_z": _point(landmark.final.point),
        "expected_design_u_z": _point(landmark.expected_point),
        "local_actual_segment_count": landmark.local_actual_segment_count,
        "local_gap_m": landmark.local_gap_m,
        "best_score": landmark.best_score,
        "runner_up_score": landmark.runner_up_score,
        "ambiguity_margin": landmark.ambiguity_margin,
        "preprocessing": _preprocessing(profile, raw_actual, landmark),
        "candidates": [_candidate_payload(candidate) for candidate in landmark.candidates],
        "rejected_candidate_count": len(rejected),
    }


def _hypotheses(landmark, settings):
    rejected = [candidate for candidate in landmark.candidates if candidate.rejection_gate]
    gates = {candidate.rejection_gate for candidate in rejected}
    failed = landmark.final.detection.state != "detected"
    return {
        "spatial_offset": "supported" if failed and "spatial_offset" in gates else "not_observed",
        "search_window": "not_observed",
        "missing_previous_face_context": (
            "supported" if landmark.final.detection.reason_code == "design_landmark_unsupported"
            else "not_observed"
        ),
            "physical_topology_or_residual": (
                "supported" if failed and gates.intersection({"transition_topology", "fit_residual", "line_fit_unavailable"})
                else "not_observed"
            ),
        "preprocessing": (
            "supported" if failed and landmark.local_actual_segment_count == 0 else "not_observed"
        ),
        "limits": {
                "design_distance_preference_m": settings.design_distance_preference_m,
            "search_half_window_m": settings.search_half_window_m,
        },
    }


def build_report():
    settings = WallMeasurementTolerances()
    assembly, actual_surface = assembled()
    diagnostics = diagnose_profile_landmark_set(assembly.profiles, settings)
    reports = []
    for index, (profile, diagnostic) in enumerate(zip(assembly.profiles, diagnostics)):
        raw_actual = intersect_surface_with_profile(actual_surface, profile.alignment)
        landmarks = {
            name: _landmark_payload(profile, raw_actual, getattr(diagnostic, name))
            for name in LANDMARKS
        }
        reports.append({
            "profile_index": index,
            "chainage_m": diagnostic.chainage_m,
            "actual_segments": {
                "raw": len(raw_actual),
                "evaluated_display": len(profile.actual_segments),
                "measurement_context": len(profile.measurement_context.actual_segments),
            },
            "landmarks": landmarks,
            "hypotheses": {name: _hypotheses(getattr(diagnostic, name), settings) for name in LANDMARKS},
        })

    summary = {}
    for name in LANDMARKS:
        landmark_reports = [profile["landmarks"][name] for profile in reports]
        candidates = [
            candidate for report in landmark_reports for candidate in report["candidates"]
            if candidate["rejection_gate"]
        ]
        summary[name] = {
            "final_reason_counts": dict(Counter(report["reason"] for report in landmark_reports)),
            "rejection_gate_counts": dict(Counter(candidate["rejection_gate"] for candidate in candidates)),
            "rejected_candidate_distributions": {
                "spatial_offset_m": _distribution(candidate["spatial_offset_m"] for candidate in candidates),
                "delta_u_m": _distribution(candidate["delta_u_m"] for candidate in candidates),
                "delta_z_m": _distribution(candidate["delta_z_m"] for candidate in candidates),
                "orientation_error_deg": _distribution(
                    value for candidate in candidates
                    for value in (candidate["left_orientation_error_deg"], candidate["right_orientation_error_deg"])
                ),
                "support_m": _distribution(
                    value for candidate in candidates
                    for value in (candidate["left_support_m"], candidate["right_support_m"])
                ),
            },
        }

    def first_profile(predicate):
        return next((report for report in reports if predicate(report)), None)

    samples = {
        "all_detected": first_profile(lambda report: all(
            report["landmarks"][name]["final_status"] == "detected" for name in LANDMARKS
        )),
        **{
            f"missing_{name}": first_profile(
                lambda report, name=name: report["landmarks"][name]["final_status"] != "detected"
            ) for name in LANDMARKS
        },
        "spatially_displaced": first_profile(lambda report: any(
            candidate["rejection_gate"] == "spatial_offset"
            for landmark in report["landmarks"].values()
            for candidate in landmark["candidates"]
        )),
    }
    return {
        "fixture": "tests.test_wall_conformance_measurement_context.assembled",
        "profile_count": len(reports),
        "profiles": reports,
        "summary": summary,
        "representative_profiles": samples,
    }


def main() -> None:
    print(json.dumps(build_report(), indent=2))


if __name__ == "__main__":
    main()
