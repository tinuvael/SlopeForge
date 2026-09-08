from __future__ import annotations

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import (
    SurfaceRoleMapping,
    build_transverse_profiles,
    extract_design_wall_topology,
)


MAPPING = SurfaceRoleMapping(
    "COLOUR", ((2, "face"), (5, "berm"), (3, "road"))
)


def _wedge_surface() -> TriangleSurface:
    # Upper Crest A->B and Lower Toe C->B share B. The Face height and
    # transverse wall width taper toward B, so the exact apex is zero-width.
    vertices = (
        SurfaceVertex(0.0, 0.0, 20.0),   # A upper-crest start
        SurfaceVertex(0.0, 20.0, 18.0),  # B shared crest/toe endpoint
        SurfaceVertex(6.0, 0.0, 10.0),   # C lower-toe start
        SurfaceVertex(-2.0, 0.0, 20.0),  # upstream platform
        SurfaceVertex(8.0, 0.0, 10.0),   # downstream platform
    )
    triangles = (
        SurfaceTriangle((0, 2, 1), source_attributes={"COLOUR": 2}),
        SurfaceTriangle((3, 0, 1), source_attributes={"COLOUR": 5}),
        SurfaceTriangle((2, 4, 1), source_attributes={"COLOUR": 5}),
    )
    return TriangleSurface(vertices, triangles)


def test_shared_endpoint_wedge_runs_through_full_pipeline() -> None:
    surface = _wedge_surface()
    topology = extract_design_wall_topology(surface, MAPPING)
    crests = [line for line in topology.transitions if line.kind == "crest"]
    toes = [line for line in topology.transitions if line.kind == "toe"]

    assert len(crests) == 1
    assert len(toes) == 1
    assert set(crests[0].points) & set(toes[0].points) == {
        SurfaceVertex(0.0, 20.0, 18.0)
    }

    area = PlanPolygon((
        PlanPoint(0.0, 0.0),
        PlanPoint(6.0, 0.0),
        PlanPoint(0.0, 20.0),
        PlanPoint(0.0, 0.0),
    ))
    result = build_transverse_profiles(
        surface,
        surface,
        area,
        MAPPING,
        spacing_m=4.0,
        tangent_window_m=2.0,
    )

    assert result.profiles
    widths = [
        upper - lower
        for lower, upper in (
            profile.assessment_u_interval for profile in result.profiles
        )
    ]
    assert widths == sorted(widths, reverse=True)
    assert widths[0] == 6.0
    assert widths[-1] < widths[0] / 4.0

    # The exact shared endpoint has zero transverse width, so it is not emitted
    # as an unstable profile origin; nearby useful stations remain.
    assert all(profile.alignment.origin.y < 20.0 for profile in result.profiles)
    assert result.toe_lines
    assert any(
        SurfaceVertex(0.0, 20.0, 18.0) in line.points
        for line in result.toe_lines
    )
