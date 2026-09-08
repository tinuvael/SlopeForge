"""Engineering invariants for Design-derived wall-conformance profiles.

These checks are intentionally independent of the heuristic used to discover
candidate crest chains.  A candidate may look locally plausible at ``U=0`` but
still be an internal bench transition when a complex/fragmented TIN causes the
rest of the connected section to climb to a higher bench.  Such geometry must
never reach the diagnostic UI as an external-wall profile.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import logging
from math import hypot

from .models import TransverseProfile, WallProfileSet
from .semantic_sections import build_design_variants


logger = logging.getLogger(__name__)

_VERTICAL_TOLERANCE_M = 1e-5
_CONVERGENCE_PLAN_TOLERANCE_M = 1e-4


def profile_vertical_order_issue(
    profile: TransverseProfile,
    *,
    tolerance_m: float = _VERTICAL_TOLERANCE_M,
) -> str | None:
    """Return why a profile violates open-pit crest/toe ordering, if it does.

    ``+U`` is defined as down-wall.  Therefore every evaluated Design Face must
    descend (or, within numerical tolerance, remain level) as U increases.  An
    explicit Lower Toe may never lie above the sampled Upper Crest.  Equal
    crest/toe elevation is not a useful section either: physically that is only
    admissible at the point where the two boundaries converge, where the wall
    height is zero and a transverse profile should not be generated.
    """
    section = profile.design_section
    if section is None or not section.elements:
        return "missing evaluated Design section"

    faces = tuple(
        element
        for element in section.elements
        if element.role == "face" and element.horizontal_width > tolerance_m
    )
    if not faces:
        return "evaluated Design section has no Face"

    if any(element.vertical_change > tolerance_m for element in faces):
        return "Design Face rises with +U"

    toe = profile.external_toe
    if toe is None:
        return None

    crest = profile.alignment.origin
    elevation_drop = crest.z - toe.z
    if elevation_drop < -tolerance_m:
        return "Lower Toe is above Upper Crest"

    if elevation_drop <= tolerance_m:
        plan_distance = hypot(toe.x - crest.x, toe.y - crest.y)
        if plan_distance <= _CONVERGENCE_PLAN_TOLERANCE_M:
            return "Upper Crest and Lower Toe converge at zero wall height"
        return "Upper Crest and Lower Toe have equal elevation at nonzero width"

    return None


def enforce_profile_engineering_invariants(
    profile_set: WallProfileSet,
) -> WallProfileSet:
    """Remove impossible external-wall profiles and rebuild dependent metadata.

    Candidate discovery is deliberately permissive so irregular real TINs do
    not lose a true Upper Crest too early.  This function is the final physical
    gate: an internal/reversed candidate cannot survive merely because its first
    adjacent Face happened to descend locally.
    """
    checked = tuple(
        (profile, profile_vertical_order_issue(profile))
        for profile in profile_set.profiles
    )
    retained = tuple(profile for profile, issue in checked if issue is None)
    rejected = tuple((profile, issue) for profile, issue in checked if issue is not None)
    if rejected:
        reasons = Counter(issue for _, issue in rejected)
        logger.warning(
            "Rejected %d/%d impossible Design wall profiles by engineering "
            "invariants: %s",
            len(rejected),
            len(checked),
            ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items())),
        )
        for profile, issue in rejected:
            logger.debug(
                "Rejected wall profile component=%d chainage=%.3f "
                "crest=(%.3f, %.3f, %.3f) reason=%s",
                profile.alignment.boundary_component_index,
                profile.alignment.chainage_m,
                profile.alignment.origin.x,
                profile.alignment.origin.y,
                profile.alignment.origin.z,
                issue,
            )
    if not retained:
        raise ValueError(
            "No physically valid Design wall profiles remain: Upper Crest must "
            "be above Lower Toe and Design Faces must descend with +U"
        )

    used_components = tuple(sorted({
        profile.alignment.boundary_component_index for profile in retained
    }))
    component_map = {
        old_index: new_index for new_index, old_index in enumerate(used_components)
    }

    remapped = tuple(
        replace(
            profile,
            alignment=replace(
                profile.alignment,
                boundary_component_index=component_map[
                    profile.alignment.boundary_component_index
                ],
            ),
        )
        for profile in retained
    )
    crest_lines = tuple(
        profile_set.crest_lines[index]
        for index in used_components
        if 0 <= index < len(profile_set.crest_lines)
    )
    if not crest_lines:
        raise ValueError("No physically valid Design Upper Crest component remains")

    # Re-evaluate which displayed Lower Toe components are actually observed by
    # the retained profiles.  Import locally to avoid making the engine depend
    # on this final invariant layer.
    from .engine import _external_toe_lines

    toe_lines = _external_toe_lines(remapped, profile_set.toe_lines)
    return WallProfileSet(
        crest_lines=crest_lines,
        toe_lines=toe_lines,
        profiles=remapped,
        design_variants=build_design_variants(remapped),
    )
