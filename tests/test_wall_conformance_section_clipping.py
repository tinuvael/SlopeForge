import pytest

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.wall_conformance import (
    SectionPoint,
    SectionSegment,
    WallAlignmentSample,
    clip_section_segments_to_z_range,
    intersect_surface_with_profile,
)
from domain.wall_conformance.sections import connected_section_segments


def test_profile_half_width_clips_one_segment_at_both_limits() -> None:
    surface = TriangleSurface(
        vertices=(
            SurfaceVertex(-10, -1, 0),
            SurfaceVertex(10, -1, 20),
            SurfaceVertex(0, 1, 10),
        ),
        triangles=(SurfaceTriangle((0, 1, 2), source_id="wide"),),
    )
    alignment = WallAlignmentSample(
        chainage_m=0,
        origin=SurfaceVertex(0, 0, 10),
        tangent_xy=(0.0, 1.0),
        normal_xy=(1.0, 0.0),
    )

    segments = intersect_surface_with_profile(
        surface,
        alignment,
        half_width_m=2.0,
    )

    assert len(segments) == 1
    assert segments[0].start.u == pytest.approx(-2.0)
    assert segments[0].start.z == pytest.approx(8.0)
    assert segments[0].end.u == pytest.approx(2.0)
    assert segments[0].end.z == pytest.approx(12.0)


def test_actual_segment_is_clipped_at_design_elevation_limit() -> None:
    segment = SectionSegment(
        SectionPoint(0.0, 625.0, 0.0, 0.0),
        SectionPoint(6.0, 640.0, 6.0, 0.0),
        4,
    )

    clipped = clip_section_segments_to_z_range((segment,), 600.0, 630.0)

    assert len(clipped) == 1
    assert clipped[0].start.z == pytest.approx(625.0)
    assert clipped[0].end.z == pytest.approx(630.0)
    assert clipped[0].end.u == pytest.approx(2.0)


def test_actual_segment_outside_design_elevation_is_removed() -> None:
    segment = SectionSegment(
        SectionPoint(0.0, 665.0, 0.0, 0.0),
        SectionPoint(2.0, 670.0, 2.0, 0.0),
        5,
    )

    assert clip_section_segments_to_z_range((segment,), 600.0, 630.0) == ()


def test_section_connectivity_requires_endpoint_proximity_in_u_and_z() -> None:
    local_platform = SectionSegment(
        SectionPoint(-0.3, 630.0, -0.3, 0.0),
        SectionPoint(0.0, 630.0, 0.0, 0.0),
        1,
        "berm",
    )
    remote_face = SectionSegment(
        SectionPoint(-2.0, 680.0, -2.0, 0.0),
        SectionPoint(-0.2, 670.0, -0.2, 0.0),
        2,
        "face",
    )

    connected = connected_section_segments(
        (remote_face, local_platform), SectionPoint(0.0, 630.0, 0.0, 0.0)
    )

    assert connected == (local_platform,)


def test_section_connectivity_walks_truly_touching_u_z_endpoints() -> None:
    platform = SectionSegment(
        SectionPoint(-3.0, 630.0, -3.0, 0.0),
        SectionPoint(0.0, 630.0, 0.0, 0.0),
        1,
        "berm",
    )
    face = SectionSegment(
        SectionPoint(-6.0, 640.0, -6.0, 0.0),
        SectionPoint(-3.0, 630.0, -3.0, 0.0),
        2,
        "face",
    )

    connected = connected_section_segments(
        (face, platform), SectionPoint(0.0, 630.0, 0.0, 0.0)
    )

    assert set(connected) == {platform, face}
