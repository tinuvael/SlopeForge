from __future__ import annotations

import pytest

import domain.wall_conformance.design as design_module
from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import SurfaceRoleMapping, build_transverse_profiles


MAPPING = SurfaceRoleMapping("COLOUR", ((2, "face"), (5, "berm")))


def _single_bench_design() -> TriangleSurface:
    """Upper berm -> descending face -> lower berm, extruded along strike."""
    xs = (-5.0, 0.0, 10.0, 15.0)
    ys = (0.0, 20.0)
    zs = (20.0, 20.0, 10.0, 10.0)
    roles = (5, 2, 5)
    vertices = tuple(
        SurfaceVertex(x, y, zs[column])
        for column, x in enumerate(xs)
        for y in ys
    )
    triangles = []
    for column, role in enumerate(roles):
        a = column * len(ys)
        b = a + 1
        c = (column + 1) * len(ys)
        d = c + 1
        triangles.extend((
            SurfaceTriangle((a, c, b), source_attributes={"COLOUR": role}),
            SurfaceTriangle((c, d, b), source_attributes={"COLOUR": role}),
        ))
    return TriangleSurface(vertices, tuple(triangles))


def _assessment_strip() -> PlanPolygon:
    return PlanPolygon((
        PlanPoint(0.0, 2.0),
        PlanPoint(10.0, 2.0),
        PlanPoint(10.0, 18.0),
        PlanPoint(0.0, 18.0),
        PlanPoint(0.0, 2.0),
    ))


def test_exact_section_can_rescue_upper_face_platform_transition_preclassified_as_toe(
    monkeypatch,
) -> None:
    """Preliminary triangle-plane labels must not decide Upper Crest identity.

    Real graded/irregular TINs can make a true upper Face/Platform transition
    look like a toe to the plan-gradient heuristic. The exact transverse Design
    section is authoritative: the accepted origin must still be the upper crest
    and every Face must descend with positive U.
    """
    original_classifier = design_module._face_transition_kind

    def misclassify_preliminary_crest(*args, **kwargs):
        kind = original_classifier(*args, **kwargs)
        return "toe" if kind == "crest" else kind

    monkeypatch.setattr(
        design_module,
        "_face_transition_kind",
        misclassify_preliminary_crest,
    )

    design = _single_bench_design()
    result = build_transverse_profiles(
        design,
        design,
        _assessment_strip(),
        MAPPING,
        spacing_m=4.0,
        tangent_window_m=4.0,
    )

    assert result.profiles
    assert all(profile.alignment.origin.x == pytest.approx(0.0) for profile in result.profiles)
    assert all(profile.alignment.origin.z == pytest.approx(20.0) for profile in result.profiles)

    for profile in result.profiles:
        faces = [
            element
            for element in profile.design_section.elements
            if element.role == "face"
        ]
        assert faces
        assert all(face.start.u >= -1e-6 for face in faces)
        assert all(face.vertical_change < 0.0 for face in faces)
