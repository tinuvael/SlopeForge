from __future__ import annotations

from dataclasses import replace
from math import isclose
from types import SimpleNamespace

import pytest

from domain.geometry.surfaces import SurfaceVertex
from domain.wall_conformance import (
    MeasurementDetection,
    ProfileLandmark,
    WallMeasurementTolerances,
    WallProfileLandmarks,
    WallProfileMeasurements,
    aggregate_measurements,
    detect_actual_landmarks,
    diagnose_profile_landmark_set,
    diagnose_profile_landmarks,
    extract_design_landmarks,
    measure_profile,
)
from domain.wall_conformance.models import (
    DesignSection,
    DesignSectionElement,
    ProfileMeasurementContext,
    SectionPoint,
    SectionSegment,
    TransverseProfile,
    WallAlignmentSample,
)


def point(u: float, z: float) -> SectionPoint:
    return SectionPoint(u, z, u, 0.0)


def segment(
    start: tuple[float, float],
    end: tuple[float, float],
    index: int,
    role: str | None = None,
) -> SectionSegment:
    return SectionSegment(point(*start), point(*end), index, role)


def element(
    role: str,
    start: tuple[float, float],
    end: tuple[float, float],
    index: int,
) -> DesignSectionElement:
    return DesignSectionElement(role, point(*start), point(*end), (index,))


def design_profile(
    *,
    context_role: str | None = "berm",
    multi_bench: bool = False,
    lower_platform: bool = True,
    actual: tuple[SectionSegment, ...] = (),
    chainage: float = 12.0,
) -> TransverseProfile:
    context = (
        element(context_role, (-4.0, 100.5), (0.0, 100.0), 1)
        if context_role is not None
        else None
    )
    elements = [element("face", (0.0, 100.0), (4.0, 90.0), 2)]
    if multi_bench:
        elements.extend((
            element("berm", (4.0, 90.0), (7.0, 89.5), 3),
            element("face", (7.0, 89.5), (11.0, 79.5), 4),
        ))
    if lower_platform:
        toe = elements[-1].end
        elements.append(
            DesignSectionElement(
                "berm", toe, point(toe.u + 3.0, toe.z - 0.2), (5,)
            )
        )
    design_segments = []
    if context is not None:
        design_segments.extend((
            segment((-6.0, 106.0), (-4.0, 100.5), 0, "face"),
            SectionSegment(context.start, context.end, 1, context.role),
        ))
    design_segments.extend(
        SectionSegment(item.start, item.end, item.source_triangle_indices[0], item.role)
        for item in elements
    )
    return TransverseProfile(
        WallAlignmentSample(
            chainage,
            SurfaceVertex(0.0, 0.0, 100.0),
            (0.0, 1.0),
            (1.0, 0.0),
        ),
        tuple(design_segments),
        actual,
        DesignSection(tuple(elements), context),
        assessment_u_interval=(-7.0, 16.0),
    )


def actual_profile_segments(
    *,
    berm_start_u: float = -3.5,
    crest_u: float = 0.2,
    toe_u: float = 11.5,
    multi_bench: bool = True,
    noise: float = 0.0,
) -> tuple[SectionSegment, ...]:
    items = [
        segment((-6.0, 106.0), (berm_start_u, 100.2), 100),
        segment(
            (berm_start_u, 100.2),
            ((berm_start_u + crest_u) / 2.0, 99.95 + noise),
            101,
        ),
        segment(
            ((berm_start_u + crest_u) / 2.0, 99.95 + noise),
            (crest_u, 99.7),
            102,
        ),
    ]
    if multi_bench:
        items.extend((
            segment((crest_u, 99.7), (4.8, 89.7 + noise), 103),
            segment((4.8, 89.7 + noise), (7.1, 89.4 - noise), 104),
            segment((7.1, 89.4 - noise), (toe_u, 79.4), 105),
        ))
    else:
        items.append(segment((crest_u, 99.7), (toe_u, 79.4), 103))
    items.extend((
        segment((toe_u, 79.4), (toe_u + 1.7, 79.3 + noise), 106),
        segment((toe_u + 1.7, 79.3 + noise), (toe_u + 3.2, 79.2), 107),
    ))
    return tuple(items)


def with_raw_measurement_context(
    profile: TransverseProfile,
    actual: tuple[SectionSegment, ...],
) -> TransverseProfile:
    return replace(
        profile,
        measurement_context=ProfileMeasurementContext(
            profile.design_section,
            profile.design_segments,
            actual,
            ((-8.0, 18.0),),
        ),
    )


def test_design_landmarks_single_bench_use_only_external_transitions() -> None:
    landmarks = extract_design_landmarks(design_profile())

    assert landmarks.upper_berm_start.point == point(-4.0, 100.5)
    assert landmarks.upper_crest.point == point(0.0, 100.0)
    assert landmarks.lower_toe.point == point(4.0, 90.0)


def test_design_landmarks_support_road_context_and_multi_bench_endpoint() -> None:
    landmarks = extract_design_landmarks(
        design_profile(context_role="road", multi_bench=True)
    )

    assert landmarks.upper_berm_start.detection.reliable
    assert landmarks.upper_crest.point == point(0.0, 100.0)
    assert landmarks.lower_toe.point == point(11.0, 79.5)


def test_design_upper_berm_requires_context_or_preceding_face_boundary() -> None:
    profile = design_profile(context_role=None)
    landmarks = extract_design_landmarks(profile)

    assert landmarks.upper_berm_start.detection.reason_code == "design_landmark_unsupported"
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.point == point(0.0, 100.0)

    full = replace(
        profile,
        design_segments=(
            segment((-6.0, 106.0), (-4.0, 100.5), 10, "face"),
            segment((-4.0, 100.5), (0.0, 100.0), 11, "berm"),
            *profile.design_segments,
        ),
    )
    supported = extract_design_landmarks(full)
    assert supported.upper_berm_start.point == point(-4.0, 100.5)
    assert supported.upper_crest.point == point(0.0, 100.0)


def test_design_lower_toe_rejects_clipped_face_terminal() -> None:
    profile = design_profile(lower_platform=False)
    landmarks = extract_design_landmarks(profile)

    assert landmarks.lower_toe.point is None
    assert landmarks.lower_toe.detection.reason_code == "design_landmark_unsupported"


def test_actual_landmarks_detect_clean_sloping_berm_and_multi_bench_wall() -> None:
    profile = design_profile(
        multi_bench=True,
        actual=actual_profile_segments(),
    )
    landmarks = detect_actual_landmarks(profile)

    assert landmarks.upper_berm_start.detection.reliable
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.lower_toe.detection.reliable
    assert isclose(landmarks.upper_berm_start.point.u, -3.5)
    assert isclose(landmarks.upper_crest.point.u, 0.2)
    assert isclose(landmarks.lower_toe.point.u, 11.5)


def test_actual_landmarks_tolerate_small_noise_and_local_irregularities() -> None:
    actual = list(actual_profile_segments(noise=0.08))
    actual.insert(3, segment((0.2, 99.7), (0.28, 99.58), 150))
    actual[4] = segment((0.28, 99.58), (4.8, 89.78), 103)
    profile = design_profile(multi_bench=True, actual=tuple(actual))
    landmarks = detect_actual_landmarks(profile)

    assert landmarks.upper_crest.detection.reliable
    assert landmarks.lower_toe.detection.reliable


def test_actual_missing_and_insufficient_coverage_are_explicit() -> None:
    missing = detect_actual_landmarks(design_profile())
    assert missing.upper_crest.detection.reason_code == "no_actual_coverage"
    assert missing.lower_toe.detection.reason_code == "no_actual_coverage"

    short = design_profile(
        actual=(
            segment((-2.0, 100.0), (0.0, 100.0), 200),
            segment((0.0, 100.0), (0.2, 99.7), 201),
        )
    )
    detected = detect_actual_landmarks(short)
    assert detected.upper_crest.detection.reason_code == "insufficient_right_support"


def test_actual_lower_toe_requires_downstream_platform_support() -> None:
    without_lower_platform = tuple(
        item for item in actual_profile_segments() if item.source_triangle_index < 106
    )
    toe = detect_actual_landmarks(
        design_profile(multi_bench=True, actual=without_lower_platform)
    ).lower_toe

    assert toe.point is None
    assert toe.detection.reason_code == "insufficient_right_support"


@pytest.mark.parametrize("toe_z", (82.5, 87.5))
def test_actual_lower_toe_above_design_remains_a_physical_transition(toe_z) -> None:
    actual = (
        segment((-6.0, 106.0), (-3.5, 100.2), 100),
        segment((-3.5, 100.2), (0.2, 99.7), 101),
        segment((0.2, 99.7), (11.5, toe_z), 102),
        segment((11.5, toe_z), (14.7, toe_z - 0.2), 103),
    )
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), actual
    )

    toe = detect_actual_landmarks(profile).lower_toe

    assert toe.detection.reliable
    assert toe.point == point(11.5, toe_z)


def test_actual_lower_toe_is_the_direct_face_to_floor_onset() -> None:
    """Later floor tessellation and small kinks cannot move the physical toe."""
    actual = (
        segment((-6.0, 106.0), (-3.5, 100.2), 100),
        segment((-3.5, 100.2), (0.2, 99.7), 101),
        segment((0.2, 99.7), (11.5, 79.4), 102),
        segment((11.5, 79.4), (12.5, 79.3), 103),
        segment((12.5, 79.3), (14.0, 79.2), 104),
        # A later, still platform-like survey kink is inside the local search
        # window. It must not replace the onset at U=11.5.
        segment((14.0, 79.2), (16.0, 78.8), 105),
        segment((16.0, 78.8), (19.0, 78.7), 106),
    )
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), actual
    )

    toe = detect_actual_landmarks(profile).lower_toe

    assert toe.detection.reliable
    assert toe.point == point(11.5, 79.4)


def test_displaced_physical_toe_and_upper_platform_remain_eligible() -> None:
    toe_profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()),
        actual_profile_segments(toe_u=15.0),
    )
    upper_profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()),
        (
            segment((-6.0, 106.0), (1.0, 100.2), 200),
            segment((1.0, 100.2), (5.0, 99.7), 201),
            segment((5.0, 99.7), (15.0, 79.4), 202),
            segment((15.0, 79.4), (18.0, 79.2), 203),
        ),
    )

    toe = detect_actual_landmarks(toe_profile).lower_toe
    upper = detect_actual_landmarks(upper_profile)

    assert toe.detection.reliable
    assert toe.point.u == pytest.approx(15.0)
    assert upper.upper_berm_start.detection.reliable
    assert upper.upper_berm_start.point.u == pytest.approx(1.0)
    assert upper.upper_crest.detection.reliable
    assert upper.upper_crest.point.u == pytest.approx(5.0)


def test_actual_lower_toe_is_physical_when_assessment_mask_ends_on_the_face() -> None:
    actual = actual_profile_segments()
    profile = with_raw_measurement_context(
        replace(
            design_profile(multi_bench=True, actual=()),
            assessment_u_interval=(-7.0, 10.0),
        ),
        actual,
    )

    toe = detect_actual_landmarks(profile).lower_toe

    assert toe.detection.reliable
    assert isclose(toe.point.u, 11.5)
    assert toe.point.u > profile.assessment_u_interval[1]


def test_actual_lower_toe_remains_physical_when_assessment_mask_ends_after_toe() -> None:
    actual = actual_profile_segments()
    profile = with_raw_measurement_context(
        replace(
            design_profile(multi_bench=True, actual=()),
            assessment_u_interval=(-7.0, 13.0),
        ),
        actual,
    )

    toe = detect_actual_landmarks(profile).lower_toe

    assert toe.detection.reliable
    assert isclose(toe.point.u, 11.5)
    assert toe.point.u < profile.assessment_u_interval[1]


def test_actual_lower_toe_requires_a_connected_downstream_platform_not_context_end() -> None:
    face_only = tuple(
        item for item in actual_profile_segments() if item.source_triangle_index < 106
    )
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), face_only
    )

    toe = detect_actual_landmarks(profile).lower_toe

    assert toe.point is None
    assert toe.detection.reason_code == "insufficient_right_support"


def test_actual_lower_toe_does_not_bridge_a_gap_to_downstream_platform() -> None:
    actual = (
        segment((-4.0, 100.0), (0.0, 99.7), 600),
        segment((0.0, 99.7), (5.0, 89.5), 601),
        segment((5.0, 89.5), (10.6, 79.8), 602),
        segment((12.0, 79.6), (15.0, 79.4), 603),
    )
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), actual
    )

    toe = detect_actual_landmarks(profile).lower_toe

    assert toe.point is None
    assert toe.detection.reason_code == "data_gap"


def _boundary_truncated_actual(*, start_u: float = -6.0) -> tuple[SectionSegment, ...]:
    return (
        segment((start_u, 100.5), (-2.0, 100.1), 700),
        segment((-2.0, 100.1), (0.2, 99.7), 701),
        segment((0.2, 99.7), (11.5, 79.4), 702),
        segment((11.5, 79.4), (13.5, 79.3), 703),
    )


def test_actual_upstream_boundary_keeps_crest_and_toe_without_berm_start() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), _boundary_truncated_actual(),
    )

    landmarks = detect_actual_landmarks(profile)

    assert landmarks.upper_berm_start.point is None
    assert landmarks.upper_berm_start.detection.reason_code == "boundary_truncated"
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.point.u == pytest.approx(0.2)
    assert landmarks.lower_toe.detection.reliable
    assert landmarks.lower_toe.point.u == pytest.approx(11.5)


def test_long_boundary_truncated_upper_platform_still_detects_crest() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()),
        _boundary_truncated_actual(start_u=-7.5),
    )

    landmarks = detect_actual_landmarks(profile)

    assert landmarks.upper_berm_start.detection.reason_code == "boundary_truncated"
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.lower_toe.detection.reliable


def test_boundary_truncated_berm_start_only_removes_berm_kpi() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), _boundary_truncated_actual(),
    )

    measured = measure_profile(profile)
    summary = aggregate_measurements((measured,))

    assert measured.actual_landmarks.upper_berm_start.detection.reason_code == "boundary_truncated"
    assert measured.angle_status.reliable
    assert measured.actual_overall_angle_deg is not None
    assert measured.toe_status.reliable
    assert measured.toe_deviation_m is not None
    assert not measured.berm_status.reliable
    assert measured.actual_upper_berm_width_m is None
    assert measured.upper_berm_deficit_m is None
    assert summary.angle_shortfall_deg.valid_count == 1
    assert summary.toe_deviation_m.valid_count == 1
    assert summary.upper_berm_deficit_m.valid_count == 0


def test_upstream_data_gap_is_not_classified_as_a_pit_boundary() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()),
        (
            segment((-9.0, 108.0), (-7.0, 101.0), 710),
            *_boundary_truncated_actual(),
        ),
    )

    landmarks = detect_actual_landmarks(profile)

    assert landmarks.upper_berm_start.detection.reason_code != "boundary_truncated"
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.lower_toe.detection.reliable


def _boundary_face_actual(*, crest_z: float = 100.0, toe_z: float = 79.4):
    return (
        segment((-1.0, crest_z), (11.5, toe_z), 720),
        segment((11.5, toe_z), (14.5, toe_z - 0.1), 722),
    )


def test_actual_surface_boundary_can_be_the_crest_of_a_face_to_floor_run() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), _boundary_face_actual(),
    )

    measured = measure_profile(profile)

    assert measured.actual_landmarks.upper_berm_start.point is None
    assert measured.actual_landmarks.upper_berm_start.detection.reason_code == "boundary_truncated"
    assert measured.actual_landmarks.upper_crest.detection.reliable
    assert measured.actual_landmarks.upper_crest.point == point(-1.0, 100.0)
    assert measured.actual_landmarks.lower_toe.detection.reliable
    assert measured.actual_landmarks.lower_toe.point == point(11.5, 79.4)
    assert measured.angle_status.reliable
    assert measured.actual_overall_angle_deg is not None
    assert not measured.berm_status.reliable
    assert measured.actual_upper_berm_width_m is None
    assert measured.toe_status.reliable


def test_boundary_face_crest_remains_valid_with_toe_above_design_toe() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()),
        _boundary_face_actual(toe_z=87.5),
    )

    measured = measure_profile(profile)

    assert measured.actual_landmarks.upper_crest.detection.reliable
    assert measured.actual_landmarks.lower_toe.detection.reliable
    assert measured.angle_status.reliable
    assert measured.toe_status.reliable
    assert measured.actual_upper_berm_width_m is None


def test_measurement_context_clip_endpoint_is_not_a_boundary_crest() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), _boundary_face_actual(),
    )
    profile = replace(
        profile,
        measurement_context=replace(profile.measurement_context, u_intervals=((-1.0, 18.0),)),
    )

    landmarks = detect_actual_landmarks(profile)

    assert not landmarks.upper_crest.detection.reliable
    assert landmarks.upper_berm_start.detection.reason_code != "boundary_truncated"
    assert landmarks.lower_toe.detection.reliable


def test_internal_gap_endpoint_is_not_a_boundary_crest() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()),
        (
            segment((-6.0, 105.0), (-3.0, 101.0), 730),
            *_boundary_face_actual(),
        ),
    )

    landmarks = detect_actual_landmarks(profile)

    assert not landmarks.upper_crest.detection.reliable
    assert landmarks.upper_berm_start.detection.reason_code != "boundary_truncated"
    assert landmarks.lower_toe.detection.reliable


def test_ordinary_berm_to_face_crest_wins_over_component_boundary() -> None:
    profile = with_raw_measurement_context(
        design_profile(multi_bench=True, actual=()), actual_profile_segments(),
    )

    landmarks = detect_actual_landmarks(profile)

    assert landmarks.upper_berm_start.detection.reliable
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.point.u == pytest.approx(0.2)
    assert landmarks.upper_crest.point.u != pytest.approx(-6.0)


def test_actual_gap_near_transition_is_not_bridged() -> None:
    profile = design_profile(
        actual=(
            segment((-3.0, 100.4), (-0.3, 100.0), 300),
            segment((0.3, 99.5), (3.0, 92.0), 301),
        )
    )
    crest = detect_actual_landmarks(profile).upper_crest

    assert crest.point is None
    assert crest.detection.reason_code == "data_gap"


def test_actual_ambiguous_breakpoints_are_retained_but_not_reliable() -> None:
    alternative_one = (
        segment((-3.0, 100.0), (-0.8, 100.0), 400),
        segment((-0.8, 100.0), (1.5, 94.0), 401),
    )
    alternative_two = (
        segment((-1.5, 101.0), (0.8, 101.0), 402),
        segment((0.8, 101.0), (3.0, 95.0), 403),
    )
    profile = design_profile(actual=(*alternative_one, *alternative_two))
    crest = detect_actual_landmarks(profile).upper_crest

    assert crest.point is not None
    assert crest.detection.state == "low_confidence"
    assert crest.detection.reason_code == "ambiguous_breakpoint"
    measured = measure_profile(profile)
    assert measured.actual_overall_angle_deg is None
    assert measured.angle_shortfall_deg is None
    assert not measured.angle_status.reliable


def test_upper_berm_kpi_uses_external_platform_not_internal_berms() -> None:
    single = measure_profile(
        design_profile(actual=actual_profile_segments(multi_bench=False))
    )
    multiple = measure_profile(
        design_profile(multi_bench=True, actual=actual_profile_segments())
    )

    assert isclose(single.design_upper_berm_width_m, 4.0)
    assert isclose(multiple.design_upper_berm_width_m, 4.0)
    assert isclose(single.actual_upper_berm_width_m, 3.7)
    assert isclose(multiple.actual_upper_berm_width_m, 3.7)


def test_single_bench_overall_angle_uses_crest_to_lower_toe() -> None:
    actual = (
        segment((-6.0, 106.0), (-3.5, 100.2), 500),
        segment((-3.5, 100.2), (0.2, 99.7), 501),
        segment((0.2, 99.7), (4.5, 89.8), 502),
        segment((4.5, 89.8), (7.0, 89.6), 503),
    )
    measured = measure_profile(design_profile(actual=actual))

    assert measured.angle_status.reliable
    assert isclose(measured.design_overall_angle_deg, degrees_atan(10.0, 4.0))
    assert isclose(measured.actual_overall_angle_deg, degrees_atan(9.9, 4.3))


def test_partial_upper_berm_never_produces_a_width() -> None:
    profile = design_profile(
        actual=tuple(
            segment_item
            for segment_item in actual_profile_segments()
            if segment_item.u_min >= -2.0
        )
    )
    measured = measure_profile(profile)

    assert measured.actual_upper_berm_width_m is None
    assert measured.upper_berm_deficit_m is None
    assert measured.berm_status.state == "not_detected"


def test_overall_angle_uses_only_upper_crest_and_whole_area_lower_toe() -> None:
    measured = measure_profile(
        design_profile(multi_bench=True, actual=actual_profile_segments())
    )
    expected_design = degrees_atan(20.5, 11.0)
    expected_actual = degrees_atan(20.3, 11.3)

    assert isclose(measured.design_overall_angle_deg, expected_design)
    assert isclose(measured.actual_overall_angle_deg, expected_actual)
    assert isclose(
        measured.angle_shortfall_deg,
        max(expected_design - expected_actual, 0.0),
    )


def test_actual_equal_or_steeper_angle_has_zero_shortfall() -> None:
    actual = actual_profile_segments(crest_u=0.0, toe_u=10.0)
    measured = measure_profile(design_profile(multi_bench=True, actual=actual))

    assert measured.angle_status.reliable
    assert measured.angle_shortfall_deg == 0.0


def test_toe_retains_signed_offsets_and_aggregates_absolute_deviation() -> None:
    positive = measure_profile(
        design_profile(multi_bench=True, actual=actual_profile_segments(toe_u=11.6))
    )
    negative = measure_profile(
        design_profile(multi_bench=True, actual=actual_profile_segments(toe_u=10.4))
    )

    assert isclose(positive.toe_signed_offset_u_m, 0.6)
    assert isclose(negative.toe_signed_offset_u_m, -0.6)
    assert isclose(positive.toe_deviation_m, 0.6)
    assert isclose(negative.toe_deviation_m, 0.6)


def degrees_atan(vertical: float, horizontal: float) -> float:
    from math import atan2, degrees

    return degrees(atan2(abs(vertical), abs(horizontal)))


def _aggregate_item(
    angle: float | None,
    berm: float | None,
    toe: float | None,
) -> WallProfileMeasurements:
    detected = MeasurementDetection("detected", "detected", "ok")
    failed = MeasurementDetection("not_detected", "no_actual_coverage", "missing")
    empty_landmark = ProfileLandmark(None, failed)
    landmarks = WallProfileLandmarks(empty_landmark, empty_landmark, empty_landmark)
    return WallProfileMeasurements(
        0.0,
        landmarks,
        landmarks,
        None,
        None,
        angle,
        None,
        None,
        berm,
        None,
        toe,
        detected if angle is not None else failed,
        detected if berm is not None else failed,
        detected if toe is not None else failed,
    )


def test_aggregation_uses_paired_deviations_and_independent_valid_counts() -> None:
    summary = aggregate_measurements((
        _aggregate_item(1.0, 5.0, None),
        _aggregate_item(3.0, None, 2.0),
        _aggregate_item(8.0, 1.0, 4.0),
        _aggregate_item(None, None, 6.0),
    ))

    assert summary.angle_shortfall_deg.valid_count == 3
    assert summary.angle_shortfall_deg.total_count == 4
    assert summary.angle_shortfall_deg.median == 3.0
    assert summary.angle_shortfall_deg.mean == 4.0
    assert summary.angle_shortfall_deg.minimum == 1.0
    assert summary.angle_shortfall_deg.maximum == 8.0
    assert summary.upper_berm_deficit_m.valid_count == 2
    assert summary.upper_berm_deficit_m.median == 3.0
    assert summary.toe_deviation_m.valid_count == 3
    assert summary.toe_deviation_m.primary_value == 4.0


def _reliable_landmarks(crest, toe):
    detected = MeasurementDetection("detected", "detected", "ok")
    missing = MeasurementDetection("not_detected", "not_used", "not used")
    return WallProfileLandmarks(
        ProfileLandmark(None, missing),
        ProfileLandmark(point(*crest), detected),
        ProfileLandmark(point(*toe), detected),
    )


def _face_conformity_profile(*, offset=0.0, actual_segments=None, chainage=0.0):
    """One 45-degree Design face; positive offset is overbreak in U."""
    design = segment((0.0, 10.0), (10.0, 0.0), 1, "face")
    section = DesignSection((element("face", (0.0, 10.0), (10.0, 0.0), 1),))
    actual = actual_segments or (segment((-offset, 10.0), (10.0-offset, 0.0), 2),)
    profile = TransverseProfile(
        WallAlignmentSample(chainage, SurfaceVertex(0.0, 0.0, 10.0), (0.0, 1.0), (1.0, 0.0)),
        (design,), actual, section,
        measurement_context=ProfileMeasurementContext(section, (design,), actual, ((-3.0, 13.0),)),
    )
    design_landmarks = _reliable_landmarks((0.0, 10.0), (10.0, 0.0))
    actual_landmarks = _reliable_landmarks((-offset, 10.0), (10.0-offset, 0.0))
    detected = MeasurementDetection("detected", "detected", "ok")
    return profile, WallProfileMeasurements(
        chainage, design_landmarks, actual_landmarks,
        None, None, None, None, None, None, None, None,
        detected, detected, detected,
    )


def test_additional_geometry_backbreak_mean_maximum_and_reliability() -> None:
    first_profile, first = _face_conformity_profile(offset=1.2)
    second_profile, second = _face_conformity_profile(offset=-0.5, chainage=3.0)
    missing_crest = replace(
        second,
        actual_landmarks=WallProfileLandmarks(
            second.actual_landmarks.upper_berm_start,
            ProfileLandmark(None, MeasurementDetection("not_detected", "missing", "missing")),
            second.actual_landmarks.lower_toe,
        ),
    )
    summary = aggregate_measurements(
        (first, second, missing_crest), (first_profile, second_profile, second_profile),
    ).additional_geometry
    assert isclose(summary.backbreak_m.mean, 0.6)
    assert isclose(summary.backbreak_m.maximum, 1.2)
    assert summary.backbreak_m.valid_count == 2


def test_additional_geometry_face_residual_sign_and_exact_match() -> None:
    over_profile, over = _face_conformity_profile(offset=1.0)
    under_profile, under = _face_conformity_profile(offset=-1.0, chainage=3.0)
    exact_profile, exact = _face_conformity_profile(offset=0.0, chainage=6.0)
    over_summary = aggregate_measurements((over,), (over_profile,)).additional_geometry
    under_summary = aggregate_measurements((under,), (under_profile,)).additional_geometry
    exact_summary = aggregate_measurements((exact,), (exact_profile,)).additional_geometry
    missing_toe = replace(
        over,
        actual_landmarks=WallProfileLandmarks(
            over.actual_landmarks.upper_berm_start,
            over.actual_landmarks.upper_crest,
            ProfileLandmark(None, MeasurementDetection("not_detected", "gap", "gap")),
        ),
    )
    partial_support_summary = aggregate_measurements(
        (missing_toe,), (over_profile,),
    ).additional_geometry
    normal = 2 ** -0.5
    assert isclose(over_summary.mean_overbreak_m, normal)
    assert over_summary.mean_underbreak_m == 0.0
    assert isclose(under_summary.mean_underbreak_m, normal)
    assert under_summary.mean_overbreak_m == 0.0
    assert isclose(exact_summary.mean_overbreak_m, 0.0)
    assert isclose(exact_summary.mean_underbreak_m, 0.0)
    assert isclose(exact_summary.contour_rms_deviation_m, 0.0)
    assert partial_support_summary.mean_overbreak_m is None
    assert partial_support_summary.mean_underbreak_m is None
    assert partial_support_summary.contour_rms_deviation_m is None


def test_additional_geometry_mixed_signs_and_collinear_subdivision_are_invariant() -> None:
    over_profile, over = _face_conformity_profile(offset=1.0)
    under_profile, under = _face_conformity_profile(offset=-1.0, chainage=3.0)
    baseline = aggregate_measurements((over, under), (over_profile, under_profile)).additional_geometry
    subdivided_actual = (
        segment((-1.0, 10.0), (4.0, 5.0), 2),
        segment((4.0, 5.0), (9.0, 0.0), 3),
    )
    subdivided_profile, subdivided = _face_conformity_profile(
        offset=1.0, actual_segments=subdivided_actual,
    )
    subdivision = aggregate_measurements((subdivided,), (subdivided_profile,)).additional_geometry
    subdivided_section = DesignSection((
        element("face", (0.0, 10.0), (5.0, 5.0), 1),
        element("face", (5.0, 5.0), (10.0, 0.0), 2),
    ))
    subdivided_design = (
        segment((0.0, 10.0), (5.0, 5.0), 1, "face"),
        segment((5.0, 5.0), (10.0, 0.0), 2, "face"),
    )
    subdivided_design_profile = replace(
        over_profile,
        design_segments=subdivided_design,
        design_section=subdivided_section,
        measurement_context=ProfileMeasurementContext(
            subdivided_section, subdivided_design, over_profile.actual_segments,
            ((-3.0, 13.0),),
        ),
    )
    design_subdivision = aggregate_measurements(
        (over,), (subdivided_design_profile,),
    ).additional_geometry
    normal = 2 ** -0.5
    assert isclose(baseline.mean_overbreak_m, normal)
    assert isclose(baseline.mean_underbreak_m, normal)
    assert isclose(baseline.contour_rms_deviation_m, normal)
    assert isclose(subdivision.mean_overbreak_m, normal)
    assert isclose(subdivision.contour_rms_deviation_m, normal)
    assert isclose(design_subdivision.mean_overbreak_m, normal)
    assert isclose(design_subdivision.contour_rms_deviation_m, normal)


def test_additional_geometry_excludes_non_face_and_ambiguous_actual_support() -> None:
    design = (
        element("face", (0.0, 20.0), (5.0, 15.0), 1),
        element("berm", (5.0, 15.0), (10.0, 15.0), 2),
        element("face", (10.0, 15.0), (15.0, 10.0), 3),
    )
    design_segments = tuple(
        SectionSegment(item.start, item.end, item.source_triangle_indices[0], item.role)
        for item in design
    )
    actual = (
        segment((-1.0, 20.0), (4.0, 15.0), 4),
        segment((4.0, 15.0), (9.0, 15.0), 5),
        segment((9.0, 15.0), (14.0, 10.0), 6),
    )
    section = DesignSection(design)
    profile = TransverseProfile(
        WallAlignmentSample(0.0, SurfaceVertex(0.0, 0.0, 20.0), (0.0, 1.0), (1.0, 0.0)),
        design_segments, actual, section,
        measurement_context=ProfileMeasurementContext(section, design_segments, actual, ((-3.0, 18.0),)),
    )
    detected = MeasurementDetection("detected", "detected", "ok")
    measurement = WallProfileMeasurements(
        0.0, _reliable_landmarks((0.0, 20.0), (15.0, 10.0)),
        _reliable_landmarks((-1.0, 20.0), (14.0, 10.0)),
        None, None, None, None, None, None, None, None, detected, detected, detected,
    )
    summary = aggregate_measurements((measurement,), (profile,)).additional_geometry
    assert isclose(summary.mean_overbreak_m, 2 ** -0.5)
    assert isclose(summary.contour_rms_deviation_m, 2 ** -0.5)

    ambiguous = (
        segment((0.0, 10.0), (10.0, 0.0), 7),
        segment((0.0, 10.0), (2.0, 0.0), 8),
    )
    ambiguous_profile, ambiguous_measurement = _face_conformity_profile(
        actual_segments=ambiguous,
    )
    no_support = aggregate_measurements(
        (ambiguous_measurement,), (ambiguous_profile,),
    ).additional_geometry
    assert no_support.mean_overbreak_m is None
    assert no_support.mean_underbreak_m is None
    assert no_support.contour_rms_deviation_m is None


def test_additional_geometry_requires_existing_compatible_actual_profile_span() -> None:
    profile, measurement = _face_conformity_profile(offset=1.0)
    raw_context_only = replace(
        profile,
        actual_segments=(),
        measurement_context=replace(
            profile.measurement_context, actual_segments=profile.actual_segments,
        ),
    )
    summary = aggregate_measurements(
        (measurement,), (raw_context_only,),
    ).additional_geometry
    assert summary.backbreak_m.valid_count == 0
    assert summary.backbreak_m.mean is None and summary.backbreak_m.maximum is None
    assert summary.mean_overbreak_m is None
    assert summary.mean_underbreak_m is None
    assert summary.contour_rms_deviation_m is None


def test_additional_geometry_uses_only_compatible_profiles_not_raw_context() -> None:
    first_profile, first = _face_conformity_profile(offset=1.0)
    second_profile, second = _face_conformity_profile(offset=0.0, chainage=3.0)
    incompatible = []
    for index, offset in enumerate((8.0, -6.0, 40.0), start=2):
        profile, measurement = _face_conformity_profile(offset=offset, chainage=index * 3.0)
        incompatible.append((
            replace(
                profile,
                actual_segments=(),
                measurement_context=replace(
                    profile.measurement_context, actual_segments=profile.actual_segments,
                ),
            ),
            measurement,
        ))
    profiles = (first_profile, second_profile, *(item[0] for item in incompatible))
    measurements = (first, second, *(item[1] for item in incompatible))
    summary = aggregate_measurements(measurements, profiles).additional_geometry
    assert summary.backbreak_m.valid_count == 2
    assert isclose(summary.backbreak_m.mean, 0.5)
    assert isclose(summary.backbreak_m.maximum, 1.0)
    # Existing residual semantics include the exact-match profile's zero
    # support in the positive side; the incompatible raw contexts contribute
    # nothing to either support or aggregate.
    assert isclose(summary.mean_overbreak_m, 2 ** -1.5)
    assert summary.mean_underbreak_m == 0.0
    assert isclose(summary.contour_rms_deviation_m, 0.5)


def test_presentation_aggregates_preserve_signed_existing_measurements() -> None:
    measured = measure_profile(
        replace(design_profile(), actual_segments=design_profile().design_segments)
    )
    signed = replace(
        measured,
        design_overall_angle_deg=65.0,
        actual_overall_angle_deg=62.8,
        design_upper_berm_width_m=12.0,
        actual_upper_berm_width_m=10.9,
        toe_signed_offset_u_m=1.5,
        toe_deviation_m=1.5,
    )
    no_berm = replace(
        signed,
        design_upper_berm_width_m=None,
        actual_upper_berm_width_m=None,
    )

    summary = aggregate_measurements((signed, no_berm))

    assert signed.angle_deviation_deg == pytest.approx(-2.2)
    assert signed.upper_berm_width_deviation_m == pytest.approx(-1.1)
    assert summary.angle_deviation_deg.valid_count == 2
    assert summary.angle_deviation_deg.median == pytest.approx(-2.2)
    assert summary.upper_berm_width_deviation_m.valid_count == 1
    assert summary.upper_berm_width_deviation_m.median == pytest.approx(-1.1)
    assert summary.toe_signed_offset_u_m.valid_count == 2
    assert summary.toe_signed_offset_u_m.median == pytest.approx(1.5)


def test_aggregation_no_valid_profiles_returns_empty_statistics() -> None:
    summary = aggregate_measurements((
        _aggregate_item(None, None, None),
        _aggregate_item(None, None, None),
    ))

    for aggregate in (
        summary.angle_shortfall_deg,
        summary.upper_berm_deficit_m,
        summary.toe_deviation_m,
    ):
        assert aggregate.valid_count == 0
        assert aggregate.total_count == 2
        assert aggregate.primary_value is None


def test_measurements_do_not_depend_on_alignment_tangent_direction() -> None:
    profile = design_profile(multi_bench=True, actual=actual_profile_segments())
    reversed_alignment = replace(
        profile.alignment,
        tangent_xy=(0.0, -1.0),
    )

    assert measure_profile(profile) == measure_profile(
        replace(profile, alignment=reversed_alignment)
    )


def test_tolerances_are_validated_and_named() -> None:
    settings = WallMeasurementTolerances()
    assert settings.search_half_window_m > settings.minimum_side_support_m


@pytest.mark.parametrize("role", ["berm", "road"])
def test_presentation_context_alone_does_not_prove_upper_berm_start(role) -> None:
    profile = design_profile(context_role=role, multi_bench=True, actual=actual_profile_segments())
    profile = replace(profile, design_segments=tuple(
        s for s in profile.design_segments if s.source_triangle_index != 0
    ))
    result = measure_profile(profile)

    assert result.design_landmarks.upper_crest.detection.reliable
    assert result.design_landmarks.upper_berm_start.point is None
    assert result.design_upper_berm_width_m is None
    assert result.berm_status.reason_code == "design_landmark_unsupported"
    assert result.angle_status.reliable
    assert result.toe_status.reliable


def test_terminal_marker_alone_does_not_invent_a_lower_platform_orientation() -> None:
    profile = design_profile(lower_platform=False)
    profile = replace(profile, external_toe=profile.design_section.elements[-1].end)
    assert extract_design_landmarks(profile).lower_toe.detection.reason_code == "design_landmark_unsupported"


def _subdivide(segments, counts):
    output = []
    for item, count in zip(segments, counts):
        def at(fraction):
            return SectionPoint(*(getattr(item.start, field) + fraction * (
                getattr(item.end, field) - getattr(item.start, field)
            ) for field in ("u", "z", "x", "y")))
        output.extend(SectionSegment(at(i / count), at((i + 1) / count),
                                     len(output) + i, item.semantic_role)
                      for i in range(count))
    return tuple(output)


def test_actual_detection_is_stable_under_uneven_triangle_subdivision() -> None:
    actual = actual_profile_segments(noise=0.05)
    original = measure_profile(design_profile(multi_bench=True, actual=actual))
    refined = measure_profile(design_profile(
        multi_bench=True,
        actual=_subdivide(actual, (3, 100, 7, 13, 2, 9, 2, 8)),
    ))
    for name in ("upper_berm_start", "upper_crest", "lower_toe"):
        before = getattr(original.actual_landmarks, name)
        after = getattr(refined.actual_landmarks, name)
        assert before.detection.reliable and after.detection.reliable
        assert (after.point.u, after.point.z) == pytest.approx((before.point.u, before.point.z))
    assert refined.angle_shortfall_deg == pytest.approx(original.angle_shortfall_deg)


def test_actual_segment_input_order_and_direction_do_not_change_measurements() -> None:
    profile = design_profile(multi_bench=True, actual=actual_profile_segments())
    reversed_segments = tuple(replace(s, start=s.end, end=s.start)
                              for s in reversed(profile.actual_segments))
    assert measure_profile(profile) == measure_profile(replace(profile, actual_segments=reversed_segments))
    duplicated = (*reversed_segments, replace(reversed_segments[2], source_triangle_index=999))
    assert measure_profile(profile) == measure_profile(replace(profile, actual_segments=duplicated))


def test_actual_crest_requires_upstream_support_and_local_coverage() -> None:
    one_sided = design_profile(actual=(segment((0, 100), (4, 90), 1),))
    assert detect_actual_landmarks(one_sided).upper_crest.detection.reason_code == "insufficient_left_support"
    remote = replace(one_sided, actual_segments=(segment((40, 100), (44, 90), 2),))
    assert detect_actual_landmarks(remote).upper_crest.detection.reason_code == "no_actual_coverage"


def test_fit_uses_only_local_parts_of_long_segments() -> None:
    from domain.wall_conformance.measurements import _fit_line
    from domain.wall_conformance.sections import clip_section_segments_to_u_interval

    actual = actual_profile_segments()
    local = clip_section_segments_to_u_interval(actual, -2.0, 0.2)
    refined = _subdivide(local, tuple(30 for _ in local))
    original_fit, refined_fit = _fit_line(local), _fit_line(refined)
    assert original_fit.angle_deg == pytest.approx(refined_fit.angle_deg)
    assert original_fit.rms_residual_m == pytest.approx(refined_fit.rms_residual_m, abs=1e-7)
    # Changing survey geometry beyond a side-fit window cannot change its fit.
    profile = design_profile(actual=(
        segment((-4, 100.5), (0, 100), 1),
        segment((0, 100), (4, 90), 2),
        segment((4, 90), (40, 80), 3),
    ))
    assert detect_actual_landmarks(profile).upper_crest.point == point(0, 100)


def test_near_vertical_rising_actual_face_is_not_a_descending_face() -> None:
    profile = design_profile(actual=(
        segment((-4, 100), (0, 100), 1),
        segment((0, 100), (0.5, 110), 2),
    ))
    face = element("face", (0, 100), (0.5, 90), 2)
    profile = replace(profile, design_section=DesignSection((face,), profile.design_section.upstream_context))
    crest = detect_actual_landmarks(profile).upper_crest
    assert crest.point is None
    assert crest.detection.reason_code == "incompatible_geometry"


def test_gap_in_upper_berm_does_not_invalidate_angle_or_toe() -> None:
    profile = design_profile(actual=(
        segment((-13, 108), (-10, 100.5), 100),
        segment((-10, 100.5), (-6, 100.3), 101),
        segment((-4, 100.2), (0, 100), 102),
        segment((0, 100), (4, 90), 103),
        segment((4, 90), (8, 89.9), 104),
    ))
    context = element("berm", (-10, 100.5), (0, 100), 1)
    profile = replace(profile,
        design_section=replace(profile.design_section, upstream_context=context),
        design_segments=(segment((-13, 108), (-10, 100.5), 0, "face"),
                         SectionSegment(context.start, context.end, 1, "berm"),
                         *profile.design_segments[2:]),
    )
    measured = measure_profile(profile)
    assert measured.actual_landmarks.upper_berm_start.detection.reliable
    assert measured.actual_landmarks.upper_crest.detection.reliable
    assert measured.berm_status.reason_code == "data_gap"
    assert measured.actual_upper_berm_width_m is None
    assert measured.angle_status.reliable and measured.toe_status.reliable
    summary = aggregate_measurements((measured,))
    assert summary.upper_berm_deficit_m.valid_count == 0
    assert summary.angle_shortfall_deg.valid_count == summary.toe_deviation_m.valid_count == 1


def test_no_actual_preserves_design_values_without_fabricating_actual() -> None:
    measured = measure_profile(design_profile())
    assert measured.design_overall_angle_deg == pytest.approx(degrees_atan(10, 4))
    assert measured.design_upper_berm_width_m == 4
    for status in (measured.angle_status, measured.berm_status, measured.toe_status):
        assert status.state == "not_detected"
        assert status.reason_code == "no_actual_coverage"
    assert measured.actual_overall_angle_deg is measured.actual_upper_berm_width_m is None
    assert measured.toe_signed_offset_u_m is measured.toe_deviation_m is None


def test_equal_actual_angle_and_width_have_zero_deficits() -> None:
    profile = design_profile()
    profile = replace(profile, actual_segments=profile.design_segments)
    measured = measure_profile(profile)
    assert measured.angle_status.reliable and measured.berm_status.reliable
    assert measured.angle_shortfall_deg == measured.upper_berm_deficit_m == measured.toe_deviation_m == 0


def test_empty_summary_and_low_confidence_values_are_excluded_independently() -> None:
    assert aggregate_measurements(()).angle_shortfall_deg == type(aggregate_measurements(()).angle_shortfall_deg)(
        0, 0, None, None, None, None,
    )
    uncertain = replace(_aggregate_item(9, 2, 3), angle_status=MeasurementDetection(
        "low_confidence", "ambiguous_breakpoint", "Two candidates",
    ))
    summary = aggregate_measurements((uncertain,))
    assert summary.angle_shortfall_deg.valid_count == 0
    assert summary.upper_berm_deficit_m.valid_count == summary.toe_deviation_m.valid_count == 1


@pytest.mark.parametrize("settings", [
    {"search_half_window_m": 0}, {"minimum_side_support_m": float("nan")},
    {"design_distance_preference_m": float("inf")}, {"expected_change_fraction": 1.1},
    {"detected_fit_residual_m": 0.6}, {"minimum_actual_face_descent_deg": 15,
                                        "maximum_actual_platform_angle_deg": 20},
])
def test_invalid_tolerances_are_rejected(settings) -> None:
    with pytest.raises(ValueError):
        WallMeasurementTolerances(**settings)


def test_landmark_diagnostics_expose_clean_candidate_inputs_and_final_statuses() -> None:
    profile = design_profile(multi_bench=True, actual=actual_profile_segments())
    diagnostics = diagnose_profile_landmarks(profile)

    for name in ("upper_berm_start", "upper_crest", "lower_toe"):
        result = getattr(diagnostics, name)
        assert result.final.detection.reliable
        assert result.expected_point is not None
        assert result.local_actual_segment_count > 0
        assert result.best_score is not None
        assert result.ambiguity_margin == WallMeasurementTolerances().ambiguity_fit_residual_m
        accepted = [candidate for candidate in result.candidates if candidate.rejection_gate is None]
        assert accepted
        candidate = accepted[0]
        assert candidate.left_support_m >= WallMeasurementTolerances().minimum_side_support_m
        assert candidate.right_support_m >= WallMeasurementTolerances().minimum_side_support_m
        assert candidate.left_angle_deg is not None and candidate.right_angle_deg is not None
        assert candidate.left_residual_m is not None and candidate.right_residual_m is not None


def test_landmark_diagnostics_keep_displaced_physical_transition_eligible() -> None:
    profile = design_profile(
        multi_bench=True,
        actual=actual_profile_segments(crest_u=3.5, toe_u=14.5),
    )
    diagnostics = diagnose_profile_landmarks(profile)
    crest = diagnostics.upper_crest

    assert crest.final.detection.reason_code == detect_actual_landmarks(profile).upper_crest.detection.reason_code
    displaced = next(candidate for candidate in crest.candidates if candidate.delta_u_m == pytest.approx(3.5))
    assert displaced.rejection_gate is None
    assert displaced.hard_physical_gate is None
    assert displaced.physical_topology == "platform_to_face"
    assert displaced.spatial_offset_m > WallMeasurementTolerances().design_distance_preference_m
    assert displaced.left_support_m is not None and displaced.right_support_m is not None
    assert displaced.left_orientation_error_deg is not None


def test_landmark_diagnostics_keep_no_coverage_and_bulk_profile_order_explicit() -> None:
    profiles = (
        design_profile(chainage=2.0),
        design_profile(chainage=5.0, actual=actual_profile_segments()),
    )
    diagnostics = diagnose_profile_landmark_set(profiles)

    assert [item.chainage_m for item in diagnostics] == [2.0, 5.0]
    assert diagnostics[0].upper_crest.final.detection.reason_code == "no_actual_coverage"
    assert diagnostics[0].upper_crest.candidates == ()
    assert diagnostics[1].upper_crest.final.detection.reliable


def test_generated_profiles_preserve_placement_spacing_and_physical_results_when_reversed() -> None:
    from domain.wall_conformance import build_alignment_profile_sections, measure_profiles
    from tests.test_wall_conformance_alignment_placement import (
        ROLE_MAPPING, _polygon, _straight_alignment, _strip_surface,
    )

    stations = ((0.0, 0.0, (0.0, 1.0)), (12.0, 0.0, (0.0, 1.0)))
    roles = ("face", "berm", "face", "berm")
    design = _strip_surface(stations, (
        (-6, 106), (-4, 100.5), (0, 100), (4, 90), (8, 89.8),
    ), roles)
    actual = _strip_surface(stations, (
        (-6, 106), (-3.5, 100.2), (0.2, 99.7), (4.5, 89.8), (8, 89.6),
    ), roles)
    inputs = dict(design_surface=design, assessment_polygon=_polygon(
        (-1, -1), (13, -1), (13, 6), (-1, 6),
    ), role_mapping=ROLE_MAPPING, spacing_m=3.0)
    bare = build_alignment_profile_sections(alignment=_straight_alignment(), **inputs)
    assembly = build_alignment_profile_sections(alignment=_straight_alignment(), actual_surface=actual, **inputs)
    reversed_assembly = build_alignment_profile_sections(
        alignment=_straight_alignment(reverse=True), actual_surface=actual, **inputs,
    )
    assert bare.placement_result == assembly.placement_result
    assert bare.design_variants == assembly.design_variants
    assert bare.diagnostics == assembly.diagnostics
    assert [p.alignment.chainage_m for p in assembly.profiles] == [0, 3, 6, 9, 12]
    for before, after in zip(bare.profiles, assembly.profiles):
        assert before == replace(after, actual_segments=(), measurement_context=replace(
            after.measurement_context, actual_segments=(),
        ))
    original_profiles = assembly.profiles
    measured = measure_profiles(original_profiles)
    assert assembly.profiles == original_profiles
    reverse_by_x = {p.alignment.origin.x: measure_profile(p) for p in reversed_assembly.profiles}
    for profile, result in zip(original_profiles, measured):
        other = reverse_by_x[profile.alignment.origin.x]
        assert result.angle_status.reliable and result.berm_status.reliable and result.toe_status.reliable
        for name in ("design_overall_angle_deg", "actual_overall_angle_deg", "angle_shortfall_deg",
                     "design_upper_berm_width_m", "actual_upper_berm_width_m", "upper_berm_deficit_m",
                     "toe_signed_offset_u_m", "toe_deviation_m"):
            assert getattr(result, name) == pytest.approx(getattr(other, name))
        for name in ("upper_berm_start", "upper_crest", "lower_toe"):
            a, b = getattr(result.actual_landmarks, name), getattr(other.actual_landmarks, name)
            assert (a.point.x, a.point.y, a.point.z) == pytest.approx((b.point.x, b.point.y, b.point.z))


def test_application_service_exposes_profile_measurements_and_summary() -> None:
    import application.services.wall_conformance as service_module

    profile = design_profile(multi_bench=True, actual=actual_profile_segments())
    assembly = SimpleNamespace(profiles=(profile,), diagnostics=())

    class SurfaceService:
        storage_available = True

        @staticmethod
        def current(site_id, kind):
            return SimpleNamespace(
                logical_id=kind,
                semantic_mapping_json={
                    "attribute_name": "role",
                    "assignments": [{"value": "face", "role": "face"}],
                },
            )

        @staticmethod
        def load_dataset(site_id, logical_id):
            return None, SimpleNamespace(surface=object())

    original = service_module.build_alignment_profile_sections
    service_module.build_alignment_profile_sections = lambda **kwargs: assembly
    try:
        result = service_module.WallConformanceDiagnosticService(
            SurfaceService()
        ).calculate_current(
            1,
            object(),
            object(),
        )
    finally:
        service_module.build_alignment_profile_sections = original

    assert result.measurements == (measure_profile(profile),)
    assert result.measurement_summary is not None
    assert result.measurement_summary.angle_shortfall_deg.valid_count == 1
