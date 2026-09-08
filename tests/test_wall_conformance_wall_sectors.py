from __future__ import annotations

from dataclasses import replace
from math import cos, pi, sin
from pathlib import Path

import pytest

from domain.geometry.operations import clip_datamine_line_by_polygon
from domain.geometry.surfaces import (
    SurfaceTriangle,
    SurfaceVertex,
    TriangleSurface,
)
from domain.geometry.types import PlanLineString, PlanPoint, PlanPolygon
from domain.wall_conformance.design_topology import (
    LocalPortalRunPair,
    build_design_topology_index,
)
from domain.wall_conformance.models import SurfaceRoleMapping
from domain.wall_conformance import wall_sectors as wall_sector_module
from domain.wall_conformance.wall_sectors import extract_wall_sectors


ROLE_MAPPING = SurfaceRoleMapping(
    "ROLE",
    (("FACE", "face"), ("BERM", "berm"), ("ROAD", "road")),
)


class _MeshBuilder:
    def __init__(self) -> None:
        self.vertices: list[SurfaceVertex] = []
        self.triangles: list[SurfaceTriangle] = []
        self._indices: dict[tuple[float, float, float], int] = {}

    def vertex(
        self, x: float, y: float, z: float, *, reuse: bool = True
    ) -> int:
        key = (float(x), float(y), float(z))
        if reuse and key in self._indices:
            return self._indices[key]
        index = len(self.vertices)
        self.vertices.append(SurfaceVertex(*key))
        if reuse:
            self._indices[key] = index
        return index

    def triangle(
        self, indices: tuple[int, int, int], role: str, source_id: str
    ) -> None:
        self.triangles.append(SurfaceTriangle(
            indices, source_id, {"ROLE": role.upper()}
        ))

    def strip(
        self,
        x0: float,
        z0: float,
        x1: float,
        z1: float,
        y_values: tuple[float, ...],
        role: str,
        prefix: str,
        segments: tuple[int, ...] | None = None,
    ) -> None:
        wanted = range(len(y_values) - 1) if segments is None else segments
        for index in wanted:
            y0, y1 = y_values[index:index + 2]
            a = self.vertex(x0, y0, z0)
            b = self.vertex(x0, y1, z0)
            c = self.vertex(x1, y0, z1)
            d = self.vertex(x1, y1, z1)
            self.triangle((a, c, d), role, f"{prefix}:{index}:0")
            self.triangle((a, d, b), role, f"{prefix}:{index}:1")

    def quad_strip(
        self,
        upper: tuple[tuple[float, float], ...],
        lower: tuple[tuple[float, float], ...],
        *,
        prefix: str,
    ) -> None:
        for index in range(len(upper) - 1):
            a = self.vertex(*upper[index], 10.0)
            b = self.vertex(*upper[index + 1], 10.0)
            c = self.vertex(*lower[index], 0.0)
            d = self.vertex(*lower[index + 1], 0.0)
            self.triangle((a, c, d), "face", f"{prefix}:{index}:0")
            self.triangle((a, d, b), "face", f"{prefix}:{index}:1")

    def surface(self) -> TriangleSurface:
        return TriangleSurface(tuple(self.vertices), tuple(self.triangles))


def _layered(
    roles: tuple[str, ...],
    *,
    y_values: tuple[float, ...] = (0.0, 10.0),
    y_offset: float = 0.0,
) -> TriangleSurface:
    builder = _MeshBuilder()
    y_values = tuple(value + y_offset for value in y_values)
    xz = [(0.0, 30.0)]
    for role in roles:
        x, z = xz[-1]
        xz.append((x + 4.0, z - 10.0 if role == "face" else z))
    for index, role in enumerate(roles):
        builder.strip(
            xz[index][0], xz[index][1],
            xz[index + 1][0], xz[index + 1][1],
            y_values, role, f"{role}:{index}",
        )
    return builder.surface()


def _curved_layered_with_folded_global_projection() -> TriangleSurface:
    """One corridor whose complete portals fold in one global strike axis."""
    builder = _MeshBuilder()
    strike_points = (
        (0.0, 0.0),
        (0.5, 4.0),
        (1.0, 3.0),
        (1.5, 7.0),
        (2.0, 10.0),
    )
    xz = (
        (0.0, 30.0),
        (4.0, 20.0),
        (8.0, 20.0),
        (12.0, 10.0),
    )
    for layer_index, role in enumerate(("face", "berm", "face")):
        x0, z0 = xz[layer_index]
        x1, z1 = xz[layer_index + 1]
        for segment_index, (first, second) in enumerate(
            zip(strike_points, strike_points[1:])
        ):
            a = builder.vertex(first[0] + x0, first[1], z0)
            b = builder.vertex(second[0] + x0, second[1], z0)
            c = builder.vertex(first[0] + x1, first[1], z1)
            d = builder.vertex(second[0] + x1, second[1], z1)
            builder.triangle(
                (a, c, d), role, f"curved:{role}:{segment_index}:0"
            )
            builder.triangle(
                (a, d, b), role, f"curved:{role}:{segment_index}:1"
            )
    return builder.surface()


def _mixed_parent_portal_surface(*, two_local_pairs: bool) -> TriangleSurface:
    builder = _MeshBuilder()
    strike_points = (
        (
            (18.0, 0.0),
            (12.0, 1.0),
            (12.0, 5.0),
            (6.0, 6.0),
            (6.0, 10.0),
            (0.0, 11.0),
        )
        if two_local_pairs
        else (
            (12.0, 0.0),
            (6.0, 1.0),
            (6.0, 9.0),
            (0.0, 10.0),
        )
    )
    for layer_index, role in enumerate(("face", "berm", "face")):
        first_offset = 4.0 * layer_index
        second_offset = first_offset + 4.0
        for segment_index, (first, second) in enumerate(
            zip(strike_points, strike_points[1:])
        ):
            a = builder.vertex(
                first[0] + first_offset,
                first[1],
                40.0 - first[0] - first_offset,
            )
            b = builder.vertex(
                second[0] + first_offset,
                second[1],
                40.0 - second[0] - first_offset,
            )
            c = builder.vertex(
                first[0] + second_offset,
                first[1],
                40.0 - first[0] - second_offset,
            )
            d = builder.vertex(
                second[0] + second_offset,
                second[1],
                40.0 - second[0] - second_offset,
            )
            builder.triangle(
                (a, c, d), role, f"mixed:{role}:{segment_index}:0"
            )
            builder.triangle(
                (a, d, b), role, f"mixed:{role}:{segment_index}:1"
            )
    return builder.surface()


def _combine(*surfaces: TriangleSurface) -> TriangleSurface:
    vertices: list[SurfaceVertex] = []
    triangles: list[SurfaceTriangle] = []
    for surface_index, surface in enumerate(surfaces):
        offset = len(vertices)
        vertices.extend(surface.vertices)
        triangles.extend(
            replace(
                triangle,
                vertex_indices=tuple(
                    index + offset for index in triangle.vertex_indices
                ),
                source_id=f"wall:{surface_index}:{triangle.source_id}",
            )
            for triangle in surface.triangles
        )
    return TriangleSurface(tuple(vertices), tuple(triangles))


def _rectangle(x0: float, y0: float, x1: float, y1: float) -> PlanPolygon:
    return PlanPolygon((
        PlanPoint(x0, y0), PlanPoint(x1, y0), PlanPoint(x1, y1),
        PlanPoint(x0, y1), PlanPoint(x0, y0),
    ))


def test_polygon_centroid_matches_normal_coordinate_result() -> None:
    points = (
        PlanPoint(0.0, 0.0),
        PlanPoint(4.0, 0.0),
        PlanPoint(4.0, 2.0),
        PlanPoint(0.0, 2.0),
    )

    centroid = wall_sector_module._polygon_centroid(points)

    assert (centroid.x, centroid.y) == pytest.approx((2.0, 1.0))


def test_polygon_centroid_is_translation_invariant_at_large_world_offset() -> None:
    local = (
        PlanPoint(0.0, 0.0),
        PlanPoint(6.0, 1.0),
        PlanPoint(5.0, 4.0),
        PlanPoint(1.0, 3.0),
    )
    offset_x, offset_y = 10_000_000.0, -20_000_000.0
    translated = tuple(
        PlanPoint(point.x + offset_x, point.y + offset_y)
        for point in local
    )

    local_centroid = wall_sector_module._polygon_centroid(local)
    translated_centroid = wall_sector_module._polygon_centroid(translated)

    assert (
        translated_centroid.x - offset_x,
        translated_centroid.y - offset_y,
    ) == pytest.approx((local_centroid.x, local_centroid.y), abs=5e-9)


def test_thin_large_coordinate_fragment_centroid_stays_inside_station_range() -> None:
    clipped_fragment = (
        PlanPoint(10401.685163020593, 12914.575304234058),
        PlanPoint(10403.654174158004, 12912.0132182679),
        PlanPoint(10403.656, 12912.011),
    )
    source_triangle = TriangleSurface(
        (
            SurfaceVertex(10396.008, 12921.962, 640.0),
            SurfaceVertex(10403.656, 12912.011, 640.0),
            SurfaceVertex(10395.658, 12921.728000000001, 640.903),
        ),
        (SurfaceTriangle((0, 1, 2), "1267", {"ROLE": "FACE"}),),
    )
    station_by_vertex = {
        0: 0.9926473838253586,
        1: 1.0,
        2: 1.0,
    }

    centroid = wall_sector_module._polygon_centroid(clipped_fragment)
    centroid_station = wall_sector_module._station_at_point_in_triangle(
        source_triangle, 0, station_by_vertex, centroid
    )
    vertex_stations = tuple(
        wall_sector_module._station_at_point_in_triangle(
            source_triangle, 0, station_by_vertex, point
        )
        for point in clipped_fragment
    )

    assert centroid_station == pytest.approx(0.999368427133, abs=1e-12)
    assert min(vertex_stations) <= centroid_station <= max(vertex_stations)


def test_polygon_centroid_is_canonical_across_start_and_winding() -> None:
    points = (
        PlanPoint(1.0, 1.0),
        PlanPoint(7.0, 2.0),
        PlanPoint(5.0, 6.0),
        PlanPoint(2.0, 4.0),
    )
    baseline = wall_sector_module._polygon_centroid(points)

    for offset in range(len(points)):
        shifted = points[offset:] + points[:offset]
        for variant in (shifted, tuple(reversed(shifted))):
            centroid = wall_sector_module._polygon_centroid(variant)
            assert (centroid.x, centroid.y) == pytest.approx(
                (baseline.x, baseline.y), abs=1e-12
            )


def test_polygon_centroid_preserves_degenerate_mean_fallback() -> None:
    points = (
        PlanPoint(2.0, 3.0),
        PlanPoint(4.0, 5.0),
        PlanPoint(6.0, 7.0),
    )

    centroid = wall_sector_module._polygon_centroid(points)

    assert (centroid.x, centroid.y) == pytest.approx((4.0, 5.0))


def _extract(surface: TriangleSurface, area: PlanPolygon):
    return extract_wall_sectors(
        surface, build_design_topology_index(surface, ROLE_MAPPING), area
    )


def _reordered(
    surface: TriangleSurface,
    *,
    vertices: bool = False,
    triangles: bool = False,
    winding: bool = False,
) -> TriangleSurface:
    surface_vertices = list(surface.vertices)
    surface_triangles = list(surface.triangles)
    if vertices:
        order = tuple(reversed(range(len(surface_vertices))))
        mapping = {old: new for new, old in enumerate(order)}
        surface_vertices = [surface_vertices[index] for index in order]
        surface_triangles = [replace(
            triangle,
            vertex_indices=tuple(mapping[index] for index in triangle.vertex_indices),
        ) for triangle in surface_triangles]
    if winding:
        surface_triangles = [replace(
            triangle,
            vertex_indices=(
                triangle.vertex_indices[0],
                triangle.vertex_indices[2],
                triangle.vertex_indices[1],
            ),
        ) for triangle in surface_triangles]
    if triangles:
        surface_triangles.reverse()
    return TriangleSurface(tuple(surface_vertices), tuple(surface_triangles))


def test_single_straight_face_extracts_upper_extent_and_face_samples() -> None:
    result = _extract(_layered(("face",)), _rectangle(1.0, 2.0, 3.0, 8.0))

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.supported
    assert sector.lower_guide is None
    assert sector.downstream_extent is not None
    assert sector.upper_portal_id is not None
    assert sector.lower_portal_id is None
    assert sector.downstream_portal_id is not None
    assert (sector.upper_guide.points[0].x, sector.upper_guide.points[0].y) == (
        pytest.approx(0.0), pytest.approx(2.0)
    )
    assert (sector.upper_guide.points[-1].x, sector.upper_guide.points[-1].y) == (
        pytest.approx(0.0), pytest.approx(8.0)
    )
    assert all(sample.downwall_xy == pytest.approx((1.0, 0.0))
               for sample in sector.face_direction_samples)
    assert len(sector.assessed_station_intervals) == 1
    assert sector.assessed_station_intervals[0].start_fraction == pytest.approx(0.0)
    assert sector.assessed_station_intervals[0].end_fraction == pytest.approx(1.0)


@pytest.mark.parametrize("platform_role", ["berm", "road"])
def test_two_face_layers_are_one_sector_through_platform(
    platform_role: str,
) -> None:
    result = _extract(
        _layered(("face", platform_role, "face")),
        _rectangle(1.0, 2.0, 11.0, 8.0),
    )

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.supported
    assert len(sector.face_component_ids) == 2
    assert len(sector.connection_ids) == 1
    assert all(sample.semantic_role == "face" for sample in sector.face_direction_samples)


def test_curved_portals_defer_global_projection_fold_to_local_station_mapping() -> None:
    surface = _curved_layered_with_folded_global_projection()
    topology = build_design_topology_index(surface, ROLE_MAPPING)

    assert len(topology.corridor_connections) == 1
    connection = topology.corridor_connections[0]
    assert connection.order_compatible
    assert connection.status == "compatible"

    result = extract_wall_sectors(
        surface,
        topology,
        _rectangle(1.0, 5.0, 13.0, 9.0),
    )

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.supported
    assert len(sector.face_component_ids) == 2
    assert sector.connection_ids == (connection.connection_id,)


def test_unique_mixed_parent_runs_assemble_one_continuous_sector() -> None:
    surface = _mixed_parent_portal_surface(two_local_pairs=False)
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    result = extract_wall_sectors(
        surface,
        topology,
        _rectangle(6.5, 2.0, 17.5, 8.0),
    )

    assert len(topology.corridor_connections) == 1
    assert topology.corridor_connections[0].status == "compatible"
    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.supported
    assert len(sector.face_component_ids) == 2
    assert sector.connection_ids == (
        topology.corridor_connections[0].connection_id,
    )


def test_competing_mixed_parent_runs_do_not_choose_arbitrary_sector() -> None:
    surface = _mixed_parent_portal_surface(two_local_pairs=True)
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    result = extract_wall_sectors(
        surface,
        topology,
        _rectangle(6.5, 0.5, 29.5, 10.5),
    )

    assert len(topology.corridor_connections) == 1
    assert topology.corridor_connections[0].status == "ambiguous"
    assert result.sectors
    assert all(not sector.supported for sector in result.sectors)
    assert any(
        "ambiguous_portal_span_correspondence" in sector.diagnostics.codes
        or "ambiguous_external_upper_guide" in sector.diagnostics.codes
        for sector in result.sectors
    )


def _portal_span_provenance_fixture():
    surface = _layered(
        ("face", "berm", "face"), y_values=(0.0, 5.0, 10.0)
    )
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    connection = topology.corridor_connections[0]
    portal_by_id = {portal.portal_id: portal for portal in topology.portals}
    adjacency = wall_sector_module._surface_face_adjacency(surface, topology)
    source_run = wall_sector_module._portal_runs(
        surface,
        topology,
        portal_by_id[connection.source_portal_id],
        "downstream",
        adjacency,
    )[0]
    target_run = wall_sector_module._portal_runs(
        surface,
        topology,
        portal_by_id[connection.target_portal_id],
        "upstream",
        adjacency,
    )[0]
    assert len(source_run.points) == 3
    assert len(target_run.points) == 3
    target_runs = (
        replace(target_run, points=target_run.points[:2]),
        replace(target_run, points=target_run.points[1:]),
    )
    source_edges = wall_sector_module._run_edge_keys(surface, source_run)
    target_edges = tuple(
        wall_sector_module._run_edge_keys(surface, run)[0]
        for run in target_runs
    )
    return surface, connection, source_run, target_runs, source_edges, target_edges


def _folded_source_station_fixture():
    (
        surface, connection, source_run, target_runs, source_edges, target_edges,
    ) = _portal_span_provenance_fixture()
    source_vertices = wall_sector_module._resolve_run_vertices(
        surface, source_run
    )
    assert source_vertices is not None
    station_by_vertex = dict(zip(
        source_vertices, (0.0, 1.0, 0.0), strict=True
    ))
    source_edge_runs = tuple(
        wall_sector_module._run_subspan_for_edge_keys(
            surface, source_run, frozenset((edge_key,))
        )
        for edge_key in source_edges
    )
    assert all(run is not None for run in source_edge_runs)
    return (
        surface,
        connection,
        source_run,
        target_runs,
        source_edges,
        target_edges,
        station_by_vertex,
        source_edge_runs,
    )


def test_outside_assessment_source_span_survives_active_provenance() -> None:
    (
        surface, connection, source_run, target_runs,
        source_edges, target_edges, station_by_vertex, source_edge_runs,
    ) = _folded_source_station_fixture()
    connection = replace(connection, local_run_pairs=(
        LocalPortalRunPair((source_edges[0],), (target_edges[0],)),
    ))
    active_run = source_edge_runs[0]
    assert active_run is not None
    outside_assessment = _rectangle(100.0, 100.0, 101.0, 101.0)
    assert not clip_datamine_line_by_polygon(
        PlanLineString(active_run.points), outside_assessment
    )

    chains = wall_sector_module._source_portal_correspondence_chains(
        surface,
        connection,
        source_run,
        target_runs,
        station_by_vertex,
        (0.0, 1.0),
        outside_assessment,
    )

    assert len(chains) == 1
    assert wall_sector_module._run_edge_keys(
        surface, chains[0].source_run
    ) == (source_edges[0],)
    assert wall_sector_module._run_edge_keys(
        surface, chains[0].target_run
    ) == (target_edges[0],)


def test_source_provenance_preserves_two_active_physical_chains() -> None:
    (
        surface, connection, source_run, target_runs,
        source_edges, target_edges, station_by_vertex, source_edge_runs,
    ) = _folded_source_station_fixture()
    connection = replace(connection, local_run_pairs=(
        LocalPortalRunPair((source_edges[0],), (target_edges[0],)),
        LocalPortalRunPair((source_edges[1],), (target_edges[1],)),
    ))

    chains = wall_sector_module._source_portal_correspondence_chains(
        surface,
        connection,
        source_run,
        target_runs,
        station_by_vertex,
        (0.0, 1.0),
        _rectangle(-1.0, -1.0, 20.0, 20.0),
    )

    assert len(chains) == 2


def test_outside_assessment_span_without_active_provenance_is_missing() -> None:
    (
        surface, connection, source_run, target_runs,
        source_edges, target_edges, station_by_vertex, source_edge_runs,
    ) = _folded_source_station_fixture()
    connection = replace(connection, local_run_pairs=(
        LocalPortalRunPair((source_edges[1],), (target_edges[1],)),
    ))
    active_run = source_edge_runs[0]
    inactive_run = source_edge_runs[1]
    assert active_run is not None
    assert inactive_run is not None
    assert not clip_datamine_line_by_polygon(
        PlanLineString(inactive_run.points),
        _rectangle(100.0, 100.0, 101.0, 101.0),
    )

    chains = wall_sector_module._source_portal_correspondence_chains(
        surface,
        connection,
        active_run,
        target_runs,
        station_by_vertex,
        (0.0, 1.0),
        _rectangle(100.0, 100.0, 101.0, 101.0),
    )

    assert chains == ()


def test_portal_span_provenance_reduces_global_target_runs_to_one() -> None:
    (
        surface, connection, source_run, target_runs, source_edges, target_edges,
    ) = _portal_span_provenance_fixture()
    connection = replace(connection, local_run_pairs=(
        LocalPortalRunPair((source_edges[0],), (target_edges[0],)),
        LocalPortalRunPair((source_edges[1],), (target_edges[1],)),
    ))

    compatible = wall_sector_module._provenance_compatible_target_runs(
        surface, connection, source_run, 0.0, 0.49, target_runs
    )

    assert compatible == (target_runs[0],)


def test_portal_span_provenance_preserves_physical_ambiguity() -> None:
    (
        surface, connection, source_run, target_runs, source_edges, target_edges,
    ) = _portal_span_provenance_fixture()
    connection = replace(connection, local_run_pairs=(
        LocalPortalRunPair((source_edges[0],), (target_edges[0],)),
        LocalPortalRunPair((source_edges[0],), (target_edges[1],)),
    ))

    compatible = wall_sector_module._provenance_compatible_target_runs(
        surface, connection, source_run, 0.0, 0.49, target_runs
    )

    assert compatible == target_runs


def test_portal_span_provenance_reports_no_compatible_target(
    monkeypatch,
) -> None:
    (
        surface, connection, source_run, target_runs, source_edges, target_edges,
    ) = _portal_span_provenance_fixture()
    connection = replace(connection, local_run_pairs=(
        LocalPortalRunPair((source_edges[1],), (target_edges[0],)),
    ))

    compatible = wall_sector_module._provenance_compatible_target_runs(
        surface, connection, source_run, 0.0, 0.49, target_runs
    )

    assert compatible == ()

    monkeypatch.setattr(
        wall_sector_module,
        "_provenance_compatible_target_runs",
        lambda *_args: (),
    )
    result = _extract(
        _layered(("face", "berm", "face")),
        _rectangle(1.0, 1.0, 11.0, 9.0),
    )

    assert len(result.sectors) == 1
    assert not result.sectors[0].supported
    assert (
        "missing_portal_span_correspondence"
        in result.sectors[0].diagnostics.codes
    )
    assert (
        "ambiguous_portal_span_correspondence"
        not in result.sectors[0].diagnostics.codes
    )
    assert not result.supported_sectors
    assert "missing_portal_span_correspondence" in result.diagnostics.codes
    assert "ambiguous_portal_span_correspondence" not in result.diagnostics.codes


def test_three_bench_wall_traverses_two_platforms() -> None:
    result = _extract(
        _layered(("face", "berm", "face", "road", "face")),
        _rectangle(1.0, 2.0, 19.0, 8.0),
    )

    sector = result.sectors[0]
    assert sector.supported
    assert len(sector.face_component_ids) == 3
    assert len(sector.connection_ids) == 2


@pytest.mark.parametrize("x0,x1", [(9.0, 11.0), (17.0, 19.0)])
def test_assessment_on_middle_or_lower_face_retains_full_corridor(
    x0: float, x1: float,
) -> None:
    result = _extract(
        _layered(("face", "berm", "face", "berm", "face")),
        _rectangle(x0, 2.0, x1, 8.0),
    )

    sector = result.sectors[0]
    assert len(sector.face_component_ids) == 3
    assert all(point.x == pytest.approx(0.0) for point in sector.upper_guide.points)


def test_external_upper_can_be_entirely_outside_assessment() -> None:
    result = _extract(
        _layered(("face", "berm", "face")),
        _rectangle(9.0, 2.0, 11.0, 8.0),
    )

    assert all(point.x == pytest.approx(0.0)
               for point in result.sectors[0].upper_guide.points)


def test_external_lower_can_be_entirely_outside_assessment() -> None:
    result = _extract(
        _layered(("face", "berm")),
        _rectangle(1.0, 2.0, 3.0, 8.0),
    )

    sector = result.sectors[0]
    assert sector.lower_guide is not None
    assert sector.upper_portal_id is not None
    assert sector.lower_portal_id is not None
    assert sector.downstream_portal_id is None
    assert all(point.x == pytest.approx(4.0) for point in sector.lower_guide.points)
    assert sector.downstream_extent is None
    assert sector.lower_station_mapping is not None
    assert sector.lower_station_mapping.chainages_m == pytest.approx(
        sector.lower_guide.cumulative_chainages_m
    )
    assert sector.lower_station_mapping.station_fractions[0] == pytest.approx(0.0)
    assert sector.lower_station_mapping.station_fractions[-1] == pytest.approx(1.0)


def test_missing_lower_uses_connected_design_extent() -> None:
    sector = _extract(
        _layered(("face",)), _rectangle(1.0, 2.0, 3.0, 8.0)
    ).sectors[0]

    assert sector.lower_guide is None
    assert sector.downstream_extent is not None
    assert sector.downstream_extent.kind == "downstream_extent"
    assert sector.downstream_station_mapping is not None
    assert sector.downstream_station_mapping.chainages_m == pytest.approx(
        sector.downstream_extent.cumulative_chainages_m
    )
    assert sector.downstream_station_mapping.station_fractions[0] == pytest.approx(0.0)
    assert sector.downstream_station_mapping.station_fractions[-1] == pytest.approx(1.0)


def test_two_nearby_unrelated_walls_remain_two_sectors() -> None:
    surface = _combine(
        _layered(("face", "berm", "face"), y_offset=0.0),
        _layered(("face", "berm", "face"), y_offset=10.1),
    )
    result = _extract(surface, _rectangle(1.0, 1.0, 11.0, 19.0))

    assert len(result.sectors) == 2
    assert all(len(sector.face_component_ids) == 2 for sector in result.sectors)


def _broad_platform(*, branching: bool) -> TriangleSurface:
    builder = _MeshBuilder()
    ys = (0.0, 10.0, 20.0, 30.0)
    builder.strip(
        0.0, 30.0, 4.0, 20.0, ys, "face", "upper",
        (0, 1, 2) if branching else (0, 2),
    )
    builder.strip(4.0, 20.0, 8.0, 20.0, ys, "berm", "platform")
    builder.strip(8.0, 20.0, 12.0, 10.0, ys, "face", "lower", (0, 2))
    return builder.surface()


def test_broad_platform_preserves_two_local_wall_bands() -> None:
    result = _extract(
        _broad_platform(branching=False), _rectangle(1.0, 1.0, 11.0, 29.0)
    )

    assert len(result.sectors) == 2
    assert all(len(sector.face_component_ids) == 2 for sector in result.sectors)


def test_assessment_evidence_selects_one_ambiguous_platform_branch() -> None:
    result = _extract(
        _broad_platform(branching=True), _rectangle(9.0, 1.0, 11.0, 9.0)
    )

    assert len(result.sectors) == 1
    assert "assessment_local_branch_selected" in result.sectors[0].diagnostics.codes
    assert "ambiguous_corridor_branch" not in result.sectors[0].diagnostics.codes


def _different_distance_branch_surface(*, both_locally_valid: bool) -> TriangleSurface:
    builder = _MeshBuilder()
    if both_locally_valid:
        builder.strip(
            0.0, 30.0, 2.0, 28.0,
            (0.0, 5.0, 10.0), "face", "root:near",
        )
        for column in range(1, 5):
            builder.strip(
                2.0 * column, 30.0 - 2.0 * column,
                2.0 * (column + 1), 28.0 - 2.0 * column,
                (5.0, 10.0), "face", f"root:far:{column}",
            )
        near_y = (0.0, 5.0)
        far_y = (5.0, 10.0)
    else:
        for column in range(5):
            builder.strip(
                2.0 * column, 30.0 - 2.0 * column,
                2.0 * (column + 1), 28.0 - 2.0 * column,
                (0.0, 10.0), "face", f"root:main:{column}",
            )
        builder.strip(
            0.0, 30.0, 2.0, 28.0,
            (10.0, 12.0), "face", "root:near-invalid",
        )
        near_y = (10.0, 12.0)
        far_y = (0.0, 10.0)
    builder.strip(2.0, 28.0, 4.0, 28.0, near_y, "berm", "near:platform")
    builder.strip(4.0, 28.0, 6.0, 18.0, near_y, "face", "near:target")
    builder.strip(10.0, 20.0, 12.0, 20.0, far_y, "berm", "far:platform")
    builder.strip(12.0, 20.0, 14.0, 10.0, far_y, "face", "far:target")
    return builder.surface()


def test_farther_branch_wins_when_nearest_has_no_local_span_overlap() -> None:
    surface = _different_distance_branch_surface(both_locally_valid=False)
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    result = extract_wall_sectors(
        surface, topology, _rectangle(0.5, 1.0, 1.5, 9.0)
    )

    assert len(topology.corridor_connections) == 2
    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.supported
    source_portal = next(
        portal for portal in topology.portals
        if portal.portal_id == sector.portal_correspondences[0].source_portal_id
    )
    assert min(point.x for point in source_portal.points) == pytest.approx(10.0)
    assert "assessment_local_branch_selected" in sector.diagnostics.codes


def test_two_valid_different_distance_branches_remain_ambiguous() -> None:
    surface = _different_distance_branch_surface(both_locally_valid=True)
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    result = extract_wall_sectors(
        surface, topology, _rectangle(0.5, 1.0, 1.5, 9.0)
    )

    assert len(topology.corridor_connections) == 2
    assert len(result.sectors) == 2
    assert all(not sector.supported for sector in result.sectors)
    assert all("ambiguous_corridor_branch" in sector.diagnostics.codes
               for sector in result.sectors)
    source_x = {
        min(point.x for point in next(
            portal for portal in topology.portals
            if portal.portal_id
            == sector.portal_correspondences[0].source_portal_id
        ).points)
        for sector in result.sectors
    }
    assert sorted(source_x) == pytest.approx([2.0, 10.0])


def _angled_connection(angle_degrees: float) -> TriangleSurface:
    builder = _MeshBuilder()
    source_0 = builder.vertex(4.0, 3.0, 0.0)
    source_1 = builder.vertex(4.0, 7.0, 0.0)
    upper_0 = builder.vertex(0.0, 3.0, 10.0)
    upper_1 = builder.vertex(0.0, 7.0, 10.0)
    builder.triangle((upper_0, source_0, source_1), "face", "source:0")
    builder.triangle((upper_0, source_1, upper_1), "face", "source:1")
    angle = angle_degrees * pi / 180.0
    downwall = (cos(angle), sin(angle))
    tangent = (-downwall[1], downwall[0])
    target_0 = builder.vertex(
        8.0 - 2.0 * tangent[0], 5.0 - 2.0 * tangent[1], 0.0
    )
    target_1 = builder.vertex(
        8.0 + 2.0 * tangent[0], 5.0 + 2.0 * tangent[1], 0.0
    )
    builder.triangle((source_0, target_0, target_1), "berm", "platform:0")
    builder.triangle((source_0, target_1, source_1), "berm", "platform:1")
    downstream_0 = builder.vertex(
        builder.vertices[target_0].x + 4.0 * downwall[0],
        builder.vertices[target_0].y + 4.0 * downwall[1], -10.0,
    )
    downstream_1 = builder.vertex(
        builder.vertices[target_1].x + 4.0 * downwall[0],
        builder.vertices[target_1].y + 4.0 * downwall[1], -10.0,
    )
    builder.triangle((target_0, downstream_0, downstream_1), "face", "target:0")
    builder.triangle((target_0, downstream_1, target_1), "face", "target:1")
    return builder.surface()


def test_large_phase_2a_direction_candidate_is_not_validated_as_one_corridor() -> None:
    surface = _angled_connection(85.0)
    result = _extract(surface, _rectangle(-1.0, 2.0, 13.0, 12.0))

    assert len(result.sectors) == 1
    assert result.sectors[0].connection_ids
    assert not result.sectors[0].supported
    assert "abrupt_local_direction_break" in result.sectors[0].diagnostics.codes


def test_along_strike_physical_gap_produces_separate_sectors() -> None:
    surface = _combine(
        _layered(("face",), y_offset=0.0),
        _layered(("face",), y_offset=12.0),
    )

    assert len(_extract(surface, _rectangle(1.0, 1.0, 3.0, 21.0)).sectors) == 2


def _smooth_curved_face() -> TriangleSurface:
    builder = _MeshBuilder()
    upper = ((0.0, 0.0), (0.0, 5.0), (1.0, 10.0), (4.0, 14.0), (9.0, 16.0))
    lower = ((4.0, 0.0), (4.0, 5.0), (5.0, 10.0), (8.0, 14.0), (13.0, 16.0))
    builder.quad_strip(upper, lower, prefix="curve")
    return builder.surface()


def test_smoothly_curved_wall_retains_supported_rotating_face_field() -> None:
    result = _extract(_smooth_curved_face(), _rectangle(-1.0, -1.0, 14.0, 17.0))

    sector = result.sectors[0]
    assert sector.supported
    assert "abrupt_local_direction_break" not in sector.diagnostics.codes
    assert len({tuple(round(value, 3) for value in sample.downwall_xy)
                for sample in sector.face_direction_samples}) > 1


def test_long_curved_portal_is_reclassified_for_local_assessment_span() -> None:
    result = _extract(_smooth_curved_face(), _rectangle(0.2, 0.5, 3.8, 4.5))

    sector = result.sectors[0]
    assert sector.supported
    assert max(point.y for point in sector.upper_guide.points) < 5.0
    assert all(sample.downwall_xy[0] > 0.9 for sample in sector.face_direction_samples)


def _self_near_u_wall() -> TriangleSurface:
    builder = _MeshBuilder()
    upper = (
        (0.0, 0.0), (0.0, 10.0), (5.0, 10.0),
        (5.0, 2.0), (1.0, 2.0),
    )
    lower = (
        (2.0, 0.0), (2.0, 8.0), (3.0, 8.0),
        (3.0, 4.0), (1.0, 4.0),
    )
    builder.quad_strip(upper, lower, prefix="u")
    return builder.surface()


def test_self_near_wall_station_transport_does_not_jump_upper_segments() -> None:
    result = _extract(
        _self_near_u_wall(), _rectangle(-0.2, 1.5, 2.2, 4.5)
    )

    sector = result.sectors[0]
    last_leg = tuple(
        sample for sample in sector.face_direction_samples
        if sample.source_id.startswith("u:3")
    )
    first_leg = tuple(
        sample for sample in sector.face_direction_samples
        if sample.source_id.startswith("u:0")
    )
    assert last_leg and first_leg
    assert min(sample.station_fraction for sample in last_leg) > (
        max(sample.station_fraction for sample in first_leg)
    )
    assert all(state.active_triangle_keys for state in sector.span_states)


def test_local_face_remeshing_preserves_material_stationing_and_guide() -> None:
    coarse = _extract(
        _layered(("face",), y_values=(0.0, 5.0, 10.0)),
        _rectangle(1.0, 4.0, 3.0, 6.0),
    ).sectors[0]
    refined = _extract(
        _layered(("face",), y_values=(0.0, 2.5, 5.0, 7.5, 10.0)),
        _rectangle(1.0, 4.0, 3.0, 6.0),
    ).sectors[0]

    assert len(coarse.upper_guide.points) == len(refined.upper_guide.points)
    for coarse_point, refined_point in zip(
        coarse.upper_guide.points, refined.upper_guide.points, strict=True
    ):
        assert coarse_point.x == pytest.approx(refined_point.x)
        assert coarse_point.y == pytest.approx(refined_point.y)
    coarse_mean = sum(
        sample.station_fraction * sample.geometric_weight
        for sample in coarse.face_direction_samples
    ) / sum(sample.geometric_weight for sample in coarse.face_direction_samples)
    refined_mean = sum(
        sample.station_fraction * sample.geometric_weight
        for sample in refined.face_direction_samples
    ) / sum(sample.geometric_weight for sample in refined.face_direction_samples)
    assert coarse_mean == pytest.approx(refined_mean, abs=0.03)


def _huge_face_two_bands(*, taint_band: int | None = None) -> TriangleSurface:
    builder = _MeshBuilder()
    ys = (0.0, 10.0, 100.0, 110.0)
    # Two downwall columns keep the Face component connected even when one
    # local diagonal is intentionally non-manifold.
    for column, (x0, z0, x1, z1) in enumerate((
        (0.0, 30.0, 2.0, 25.0),
        (2.0, 25.0, 4.0, 20.0),
    )):
        builder.strip(x0, z0, x1, z1, ys, "face", f"root:{column}")
    builder.strip(4.0, 20.0, 8.0, 20.0, ys, "berm", "platform", (0, 2))
    builder.strip(8.0, 20.0, 12.0, 10.0, ys, "face", "target", (0, 2))
    if taint_band is not None:
        y0, y1 = ys[taint_band:taint_band + 2]
        a = builder.vertex(0.0, y0, 30.0)
        d = builder.vertex(2.0, y1, 25.0)
        extra = builder.vertex(-1.0, (y0 + y1) / 2.0, 27.0)
        builder.triangle((a, d, extra), "berm", f"taint:{taint_band}")
    return builder.surface()


@pytest.mark.parametrize(
    "area,expected_target_prefix",
    [
        (_rectangle(1.0, 1.0, 3.0, 9.0), "target:0"),
        (_rectangle(1.0, 101.0, 3.0, 109.0), "target:2"),
    ],
)
def test_huge_face_component_activates_only_local_portal_band(
    area: PlanPolygon, expected_target_prefix: str,
) -> None:
    surface = _huge_face_two_bands()
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    result = extract_wall_sectors(surface, topology, area)

    assert len(topology.face_components) == 3
    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert len(sector.connection_ids) == 1
    target_sources = {
        sample.source_id for sample in topology.face_direction_samples
        if sample.triangle_index in topology.face_components[
            int(sector.face_component_ids[-1].split(":")[1])
        ].triangle_indices
    }
    assert any(source.startswith(expected_target_prefix) for source in target_sources)
    assert len(sector.face_component_ids) == 2


@pytest.mark.parametrize(
    "assessment_y,expected_portal_fraction,expected_triangle_y",
    [
        ((1.0, 9.0), (0.025, 0.225), (0.0, 10.0)),
        ((31.0, 39.0), (0.775, 0.975), (30.0, 40.0)),
    ],
)
def test_partial_long_portal_overlap_transports_only_matching_target_subspan(
    assessment_y: tuple[float, float],
    expected_portal_fraction: tuple[float, float],
    expected_triangle_y: tuple[float, float],
) -> None:
    surface = _layered(
        ("face", "berm", "face"),
        y_values=(0.0, 10.0, 20.0, 30.0, 40.0),
    )
    result = _extract(
        surface,
        _rectangle(1.0, assessment_y[0], 3.0, assessment_y[1]),
    )

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.supported
    assert len(sector.portal_correspondences) == 1
    correspondence = sector.portal_correspondences[0]
    assert correspondence.source_station_start == pytest.approx(0.0)
    assert correspondence.source_station_end == pytest.approx(1.0)
    assert correspondence.source_chainage_start_fraction == pytest.approx(
        expected_portal_fraction[0]
    )
    assert correspondence.source_chainage_end_fraction == pytest.approx(
        expected_portal_fraction[1]
    )
    assert correspondence.target_chainage_start_fraction == pytest.approx(
        expected_portal_fraction[0]
    )
    assert correspondence.target_chainage_end_fraction == pytest.approx(
        expected_portal_fraction[1]
    )
    target_state = sector.span_states[-1]
    target_y = tuple(
        vertex[1]
        for triangle_key in target_state.active_triangle_keys
        for vertex in triangle_key
    )
    assert min(target_y) >= expected_triangle_y[0] - 1e-9
    assert max(target_y) <= expected_triangle_y[1] + 1e-9
    terminal_guide = sector.lower_guide or sector.downstream_extent
    assert terminal_guide is not None
    assert min(point.y for point in terminal_guide.points) == pytest.approx(
        assessment_y[0]
    )
    assert max(point.y for point in terminal_guide.points) == pytest.approx(
        assessment_y[1]
    )


def _replace_portal_run_stations(
    monkeypatch,
    folded_values: tuple[float, ...],
) -> list[
    tuple[
        tuple[float, float] | None,
        tuple[tuple[PlanPoint, ...], tuple[float, ...]] | None,
    ]
]:
    original = wall_sector_module._run_station_values
    observations = []

    def run_station_values_in_active_band(
        surface, run, station_by_vertex, station_interval=None,
    ):
        vertices = wall_sector_module._resolve_run_vertices(surface, run)
        assert vertices is not None
        assert len(vertices) == len(folded_values)
        folded_station_by_vertex = dict(station_by_vertex)
        folded_station_by_vertex.update(zip(vertices, folded_values, strict=True))
        assert original(surface, run, folded_station_by_vertex) is None
        result = original(
            surface, run, folded_station_by_vertex, station_interval
        )
        observations.append((station_interval, result))
        return result

    monkeypatch.setattr(
        wall_sector_module, "_run_station_values",
        run_station_values_in_active_band,
    )
    return observations


def test_portal_transport_uses_unique_folded_run_inside_active_band(
    monkeypatch,
) -> None:
    observations = _replace_portal_run_stations(
        monkeypatch, (0.0, 0.4, 0.8, 0.6, 0.5)
    )
    result = _extract(
        _layered(
            ("face", "berm", "face"),
            y_values=(0.0, 10.0, 20.0, 30.0, 40.0),
        ),
        _rectangle(1.0, 1.0, 3.0, 9.0),
    )

    assert observations
    assert all(interval is not None for interval, _result in observations)
    assert all(value is not None for _interval, value in observations)
    assert len(result.sectors) == 1
    assert result.sectors[0].supported
    assert "non_injective_portal_station_mapping" not in (
        result.sectors[0].diagnostics.codes
    )


def test_portal_transport_rejects_two_folded_runs_inside_active_band(
    monkeypatch,
) -> None:
    observations = _replace_portal_run_stations(
        monkeypatch, (0.0, 0.4, 0.8, 0.2, 0.0)
    )
    result = _extract(
        _layered(
            ("face", "berm", "face"),
            y_values=(0.0, 10.0, 20.0, 30.0, 40.0),
        ),
        _rectangle(1.0, 1.0, 3.0, 9.0),
    )

    assert observations
    assert all(interval is not None for interval, _result in observations)
    assert all(value is None for _interval, value in observations)
    assert not any(sector.supported for sector in result.sectors)
    assert "non_injective_portal_station_mapping" in (
        result.diagnostics.codes
    )


def test_one_concave_assessment_over_two_distant_bands_extracts_two_sectors() -> None:
    surface = _huge_face_two_bands()
    area = PlanPolygon((
        PlanPoint(-2.0, 1.0), PlanPoint(3.0, 1.0),
        PlanPoint(3.0, 9.0), PlanPoint(-1.0, 9.0),
        PlanPoint(-1.0, 101.0), PlanPoint(3.0, 101.0),
        PlanPoint(3.0, 109.0), PlanPoint(-2.0, 109.0),
        PlanPoint(-2.0, 1.0),
    ))
    result = _extract(surface, area)

    assert len(result.sectors) == 2
    assert all(len(sector.connection_ids) == 1 for sector in result.sectors)
    assert result.sectors[0].connection_ids != result.sectors[1].connection_ids
    assert all("ambiguous_corridor_branch" not in sector.diagnostics.codes
               for sector in result.sectors)


def test_two_disconnected_assessment_lobes_on_same_corridor_merge_as_intervals() -> None:
    surface = _layered(
        ("face", "berm", "face"),
        y_values=(0.0, 10.0, 20.0, 30.0, 40.0),
    )
    area = PlanPolygon((
        PlanPoint(-2.0, 1.0), PlanPoint(3.0, 1.0),
        PlanPoint(3.0, 9.0), PlanPoint(-1.0, 9.0),
        PlanPoint(-1.0, 31.0), PlanPoint(3.0, 31.0),
        PlanPoint(3.0, 39.0), PlanPoint(-2.0, 39.0),
        PlanPoint(-2.0, 1.0),
    ))
    result = _extract(surface, area)

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.supported
    assert len(sector.connection_ids) == 1
    assert len(sector.assessed_station_intervals) == 2


def test_taint_on_required_corridor_outside_assessment_is_detected() -> None:
    surface = _huge_face_two_bands(taint_band=0)
    result = _extract(surface, _rectangle(9.0, 1.0, 11.0, 9.0))

    assert len(result.sectors) == 1
    assert "local_non_manifold_topology" in result.sectors[0].diagnostics.codes
    assert not result.sectors[0].supported


def test_remote_taint_in_same_huge_component_does_not_poison_local_span() -> None:
    surface = _huge_face_two_bands(taint_band=2)
    result = _extract(surface, _rectangle(1.0, 1.0, 3.0, 9.0))

    assert len(result.sectors) == 1
    assert "local_non_manifold_topology" not in result.sectors[0].diagnostics.codes


def _gradual_multibench(angles: tuple[float, ...]) -> TriangleSurface:
    builder = _MeshBuilder()
    center = (0.0, 5.0)
    upper: tuple[int, int] | None = None
    for index, angle_degrees in enumerate(angles):
        angle = angle_degrees * pi / 180.0
        direction = (cos(angle), sin(angle))
        tangent = (-direction[1], direction[0])
        if upper is None:
            upper = (
                builder.vertex(center[0] - 2 * tangent[0], center[1] - 2 * tangent[1], 30.0),
                builder.vertex(center[0] + 2 * tangent[0], center[1] + 2 * tangent[1], 30.0),
            )
        lower_center = (center[0] + 4 * direction[0], center[1] + 4 * direction[1])
        lower = (
            builder.vertex(lower_center[0] - 2 * tangent[0], lower_center[1] - 2 * tangent[1], 20.0 - 10 * index),
            builder.vertex(lower_center[0] + 2 * tangent[0], lower_center[1] + 2 * tangent[1], 20.0 - 10 * index),
        )
        builder.triangle((upper[0], lower[0], lower[1]), "face", f"face:{index}:0")
        builder.triangle((upper[0], lower[1], upper[1]), "face", f"face:{index}:1")
        if index == len(angles) - 1:
            break
        next_angle = angles[index + 1] * pi / 180.0
        next_direction = (cos(next_angle), sin(next_angle))
        next_tangent = (-next_direction[1], next_direction[0])
        next_center = (
            lower_center[0] + 3 * direction[0],
            lower_center[1] + 3 * direction[1],
        )
        next_upper = (
            builder.vertex(next_center[0] - 2 * next_tangent[0], next_center[1] - 2 * next_tangent[1], 20.0 - 10 * index),
            builder.vertex(next_center[0] + 2 * next_tangent[0], next_center[1] + 2 * next_tangent[1], 20.0 - 10 * index),
        )
        builder.triangle((lower[0], next_upper[0], next_upper[1]), "berm", f"berm:{index}:0")
        builder.triangle((lower[0], next_upper[1], lower[1]), "berm", f"berm:{index}:1")
        center = next_center
        upper = next_upper
    return builder.surface()


def test_clean_five_degree_interbench_change_remains_continuous() -> None:
    surface = _gradual_multibench((0.0, 5.0))
    result = _extract(surface, _rectangle(-5.0, -5.0, 20.0, 20.0))

    assert len(result.sectors) == 1
    assert result.sectors[0].supported
    assert len(result.sectors[0].connection_ids) == 1


def test_distributed_multibench_rotation_remains_continuous() -> None:
    surface = _gradual_multibench((0.0, 7.5, 15.0))
    result = _extract(surface, _rectangle(-5.0, -5.0, 30.0, 25.0))

    assert len(result.sectors) == 1
    assert result.sectors[0].supported
    assert len(result.sectors[0].connection_ids) == 2


def test_duplicate_triangle_geometry_is_not_resolved_by_storage_index() -> None:
    builder = _MeshBuilder()
    a = builder.vertex(0.0, 0.0, 10.0)
    b = builder.vertex(4.0, 0.0, 0.0)
    c = builder.vertex(0.0, 4.0, 10.0)
    builder.triangle((a, b, c), "face", "duplicate:a")
    builder.triangle((a, c, b), "face", "duplicate:b")
    surface = builder.surface()
    area = _rectangle(0.2, 0.2, 2.0, 2.0)
    baseline = _extract(surface, area)
    reordered = _extract(_reordered(surface, triangles=True), area)

    assert not baseline.sectors
    assert "missing_external_upper_guide" in baseline.diagnostics.codes
    assert reordered.diagnostics.codes == baseline.diagnostics.codes


def _abrupt_face() -> TriangleSurface:
    builder = _MeshBuilder()
    builder.strip(
        0.0, 10.0, 4.0, 0.0, (0.0, 5.0, 10.0), "face", "straight"
    )
    # The second planar patch shares the first patch's transverse corner edge,
    # but its downwall direction turns abruptly from +X to -Y.
    a = builder.vertex(0.0, 10.0, 10.0)
    b = builder.vertex(4.0, 10.0, 0.0)
    c = builder.vertex(10.0, 10.0, 10.0)
    d = builder.vertex(10.0, 6.0, 0.0)
    e = builder.vertex(4.0, 6.0, 0.0)
    builder.triangle((a, b, e), "face", "corner:0")
    builder.triangle((a, e, c), "face", "corner:1")
    builder.triangle((c, e, d), "face", "corner:2")
    return builder.surface()


def test_abrupt_local_direction_break_is_not_smoothed_into_supported_sector() -> None:
    result = _extract(_abrupt_face(), _rectangle(-1.0, -1.0, 11.0, 11.0))

    assert any(
        "abrupt_local_direction_break" in sector.diagnostics.codes
        and not sector.supported
        for sector in result.sectors
    )


def _wide_face_band(
    *, issue_segment: int | None = None, terminal: bool = True
) -> TriangleSurface:
    builder = _MeshBuilder()
    y_values = (0.0, 10.0, 20.0, 30.0)
    for column in range(3):
        builder.strip(
            2.0 * column, 30.0 - 5.0 * column,
            2.0 * (column + 1), 25.0 - 5.0 * column,
            y_values, "face", f"wide:{column}",
        )
    if terminal:
        builder.strip(6.0, 15.0, 8.0, 15.0, y_values, "berm", "terminal")
    if issue_segment is not None:
        y0, y1 = y_values[issue_segment:issue_segment + 2]
        a = builder.vertex(0.0, y0, 30.0)
        d = builder.vertex(2.0, y1, 25.0)
        extra = builder.vertex(-1.0, (y0 + y1) / 2.0, 27.0)
        builder.triangle((a, d, extra), "berm", f"issue:{issue_segment}")
    return builder.surface()


def _topology_with_test_directions(
    surface: TriangleSurface,
    angle_for_source,
):
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    evidence = tuple(
        replace(
            item,
            downwall_xy=(
                cos(angle_for_source(item.source_id) * pi / 180.0),
                sin(angle_for_source(item.source_id) * pi / 180.0),
            ),
        )
        for item in topology.face_direction_samples
    )
    evidence_by_triangle = {item.triangle_index: item for item in evidence}
    components = tuple(replace(
        component,
        direction_samples=tuple(
            evidence_by_triangle[index]
            for index in component.triangle_indices
        ),
    ) for component in topology.face_components)
    return replace(
        topology,
        face_direction_samples=evidence,
        face_components=components,
    )


def test_required_path_break_inside_one_face_component_outside_assessment() -> None:
    surface = _wide_face_band(terminal=False)
    angles = (0.0, 0.0, 60.0)
    topology = _topology_with_test_directions(
        surface, lambda source_id: angles[int(source_id.split(":")[1])]
    )
    result = extract_wall_sectors(
        surface, topology, _rectangle(5.2, 1.0, 5.8, 9.0)
    )

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert not sector.supported
    assert "abrupt_local_direction_break" in sector.diagnostics.codes
    assert len(sector.span_states[0].active_triangle_keys) >= 6


def test_required_path_smooth_bend_outside_assessment_remains_supported() -> None:
    surface = _wide_face_band(terminal=False)
    angles = (0.0, 20.0, 40.0)
    topology = _topology_with_test_directions(
        surface, lambda source_id: angles[int(source_id.split(":")[1])]
    )
    result = extract_wall_sectors(
        surface, topology, _rectangle(5.2, 1.0, 5.8, 9.0)
    )

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.supported
    assert "abrupt_local_direction_break" not in sector.diagnostics.codes


def test_wide_active_face_band_detects_off_skeleton_local_taint() -> None:
    result = _extract(
        _wide_face_band(issue_segment=0),
        _rectangle(5.2, 1.0, 5.8, 9.0),
    )

    sector = result.sectors[0]
    assert not sector.supported
    assert "local_non_manifold_topology" in sector.diagnostics.codes
    assert len(sector.span_states[0].active_triangle_keys) >= 6


def test_wide_face_taint_outside_transported_station_span_is_remote() -> None:
    result = _extract(
        _wide_face_band(issue_segment=2),
        _rectangle(5.2, 1.0, 5.8, 9.0),
    )

    sector = result.sectors[0]
    assert sector.supported
    assert "local_non_manifold_topology" not in sector.diagnostics.codes


def test_remote_direction_change_outside_active_station_span_is_clean() -> None:
    surface = _wide_face_band(terminal=False)
    topology = _topology_with_test_directions(
        surface,
        lambda source_id: (
            60.0 if int(source_id.split(":")[-2]) == 2 else 0.0
        ),
    )
    result = extract_wall_sectors(
        surface, topology, _rectangle(5.2, 1.0, 5.8, 9.0)
    )

    sector = result.sectors[0]
    assert sector.supported
    assert "abrupt_local_direction_break" not in sector.diagnostics.codes


def test_concave_assessment_preserves_two_station_intervals() -> None:
    surface = _layered(("face",), y_values=(0.0, 5.0, 10.0, 15.0, 20.0))
    area = PlanPolygon((
        PlanPoint(1.0, 0.0), PlanPoint(6.0, 0.0), PlanPoint(6.0, 20.0),
        PlanPoint(1.0, 20.0), PlanPoint(1.0, 15.0), PlanPoint(5.0, 15.0),
        PlanPoint(5.0, 5.0), PlanPoint(1.0, 5.0), PlanPoint(1.0, 0.0),
    ))
    result = _extract(surface, area)

    assert len(result.sectors) == 1
    assert len(result.sectors[0].assessed_station_intervals) == 2


def test_assessment_boundary_only_contact_creates_no_seed_or_sector() -> None:
    result = _extract(_layered(("face",)), _rectangle(4.0, 2.0, 6.0, 8.0))

    assert not result.overlap_fragments
    assert not result.sectors
    assert result.diagnostics.codes == ("no_positive_face_overlap",)


def _partial_lower_surface() -> TriangleSurface:
    builder = _MeshBuilder()
    ys = (0.0, 5.0, 10.0)
    builder.strip(0.0, 10.0, 4.0, 0.0, ys, "face", "face")
    builder.strip(4.0, 0.0, 8.0, 0.0, ys, "berm", "berm", (0,))
    return builder.surface()


def test_partial_lower_coverage_splits_constraint_models() -> None:
    result = _extract(
        _partial_lower_surface(), _rectangle(1.0, 0.5, 3.0, 9.5)
    )

    assert len(result.sectors) == 2
    assert all(sector.supported for sector in result.sectors)
    assert sum(sector.lower_guide is not None for sector in result.sectors) == 1
    assert sum(sector.downstream_extent is not None for sector in result.sectors) == 1


def _terminal_test_run(
    surface: TriangleSurface,
    x: float,
    portal_id: str,
    source_kind: str = "face_platform",
):
    vertex_indices = tuple(sorted(
        (
            index for index, vertex in enumerate(surface.vertices)
            if abs(vertex.x - x) <= 1e-9
        ),
        key=lambda index: surface.vertices[index].y,
    ))
    points = tuple(
        PlanPoint(surface.vertices[index].x, surface.vertices[index].y)
        for index in vertex_indices
    )
    triangle_indices = tuple(
        index for index, triangle in enumerate(surface.triangles)
        if set(triangle.vertex_indices) & set(vertex_indices)
    )
    return wall_sector_module._GuideRun(
        portal_id, 0, "downstream", source_kind, points,
        triangle_indices, False,
    ), vertex_indices


def test_face_rays_replace_wrong_station_crop_with_unique_lower_subspan() -> None:
    upper = wall_sector_module.WallGuide(
        (PlanPoint(0.0, 0.0), PlanPoint(0.2, 0.0)), "upper"
    )
    lower_points = (
        PlanPoint(5.0, 1.0),
        PlanPoint(5.0, 5.0),
        PlanPoint(3.0, 8.0),
        PlanPoint(1.0, 10.0),
        PlanPoint(-1.0, 10.0),
    )
    station_crop = wall_sector_module._crop_terminal_guide_by_station(
        lower_points,
        (0.0, 0.2, 0.5, 0.8, 1.0),
        0.0,
        0.05,
        "lower",
    )
    assert station_crop is not None
    assert all(point.x == pytest.approx(5.0) for point in station_crop[0].points)

    samples = (
        wall_sector_module.FaceDirectionSample(
            PlanPoint(0.1, 0.1), 0.5, (0.0, 1.0), 1.0, "face", "face:0"
        ),
    )
    correspondence = wall_sector_module._face_ray_terminal_guide(
        upper,
        lower_points,
        samples,
        _rectangle(-2.0, -1.0, 6.0, 11.0),
    )

    assert not correspondence.ambiguous
    assert correspondence.result is not None
    guide, mapping = correspondence.result
    expected_points = (
        PlanPoint(0.0, 10.0),
        PlanPoint(0.1, 10.0),
        PlanPoint(0.2, 10.0),
    )
    for point, expected in zip(guide.points, expected_points, strict=True):
        assert point.x == pytest.approx(expected.x)
        assert point.y == pytest.approx(expected.y)
    assert mapping.station_fractions == pytest.approx((0.0, 0.5, 1.0))


def test_face_rays_reject_two_distinct_lower_subspans() -> None:
    upper = wall_sector_module.WallGuide(
        (PlanPoint(0.0, 0.0), PlanPoint(0.2, 0.0)), "upper"
    )
    samples = (
        wall_sector_module.FaceDirectionSample(
            PlanPoint(0.1, 0.1), 0.5, (0.0, 1.0), 1.0, "face", "face:0"
        ),
    )
    correspondence = wall_sector_module._face_ray_terminal_guide(
        upper,
        (
            PlanPoint(-1.0, 2.0),
            PlanPoint(1.0, 2.0),
            PlanPoint(1.0, 4.0),
            PlanPoint(-1.0, 4.0),
        ),
        samples,
        _rectangle(-2.0, -1.0, 2.0, 5.0),
    )

    assert correspondence.result is None
    assert correspondence.ambiguous


def test_two_local_lower_runs_are_ambiguous_and_critical(monkeypatch) -> None:
    surface = _layered(
        ("face", "berm"), y_values=(0.0, 2.5, 5.0, 7.5, 10.0)
    )
    first, first_vertices = _terminal_test_run(surface, 0.0, "lower:a")
    second, second_vertices = _terminal_test_run(surface, 4.0, "lower:b")
    stations = {
        index: surface.vertices[index].y / 10.0
        for index in (*first_vertices, *second_vertices)
    }
    selection = wall_sector_module._select_terminal_run(
        surface, (first, second), stations, (0.2, 0.8),
        set(range(len(surface.triangles))),
    )

    assert selection.ambiguous
    assert selection.run is None
    assert selection.values is None
    assert wall_sector_module._select_terminal_run(
        surface, (second, first), stations, (0.2, 0.8),
        set(range(len(surface.triangles))),
    ).ambiguous

    original = wall_sector_module._select_terminal_run

    def select_with_ambiguous_lower(
        candidate_surface, runs, station_by_vertex, station_interval,
        active_triangles,
    ):
        if runs and all(run.source_kind == "face_platform" for run in runs):
            return selection
        return original(
            candidate_surface, runs, station_by_vertex, station_interval,
            active_triangles,
        )

    monkeypatch.setattr(
        wall_sector_module, "_select_terminal_run", select_with_ambiguous_lower
    )
    result = _extract(surface, _rectangle(1.0, 2.0, 3.0, 8.0))

    assert result.sectors
    assert all(not sector.supported for sector in result.sectors)
    assert all(sector.lower_guide is None for sector in result.sectors)
    assert all(
        "ambiguous_external_lower_guide" in sector.diagnostics.codes
        for sector in result.sectors
    )


def test_two_local_design_extents_are_ambiguous_and_critical(monkeypatch) -> None:
    surface = _layered(
        ("face",), y_values=(0.0, 2.5, 5.0, 7.5, 10.0)
    )
    first, first_vertices = _terminal_test_run(
        surface, 0.0, "extent:a", "external_rim"
    )
    second, second_vertices = _terminal_test_run(
        surface, 4.0, "extent:b", "external_rim"
    )
    stations = {
        index: surface.vertices[index].y / 10.0
        for index in (*first_vertices, *second_vertices)
    }
    selection = wall_sector_module._select_terminal_run(
        surface, (first, second), stations, (0.2, 0.8),
        set(range(len(surface.triangles))),
    )

    assert selection.ambiguous
    assert selection.run is None
    assert wall_sector_module._select_terminal_run(
        surface, (second, first), stations, (0.2, 0.8),
        set(range(len(surface.triangles))),
    ).ambiguous

    original = wall_sector_module._select_terminal_run

    def select_with_ambiguous_extent(
        candidate_surface, runs, station_by_vertex, station_interval,
        active_triangles,
    ):
        if runs and all(run.source_kind != "face_platform" for run in runs):
            return selection
        return original(
            candidate_surface, runs, station_by_vertex, station_interval,
            active_triangles,
        )

    monkeypatch.setattr(
        wall_sector_module, "_select_terminal_run", select_with_ambiguous_extent
    )
    result = _extract(surface, _rectangle(1.0, 2.0, 3.0, 8.0))

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert not sector.supported
    assert sector.downstream_extent is None
    assert "ambiguous_downstream_extent" in sector.diagnostics.codes


def test_folded_lower_with_two_local_subspans_is_not_selected(
    monkeypatch,
) -> None:
    surface = _layered(
        ("face", "berm"), y_values=(0.0, 2.5, 5.0, 7.5, 10.0)
    )
    folded, vertices = _terminal_test_run(surface, 4.0, "lower:folded")
    stations = dict(zip(vertices, (0.0, 1.0, 0.0, 1.0, 0.0)))
    selection = wall_sector_module._select_terminal_run(
        surface, (folded,), stations, (0.2, 0.8),
        set(range(len(surface.triangles))),
    )

    assert selection.ambiguous
    assert selection.non_injective
    assert selection.run is None
    assert selection.values is None

    original = wall_sector_module._select_terminal_run

    def select_with_folded_lower(
        candidate_surface, runs, station_by_vertex, station_interval,
        active_triangles,
    ):
        if runs and all(run.source_kind == "face_platform" for run in runs):
            return selection
        return original(
            candidate_surface, runs, station_by_vertex, station_interval,
            active_triangles,
        )

    monkeypatch.setattr(
        wall_sector_module, "_select_terminal_run", select_with_folded_lower
    )
    result = _extract(surface, _rectangle(1.0, 2.0, 3.0, 8.0))

    assert result.sectors
    assert all(not sector.supported for sector in result.sectors)
    assert all(
        "ambiguous_external_lower_guide" in sector.diagnostics.codes
        and "non_injective_lower_correspondence" in sector.diagnostics.codes
        for sector in result.sectors
    )


def test_remote_fold_with_one_local_terminal_subspan_is_accepted() -> None:
    surface = _layered(
        ("face",), y_values=(0.0, 2.5, 5.0, 7.5, 10.0)
    )
    folded, vertices = _terminal_test_run(
        surface, 4.0, "extent:remote-fold", "external_rim"
    )
    stations = dict(zip(vertices, (0.0, 0.6, 1.0, 0.9, 0.8)))
    selection = wall_sector_module._select_terminal_run(
        surface, (folded,), stations, (0.1, 0.5),
        set(range(len(surface.triangles))),
    )

    assert not selection.ambiguous
    assert not selection.non_injective
    assert selection.run == folded
    assert selection.values is not None
    assert selection.values[0] == folded.points[:3]
    assert selection.values[1] == (0.0, 0.6, 1.0)

    baseline = _extract(surface, _rectangle(1.0, 2.0, 3.0, 8.0))
    assert len(baseline.sectors) == 1
    assert baseline.sectors[0].supported


def test_zero_width_convergence_is_explicitly_unsupported() -> None:
    builder = _MeshBuilder()
    a = builder.vertex(0.0, 0.0, 10.0)
    b = builder.vertex(0.0, 10.0, 10.0)
    apex = builder.vertex(4.0, 5.0, 0.0)
    builder.triangle((a, apex, b), "face", "converging")
    result = _extract(builder.surface(), _rectangle(0.5, 2.0, 3.9, 8.0))

    assert len(result.sectors) == 1
    assert not result.sectors[0].supported
    assert "missing_downstream_extent" in result.sectors[0].diagnostics.codes
    assert "zero_width_convergence" in result.sectors[0].diagnostics.codes


def _non_manifold_and_clean_surface() -> TriangleSurface:
    builder = _MeshBuilder()
    builder.strip(0.0, 10.0, 4.0, 0.0, (0.0, 10.0), "face", "affected")
    a = builder.vertex(4.0, 0.0, 0.0)
    b = builder.vertex(4.0, 10.0, 0.0)
    c = builder.vertex(8.0, 0.0, 0.0)
    d = builder.vertex(8.0, 10.0, 0.0)
    builder.triangle((a, c, b), "berm", "affected:platform:a")
    builder.triangle((a, b, d), "berm", "affected:platform:b")
    builder.strip(20.0, 10.0, 24.0, 0.0, (0.0, 10.0), "face", "clean")
    return builder.surface()


def test_non_manifold_taint_is_local_to_overlapping_sector() -> None:
    result = _extract(
        _non_manifold_and_clean_surface(), _rectangle(-3.0, 0.5, 23.0, 9.5)
    )

    assert any("local_non_manifold_topology" in sector.diagnostics.codes
               for sector in result.sectors)
    assert any("local_non_manifold_topology" not in sector.diagnostics.codes
               for sector in result.sectors)


def test_coincident_distinct_vertices_remain_disconnected() -> None:
    first = _layered(("face",))
    second = TriangleSurface(
        tuple(SurfaceVertex(vertex.x, vertex.y, vertex.z) for vertex in first.vertices),
        tuple(replace(triangle, source_id=f"copy:{triangle.source_id}")
              for triangle in first.triangles),
    )
    surface = _combine(first, second)
    topology = build_design_topology_index(surface, ROLE_MAPPING)
    result = extract_wall_sectors(
        surface, topology, _rectangle(1.0, 2.0, 3.0, 8.0)
    )

    assert "coincident_distinct_vertices" in topology.diagnostics.codes
    assert len(result.sectors) == 2


def test_surface_storage_order_and_winding_do_not_change_result() -> None:
    surface = _layered(("face", "berm", "face"), y_values=(0.0, 5.0, 10.0))
    area = _rectangle(1.0, 2.0, 11.0, 8.0)
    baseline = _extract(surface, area).canonical_signature

    for variant in (
        _reordered(surface, triangles=True),
        _reordered(surface, winding=True),
        _reordered(surface, vertices=True, triangles=True, winding=True),
    ):
        assert _extract(variant, area).canonical_signature == baseline


def test_assessment_winding_and_start_vertex_do_not_change_result() -> None:
    surface = _layered(("face", "berm", "face"))
    area = _rectangle(1.0, 2.0, 11.0, 8.0)
    vertices = area.ring[:-1]
    shifted = vertices[2:] + vertices[:2]
    reversed_area = PlanPolygon(tuple(reversed(shifted)) + (shifted[-1],))

    assert _extract(surface, reversed_area).canonical_signature == (
        _extract(surface, area).canonical_signature
    )


def _closed_wall(segments: int = 8) -> TriangleSurface:
    builder = _MeshBuilder()
    inner = tuple((5.0 * cos(2 * pi * i / segments),
                   5.0 * sin(2 * pi * i / segments)) for i in range(segments))
    outer = tuple((9.0 * cos(2 * pi * i / segments),
                   9.0 * sin(2 * pi * i / segments)) for i in range(segments))
    for index in range(segments):
        following = (index + 1) % segments
        a = builder.vertex(*inner[index], 10.0)
        b = builder.vertex(*inner[following], 10.0)
        c = builder.vertex(*outer[index], 0.0)
        d = builder.vertex(*outer[following], 0.0)
        builder.triangle((a, c, d), "face", f"ring:{index}:0")
        builder.triangle((a, d, b), "face", f"ring:{index}:1")
    return builder.surface()


def test_closed_wall_has_deterministic_seam_and_periodic_defer() -> None:
    surface = _closed_wall()
    result = _extract(surface, _rectangle(-10.0, -10.0, 10.0, 10.0))

    assert len(result.sectors) == 1
    sector = result.sectors[0]
    assert sector.closed_along_strike
    assert sector.seam_point == min(sector.upper_guide.points, key=lambda p: (p.x, p.y))
    assert "phase1_periodic_spacing_deferred" in sector.diagnostics.codes
    assert not sector.supported


def test_phase_2b_module_has_no_production_or_assessment_boundary_authority() -> None:
    source = Path("domain/wall_conformance/wall_sectors.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "place_profile_traces",
        "application.services",
        "PySide6",
        "Actual",
        "StraightConnector",
        "ProjectLineSpan",
        "tangent_xy",
        "normal_xy",
    )
    assert all(name not in source for name in forbidden)
