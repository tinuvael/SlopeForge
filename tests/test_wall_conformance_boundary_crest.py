from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import (
    SurfaceRoleMapping,
    build_transverse_profiles,
    extract_design_transition_lines,
    extract_design_wall_topology,
)


MAPPING = SurfaceRoleMapping("COLOUR", ((2, "face"), (3, "road")))


def _triangle(indices, role):
    return SurfaceTriangle(indices, source_attributes={"COLOUR": role})


def _uppermost_bench():
    vertices = (
        SurfaceVertex(0, 0, 10), SurfaceVertex(0, 20, 10),
        SurfaceVertex(5, 0, 0), SurfaceVertex(5, 20, 0),
        SurfaceVertex(10, 0, 0), SurfaceVertex(10, 20, 0),
    )
    triangles = (
        _triangle((0, 2, 1), 2), _triangle((2, 3, 1), 2),
        _triangle((2, 4, 3), 3), _triangle((4, 5, 3), 3),
    )
    return TriangleSurface(vertices, triangles)


def test_uppermost_face_boundary_is_crest_and_normal_toe_is_preserved():
    surface = _uppermost_bench()
    transitions = extract_design_transition_lines(surface, MAPPING)
    crest = next(line for line in transitions if line.kind == "crest")
    toe = next(line for line in transitions if line.kind == "toe")
    assert {(p.x, p.z) for p in crest.points} == {(0, 10)}
    assert {(p.x, p.z) for p in toe.points} == {(5, 0)}

    area = PlanPolygon((
        PlanPoint(-1, 3), PlanPoint(8, 3), PlanPoint(8, 17),
        PlanPoint(-1, 17), PlanPoint(-1, 3),
    ))
    result = build_transverse_profiles(
        surface, surface, area, MAPPING,
        spacing_m=5, tangent_window_m=4,
    )
    assert result.profiles
    assert all(profile.alignment.normal_xy[0] > 0 for profile in result.profiles)


def test_sloping_lateral_face_boundaries_are_not_crest_candidates():
    transitions = extract_design_transition_lines(_uppermost_bench(), MAPPING)
    crests = [line for line in transitions if line.kind == "crest"]
    crest_edges = {
        ((a.x, a.y, a.z), (b.x, b.y, b.z))
        for line in crests for a, b in zip(line.points, line.points[1:])
    }
    assert all(a[2] == b[2] == 10 for a, b in crest_edges)


def test_uppermost_alignment_accepts_material_crest_and_toe_elevation_change():
    vertices = (
        SurfaceVertex(0, 0, 120), SurfaceVertex(0, 20, 108),
        SurfaceVertex(5, 0, 100), SurfaceVertex(5, 20, 91),
        SurfaceVertex(10, 0, 99), SurfaceVertex(10, 20, 90),
    )
    surface = TriangleSurface(vertices, (
        _triangle((0, 2, 1), 2), _triangle((2, 3, 1), 2),
        _triangle((2, 4, 3), 3), _triangle((4, 5, 3), 3),
    ))
    transitions = extract_design_transition_lines(surface, MAPPING)
    crest = next(line for line in transitions if line.kind == "crest")
    toe = next(line for line in transitions if line.kind == "toe")
    assert {p.z for p in crest.points} == {108, 120}
    assert {p.z for p in toe.points} == {91, 100}
    area = PlanPolygon((
        PlanPoint(-1, 2), PlanPoint(8, 2), PlanPoint(8, 18),
        PlanPoint(-1, 18), PlanPoint(-1, 2),
    ))
    result = build_transverse_profiles(
        surface, surface, area, MAPPING,
        spacing_m=4, tangent_window_m=5,
    )
    assert len(result.profiles) >= 3
    assert max(p.alignment.origin.z for p in result.profiles) - min(
        p.alignment.origin.z for p in result.profiles
    ) > 5


def _multi_row_upper_face(*, reverse_winding=False):
    chainages = (0.0, 6.0, 14.0, 22.0)
    crest_z = (120.0, 128.0, 113.0, 125.0)
    down_rows = (0.0, 2.0, 5.0, 8.0, 10.0)
    vertices = tuple(
        SurfaceVertex(
            down + (0.12 * row if column in {1, 2} else 0.0),
            chainage,
            crest_z[row] - 2.0 * down,
        )
        for column, down in enumerate(down_rows)
        for row, chainage in enumerate(chainages)
    )
    triangles = []
    for column in range(len(down_rows) - 1):
        role = 2 if column < len(down_rows) - 2 else 3
        for row in range(len(chainages) - 1):
            a = column * 4 + row
            b = a + 1
            c = (column + 1) * 4 + row
            d = c + 1
            for indices in ((a, c, b), (c, d, b)):
                if reverse_winding:
                    indices = tuple(reversed(indices))
                triangles.append(_triangle(indices, role))
    return TriangleSurface(vertices, tuple(triangles))


def test_multi_row_lateral_boundary_never_turns_into_upper_alignment():
    area = PlanPolygon((
        PlanPoint(-1, 1), PlanPoint(11, 1), PlanPoint(11, 21),
        PlanPoint(-1, 21), PlanPoint(-1, 1),
    ))
    expected = {
        (0.0, 0.0, 120.0),
        (0.0, 6.0, 128.0),
        (0.0, 14.0, 113.0),
        (0.0, 22.0, 125.0),
    }
    for reverse_winding in (False, True):
        surface = _multi_row_upper_face(reverse_winding=reverse_winding)
        topology = extract_design_wall_topology(surface, MAPPING)
        candidates = topology.alignment_boundaries
        assert candidates
        # New topology intentionally exposes preliminary Face/Platform toe
        # chains as crest candidates so the exact section can rescue a true
        # Upper Crest on irregular real TINs.  That must not admit the lateral
        # crop edges: every candidate in this fixture remains strike-oriented.
        assert all(
            max(point.x for point in boundary.line.points)
            - min(point.x for point in boundary.line.points)
            <= 1e-6
            for boundary in candidates
        )
        upper = [
            boundary
            for boundary in candidates
            if {(point.x, point.y, point.z) for point in boundary.line.points}
            == expected
        ]
        assert len(upper) == 1
        assert len(upper[0].interior_points) == 3

        result = build_transverse_profiles(
            surface, surface, area, MAPPING,
            spacing_m=4.0, tangent_window_m=4.0,
        )
        assert result.profiles
        assert all(profile.alignment.normal_xy[0] > 0 for profile in result.profiles)
