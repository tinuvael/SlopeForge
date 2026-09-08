from __future__ import annotations

import pytest

import domain.wall_conformance as wall_conformance
from domain.geometry.surfaces import SurfaceVertex
from domain.wall_conformance import (
    SectionPoint,
    SectionSegment,
    TransverseProfile,
    WallAlignmentSample,
    WallProfileSet,
    WallTransitionLine,
    enforce_profile_engineering_invariants,
    profile_vertical_order_issue,
)
from domain.wall_conformance.models import DesignSection, DesignSectionElement


def _profile(
    *,
    component_index: int,
    crest_z: float,
    face_end_z: float,
    toe_z: float | None,
    toe_u: float = 10.0,
) -> TransverseProfile:
    alignment = WallAlignmentSample(
        3.0,
        SurfaceVertex(0.0, float(component_index), crest_z),
        (0.0, 1.0),
        (1.0, 0.0),
        component_index,
    )
    start = SectionPoint(0.0, crest_z, 0.0, float(component_index))
    end = SectionPoint(10.0, face_end_z, 10.0, float(component_index))
    segment = SectionSegment(start, end, 0, "face")
    element = DesignSectionElement("face", start, end, (0,))
    toe = (
        None
        if toe_z is None
        else SectionPoint(toe_u, toe_z, toe_u, float(component_index))
    )
    return TransverseProfile(
        alignment=alignment,
        design_segments=(segment,),
        actual_segments=(),
        design_section=DesignSection((element,)),
        assessment_u_interval=(0.0, 10.0),
        external_toe=toe,
    )


def _crest(y: float, z: float) -> WallTransitionLine:
    return WallTransitionLine(
        "crest",
        (
            SurfaceVertex(0.0, y, z),
            SurfaceVertex(0.0, y + 1.0, z),
        ),
    )


def _mixed_profile_set() -> WallProfileSet:
    good = _profile(
        component_index=0,
        crest_z=30.0,
        face_end_z=20.0,
        toe_z=20.0,
    )
    inverted = _profile(
        component_index=1,
        crest_z=10.0,
        face_end_z=20.0,
        toe_z=20.0,
    )
    return WallProfileSet(
        crest_lines=(_crest(0.0, 30.0), _crest(2.0, 10.0)),
        toe_lines=(),
        profiles=(good, inverted),
    )


def test_rising_internal_candidate_is_removed_before_public_profile_result() -> None:
    filtered = enforce_profile_engineering_invariants(_mixed_profile_set())

    assert len(filtered.profiles) == 1
    assert filtered.profiles[0].alignment.origin.z == pytest.approx(30.0)
    assert filtered.profiles[0].alignment.boundary_component_index == 0
    assert len(filtered.crest_lines) == 1
    assert profile_vertical_order_issue(filtered.profiles[0]) is None


def test_public_builder_always_applies_engineering_invariant_gate(monkeypatch) -> None:
    raw = _mixed_profile_set()
    monkeypatch.setattr(
        wall_conformance,
        "_build_transverse_profiles",
        lambda *args, **kwargs: raw,
    )

    result = wall_conformance.build_transverse_profiles(object())

    assert len(result.profiles) == 1
    assert result.profiles[0].alignment.origin.z == pytest.approx(30.0)


def test_lower_toe_may_not_be_above_upper_crest() -> None:
    profile = _profile(
        component_index=0,
        crest_z=20.0,
        face_end_z=10.0,
        toe_z=25.0,
    )

    assert profile_vertical_order_issue(profile) == "Lower Toe is above Upper Crest"


def test_equal_crest_and_toe_at_convergence_is_not_a_profile() -> None:
    profile = _profile(
        component_index=0,
        crest_z=20.0,
        face_end_z=10.0,
        toe_z=20.0,
        toe_u=0.0,
    )
    raw = WallProfileSet(
        crest_lines=(_crest(0.0, 20.0),),
        toe_lines=(),
        profiles=(profile,),
    )

    assert (
        profile_vertical_order_issue(profile)
        == "Upper Crest and Lower Toe converge at zero wall height"
    )
    with pytest.raises(ValueError, match="No physically valid Design wall profiles"):
        enforce_profile_engineering_invariants(raw)


def test_equal_crest_and_toe_without_convergence_is_impossible() -> None:
    profile = _profile(
        component_index=0,
        crest_z=20.0,
        face_end_z=10.0,
        toe_z=20.0,
        toe_u=8.0,
    )

    assert (
        profile_vertical_order_issue(profile)
        == "Upper Crest and Lower Toe have equal elevation at nonzero width"
    )


def test_rising_face_is_invalid_even_without_explicit_toe() -> None:
    profile = _profile(
        component_index=0,
        crest_z=10.0,
        face_end_z=20.0,
        toe_z=None,
    )

    assert profile_vertical_order_issue(profile) == "Design Face rises with +U"
