"""Physical boundary support must not inherit the Assessment display clip."""
from dataclasses import replace

import pytest

from domain.wall_conformance import (
    build_alignment_profile_sections, measure_profile, extract_design_landmarks,
)
from domain.wall_conformance.measurements import WallMeasurementTolerances
from domain.wall_conformance.measurement_context import build_measurement_context
from domain.wall_conformance.models import DesignSection
from domain.wall_conformance.sections import (
    clip_section_segments_to_u_interval, clip_section_segments_to_z_range,
    intersect_surface_with_profile,
)
from tests.test_wall_conformance_measurements import design_profile, segment, point
from tests.test_wall_conformance_alignment_placement import (
    _strip_surface, _polygon, _straight_alignment, ROLE_MAPPING, _combined_surface,
)


ACTUAL = (
    segment((-6, 106), (-3.5, 100.2), 100),
    segment((-3.5, 100.2), (0.2, 99.7), 101),
    segment((0.2, 99.7), (4.5, 89.8), 102),
    segment((4.5, 89.8), (8, 89.6), 103),
)


def contextual_profile(actual=ACTUAL, interval=(0.5, 3.5), settings=None):
    source = design_profile()
    # The existing semantic reducer has already selected this assessed Face.
    face = source.design_section.elements[0]
    clipped = clip_section_segments_to_u_interval(source.design_segments, *interval)
    profile = replace(
        source, design_segments=clipped,
        design_section=DesignSection((replace(
            face, start=point(interval[0], 100 - 2.5 * interval[0]),
            end=point(interval[1], 100 - 2.5 * interval[1]),
        ),)),
        actual_segments=clip_section_segments_to_u_interval(actual, *interval),
        assessment_u_interval=interval,
    )
    return replace(profile, measurement_context=build_measurement_context(
        profile, source.design_segments, actual, settings,
    ))


def test_crest_outside_mask_uses_actual_context():
    profile = contextual_profile()
    measured = measure_profile(profile)
    assert measured.actual_landmarks.upper_crest.detection.reliable
    assert measured.actual_landmarks.upper_crest.point.u == pytest.approx(0.2)
    assert measured.actual_landmarks.upper_crest.point.u < profile.assessment_u_interval[0]
    assert all(s.u_min >= 0.5 for s in profile.actual_segments)


def test_full_upper_berm_outside_mask_is_measurable():
    measured = measure_profile(contextual_profile())
    assert measured.design_upper_berm_width_m == pytest.approx(4)
    assert measured.actual_upper_berm_width_m == pytest.approx(3.7)
    assert measured.upper_berm_deficit_m == pytest.approx(0.3)


def test_lower_toe_outside_mask_supports_angle_and_toe_kpis():
    profile = contextual_profile()
    measured = measure_profile(profile)
    assert measured.design_landmarks.lower_toe.point.u == pytest.approx(4)
    assert measured.actual_landmarks.lower_toe.point.u == pytest.approx(4.5)
    assert measured.actual_landmarks.lower_toe.point.u > profile.assessment_u_interval[1]
    assert measured.angle_status.reliable
    assert measured.angle_shortfall_deg > 0
    assert measured.toe_signed_offset_u_m == pytest.approx(0.5)


def test_bounds_come_from_supported_landmarks_and_named_detector_support():
    settings = WallMeasurementTolerances(search_half_window_m=6, side_fit_window_m=4)
    profile = contextual_profile(settings=settings)
    radius = 6 + 4 + 4 + 2 * settings.actual_connection_tolerance_m
    assert profile.measurement_context.u_intervals == ((-4 - radius, 4 + radius),)
    assert profile.assessment_u_interval == (0.5, 3.5)


def test_remote_actual_is_not_retained_in_context():
    remote = segment((100, 100), (120, 60), 900)
    profile = contextual_profile((*ACTUAL, remote))
    assert all(s.source_triangle_index != 900 for s in profile.measurement_context.actual_segments)
    assert measure_profile(profile) == measure_profile(contextual_profile())


def test_context_retains_a_full_fit_margin_beyond_candidate_envelope():
    displaced = (
        *ACTUAL[:2],
        segment((0.2, 99.7), (11.2, 80.0), 905),
        segment((11.2, 80.0), (15.0, 79.8), 906),
    )
    profile = contextual_profile(displaced)
    context = profile.measurement_context

    assert context.u_intervals[0][1] == pytest.approx(
        4.0 + WallMeasurementTolerances().context_half_window_m
    )
    assert max(segment.u_max for segment in context.actual_segments) == pytest.approx(
        context.u_intervals[0][1]
    )
    assert 11.2 - 4.0 < WallMeasurementTolerances().candidate_half_window_m
    toe = measure_profile(profile).actual_landmarks.lower_toe
    assert toe.detection.reliable
    assert toe.point.u == pytest.approx(11.2)


def test_measurement_context_retains_actual_geometry_outside_design_z_envelope():
    retained = segment((-2, 106.8), (0, 106.8), 910)
    above = segment((-2, 116), (0, 116), 911)
    below = segment((-2, 78), (0, 78), 912)
    profile = contextual_profile((*ACTUAL, retained, above, below))

    assert profile.measurement_context.design_z_interval == pytest.approx((89.8, 106.0))
    retained_indices = {
        segment.source_triangle_index for segment in profile.measurement_context.actual_segments
    }
    assert 910 in retained_indices
    assert {910, 911, 912}.issubset(retained_indices)


def test_measurement_context_does_not_apply_design_z_clip_to_detector_input():
    upper_crossing = segment((-2, 106.8), (1, 108.2), 920)
    lower_crossing = segment((1, 90), (4, 88), 921)
    profile = contextual_profile((*ACTUAL, upper_crossing, lower_crossing))
    clipped = {
        segment.source_triangle_index: segment
        for segment in profile.measurement_context.actual_segments
        if segment.source_triangle_index in {920, 921}
    }

    assert clipped[920] == upper_crossing
    assert clipped[921] == lower_crossing


def test_design_z_difference_does_not_remove_physical_detector_input():
    elevated_actual = tuple(
        segment(
            (item.start.u, item.start.z + 20),
            (item.end.u, item.end.z + 20),
            item.source_triangle_index,
        )
        for item in ACTUAL
    )
    measured = measure_profile(contextual_profile(elevated_actual))

    assert measured.actual_landmarks.upper_crest.detection.reliable
    assert measured.actual_landmarks.lower_toe.detection.reliable


def test_z_envelope_boundary_is_not_a_lower_toe_without_downstream_support():
    actual = (
        segment((-3, 100.4), (0.2, 99.7), 930),
        segment((0.2, 99.7), (4.5, 89.8), 931),
        segment((4.5, 89.8), (5.0, 87.0), 932),
    )
    measured = measure_profile(contextual_profile(actual))

    assert measured.actual_landmarks.lower_toe.point is None
    assert measured.toe_status.state == "not_detected"


@pytest.mark.parametrize('name,actual', [
    ('upper_crest', (segment((-6, 101), (-0.4, 100), 300),)),
    ('lower_toe', ACTUAL[:2] + (segment((0.2, 99.7), (3, 92), 301),)),
])
def test_dataset_termination_never_extrapolates(name, actual):
    measured = measure_profile(contextual_profile(actual))
    landmark = getattr(measured.actual_landmarks, name)
    assert landmark.point is None
    assert landmark.detection.state == 'not_detected'
    assert landmark.detection.reason_code == 'insufficient_right_support'


def test_large_gap_outside_assessment_is_not_bridged():
    actual = (
        segment((-6, 106), (-4.8, 102), 400),
        segment((-2.8, 100.1), (0.2, 99.7), 401),
        *ACTUAL[2:],
    )
    measured = measure_profile(contextual_profile(actual))
    assert measured.actual_landmarks.upper_berm_start.point is None
    assert measured.actual_landmarks.upper_berm_start.detection.reason_code == 'data_gap'
    assert measured.actual_upper_berm_width_m is None


def test_empty_context_does_not_fall_back_to_display_geometry():
    profile = contextual_profile(())
    profile = replace(profile, actual_segments=ACTUAL)
    measured = measure_profile(profile)
    assert measured.angle_status.reason_code == 'no_actual_coverage'


def test_missing_design_platform_boundary_does_not_invent_berm_start():
    source = design_profile()
    local = source.design_segments[1:]  # platform ends at the dataset boundary
    profile = replace(source, measurement_context=build_measurement_context(source, local, ACTUAL))
    landmarks = extract_design_landmarks(profile)
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_berm_start.detection.reason_code == 'design_landmark_unsupported'


def surface(points, roles):
    return _strip_surface(((0, 0, (0, 1)), (12, 0, (0, 1))), points, roles)


def assembled(actual=True, reverse=False, remote=False):
    design = surface(
        ((-6, 106), (-4, 100.5), (0, 100), (4, 90), (8, 89.8)),
        ('face', 'berm', 'face', 'berm'),
    )
    if remote:
        design = _combined_surface(design, surface(
            ((40, 100), (44, 90), (48, 90)), ('face', 'berm'),
        ))
    actual_surface = surface(
        ((-6, 106), (-3.5, 100.2), (0.2, 99.7), (4.5, 89.8), (8, 89.6)),
        ('face', 'berm', 'face', 'berm'),
    ) if actual else None
    result = build_alignment_profile_sections(
        alignment=_straight_alignment(reverse=reverse),
        design_surface=design, actual_surface=actual_surface,
        assessment_polygon=_polygon((-1, 0.5), (13, 0.5), (13, 3.5), (-1, 3.5)),
        role_mapping=ROLE_MAPPING, spacing_m=3,
    )
    return result, actual_surface


def test_assembly_preserves_placement_and_exact_display_clipping():
    result, actual = assembled()
    without_actual, _ = assembled(actual=False)
    assert result.placement_result == without_actual.placement_result
    assert len(result.profiles) == len(without_actual.profiles) == 5
    assert result.design_variants == without_actual.design_variants
    for profile, other in zip(result.profiles, without_actual.profiles):
        assert profile.alignment == other.alignment
        assert profile.assessment_u_interval == other.assessment_u_interval
        assert profile.design_section == other.design_section
        assert profile.design_segments == other.design_segments
        expected = clip_section_segments_to_u_interval(
            intersect_surface_with_profile(actual, profile.alignment),
            *profile.assessment_u_interval,
        )
        z = [p.z for e in profile.design_section.elements for p in (e.start, e.end)]
        expected = clip_section_segments_to_z_range(expected, min(z), max(z))
        assert profile.actual_segments == expected
        assert measure_profile(profile).angle_status.reliable


def test_remote_design_component_cannot_supply_context():
    result, _ = assembled(remote=True)
    baseline, _ = assembled()
    assert result.profiles == baseline.profiles


def test_reversing_alignment_preserves_physical_measurements():
    forward, _ = assembled()
    reverse, _ = assembled(reverse=True)
    by_origin = {p.alignment.origin: p for p in forward.profiles}
    for profile in reverse.profiles:
        other = by_origin[profile.alignment.origin]
        assert profile.measurement_context == other.measurement_context
        assert replace(measure_profile(profile), chainage_m=0) == replace(
            measure_profile(other), chainage_m=0,
        )
