"""General invariants for Actual landmark stability.

The coordinates are synthetic and deliberately unrelated to saved Projects.
They exercise one physical wall under harmless representation changes, then
separate supported boundary topologies from genuinely unsupported geometry.
"""
from __future__ import annotations

from dataclasses import replace
from random import Random

import pytest

from domain.geometry.surfaces import SurfaceVertex
from domain.wall_conformance import detect_actual_landmarks, measure_profile
from domain.wall_conformance.models import (
    DesignSection,
    DesignSectionElement,
    ProfileMeasurementContext,
    SectionPoint,
    SectionSegment,
    TransverseProfile,
    WallAlignmentSample,
)


LANDMARK_NAMES = ("upper_berm_start", "upper_crest", "lower_toe")


def _point(u: float, z: float) -> SectionPoint:
    return SectionPoint(u, z, u, 0.0)


def _segment(
    start: tuple[float, float], end: tuple[float, float], index: int,
    role: str | None = None,
) -> SectionSegment:
    return SectionSegment(_point(*start), _point(*end), index, role)


def _element(
    role: str, start: tuple[float, float], end: tuple[float, float], index: int,
) -> DesignSectionElement:
    return DesignSectionElement(role, _point(*start), _point(*end), (index,))


def _profile(
    actual: tuple[SectionSegment, ...], *, raw_context: bool = False,
    multi_bench: bool = False, outer_boundary: bool = False,
) -> TransverseProfile:
    upstream = (
        None if outer_boundary
        else _element("berm", (-5.0, 104.0), (1.0, 103.4), 1)
    )
    if multi_bench:
        elements = (
            _element("face", (1.0, 103.4), (4.0, 96.0), 2),
            _element("berm", (4.0, 96.0), (6.0, 95.8), 3),
            _element("face", (6.0, 95.8), (10.0, 83.0), 4),
            _element("berm", (10.0, 83.0), (16.0, 82.7), 5),
        )
    else:
        elements = (
            _element("face", (1.0, 103.4), (10.0, 83.0), 2),
            _element("berm", (10.0, 83.0), (16.0, 82.7), 3),
        )
    design_segments = (
        (
            _segment((-9.0, 112.0), (-5.0, 104.0), 0, "face"),
            SectionSegment(upstream.start, upstream.end, 1, "berm"),
        )
        if upstream is not None else ()
    ) + tuple(
        SectionSegment(item.start, item.end, item.source_triangle_indices[0], item.role)
        for item in elements
    )
    profile = TransverseProfile(
        WallAlignmentSample(
            18.0,
            SurfaceVertex(0.0, 0.0, 100.0),
            (0.0, 1.0),
            (1.0, 0.0),
        ),
        design_segments,
        actual,
        DesignSection(elements, upstream),
        assessment_u_interval=(-10.0, 18.0),
    )
    if not raw_context:
        return profile
    return replace(
        profile,
        measurement_context=ProfileMeasurementContext(
            profile.design_section,
            profile.design_segments,
            actual,
            ((-10.0, 20.0),),
        ),
    )


def _clean_actual(*, shift_u: float = 0.0, toe_z: float = 83.8):
    def shifted(u: float, z: float) -> tuple[float, float]:
        return u + shift_u, z

    return (
        _segment(shifted(-9.2, 112.1), shifted(-4.6, 103.9), 100),
        _segment(shifted(-4.6, 103.9), shifted(1.3, 103.35), 101),
        _segment(shifted(1.3, 103.35), shifted(11.0, toe_z), 102),
        _segment(shifted(11.0, toe_z), shifted(17.0, toe_z - 0.3), 103),
    )


def _subdivide(
    segments: tuple[SectionSegment, ...], counts: tuple[int, ...],
    *, noise_m: float = 0.0,
) -> tuple[SectionSegment, ...]:
    rng = Random(713)
    output: list[SectionSegment] = []
    for source, count in zip(segments, counts, strict=True):
        points = []
        for index in range(count + 1):
            fraction = index / count
            u = source.start.u + fraction * (source.end.u - source.start.u)
            z = source.start.z + fraction * (source.end.z - source.start.z)
            if 0 < index < count:
                z += rng.uniform(-noise_m, noise_m)
            points.append(_point(u, z))
        for start, end in zip(points, points[1:]):
            output.append(SectionSegment(start, end, len(output), None))
    return tuple(output)


def _curved_platforms(
    actual: tuple[SectionSegment, ...], *, upper_curve: bool, floor_curve: bool,
):
    previous_face, upper, face, floor = actual
    upper_middle = _point(
        (upper.start.u + upper.end.u) / 2.0,
        (upper.start.z + upper.end.z) / 2.0 + (0.05 if upper_curve else 0.0),
    )
    floor_middle = _point(
        (floor.start.u + floor.end.u) / 2.0,
        (floor.start.z + floor.end.z) / 2.0 - (0.06 if floor_curve else 0.0),
    )
    return (
        previous_face,
        SectionSegment(upper.start, upper_middle, 201, None),
        SectionSegment(upper_middle, upper.end, 202, None),
        face,
        SectionSegment(floor.start, floor_middle, 203, None),
        SectionSegment(floor_middle, floor.end, 204, None),
    )


def _assert_complete_at(
    actual: tuple[SectionSegment, ...],
    expected: tuple[tuple[float, float], ...],
) -> None:
    landmarks = detect_actual_landmarks(_profile(actual))
    for name, coordinates in zip(LANDMARK_NAMES, expected, strict=True):
        landmark = getattr(landmarks, name)
        assert landmark.detection.reliable, (name, landmark.detection)
        assert (landmark.point.u, landmark.point.z) == pytest.approx(coordinates)


def test_equivalent_wall_is_stable_under_sampling_noise_curvature_and_shift() -> None:
    """Matrix cases 1-8 preserve the same three physical transitions."""
    clean = _clean_actual()
    variants = (
        (clean, ((-4.6, 103.9), (1.3, 103.35), (11.0, 83.8))),
        (_subdivide(clean, (25, 40, 60, 35)),
         ((-4.6, 103.9), (1.3, 103.35), (11.0, 83.8))),
        (_subdivide(clean, (1, 1, 1, 1)),
         ((-4.6, 103.9), (1.3, 103.35), (11.0, 83.8))),
        (_subdivide(clean, (3, 7, 11, 9)),
         ((-4.6, 103.9), (1.3, 103.35), (11.0, 83.8))),
        (_curved_platforms(clean, upper_curve=True, floor_curve=False),
         ((-4.6, 103.9), (1.3, 103.35), (11.0, 83.8))),
        (_curved_platforms(clean, upper_curve=False, floor_curve=True),
         ((-4.6, 103.9), (1.3, 103.35), (11.0, 83.8))),
        (_subdivide(clean, (4, 8, 12, 10), noise_m=0.018),
         ((-4.6, 103.9), (1.3, 103.35), (11.0, 83.8))),
        (_clean_actual(shift_u=0.18),
         ((-4.42, 103.9), (1.48, 103.35), (11.18, 83.8))),
    )
    for actual, expected in variants:
        _assert_complete_at(actual, expected)


@pytest.mark.parametrize(
    "facet_end",
    (
        (11.2, 83.731),  # approximately 19 degrees
        (11.2, 83.723),  # approximately 21 degrees
        (11.2, 83.702),  # approximately 26 degrees
    ),
)
def test_toe_does_not_flip_at_single_facet_orientation_threshold(facet_end) -> None:
    """A short transitional floor facet cannot define physical existence."""
    previous_face, upper, face, floor = _clean_actual()
    actual = (
        previous_face,
        upper,
        face,
        _segment((11.0, 83.8), facet_end, 301),
        _segment(facet_end, (17.0, 83.5), 302),
    )

    toe = detect_actual_landmarks(_profile(actual)).lower_toe

    assert toe.detection.reliable
    assert (toe.point.u, toe.point.z) == pytest.approx((11.0, 83.8))


def test_toe_can_be_metres_above_design_and_wall_can_be_displaced() -> None:
    """Matrix cases 9-10 keep Design as locality/preference, not definition."""
    high_toe = detect_actual_landmarks(_profile(_clean_actual(toe_z=91.0)))
    # Every transition is beyond the former +/-5 m Design gate while remaining
    # part of the same bounded, connected local Actual component.
    displaced = detect_actual_landmarks(
        _profile(_clean_actual(shift_u=6.0), raw_context=True)
    )

    assert high_toe.lower_toe.detection.reliable
    assert high_toe.lower_toe.point == _point(11.0, 91.0)
    for name in LANDMARK_NAMES:
        assert getattr(displaced, name).detection.reliable


def test_boundary_on_berm_keeps_crest_and_toe_without_fabricating_start() -> None:
    """Matrix case 11: an authoritative boundary may truncate a real Berm."""
    actual = (
        _segment((-2.0, 103.65), (1.3, 103.35), 400),
        _segment((1.3, 103.35), (11.0, 83.8), 401),
        _segment((11.0, 83.8), (17.0, 83.5), 402),
    )

    landmarks = detect_actual_landmarks(_profile(actual, raw_context=True))

    assert landmarks.upper_berm_start.point is None
    assert landmarks.upper_berm_start.detection.reason_code == "boundary_truncated"
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.lower_toe.detection.reliable


def test_boundary_at_crest_keeps_face_and_toe_without_fabricating_start() -> None:
    """Matrix cases 12 and 18: no upstream surface means no Berm start."""
    actual = (
        _segment((1.3, 103.35), (11.0, 83.8), 410),
        _segment((11.0, 83.8), (17.0, 83.5), 411),
    )

    landmarks = detect_actual_landmarks(_profile(actual, raw_context=True))

    assert landmarks.upper_berm_start.point is None
    assert landmarks.upper_berm_start.detection.reason_code == "boundary_truncated"
    assert landmarks.upper_crest.point == _point(1.3, 103.35)
    assert landmarks.lower_toe.detection.reliable


def test_topographic_surface_can_transition_directly_into_assessed_face() -> None:
    """Matrix case 13: the upstream surface need not match Design Berm angle."""
    actual = (
        _segment((-4.0, 104.75), (1.3, 103.35), 420),
        _segment((1.3, 103.35), (11.0, 83.8), 421),
        _segment((11.0, 83.8), (17.0, 83.5), 422),
    )

    landmarks = detect_actual_landmarks(_profile(actual))

    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.point == _point(1.3, 103.35)
    assert landmarks.lower_toe.detection.reliable


def _outer_boundary_profile(
    actual: tuple[SectionSegment, ...], *, raw_context: bool = True,
) -> TransverseProfile:
    return _profile(actual, raw_context=raw_context, outer_boundary=True)


def _outer_segments(points: tuple[tuple[float, float], ...]) -> tuple[SectionSegment, ...]:
    return tuple(
        _segment(start, end, 800 + index)
        for index, (start, end) in enumerate(zip(points, points[1:]))
    )


@pytest.mark.parametrize(
    "topography_start",
    (
        (-5.0, 101.0),  # rising into the rim
        (-5.0, 103.4),  # approximately flat
        (-5.0, 105.8),  # falling into the rim, steeper than a Berm classification
    ),
)
def test_outer_boundary_sharp_topography_to_face_uses_physical_crest(topography_start) -> None:
    """Matrix cases 1-3: topography shape does not need a Berm role."""
    actual = _outer_segments((
        topography_start,
        (1.2, 103.4),
        (10.8, 83.7),
        (17.0, 83.5),
    ))

    measured = detect_actual_landmarks(_outer_boundary_profile(actual))

    assert measured.upper_berm_start.point is None
    assert measured.upper_berm_start.detection.reason_code == "boundary_truncated"
    assert measured.upper_crest.detection.reliable
    assert measured.upper_crest.source == "physical_breakpoint"
    assert measured.upper_crest.point == _point(1.2, 103.4)
    assert measured.lower_toe.detection.reliable
    assert measured.lower_toe.point == _point(10.8, 83.7)


@pytest.mark.parametrize(
    ("crest_z", "topography_start"),
    (
        (106.2, (-5.0, 108.0)),  # Actual crest above the Design crest.
        (100.6, (-5.0, 100.6)),  # Actual crest below the Design crest.
        (103.4, (-5.0, 100.8)),  # Local high point: terrain rises into crest.
    ),
)
def test_outer_boundary_design_crest_z_anchors_but_never_defines_actual_crest(
    crest_z, topography_start,
) -> None:
    """Actual breakpoint wins within the Design-face-height search band."""
    actual = _outer_segments((
        topography_start,
        (1.2, crest_z),
        (10.8, 83.7),
        (17.0, 83.5),
    ))

    landmarks = detect_actual_landmarks(_outer_boundary_profile(actual))

    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.source == "physical_breakpoint"
    assert landmarks.upper_crest.point == _point(1.2, crest_z)
    assert landmarks.upper_crest.point.z != pytest.approx(103.4) or crest_z == 103.4
    assert landmarks.lower_toe.point == _point(10.8, 83.7)


def test_outer_boundary_local_design_anchor_rejects_remote_connected_face() -> None:
    """A distant topographic excursion cannot outrank the upper-wall crest."""
    actual = _outer_segments((
        (-11.0, 114.0),
        (-8.5, 105.0),
        (-6.5, 96.0),  # remote descending surface
        (-4.5, 105.0),
        (-2.0, 104.6),
        (1.2, 103.4),  # relevant local Actual crest
        (10.8, 83.7),
        (17.0, 83.5),
    ))

    landmarks = detect_actual_landmarks(_outer_boundary_profile(actual))

    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.point == _point(1.2, 103.4)
    assert landmarks.lower_toe.point == _point(10.8, 83.7)


def _rounded_outer_topography(kind: str) -> tuple[SectionSegment, ...]:
    upstream = {
        "rising": ((-6.0, 101.2), (-4.0, 102.5), (-2.0, 104.36)),
        "flat": ((-6.0, 104.36), (-4.0, 104.36), (-2.0, 104.36)),
        "falling": ((-6.0, 106.0), (-4.0, 105.18), (-2.0, 104.36)),
    }[kind]
    # The Design crest elevation is incidental: the wall Face only becomes a
    # sustained run at U=3.0 after this rounded topographic rollover.
    return _outer_segments((*upstream, (0.5, 103.4), (3.0, 102.126), (10.8, 83.7), (17.0, 83.5)))


@pytest.mark.parametrize("kind", ("rising", "flat", "falling"))
def test_outer_boundary_smooth_rollover_uses_toe_connected_face_run_onset(kind) -> None:
    """Matrix cases 4-6: a rounded rim has a physical Face-run crest."""
    landmarks = detect_actual_landmarks(
        _outer_boundary_profile(_rounded_outer_topography(kind))
    )

    assert landmarks.upper_berm_start.point is None
    assert landmarks.upper_berm_start.detection.reason_code == "boundary_truncated"
    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.source == "physical_breakpoint"
    assert landmarks.upper_crest.point == _point(3.0, 102.126)
    assert landmarks.lower_toe.detection.reliable
    assert landmarks.lower_toe.point == _point(10.8, 83.7)


def test_outer_boundary_face_run_uses_only_the_toe_connected_component() -> None:
    """Remote or earlier topographic crossings cannot become the wall crest."""
    wall = _rounded_outer_topography("flat")
    remote = _outer_segments(((-14.0, 102.0), (-12.0, 105.0)))
    # The connected topographic rise also crosses 103.4 before the rollover;
    # only the later crossing has a sustained Face immediately downwall.
    actual = (
        remote[0],
        _segment((-6.0, 102.0), (-4.0, 104.36), 830),
        *wall[1:],
    )

    landmarks = detect_actual_landmarks(_outer_boundary_profile(actual))

    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.source == "physical_breakpoint"
    assert landmarks.upper_crest.point == _point(3.0, 102.126)


def test_gap_at_a_face_run_onset_cannot_fabricate_crest() -> None:
    """Matrix case 9: an unsupported component endpoint is not a rim."""
    actual = _outer_segments((
        (-6.0, 104.36),
        (-2.0, 104.36),
        (-0.2, 103.75),
    )) + _outer_segments((
        (0.5, 103.4),
        (3.0, 102.126),
        (10.8, 83.7),
        (17.0, 83.5),
    ))

    landmarks = detect_actual_landmarks(_outer_boundary_profile(actual))

    assert not landmarks.upper_crest.detection.reliable
    assert landmarks.lower_toe.detection.reliable


def test_internal_design_berm_never_uses_outer_boundary_face_run_path() -> None:
    """Matrix case 10: Design provenance, not a failed crest, enables this path."""
    landmarks = detect_actual_landmarks(
        _profile(_rounded_outer_topography("flat"), raw_context=True)
    )

    assert not landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.source != "boundary_face_run_onset"


def test_outer_boundary_physical_breakpoint_wins_over_face_run_onset() -> None:
    """Matrix case 11: a reliable sharp crest retains its direct provenance."""
    actual = _outer_segments((
        (-5.0, 104.6),
        (0.9, 103.4),
        (1.2, 103.1),
        (10.8, 83.7),
        (17.0, 83.5),
    ))

    crest = detect_actual_landmarks(_outer_boundary_profile(actual)).upper_crest

    assert crest.detection.reliable
    assert crest.source == "physical_breakpoint"
    assert crest.point.u == pytest.approx(0.9)


def test_outer_boundary_face_run_preserves_physical_high_toe_and_requires_floor() -> None:
    """Matrix cases 12-14: no lower toe means no synthetic complete wall."""
    high_toe = _rounded_outer_topography("flat")[:-2] + _outer_segments((
        (3.0, 102.126), (10.8, 90.2), (17.0, 90.0),
    ))
    no_floor = _rounded_outer_topography("flat")[:-1]
    unexposed = _outer_segments((
        (-6.0, 104.36), (-2.0, 104.36), (3.0, 102.126),
    ))

    high = detect_actual_landmarks(_outer_boundary_profile(high_toe))
    missing_floor = detect_actual_landmarks(_outer_boundary_profile(no_floor))
    missing_face = detect_actual_landmarks(_outer_boundary_profile(unexposed))

    assert high.upper_crest.source == "physical_breakpoint"
    assert high.lower_toe.detection.reliable
    assert high.lower_toe.point == _point(10.8, 90.2)
    assert not missing_floor.lower_toe.detection.reliable
    assert not missing_floor.upper_crest.detection.reliable
    assert not missing_face.upper_crest.detection.reliable


def test_outer_boundary_face_run_onset_is_stable_under_tin_subdivision() -> None:
    """Matrix case 15: representation changes preserve physical provenance."""
    actual = _rounded_outer_topography("flat")
    dense = _subdivide(actual, (5, 7, 11, 13, 17, 19))

    sparse = detect_actual_landmarks(_outer_boundary_profile(actual))
    subdivided = detect_actual_landmarks(_outer_boundary_profile(dense))

    for landmarks in (sparse, subdivided):
        assert landmarks.upper_crest.detection.reliable
        assert landmarks.upper_crest.source == "physical_breakpoint"
        assert (landmarks.upper_crest.point.u, landmarks.upper_crest.point.z) == pytest.approx(
            (3.0, 102.126)
        )


def test_outer_boundary_face_run_keeps_angle_and_toe_kpis_without_a_berm_width() -> None:
    measured = measure_profile(_outer_boundary_profile(_rounded_outer_topography("flat")))

    assert measured.actual_landmarks.upper_crest.source == "physical_breakpoint"
    assert measured.angle_status.reliable
    assert measured.actual_overall_angle_deg is not None
    assert not measured.berm_status.reliable
    assert measured.actual_upper_berm_width_m is None
    assert measured.toe_status.reliable
    assert measured.toe_deviation_m is not None


def _shift_u(
    segments: tuple[SectionSegment, ...], delta_u: float,
) -> tuple[SectionSegment, ...]:
    return tuple(
        replace(
            segment,
            start=replace(segment.start, u=segment.start.u + delta_u, x=segment.start.x + delta_u),
            end=replace(segment.end, u=segment.end.u + delta_u, x=segment.end.x + delta_u),
        )
        for segment in segments
    )


@pytest.mark.parametrize(
    "topography_start",
    (
        (-5.0, 105.8),  # A: descends toward crest
        (-5.0, 103.4),  # B: flat near crest
        (-5.0, 101.0),  # C: rises to the local crest apex
    ),
)
def test_outer_boundary_sharp_face_is_stable_under_representation_variants(topography_start) -> None:
    """Cases 1-3 and variants 7-12 share one physical wall Face."""
    base = _outer_segments((
        topography_start,
        (1.2, 103.4),
        (10.8, 83.7),
        (17.0, 83.5),
    ))
    variants = (
        (base, (1.2, 103.4), (10.8, 83.7)),
        (_subdivide(base, (19, 31, 23)), (1.2, 103.4), (10.8, 83.7)),
        (_subdivide(base, (1, 1, 1)), (1.2, 103.4), (10.8, 83.7)),
        (_subdivide(base, (3, 7, 11)), (1.2, 103.4), (10.8, 83.7)),
        (_subdivide(base, (5, 9, 7), noise_m=0.015), (1.2, 103.4), (10.8, 83.7)),
        (_shift_u(base, 0.16), (1.36, 103.4), (10.96, 83.7)),
    )
    for actual, expected_crest, expected_toe in variants:
        landmarks = detect_actual_landmarks(_outer_boundary_profile(actual))
        assert landmarks.upper_crest.detection.reliable
        assert landmarks.upper_crest.source == "physical_breakpoint"
        assert (landmarks.upper_crest.point.u, landmarks.upper_crest.point.z) == pytest.approx(
            expected_crest
        )
        assert (landmarks.lower_toe.point.u, landmarks.lower_toe.point.z) == pytest.approx(
            expected_toe
        )


@pytest.mark.parametrize("kind", ("rising", "flat", "falling"))
def test_outer_boundary_rounded_face_run_is_stable_under_representation_variants(kind) -> None:
    """Cases 4-6 retain their physical onset, never a Design-Z crossing."""
    base = _rounded_outer_topography(kind)
    variants = (
        (base, (3.0, 102.126), (10.8, 83.7)),
        (_subdivide(base, (5, 7, 11, 13, 17, 19)), (3.0, 102.126), (10.8, 83.7)),
        (_subdivide(base, (1, 1, 1, 1, 1, 1)), (3.0, 102.126), (10.8, 83.7)),
        (_subdivide(base, (3, 5, 7, 9, 11, 13), noise_m=0.012), (3.0, 102.126), (10.8, 83.7)),
        (_shift_u(base, 0.14), (3.14, 102.126), (10.94, 83.7)),
    )
    for actual, expected_crest, expected_toe in variants:
        landmarks = detect_actual_landmarks(_outer_boundary_profile(actual))
        assert landmarks.upper_crest.detection.reliable
        assert landmarks.upper_crest.source == "physical_breakpoint"
        assert (landmarks.upper_crest.point.u, landmarks.upper_crest.point.z) == pytest.approx(
            expected_crest
        )
        assert (landmarks.lower_toe.point.u, landmarks.lower_toe.point.z) == pytest.approx(
            expected_toe
        )


def test_outer_boundary_toe_is_invariant_to_floor_extension_and_subdivision() -> None:
    """Cases 13-14: floor representation cannot move the Face-to-floor onset."""
    base = _outer_segments(((-5.0, 105.8), (1.2, 103.4), (10.8, 83.7), (17.0, 83.5)))
    extended = _outer_segments((
        (-5.0, 105.8), (1.2, 103.4), (10.8, 83.7), (17.0, 83.5), (42.0, 83.2),
    ))
    subdivided = (*base[:-1], *_subdivide((base[-1],), (17,)))

    for actual in (base, extended, subdivided):
        toe = detect_actual_landmarks(_outer_boundary_profile(actual)).lower_toe
        assert toe.detection.reliable
        assert (toe.point.u, toe.point.z) == pytest.approx((10.8, 83.7))


def test_steep_topography_with_a_distinct_face_regime_keeps_outer_crest() -> None:
    """Cases 15-16: slope sign/magnitude is not the outer crest criterion."""
    actual = _outer_segments((
        (-5.0, 107.0),
        (1.2, 103.4),  # moderately steep descending topography
        (10.8, 83.7),
        (17.0, 83.5),
    ))

    landmarks = detect_actual_landmarks(_outer_boundary_profile(actual))

    assert landmarks.upper_crest.detection.reliable
    assert landmarks.upper_crest.source == "physical_breakpoint"
    assert landmarks.lower_toe.detection.reliable


def test_multi_bench_wall_uses_external_crest_and_final_toe() -> None:
    """Matrix case 14: internal Berms do not replace the external landmarks."""
    actual = (
        _segment((-9.2, 112.1), (-4.6, 103.9), 500),
        _segment((-4.6, 103.9), (1.3, 103.35), 501),
        _segment((1.3, 103.35), (4.5, 96.2), 502),
        _segment((4.5, 96.2), (6.4, 96.0), 503),
        _segment((6.4, 96.0), (11.0, 83.8), 504),
        _segment((11.0, 83.8), (17.0, 83.5), 505),
    )

    landmarks = detect_actual_landmarks(_profile(actual, multi_bench=True))

    assert landmarks.upper_crest.point == _point(1.3, 103.35)
    assert landmarks.lower_toe.point == _point(11.0, 83.8)


def test_gap_remote_surface_and_missing_floor_remain_unsupported() -> None:
    """Matrix cases 15-17 retain genuine negative evidence."""
    previous_face, upper, face, _floor = _clean_actual()
    gap = (
        previous_face,
        upper,
        _segment((1.3, 103.35), (9.6, 84.5), 600),
        _segment((10.4, 83.7), (17.0, 83.5), 601),
    )
    remote = (
        previous_face,
        upper,
        face,
        _segment((40.0, 83.8), (46.0, 83.5), 602),
    )
    no_floor = (previous_face, upper, face)

    gap_toe = detect_actual_landmarks(_profile(gap)).lower_toe
    remote_toe = detect_actual_landmarks(_profile(remote)).lower_toe
    missing_toe = detect_actual_landmarks(_profile(no_floor)).lower_toe

    assert not gap_toe.detection.reliable
    assert gap_toe.detection.reason_code == "data_gap"
    assert not remote_toe.detection.reliable
    assert not missing_toe.detection.reliable
    assert missing_toe.detection.reason_code in {
        "insufficient_right_support", "incompatible_geometry",
    }
