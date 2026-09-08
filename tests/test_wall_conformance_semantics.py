from types import SimpleNamespace

from application.services.wall_conformance import WallConformanceDiagnosticService
from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.wall_conformance import (
    PROTOTYPE_DESIGN_ROLE_MAPPING, SurfaceRoleMapping,
    extract_design_transition_lines,
)


def _surface(values=(10, 20, 30, 99)):
    vertices = (
        SurfaceVertex(-5, 0, 10), SurfaceVertex(0, 0, 10), SurfaceVertex(-5, 20, 10),
        SurfaceVertex(0, 20, 10), SurfaceVertex(5, 0, 0), SurfaceVertex(5, 20, 0),
        SurfaceVertex(10, 0, 0), SurfaceVertex(10, 20, 0),
    )
    specs = (((0, 1, 2), values[1]), ((1, 3, 2), values[1]),
             ((1, 4, 3), values[0]), ((4, 5, 3), values[0]),
             ((4, 6, 5), values[2]), ((6, 7, 5), values[3]))
    return TriangleSurface(vertices, tuple(
        SurfaceTriangle(indices, source_attributes={"COLOUR": value, "ZONE": "A"})
        for indices, value in specs
    ))


class _SurfaceService:
    storage_available = True

    def __init__(self, mapping=None):
        self.dataset = SimpleNamespace(
            logical_id="D", dataset_kind="design", revision_number=4,
            semantic_mapping_json=mapping,
        )
        self.saved = None

    def current(self, _site_id, kind):
        return self.dataset if kind == "design" else None

    def load_current(self, _site_id, kind):
        return (self.dataset, SimpleNamespace(surface=_surface())) if kind == "design" else None

    def save_design_semantic_mapping(self, _site_id, _logical_id, mapping):
        self.saved = mapping
        self.dataset.semantic_mapping_json = mapping.to_dict()
        return self.dataset


def test_mapping_round_trip_normalizes_numeric_and_string_values():
    mapping = SurfaceRoleMapping("COLOUR", ((10, "face"), ("20.000", "berm"), (30.0, "road")))
    restored = SurfaceRoleMapping.from_dict(mapping.to_dict())
    assert restored.resolve({"colour": "10.0"}) == "face"
    assert restored.resolve({"COLOUR": 20}) == "berm"
    assert restored.resolve({"COLOUR": "30"}) == "road"


def test_missing_mapping_is_explicit_fallback_and_saved_mapping_overrides_it():
    surface_service = _SurfaceService()
    service = WallConformanceDiagnosticService(surface_service)
    mapping, fallback = service.mapping_for_dataset(surface_service.dataset)
    assert mapping == PROTOTYPE_DESIGN_ROLE_MAPPING
    assert fallback is True

    custom = SurfaceRoleMapping("COLOUR", ((10, "face"), (20, "berm"), (30, "road"), (99, "ignore")))
    service.save_design_semantics(1, "D", custom)
    mapping, fallback = service.mapping_for_dataset(surface_service.dataset)
    assert mapping == custom
    assert fallback is False


def test_inspection_counts_distinct_values_and_keeps_unknown_visible():
    service = WallConformanceDiagnosticService(_SurfaceService())
    inspection = service.inspect_design_semantics(1)
    counts = {entry.value: entry.triangle_count for entry in inspection.attribute_values["COLOUR"]}
    assert counts == {10: 2, 20: 2, 30: 1, 99: 1}
    assert inspection.mapping.resolve({"COLOUR": 99}) == "unknown"


def test_custom_values_drive_design_transition_extraction():
    mapping = SurfaceRoleMapping(
        "COLOUR", ((10, "face"), (20, "berm"), (30, "road"), (99, "ignore"))
    )
    transitions = extract_design_transition_lines(_surface(), mapping)
    assert {line.kind for line in transitions} == {"crest", "toe"}


def test_missing_source_attribute_is_counted_as_unknown():
    base = _surface()
    triangles = list(base.triangles)
    triangles[-1] = SurfaceTriangle(
        triangles[-1].vertex_indices, source_attributes={"ZONE": "A"}
    )
    service = WallConformanceDiagnosticService(_SurfaceService())
    service.surface_service.load_current = lambda *_: (
        service.surface_service.dataset,
        SimpleNamespace(surface=TriangleSurface(base.vertices, tuple(triangles))),
    )
    inspection = service.inspect_design_semantics(1)
    counts = {entry.value: entry.triangle_count for entry in inspection.attribute_values["COLOUR"]}
    assert counts["<missing>"] == 1
    assert sum(counts.values()) == len(triangles)
