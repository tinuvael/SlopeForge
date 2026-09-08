import pytest

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import SurfaceRoleMapping, build_transverse_profiles


MAPPING = SurfaceRoleMapping("COLOUR", ((2, "face"), (5, "berm"), (3, "road")))


def _bench(dx: float = 0.0) -> TriangleSurface:
    vertices = (
        SurfaceVertex(-5 + dx, 0, 10),
        SurfaceVertex(0 + dx, 0, 10),
        SurfaceVertex(-5 + dx, 20, 10),
        SurfaceVertex(0 + dx, 20, 10),
        SurfaceVertex(5 + dx, 0, 0),
        SurfaceVertex(5 + dx, 20, 0),
        SurfaceVertex(10 + dx, 0, 0),
        SurfaceVertex(10 + dx, 20, 0),
    )
    specifications = (
        ((0, 1, 2), 5),
        ((1, 3, 2), 5),
        ((1, 4, 3), 2),
        ((4, 5, 3), 2),
        ((4, 6, 5), 3),
        ((6, 7, 5), 3),
    )
    triangles = tuple(
        SurfaceTriangle(indices, source_attributes={"COLOUR": colour})
        for indices, colour in specifications
    )
    return TriangleSurface(vertices, triangles)


def test_actual_surface_offset_is_preserved_in_design_wall_normal_coordinates() -> None:
    area = PlanPolygon(
        (
            PlanPoint(-2, 4),
            PlanPoint(8, 4),
            PlanPoint(8, 16),
            PlanPoint(-2, 16),
            PlanPoint(-2, 4),
        )
    )
    result = build_transverse_profiles(
        _bench(),
        _bench(dx=1.0),
        area,
        MAPPING,
        spacing_m=5.0,
        tangent_window_m=4.0,
    )

    profile = result.profiles[0]
    design_points = {(round(point.u, 6), round(point.z, 6)) for segment in profile.design_segments for point in (segment.start, segment.end)}
    actual_points = {(round(point.u, 6), round(point.z, 6)) for segment in profile.actual_segments for point in (segment.start, segment.end)}

    assert (0.0, 10.0) in design_points
    assert (5.0, 0.0) in design_points
    assert (1.0, 10.0) in actual_points
    assert (6.0, 0.0) in actual_points
    assert profile.alignment.normal_xy == pytest.approx((1.0, 0.0))
