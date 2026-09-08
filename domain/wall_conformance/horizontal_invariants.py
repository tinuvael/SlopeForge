"""Cross-layer horizontal validity checks for integrated profile families."""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from domain.geometry.types import PlanPoint
from domain.wall_conformance.profile_placement import (
    PlacementDiagnostics,
    ProfileTrace,
)
from domain.wall_conformance.wall_sectors import StationInterval, WallSector


_GEOMETRY_TOLERANCE = 1e-9
_CHAINAGE_TOLERANCE = 1e-8


@dataclass(frozen=True)
class HorizontalInvariantViolation:
    code: str
    message: str


@dataclass(frozen=True)
class HorizontalInvariantDiagnostics:
    """Geometry validity checks; this is not a wall-quality score."""

    transversality_valid: bool
    order_preserved: bool
    non_crossing: bool
    spacing_within_bound: bool
    positive_corridor_width: bool
    assessment_interval_ownership: bool
    max_upper_spacing_m: float
    max_lower_spacing_m: float | None
    max_downstream_spacing_m: float
    violations: tuple[HorizontalInvariantViolation, ...] = ()

    @property
    def all_valid(self) -> bool:
        return (
            self.transversality_valid
            and self.order_preserved
            and self.non_crossing
            and self.spacing_within_bound
            and self.positive_corridor_width
            and self.assessment_interval_ownership
        )

    @property
    def diagnostic_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.violations)


@dataclass(frozen=True)
class ProfileIntervalEvidence:
    interval: StationInterval
    profiles: tuple[ProfileTrace, ...]
    phase1_diagnostics: PlacementDiagnostics


def _strictly_increasing(values: tuple[float, ...]) -> bool:
    return all(
        second - first > _CHAINAGE_TOLERANCE
        for first, second in zip(values, values[1:])
    )


def _properly_cross(
    first: tuple[PlanPoint, PlanPoint],
    second: tuple[PlanPoint, PlanPoint],
) -> bool:
    a, b = first
    c, d = second
    ray = b.x - a.x, b.y - a.y
    other = d.x - c.x, d.y - c.y
    denominator = ray[0] * other[1] - ray[1] * other[0]
    if abs(denominator) <= _GEOMETRY_TOLERANCE:
        return False
    offset = c.x - a.x, c.y - a.y
    first_fraction = (
        offset[0] * other[1] - offset[1] * other[0]
    ) / denominator
    second_fraction = (
        offset[0] * ray[1] - offset[1] * ray[0]
    ) / denominator
    return (
        _GEOMETRY_TOLERANCE
        < first_fraction
        < 1.0 - _GEOMETRY_TOLERANCE
        and _GEOMETRY_TOLERANCE
        < second_fraction
        < 1.0 - _GEOMETRY_TOLERANCE
    )


def validate_horizontal_profile_invariants(
    sector: WallSector,
    interval_evidence: tuple[ProfileIntervalEvidence, ...],
    requested_spacing_m: float,
) -> HorizontalInvariantDiagnostics:
    """Validate only retained, interval-owned Phase-1 profile families."""
    profiles = tuple(
        profile
        for evidence in interval_evidence
        for profile in evidence.profiles
    )
    phase1_diagnostics = tuple(
        evidence.phase1_diagnostics for evidence in interval_evidence
    )
    transversality = bool(profiles) and all(
        item.transversality_valid for item in phase1_diagnostics
    ) and all(profile.transversality_valid for profile in profiles)
    order_preserved = (
        bool(profiles)
        and _strictly_increasing(tuple(
            profile.upper_chainage_m for profile in profiles
        ))
        and _strictly_increasing(tuple(
            profile.downstream_chainage_m for profile in profiles
        ))
    )
    non_crossing = bool(profiles) and all(
        not _properly_cross(
            (first.plan_start, first.plan_end),
            (second.plan_start, second.plan_end),
        )
        for evidence in interval_evidence
        for first, second in zip(evidence.profiles, evidence.profiles[1:])
    )
    max_upper_spacing = max(
        (item.max_upper_spacing_m for item in phase1_diagnostics),
        default=0.0,
    )
    lower_spacings = tuple(
        item.max_lower_spacing_m
        for item in phase1_diagnostics
        if item.max_lower_spacing_m is not None
    )
    max_lower_spacing = max(lower_spacings) if lower_spacings else None
    max_downstream_spacing = max(
        (item.max_downstream_spacing_m for item in phase1_diagnostics),
        default=0.0,
    )
    spacing_within_bound = bool(profiles) and all(
        item.spacing_within_bound
        and item.max_upper_spacing_m
        <= requested_spacing_m + _CHAINAGE_TOLERANCE
        and item.max_downstream_spacing_m
        <= requested_spacing_m + _CHAINAGE_TOLERANCE
        for item in phase1_diagnostics
    )
    positive_width = bool(profiles) and all(
        hypot(
            profile.plan_end.x - profile.plan_start.x,
            profile.plan_end.y - profile.plan_start.y,
        ) > _GEOMETRY_TOLERANCE
        for profile in profiles
    )
    ownership = bool(profiles) and all(
        evidence.interval.start_fraction - _GEOMETRY_TOLERANCE
        <= profile.upper_chainage_m / sector.upper_guide.length_m
        <= evidence.interval.end_fraction + _GEOMETRY_TOLERANCE
        for evidence in interval_evidence
        for profile in evidence.profiles
    )

    failures = []
    checks = (
        (
            transversality,
            "transversality_invalid",
            "Profiles are not transverse to local Design Face evidence",
        ),
        (
            order_preserved,
            "profile_order_not_preserved",
            "Profile correspondence is not monotone on both guides",
        ),
        (
            non_crossing,
            "adjacent_profiles_cross",
            "Adjacent ordinary profiles cross inside an assessed interval",
        ),
        (
            spacing_within_bound,
            "profile_spacing_bound_exceeded",
            "Requested spacing is exceeded on a corridor guide",
        ),
        (
            positive_width,
            "non_positive_corridor_width",
            "A retained profile has zero corridor width",
        ),
        (
            ownership,
            "profile_outside_assessed_interval",
            "A retained profile has no assessed interval owner",
        ),
    )
    for valid, code, message in checks:
        if not valid:
            failures.append(HorizontalInvariantViolation(code, message))
    return HorizontalInvariantDiagnostics(
        transversality,
        order_preserved,
        non_crossing,
        spacing_within_bound,
        positive_width,
        ownership,
        max_upper_spacing,
        max_lower_spacing,
        max_downstream_spacing,
        tuple(failures),
    )


__all__ = [
    "HorizontalInvariantDiagnostics",
    "HorizontalInvariantViolation",
    "ProfileIntervalEvidence",
    "validate_horizontal_profile_invariants",
]
