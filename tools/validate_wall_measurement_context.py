"""Read-only finite real-data check using the existing local validation inputs.

Run as ``python -m tools.validate_wall_measurement_context``. No database writes,
new alignment selection, or alternate surface intersection workflow is used.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

from domain.geometry.types import PlanPoint
from domain.wall_conformance import (
    WallAlignment, build_alignment_profile_sections, measure_profiles,
)
from domain.wall_conformance.measurements import WallMeasurementTolerances
from domain.wall_conformance.sections import intersect_surface_with_profile, clip_section_segments_to_u_interval
from tools.validate_wall_alignment_real_data import (
    MANUAL_ALIGNMENT_XY, SPACING_M, _load_context,
)


def main() -> None:
    context = _load_context()
    assembly = build_alignment_profile_sections(
        alignment=WallAlignment(tuple(PlanPoint(*point) for point in MANUAL_ALIGNMENT_XY)),
        design_surface=context['design'], actual_surface=context['actual'],
        assessment_polygon=context['assessment'], role_mapping=context['mapping'],
        spacing_m=SPACING_M,
    )
    measurements = measure_profiles(assembly.profiles)
    supported = [
        i for i, measurement in enumerate(measurements)
        if any(status.reliable for status in (
            measurement.angle_status, measurement.berm_status, measurement.toe_status,
        ))
    ]
    extended = [
        i for i, profile in enumerate(assembly.profiles)
        if profile.measurement_context.actual_segments and not profile.actual_segments
    ]
    indices = sorted({
        0, len(measurements) // 2, len(measurements) - 1,
        *(supported[:1]), *(extended[:1]),
    }) if measurements else []
    rows = []
    for index in indices:
        profile, measurement = assembly.profiles[index], measurements[index]
        lower, upper = profile.assessment_u_interval
        tolerance = WallMeasurementTolerances().design_connection_tolerance_m
        u_only_actual = clip_section_segments_to_u_interval(
            intersect_surface_with_profile(context['actual'], profile.alignment),
            lower, upper,
        )

        def landmark_payload(landmark):
            point = landmark.point
            return {
                'u_z': None if point is None else [point.u, point.z],
                'outside_assessment': (
                    None if point is None
                    else not lower - tolerance <= point.u <= upper + tolerance
                ),
                'status': asdict(landmark.detection),
            }

        rows.append({
            'chainage_m': measurement.chainage_m,
            'assessment_u_interval': profile.assessment_u_interval,
            'measurement_u_intervals': profile.measurement_context.u_intervals,
            'actual_segment_counts': {
                'evaluation': len(profile.actual_segments),
                'u_mask_before_z_clip': len(u_only_actual),
                'context': len(profile.measurement_context.actual_segments),
            },
            'actual_context_z_range': (
                [min(p.z for s in profile.measurement_context.actual_segments for p in (s.start, s.end)),
                 max(p.z for s in profile.measurement_context.actual_segments for p in (s.start, s.end))]
                if profile.measurement_context.actual_segments else None
            ),
            'landmarks': {
                source: {
                    name: landmark_payload(getattr(landmarks, name))
                    for name in ('upper_berm_start', 'upper_crest', 'lower_toe')
                }
                for source, landmarks in (
                    ('design', measurement.design_landmarks),
                    ('actual', measurement.actual_landmarks),
                )
            },
            'measurements': {
                key: value for key, value in asdict(measurement).items()
                if key not in ('design_landmarks', 'actual_landmarks', 'chainage_m')
            },
        })
    print(json.dumps({
        'profile_count': len(measurements),
        'profiles_with_actual_evaluation': sum(bool(p.actual_segments) for p in assembly.profiles),
        'profiles_with_actual_context': sum(
            bool(p.measurement_context.actual_segments) for p in assembly.profiles
        ),
        'kpi_status_counts': {
            name: dict(Counter(getattr(m, name).reason_code for m in measurements))
            for name in ('angle_status', 'berm_status', 'toe_status')
        },
        'examples': rows,
    }, indent=2))


if __name__ == '__main__':
    main()
