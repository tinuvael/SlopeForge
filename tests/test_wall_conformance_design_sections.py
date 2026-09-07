from domain.geometry.surfaces import SurfaceVertex
from domain.wall_conformance import build_design_section, build_design_variants
from domain.wall_conformance.models import (
    SectionPoint, SectionSegment, TransverseProfile, WallAlignmentSample,
)


def point(u, z):
    return SectionPoint(u, z, u, 0)


def segment(a, b, role, index):
    return SectionSegment(point(*a), point(*b), index, role)


def profile(segments, crest_z=100):
    return TransverseProfile(
        WallAlignmentSample(0, SurfaceVertex(0, 0, crest_z), (0, 1), (1, 0)),
        tuple(segments), (), build_design_section(tuple(segments)),
    )


def profile_with_upstream_context(role: str | None):
    core = (
        segment((0, 100), (5, 90), "face", 2),
        segment((5, 90), (9, 90), "berm", 3),
        segment((9, 90), (19, 70), "face", 4),
    )
    if role is None:
        return profile(core)
    return profile((segment((-6, 101), (0, 100), role, 1), *core))


def test_semantic_section_collapses_fragments_and_preserves_real_transitions():
    section = build_design_section((
        segment((0, 100), (2, 96), "face", 1),
        segment((2, 96), (4, 92), "face", 2),
        segment((4, 92), (10, 91), "berm", 3),
        segment((10, 91), (13, 86), "face", 4),
        segment((13, 86), (18, 84), "road", 5),
        segment((18, 84), (20, 80), "face", 6),
    ))
    assert section.topology_signature == "FACE-BERM-FACE-ROAD-FACE"
    assert section.elements[0].source_triangle_indices == (1, 2)
    assert section.elements[0].angle_degrees != section.elements[2].angle_degrees


def test_ignore_is_excluded_and_unknown_remains_diagnostic():
    section = build_design_section((
        segment((-2, 101), (0, 100), "ignore", 1),
        segment((0, 100), (4, 90), "face", 2),
        segment((4, 90), (6, 89), "unknown", 3),
    ))
    assert section.topology_signature == "FACE-UNKNOWN"
    assert all(element.role != "ignore" for element in section.elements)


def test_multiple_berms_and_road_create_distinct_topology_signatures():
    roles = ("face", "berm", "face", "berm", "face")
    segments = tuple(
        segment((i * 2, 100 - i * 3), ((i + 1) * 2, 97 - i * 3), role, i)
        for i, role in enumerate(roles)
    )
    assert build_design_section(segments).topology_signature == "FACE-BERM-FACE-BERM-FACE"
    road = list(segments)
    road[1] = segment((2, 97), (4, 94), "road", 1)
    assert build_design_section(tuple(road)).topology_signature == "FACE-ROAD-FACE-BERM-FACE"


def test_representative_normalizes_elevation_and_separates_variants():
    first = profile((segment((0, 100), (5, 90), "face", 1),), 100)
    second = profile((segment((0, 130), (5, 120), "face", 2),), 130)
    other = profile((
        segment((0, 100), (5, 90), "face", 3),
        segment((5, 90), (12, 90), "berm", 4),
        segment((12, 90), (16, 82), "face", 5),
    ), 100)
    variants = build_design_variants((first, second, other))
    assert len(variants) == 2
    face_variant = next(v for v in variants if v.signature == "FACE")
    assert face_variant.profile_indices == (0, 1)
    assert face_variant.elements[0].start_dz == 0
    assert face_variant.elements[0].end_dz == -10
    assert face_variant.elements[0].angle_range[0] == face_variant.elements[0].angle_range[1]
    assert next(v for v in variants if v.signature == "FACE-BERM-FACE").elements[1].width_median == 7


def test_representative_preserves_upper_platform_as_context_only():
    design = (
        segment((-6, 101), (0, 100), "berm", 1),
        segment((0, 100), (5, 90), "face", 2),
        segment((5, 90), (9, 90), "berm", 3),
        segment((9, 90), (19, 70), "face", 4),
    )
    section = build_design_section(design)
    assert section.upstream_context.role == "berm"
    assert section.upstream_context.end.u == 0
    assert section.topology_signature == "FACE-BERM-FACE"
    variant = build_design_variants((profile(design),))[0]
    assert variant.upstream_context.role == "berm"
    assert variant.upstream_context.end_u == 0
    assert [element.height_median for element in variant.elements if element.role == "face"] == [10, 20]


def test_representative_variants_split_context_from_same_assessed_topology():
    road_first = profile_with_upstream_context("road")
    road_second = profile_with_upstream_context("road")
    no_context = profile_with_upstream_context(None)

    variants = build_design_variants((road_first, road_second, no_context))

    assert len(variants) == 2
    road_variant = next(variant for variant in variants if variant.upstream_context)
    empty_variant = next(variant for variant in variants if not variant.upstream_context)
    assert road_variant.profile_indices == (0, 1)
    assert road_variant.signature == empty_variant.signature == "FACE-BERM-FACE"
    assert road_variant.upstream_context.role == "road"
    assert road_variant.upstream_context.start_u == -6
    assert road_variant.upstream_context.end_u == 0
    assert empty_variant.profile_indices == (2,)
    assert empty_variant.upstream_context is None
    assert [element.role for element in road_variant.elements] == ["face", "berm", "face"]


def test_representative_variants_keep_road_and_berm_contexts_separate():
    variants = build_design_variants((
        profile_with_upstream_context("road"),
        profile_with_upstream_context("berm"),
    ))

    assert len(variants) == 2
    assert {variant.upstream_context.role for variant in variants} == {"road", "berm"}
    assert all(variant.signature == "FACE-BERM-FACE" for variant in variants)


def test_representative_design_parameters_ignore_actual_geometry():
    design = (
        segment((0, 100), (5, 90), "face", 1),
        segment((5, 90), (9, 90), "berm", 2),
        segment((9, 90), (19, 70), "face", 3),
    )
    first = profile(design)
    second = TransverseProfile(
        first.alignment,
        first.design_segments,
        (segment((0, 130), (40, 5), None, 99),),
        first.design_section,
    )
    assert build_design_variants((first,)) == build_design_variants((second,))
