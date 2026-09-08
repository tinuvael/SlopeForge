from __future__ import annotations

from types import SimpleNamespace

from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import (
    DesignBoundaryEdge,
    SectionPoint,
    SurfaceRoleMapping,
    WallTransitionLine,
    build_transverse_profiles,
)
from domain.wall_conformance.design import _boundary_chains
from domain.wall_conformance.engine import _external_toe_lines


MAPPING = SurfaceRoleMapping("COLOUR", ((2, "face"), (5, "berm"), (3, "road")))


def _surface() -> TriangleSurface:
    ys = (0.0, 10.0, 20.0)
    xs = (0.0, 4.0, 9.0, 14.0)
    zs = (30.0, 20.0, 19.0, 18.0)
    vertices = tuple(
        SurfaceVertex(x, y, zs[column] + y * 0.05)
        for column, x in enumerate(xs)
        for y in ys
    )
    triangles = []
    for column, role in enumerate((2, 5, 3)):
        for row in range(2):
            a = column * 3 + row
            b = a + 1
            c = (column + 1) * 3 + row
            d = c + 1
            triangles.extend((
                SurfaceTriangle((a, c, b), source_attributes={"COLOUR": role}),
                SurfaceTriangle((c, d, b), source_attributes={"COLOUR": role}),
            ))
    return TriangleSurface(vertices, tuple(triangles))


def _reordered(surface: TriangleSurface, *, reverse_vertices=False, reverse_triangles=False,
               reverse_winding=False) -> TriangleSurface:
    order = list(range(len(surface.vertices)))
    if reverse_vertices:
        order.reverse()
    new_index = {old: new for new, old in enumerate(order)}
    vertices = tuple(surface.vertices[index] for index in order)
    triangles = list(surface.triangles)
    if reverse_triangles:
        triangles.reverse()
    remapped = []
    for triangle in triangles:
        indices = tuple(new_index[index] for index in triangle.vertex_indices)
        if reverse_winding:
            indices = tuple(reversed(indices))
        remapped.append(SurfaceTriangle(
            indices, source_attributes=triangle.source_attributes
        ))
    return TriangleSurface(vertices, tuple(remapped))


def _result_signature(surface: TriangleSurface):
    area = PlanPolygon((
        PlanPoint(0, 1), PlanPoint(14, 1), PlanPoint(14, 19),
        PlanPoint(0, 19), PlanPoint(0, 1),
    ))
    result = build_transverse_profiles(
        surface, surface, area, MAPPING, spacing_m=4.0, tangent_window_m=3.0,
    )
    return (
        tuple(tuple((round(p.x, 6), round(p.y, 6), round(p.z, 6))
                    for p in line.points) for line in result.crest_lines),
        tuple((round(profile.alignment.origin.x, 6),
               round(profile.alignment.origin.y, 6),
               tuple(round(value, 6) for value in profile.alignment.normal_xy),
               tuple(round(value, 6) for value in profile.assessment_u_interval),
               profile.design_section.topology_signature)
              for profile in result.profiles),
        tuple(tuple((round(p.x, 6), round(p.y, 6), round(p.z, 6))
                    for p in line.points) for line in result.toe_lines),
    )


def test_full_pipeline_is_invariant_to_surface_storage_order() -> None:
    surface = _surface()
    expected = _result_signature(surface)

    for variant in (
        _reordered(surface, reverse_vertices=True),
        _reordered(surface, reverse_triangles=True),
        _reordered(surface, reverse_winding=True),
        _reordered(surface, reverse_vertices=True, reverse_triangles=True,
                   reverse_winding=True),
    ):
        assert _result_signature(variant) == expected


def test_degree_three_junction_preserves_every_geometric_branch() -> None:
    junction = SurfaceVertex(0, 0, 10)
    interior = SurfaceVertex(0, 1, 9)
    endpoints = (
        SurfaceVertex(-2, 0, 10), SurfaceVertex(2, 0, 10),
        SurfaceVertex(0, 2, 10),
    )
    edges = tuple(
        DesignBoundaryEdge("crest", min(junction, endpoint, key=lambda p: (p.x, p.y, p.z)),
                           max(junction, endpoint, key=lambda p: (p.x, p.y, p.z)),
                           0, interior, "Face/Platform")
        for endpoint in endpoints
    )

    chains = _boundary_chains(tuple(reversed(edges)), "crest")

    assert len(chains) == 3
    assert {frozenset((line.points[0], line.points[-1])) for line, _ in chains} == {
        frozenset((junction, endpoint)) for endpoint in endpoints
    }


def test_all_observed_lower_toe_components_are_retained() -> None:
    first = WallTransitionLine("toe", (SurfaceVertex(0, 0, 0), SurfaceVertex(0, 20, 0)))
    second = WallTransitionLine("toe", (SurfaceVertex(10, 0, 0), SurfaceVertex(10, 4, 0)))
    profiles = tuple(
        SimpleNamespace(external_toe=SectionPoint(0, 0, 0, float(y)))
        for y in range(20)
    ) + tuple(
        SimpleNamespace(external_toe=SectionPoint(0, 0, 10, float(y)))
        for y in range(4)
    )

    assert _external_toe_lines(profiles, (first, second)) == (first, second)
