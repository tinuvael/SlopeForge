from __future__ import annotations

from dataclasses import replace
from math import cos, hypot, radians, sin
from pathlib import Path

import pytest

from domain.geometry.surfaces import (
    SurfaceTriangle,
    SurfaceVertex,
    TriangleSurface,
)
from domain.geometry.types import PlanPoint
from domain.wall_conformance import design_topology as topology_module
from domain.wall_conformance.design_topology import (
    FaceDirectionEvidence,
    build_design_topology_index,
)
from domain.wall_conformance.models import SurfaceRoleMapping


ROLE_MAPPING = SurfaceRoleMapping(
    "ROLE",
    (("FACE", "face"), ("BERM", "berm"), ("ROAD", "road")),
)


class _MeshBuilder:
    def __init__(self) -> None:
        self.vertices: list[SurfaceVertex] = []
        self.triangles: list[SurfaceTriangle] = []
        self._vertex_indices: dict[tuple[float, float, float], int] = {}

    def vertex(
        self, x: float, y: float, z: float, *, reuse: bool = True
    ) -> int:
        key = (float(x), float(y), float(z))
        if reuse and key in self._vertex_indices:
            return self._vertex_indices[key]
        index = len(self.vertices)
        self.vertices.append(SurfaceVertex(*key))
        if reuse:
            self._vertex_indices[key] = index
        return index

    def triangle(
        self,
        indices: tuple[int, int, int],
        role: str,
        source_id: str,
    ) -> None:
        self.triangles.append(SurfaceTriangle(
            indices,
            source_id,
            {"ROLE": role.upper()},
        ))

    def strip(
        self,
        *,
        x0: float,
        z0: float,
        x1: float,
        z1: float,
        y_values: tuple[float, ...],
        role: str,
        source_prefix: str,
        segment_indices: tuple[int, ...] | None = None,
    ) -> None:
        wanted = (
            range(len(y_values) - 1)
            if segment_indices is None
            else segment_indices
        )
        for segment_index in wanted:
            y0, y1 = y_values[segment_index : segment_index + 2]
            a = self.vertex(x0, y0, z0)
            b = self.vertex(x0, y1, z0)
            c = self.vertex(x1, y0, z1)
            d = self.vertex(x1, y1, z1)
            self.triangle(
                (a, c, d), role, f"{source_prefix}:{segment_index}:0"
            )
            self.triangle(
                (a, d, b), role, f"{source_prefix}:{segment_index}:1"
            )

    def surface(self) -> TriangleSurface:
        return TriangleSurface(tuple(self.vertices), tuple(self.triangles))


def _layered_surface(
    strip_roles: tuple[str, ...],
    *,
    y_values: tuple[float, ...] = (0.0, 10.0),
    y_offset: float = 0.0,
) -> TriangleSurface:
    builder = _MeshBuilder()
    shifted_y = tuple(value + y_offset for value in y_values)
    xz = [(0.0, 30.0)]
    for role in strip_roles:
        previous_x, previous_z = xz[-1]
        if role == "face":
            xz.append((previous_x + 4.0, previous_z - 10.0))
        else:
            xz.append((previous_x + 4.0, previous_z))
    for index, role in enumerate(strip_roles):
        builder.strip(
            x0=xz[index][0],
            z0=xz[index][1],
            x1=xz[index + 1][0],
            z1=xz[index + 1][1],
            y_values=shifted_y,
            role=role,
            source_prefix=f"{role}:{index}",
        )
    return builder.surface()


def _mixed_parent_portal_surface(*, two_local_pairs: bool) -> TriangleSurface:
    """Two Faces whose platform rims contain separated edge-side runs."""
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


def _combine_surfaces(*surfaces: TriangleSurface) -> TriangleSurface:
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


def _reordered_surface(
    surface: TriangleSurface,
    *,
    reverse_vertices: bool = False,
    reverse_triangles: bool = False,
    reverse_winding: bool = False,
) -> TriangleSurface:
    vertices = list(surface.vertices)
    triangles = list(surface.triangles)
    if reverse_vertices:
        order = tuple(reversed(range(len(vertices))))
        old_to_new = {old: new for new, old in enumerate(order)}
        vertices = [vertices[index] for index in order]
        triangles = [replace(
            triangle,
            vertex_indices=tuple(
                old_to_new[index] for index in triangle.vertex_indices
            ),
        ) for triangle in triangles]
    if reverse_winding:
        triangles = [replace(
            triangle,
            vertex_indices=(
                triangle.vertex_indices[0],
                triangle.vertex_indices[2],
                triangle.vertex_indices[1],
            ),
        ) for triangle in triangles]
    if reverse_triangles:
        triangles.reverse()
    return TriangleSurface(tuple(vertices), tuple(triangles))


def _compatible_pairs(index) -> set[tuple[int, int]]:
    portal_by_id = {portal.portal_id: portal for portal in index.portals}
    return {
        (
            portal_by_id[connection.source_portal_id].face_component_index,
            portal_by_id[connection.target_portal_id].face_component_index,
        )
        for connection in index.compatible_corridor_connections
    }


def test_single_straight_face_builds_component_and_all_outer_rims() -> None:
    index = build_design_topology_index(
        _layered_surface(("face",)), ROLE_MAPPING
    )

    assert len(index.face_components) == 1
    assert not index.platform_components
    assert not index.corridor_connections
    component = index.face_components[0]
    assert set(component.portal_ids) == set(component.outer_rim_portal_ids)
    assert len(index.portals) == 1
    assert index.portals[0].points[0] == index.portals[0].points[-1]
    assert index.portals[0].provisional_side == "ambiguous"
    edge_sides = tuple(
        edge.provisional_side for edge in index.portals[0].edge_provenance
    )
    assert edge_sides.count("upstream") == 1
    assert edge_sides.count("downstream") == 1
    assert edge_sides.count("lateral") == 2
    assert "ambiguous" not in edge_sides
    local_runs = tuple(
        side
        for previous, side in zip(
            edge_sides[-1:] + edge_sides[:-1], edge_sides, strict=True
        )
        if side != previous
    )
    assert sorted(local_runs) == [
        "downstream", "lateral", "lateral", "upstream"
    ]
    assert all(
        sample.downwall_xy == pytest.approx((1.0, 0.0))
        for sample in component.direction_samples
    )


@pytest.mark.parametrize("platform_role", ["berm", "road"])
def test_one_platform_connects_two_face_layers(
    platform_role: str,
) -> None:
    index = build_design_topology_index(
        _layered_surface(("face", platform_role, "face")), ROLE_MAPPING
    )

    assert len(index.face_components) == 2
    assert len(index.platform_components) == 1
    assert index.platform_components[0].semantic_roles == (platform_role,)
    assert len(index.compatible_corridor_connections) == 1
    connection = index.compatible_corridor_connections[0]
    assert not connection.validated_wall_continuation
    assert connection.source_advance_m > 0.0
    assert connection.target_advance_m > 0.0
    assert connection.order_compatible
    if platform_role == "road":
        assert all(
            sample.triangle_index not in index.platform_components[0].triangle_indices
            for sample in index.face_direction_samples
        )


def test_three_face_layers_compose_through_two_platforms() -> None:
    index = build_design_topology_index(
        _layered_surface(("face", "berm", "face", "berm", "face")),
        ROLE_MAPPING,
    )

    assert len(index.face_components) == 3
    assert len(index.platform_components) == 2
    assert len(index.compatible_corridor_connections) == 2
    assert _compatible_pairs(index) == {(0, 1), (1, 2)}


def test_mixed_parent_portals_connect_through_unique_local_side_runs() -> None:
    surface = _mixed_parent_portal_surface(two_local_pairs=False)
    index = build_design_topology_index(surface, ROLE_MAPPING)

    assert len(index.corridor_connections) == 1
    connection = index.corridor_connections[0]
    portals = {portal.portal_id: portal for portal in index.portals}
    source = portals[connection.source_portal_id]
    target = portals[connection.target_portal_id]
    source_runs = topology_module._local_side_runs(
        surface, source, "downstream"
    )
    target_runs = topology_module._local_side_runs(surface, target, "upstream")

    assert source.provisional_side == "lateral"
    assert target.provisional_side == "lateral"
    assert connection.status == "compatible"
    assert connection.order_compatible
    assert len(connection.local_run_pairs) == 1
    assert len(source_runs) == len(target_runs) == 1
    assert sum(
        hypot(second.x - first.x, second.y - first.y)
        for first, second in zip(
            source_runs[0].points, source_runs[0].points[1:]
        )
    ) == pytest.approx(8.0)
    assert sum(
        hypot(second.x - first.x, second.y - first.y)
        for first, second in zip(
            target_runs[0].points, target_runs[0].points[1:]
        )
    ) == pytest.approx(8.0)
    for variant in (
        _reordered_surface(surface, reverse_triangles=True),
        _reordered_surface(surface, reverse_winding=True),
        _reordered_surface(
            surface,
            reverse_vertices=True,
            reverse_triangles=True,
            reverse_winding=True,
        ),
    ):
        assert build_design_topology_index(
            variant, ROLE_MAPPING
        ).canonical_signature == index.canonical_signature


def test_two_plausible_local_run_pairs_remain_explicitly_ambiguous() -> None:
    index = build_design_topology_index(
        _mixed_parent_portal_surface(two_local_pairs=True), ROLE_MAPPING
    )

    assert len(index.corridor_connections) == 1
    connection = index.corridor_connections[0]
    assert connection.status == "ambiguous"
    assert len(connection.local_run_pairs) == 2
    assert "2 competing locally compatible run pairs" in connection.reason
    assert "ambiguous_local_run_correspondence" in index.diagnostics.codes


def test_nearby_unrelated_walls_remain_topologically_separate() -> None:
    surface = _combine_surfaces(
        _layered_surface(("face", "berm", "face"), y_offset=0.0),
        _layered_surface(("face", "berm", "face"), y_offset=10.01),
    )
    index = build_design_topology_index(surface, ROLE_MAPPING)

    assert len(index.face_components) == 4
    assert len(index.platform_components) == 2
    assert len(index.compatible_corridor_connections) == 2
    assert {
        connection.platform_component_index
        for connection in index.compatible_corridor_connections
    } == {0, 1}


def _broad_platform_surface(
    *, branching: bool, refined_platform: bool = False
) -> TriangleSurface:
    builder = _MeshBuilder()
    y_values = (0.0, 10.0, 20.0, 30.0)
    builder.strip(
        x0=0.0,
        z0=30.0,
        x1=4.0,
        z1=20.0,
        y_values=y_values,
        role="face",
        source_prefix="upper",
        segment_indices=(0, 1, 2) if branching else (0, 2),
    )
    if not refined_platform:
        builder.strip(
            x0=4.0,
            z0=20.0,
            x1=8.0,
            z1=20.0,
            y_values=y_values,
            role="berm",
            source_prefix="broad-platform",
        )
    else:
        for segment_index, (y0, y1) in enumerate(
            zip(y_values, y_values[1:])
        ):
            a = builder.vertex(4.0, y0, 20.0)
            b = builder.vertex(4.0, y1, 20.0)
            c = builder.vertex(8.0, y0, 20.0)
            d = builder.vertex(8.0, y1, 20.0)
            if segment_index == 1:
                middle = builder.vertex(5.0, y0 + 0.2 * (y1 - y0), 20.0)
                for triangle_index, indices in enumerate((
                    (a, c, middle),
                    (c, d, middle),
                    (d, b, middle),
                    (b, a, middle),
                )):
                    builder.triangle(
                        indices,
                        "berm",
                        f"refined-platform:{segment_index}:{triangle_index}",
                    )
            else:
                builder.triangle(
                    (a, c, d), "berm", f"platform:{segment_index}:0"
                )
                builder.triangle(
                    (a, d, b), "berm", f"platform:{segment_index}:1"
                )
    builder.strip(
        x0=8.0,
        z0=20.0,
        x1=12.0,
        z1=10.0,
        y_values=y_values,
        role="face",
        source_prefix="lower",
        segment_indices=(0, 2),
    )
    return builder.surface()


def test_broad_platform_keeps_two_local_corridor_bands() -> None:
    index = build_design_topology_index(
        _broad_platform_surface(branching=False), ROLE_MAPPING
    )

    assert len(index.platform_components) == 1
    assert len(index.face_components) == 4
    assert len(index.compatible_corridor_connections) == 2
    pairs = _compatible_pairs(index)
    assert len({source for source, _target in pairs}) == 2
    assert len({target for _source, target in pairs}) == 2


def test_branching_platform_is_ambiguous_without_arbitrary_successor() -> None:
    index = build_design_topology_index(
        _broad_platform_surface(branching=True), ROLE_MAPPING
    )

    assert not index.compatible_corridor_connections
    assert len(index.ambiguous_corridor_connections) == 2
    assert "ambiguous_corridor_successors" in index.diagnostics.codes


def _corridor_candidate_geometry_signature(index) -> tuple[object, ...]:
    portals = {portal.portal_id: portal for portal in index.portals}
    return tuple(sorted(
        (
            portals[connection.source_portal_id].geometry_key,
            portals[connection.target_portal_id].geometry_key,
            connection.status,
            round(connection.direction_mismatch_degrees, 10),
            connection.order_compatible,
        )
        for connection in index.corridor_connections
    ))


def test_platform_neighbours_are_invariant_to_local_remeshing() -> None:
    coarse = build_design_topology_index(
        _broad_platform_surface(branching=False), ROLE_MAPPING
    )
    refined = build_design_topology_index(
        _broad_platform_surface(
            branching=False, refined_platform=True
        ),
        ROLE_MAPPING,
    )

    assert _corridor_candidate_geometry_signature(
        refined
    ) == _corridor_candidate_geometry_signature(coarse)
    assert len(refined.compatible_corridor_connections) == 2


def test_shared_endpoint_does_not_create_edge_connectivity() -> None:
    builder = _MeshBuilder()
    shared = builder.vertex(0.0, 0.0, 10.0)
    face_b = builder.vertex(4.0, 0.0, 0.0)
    face_c = builder.vertex(0.0, 4.0, 10.0)
    platform_b = builder.vertex(-4.0, 0.0, 10.0)
    platform_c = builder.vertex(0.0, -4.0, 10.0)
    builder.triangle((shared, face_b, face_c), "face", "face")
    builder.triangle((shared, platform_b, platform_c), "berm", "berm")

    index = build_design_topology_index(builder.surface(), ROLE_MAPPING)

    assert len(index.face_components) == 1
    assert len(index.platform_components) == 1
    assert not index.platform_components[0].portal_ids
    assert not index.corridor_connections
    assert "shared_endpoint_without_shared_edge" in index.diagnostics.codes


def test_non_manifold_edge_taints_only_affected_components() -> None:
    builder = _MeshBuilder()
    a = builder.vertex(0.0, 0.0, 10.0)
    b = builder.vertex(0.0, 10.0, 10.0)
    c = builder.vertex(4.0, 0.0, 0.0)
    d = builder.vertex(-4.0, 0.0, 0.0)
    e = builder.vertex(2.0, 5.0, 10.0)
    builder.triangle((a, c, b), "face", "affected-face-a")
    builder.triangle((a, b, d), "face", "affected-face-b")
    builder.triangle((a, e, b), "berm", "affected-platform")
    clean_a = builder.vertex(20.0, 20.0, 10.0)
    clean_b = builder.vertex(24.0, 20.0, 0.0)
    clean_c = builder.vertex(20.0, 24.0, 10.0)
    builder.triangle((clean_a, clean_b, clean_c), "face", "clean-face")

    index = build_design_topology_index(builder.surface(), ROLE_MAPPING)

    assert "non_manifold_edge" in index.diagnostics.codes
    non_manifold_key = tuple(sorted((
        (builder.vertices[a].x, builder.vertices[a].y, builder.vertices[a].z),
        (builder.vertices[b].x, builder.vertices[b].y, builder.vertices[b].z),
    )))
    assert all(
        provenance.edge_key != non_manifold_key
        for portal in index.portals
        for provenance in portal.edge_provenance
    )
    affected_face_components = tuple(
        component
        for component in index.face_components
        if any(
            sample.source_id.startswith("affected")
            for sample in component.direction_samples
        )
    )
    clean_component = next(
        component
        for component in index.face_components
        if any(
            sample.source_id == "clean-face"
            for sample in component.direction_samples
        )
    )
    assert len(affected_face_components) == 2
    assert all(
        component.topology_issue_codes == ("non_manifold_edge",)
        and component.topology_issue_edge_keys == (non_manifold_key,)
        for component in affected_face_components
    )
    assert clean_component.topology_issue_codes == ()
    assert clean_component.topology_issue_edge_keys == ()
    assert index.platform_components[0].topology_issue_codes == (
        "non_manifold_edge",
    )
    diagnostic = next(
        item for item in index.diagnostics.items
        if item.code == "non_manifold_edge"
    )
    assert set(diagnostic.related_ids) == {
        *(component.component_id for component in affected_face_components),
        index.platform_components[0].component_id,
    }


def test_unstable_face_gradient_is_retained_with_diagnostic() -> None:
    builder = _MeshBuilder()
    a = builder.vertex(0.0, 0.0, 10.0)
    b = builder.vertex(0.0, 10.0, 10.0)
    c = builder.vertex(0.0, 0.0, 0.0)
    builder.triangle((a, b, c), "face", "vertical-face")

    index = build_design_topology_index(builder.surface(), ROLE_MAPPING)

    assert len(index.face_components) == 1
    assert index.face_direction_samples[0].downwall_xy is None
    assert "degenerate_face_direction" in index.diagnostics.codes
    assert all(
        portal.provisional_side == "ambiguous" for portal in index.portals
    )


def test_duplicate_triangle_geometry_is_diagnosed_without_deduplication() -> None:
    builder = _MeshBuilder()
    a = builder.vertex(0.0, 0.0, 10.0)
    b = builder.vertex(4.0, 0.0, 0.0)
    c = builder.vertex(0.0, 4.0, 10.0)
    builder.triangle((a, b, c), "face", "first")
    builder.triangle((a, c, b), "face", "second")

    index = build_design_topology_index(builder.surface(), ROLE_MAPPING)

    assert "duplicate_triangle_geometry" in index.diagnostics.codes
    assert sum(
        len(component.triangle_indices) for component in index.face_components
    ) == 2


def test_coincident_distinct_vertices_are_diagnosed_but_not_welded() -> None:
    builder = _MeshBuilder()
    face_a = builder.vertex(0.0, 0.0, 10.0, reuse=False)
    face_b = builder.vertex(4.0, 0.0, 0.0)
    face_c = builder.vertex(0.0, 4.0, 10.0)
    platform_a = builder.vertex(0.0, 0.0, 10.0, reuse=False)
    platform_b = builder.vertex(-4.0, 0.0, 10.0)
    platform_c = builder.vertex(0.0, -4.0, 10.0)
    builder.triangle((face_a, face_b, face_c), "face", "face")
    builder.triangle(
        (platform_a, platform_b, platform_c), "berm", "berm"
    )

    index = build_design_topology_index(builder.surface(), ROLE_MAPPING)

    assert "coincident_distinct_vertices" in index.diagnostics.codes
    assert "shared_endpoint_without_shared_edge" not in index.diagnostics.codes
    assert not index.platform_components[0].portal_ids


def test_winding_and_storage_order_do_not_change_canonical_topology() -> None:
    surface = _layered_surface(("face", "road", "face"))
    baseline = build_design_topology_index(surface, ROLE_MAPPING)
    variants = (
        _reordered_surface(surface, reverse_winding=True),
        _reordered_surface(surface, reverse_triangles=True),
        _reordered_surface(surface, reverse_vertices=True),
        _reordered_surface(
            surface,
            reverse_vertices=True,
            reverse_triangles=True,
            reverse_winding=True,
        ),
    )

    for variant in variants:
        assert build_design_topology_index(
            variant, ROLE_MAPPING
        ).canonical_signature == baseline.canonical_signature
    reordered_mapping = SurfaceRoleMapping(
        ROLE_MAPPING.attribute_name,
        tuple(reversed(ROLE_MAPPING.assignments)),
    )
    assert build_design_topology_index(
        surface, reordered_mapping
    ).canonical_signature == baseline.canonical_signature


def _continuous_noisy_transition_surface() -> TriangleSurface:
    builder = _MeshBuilder()
    boundary_0 = builder.vertex(4.0, 0.0, 0.0)
    boundary_1 = builder.vertex(4.0, 0.1, 0.0)
    boundary_2 = builder.vertex(4.0, 10.0, 0.0)
    noisy_interior = builder.vertex(3.99, 0.05, -0.1)
    dominant_interior = builder.vertex(0.0, 5.0, 10.0)
    builder.triangle(
        (boundary_0, noisy_interior, boundary_1),
        "face",
        "tiny-noisy-edge",
    )
    builder.triangle(
        (noisy_interior, dominant_interior, boundary_1),
        "face",
        "face-connector",
    )
    builder.triangle(
        (boundary_1, dominant_interior, boundary_2),
        "face",
        "dominant-edge",
    )
    builder.strip(
        x0=4.0,
        z0=0.0,
        x1=8.0,
        z1=0.0,
        y_values=(0.0, 0.1, 10.0),
        role="berm",
        source_prefix="platform",
    )
    return builder.surface()


def test_portal_topology_precedes_noisy_edge_side_classification() -> None:
    surface = _continuous_noisy_transition_surface()
    baseline = build_design_topology_index(surface, ROLE_MAPPING)
    source_component = next(
        component
        for component in baseline.face_components
        if any(
            sample.source_id == "tiny-noisy-edge"
            for sample in component.direction_samples
        )
    )
    transition_portals = tuple(
        portal
        for portal in baseline.portals
        if portal.face_component_index == source_component.canonical_index
        and portal.source_kind == "face_platform"
    )

    assert len(transition_portals) == 1
    portal = transition_portals[0]
    assert len(portal.edge_provenance) == 2
    samples = {
        sample.source_id: sample for sample in source_component.direction_samples
    }
    assert samples["tiny-noisy-edge"].downwall_xy[0] < -0.99
    assert portal.direction_support.downwall_xy[0] > 0.99
    assert portal.provisional_side == "downstream"
    assert tuple(
        edge.provisional_side for edge in portal.edge_provenance
    ) == ("downstream", "downstream")

    for variant in (
        _reordered_surface(surface, reverse_triangles=True),
        _reordered_surface(surface, reverse_winding=True),
        _reordered_surface(
            surface, reverse_vertices=True, reverse_triangles=True
        ),
    ):
        assert build_design_topology_index(
            variant, ROLE_MAPPING
        ).canonical_signature == baseline.canonical_signature


def _angled_face_connection_surface(angle_degrees: float) -> TriangleSurface:
    builder = _MeshBuilder()
    source_0 = builder.vertex(4.0, 3.0, 0.0)
    source_1 = builder.vertex(4.0, 7.0, 0.0)
    upper_0 = builder.vertex(0.0, 3.0, 10.0)
    upper_1 = builder.vertex(0.0, 7.0, 10.0)
    builder.triangle((upper_0, source_0, source_1), "face", "source:0")
    builder.triangle((upper_0, source_1, upper_1), "face", "source:1")

    angle = radians(angle_degrees)
    downwall = (cos(angle), sin(angle))
    tangent = (-downwall[1], downwall[0])
    target_0 = builder.vertex(
        8.0 - 2.0 * tangent[0],
        5.0 - 2.0 * tangent[1],
        0.0,
    )
    target_1 = builder.vertex(
        8.0 + 2.0 * tangent[0],
        5.0 + 2.0 * tangent[1],
        0.0,
    )
    builder.triangle((source_0, target_0, target_1), "berm", "platform:0")
    builder.triangle((source_0, target_1, source_1), "berm", "platform:1")

    downstream_0 = builder.vertex(
        builder.vertices[target_0].x + 4.0 * downwall[0],
        builder.vertices[target_0].y + 4.0 * downwall[1],
        -10.0,
    )
    downstream_1 = builder.vertex(
        builder.vertices[target_1].x + 4.0 * downwall[0],
        builder.vertices[target_1].y + 4.0 * downwall[1],
        -10.0,
    )
    builder.triangle(
        (target_0, downstream_0, downstream_1), "face", "target:0"
    )
    builder.triangle(
        (target_0, downstream_1, target_1), "face", "target:1"
    )
    return builder.surface()


def test_large_direction_turn_remains_unvalidated_candidate() -> None:
    index = build_design_topology_index(
        _angled_face_connection_surface(85.0), ROLE_MAPPING
    )

    assert len(index.compatible_corridor_connections) == 1
    candidate = index.compatible_corridor_connections[0]
    assert candidate.status == "compatible"
    assert not candidate.validated_wall_continuation
    assert candidate.direction_mismatch_degrees == pytest.approx(85.0)
    assert candidate.source_alignment_degrees == pytest.approx(0.0)
    assert candidate.target_alignment_degrees == pytest.approx(85.0)
    assert candidate.source_direction_dispersion_degrees == pytest.approx(0.0)
    assert candidate.target_direction_dispersion_degrees == pytest.approx(0.0)
    assert "Phase 2B validation required" in candidate.reason


def test_tiny_noisy_face_triangle_cannot_reverse_local_support() -> None:
    builder = _MeshBuilder()
    a = builder.vertex(0.0, 0.0, 10.0)
    b = builder.vertex(4.0, 0.0, 0.0)
    c = builder.vertex(4.0, 0.1, 0.0)
    d = builder.vertex(3.99, 0.05, -0.1)
    builder.triangle((a, b, c), "face", "large-intended")
    builder.triangle((b, d, c), "face", "tiny-opposite")

    index = build_design_topology_index(builder.surface(), ROLE_MAPPING)

    samples = {
        sample.source_id: sample for sample in index.face_direction_samples
    }
    assert samples["large-intended"].downwall_xy == pytest.approx((1.0, 0.0))
    assert samples["tiny-opposite"].downwall_xy == pytest.approx((-1.0, 0.0))
    assert samples["tiny-opposite"].geometric_weight < (
        samples["large-intended"].geometric_weight / 50.0
    )
    assert any(
        portal.direction_support.downwall_xy is not None
        and portal.direction_support.downwall_xy[0] > 0.99
        and len(portal.direction_support.triangle_keys) == 2
        and portal.direction_support.angular_dispersion_degrees < 20.0
        and portal.direction_support.angular_support_degrees == pytest.approx(0.0)
        for portal in index.portals
    )


def _direction_evidence(
    index: int,
    direction: tuple[float, float],
    weight: float,
) -> FaceDirectionEvidence:
    coordinate = float(index)
    return FaceDirectionEvidence(
        index,
        (
            (coordinate, 0.0, 0.0),
            (coordinate, 1.0, 0.0),
            (coordinate, 0.0, 1.0),
        ),
        PlanPoint(coordinate, 0.0),
        direction,
        weight,
        f"support:{index}",
    )


def test_direction_support_uses_weighted_rms_and_95_percent_envelope() -> None:
    support = topology_module._aggregate_direction_support((
        _direction_evidence(0, (1.0, 0.0), 100.0),
        _direction_evidence(1, (-1.0, 0.0), 0.1),
    ))

    assert support.downwall_xy == pytest.approx((1.0, 0.0))
    assert support.angular_dispersion_degrees < 6.0
    assert support.angular_support_degrees == pytest.approx(0.0)


def test_direction_support_reports_substantial_mixed_directions() -> None:
    support = topology_module._aggregate_direction_support((
        _direction_evidence(0, (1.0, 0.0), 1.0),
        _direction_evidence(1, (0.0, 1.0), 1.0),
    ))

    assert support.downwall_xy == pytest.approx((2**-0.5, 2**-0.5))
    assert support.angular_dispersion_degrees == pytest.approx(45.0)
    assert support.angular_support_degrees == pytest.approx(45.0)


def test_phase_2a_module_has_no_rejected_alignment_or_placement_layer() -> None:
    source = Path(
        "domain/wall_conformance/design_topology.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "DesignAlignmentBoundary",
        "UpperCrestStationEvaluation",
        "sample_wall_alignment",
        "place_profile_traces",
        "station_fraction",
        "tangent_xy",
        "normal_xy",
    )
    assert all(name not in source for name in forbidden)
