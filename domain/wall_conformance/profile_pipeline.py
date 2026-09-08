"""Pure-domain composition of Wall Conformance v2 Phases 2B and 1.

The pipeline deliberately owns no corridor-discovery or profile-placement
geometry.  It validates Phase-2B ``WallSector`` eligibility, partitions an
eligible open sector by its assessed station intervals, adapts each interval
to the accepted Phase-1 API, and reports cross-layer horizontal invariants.

Assessment geometry remains a spatial mask.  Direction is supplied only by
the Design Face samples carried by ``WallSector``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import hypot, isfinite

from domain.geometry.surfaces import TriangleSurface
from domain.geometry.types import PlanPolygon
from domain.wall_conformance.design_topology import DesignTopologyIndex
from domain.wall_conformance.horizontal_invariants import (
    HorizontalInvariantDiagnostics,
    ProfileIntervalEvidence,
    validate_horizontal_profile_invariants,
)
from domain.wall_conformance.profile_placement import (
    FaceDirectionSample,
    ProfilePlacementSet,
    ProfileTrace,
    WallGuide,
    place_profile_traces,
)
from domain.wall_conformance.wall_sectors import (
    GuideStationMapping,
    StationInterval,
    WallSector,
    WallSectorExtractionResult,
    extract_wall_sectors,
)


_GEOMETRY_TOLERANCE = 1e-9
_CHAINAGE_TOLERANCE = 1e-8


@dataclass(frozen=True)
class ProfilePipelineDiagnostic:
    """One stable machine-readable integration diagnostic."""

    code: str
    message: str
    source: str = "profile_pipeline"


@dataclass(frozen=True)
class IntervalProfilePlacement:
    """Phase-1 result owned by exactly one assessed station interval."""

    interval_index: int
    interval: StationInterval
    profiles: tuple[ProfileTrace, ...]
    supported: bool
    phase1_result: ProfilePlacementSet | None
    diagnostics: tuple[ProfilePipelineDiagnostic, ...] = ()

    @property
    def diagnostic_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.diagnostics)


@dataclass(frozen=True)
class SectorProfilePlacement:
    """Integrated profile placements and diagnostics for one WallSector."""

    sector_id: str
    interval_results: tuple[IntervalProfilePlacement, ...]
    supported: bool
    invariants: HorizontalInvariantDiagnostics | None
    diagnostics: tuple[ProfilePipelineDiagnostic, ...] = ()

    @property
    def profiles(self) -> tuple[ProfileTrace, ...]:
        return tuple(
            profile
            for interval_result in self.interval_results
            for profile in interval_result.profiles
        )

    @property
    def diagnostic_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.diagnostics)


@dataclass(frozen=True)
class WallProfilePlacementResult:
    """Immutable Phase-3 output with partial success across wall sectors."""

    sector_results: tuple[SectorProfilePlacement, ...]
    diagnostics: tuple[ProfilePipelineDiagnostic, ...] = ()

    @property
    def supported_sector_results(self) -> tuple[SectorProfilePlacement, ...]:
        return tuple(result for result in self.sector_results if result.supported)

    @property
    def profiles(self) -> tuple[ProfileTrace, ...]:
        return tuple(
            profile
            for sector_result in self.sector_results
            for profile in sector_result.profiles
        )

    @property
    def diagnostic_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.diagnostics)

def _diagnostic(
    code: str,
    message: str,
    *,
    source: str = "profile_pipeline",
) -> ProfilePipelineDiagnostic:
    return ProfilePipelineDiagnostic(code, message, source)


def _phase2b_diagnostics(
    sector: WallSector,
) -> tuple[ProfilePipelineDiagnostic, ...]:
    return tuple(
        _diagnostic(
            code,
            f"Phase 2B marked sector {sector.sector_id!r} with {code!r}",
            source="phase2b",
        )
        for code in sector.diagnostics.codes
    )


def _interval_coverage_valid(
    intervals: tuple[StationInterval, ...],
) -> bool:
    if not intervals:
        return False
    return all(
        first.end_fraction < second.start_fraction - _GEOMETRY_TOLERANCE
        for first, second in zip(intervals, intervals[1:])
    )


def _sector_eligibility_diagnostics(
    sector: WallSector,
) -> tuple[ProfilePipelineDiagnostic, ...]:
    diagnostics = list(_phase2b_diagnostics(sector))
    if sector.closed_along_strike:
        diagnostics.append(_diagnostic(
            "periodic_profile_placement_deferred",
            "Closed wall sectors require periodic profile placement; Phase 1 was not called",
        ))
    if not sector.supported:
        diagnostics.append(_diagnostic(
            "unsupported_wall_sector",
            "Phase 2B did not classify this wall sector as supported",
        ))
    if sector.lower_guide is None and sector.downstream_extent is None:
        diagnostics.append(_diagnostic(
            "missing_downstream_constraint",
            "Profile placement requires either a Lower guide or a downstream extent",
        ))
    terminal_guide, terminal_mapping = _terminal_guide_and_mapping(sector)
    if terminal_guide is not None and terminal_mapping is None:
        diagnostics.append(_diagnostic(
            "missing_terminal_station_mapping",
            "Terminal guide has no transported corridor-station mapping",
        ))
    elif (
        terminal_guide is not None
        and terminal_mapping is not None
        and abs(
            terminal_mapping.chainages_m[-1] - terminal_guide.length_m
        ) > _CHAINAGE_TOLERANCE
    ):
        diagnostics.append(_diagnostic(
            "invalid_terminal_station_mapping",
            "Terminal station mapping does not span the terminal guide",
        ))
    elif (
        terminal_mapping is not None
        and sector.assessed_station_intervals
        and (
            terminal_mapping.station_fractions[0]
            > sector.assessed_station_intervals[0].start_fraction
            + _GEOMETRY_TOLERANCE
            or terminal_mapping.station_fractions[-1]
            < sector.assessed_station_intervals[-1].end_fraction
            - _GEOMETRY_TOLERANCE
        )
    ):
        diagnostics.append(_diagnostic(
            "incomplete_terminal_station_coverage",
            "Terminal guide does not cover every assessed station interval",
        ))
    if not _interval_coverage_valid(sector.assessed_station_intervals):
        diagnostics.append(_diagnostic(
            "invalid_assessed_interval_coverage",
            "Assessed station intervals must be non-empty, ordered, and disconnected",
        ))
    if not sector.face_direction_samples:
        diagnostics.append(_diagnostic(
            "missing_face_direction_samples",
            "Wall sector carries no Design Face direction evidence",
        ))
    return tuple(diagnostics)


def _eligible(sector: WallSector) -> bool:
    terminal_guide, terminal_mapping = _terminal_guide_and_mapping(sector)
    return (
        sector.supported
        and not sector.closed_along_strike
        and terminal_guide is not None
        and terminal_mapping is not None
        and abs(
            terminal_mapping.chainages_m[-1] - terminal_guide.length_m
        ) <= _CHAINAGE_TOLERANCE
        and _interval_coverage_valid(sector.assessed_station_intervals)
        and terminal_mapping.station_fractions[0]
        <= sector.assessed_station_intervals[0].start_fraction
        + _GEOMETRY_TOLERANCE
        and terminal_mapping.station_fractions[-1]
        >= sector.assessed_station_intervals[-1].end_fraction
        - _GEOMETRY_TOLERANCE
        and bool(sector.face_direction_samples)
    )


def _terminal_guide_and_mapping(
    sector: WallSector,
) -> tuple[WallGuide | None, GuideStationMapping | None]:
    if sector.lower_guide is not None:
        return sector.lower_guide, sector.lower_station_mapping
    return sector.downstream_extent, sector.downstream_station_mapping


def _cropped_guide(
    guide: WallGuide,
    start_chainage: float,
    end_chainage: float,
) -> WallGuide:
    chainages = guide.cumulative_chainages_m
    points = [guide.point_at(start_chainage)]
    points.extend(
        point
        for point, chainage in zip(guide.points, chainages, strict=True)
        if (
            start_chainage + _CHAINAGE_TOLERANCE
            < chainage
            < end_chainage - _CHAINAGE_TOLERANCE
        )
    )
    points.append(guide.point_at(end_chainage))
    deduplicated = [points[0]]
    for point in points[1:]:
        if hypot(
            point.x - deduplicated[-1].x,
            point.y - deduplicated[-1].y,
        ) > _GEOMETRY_TOLERANCE:
            deduplicated.append(point)
    return WallGuide(tuple(deduplicated), guide.kind)


def _interval_samples(
    samples: tuple[FaceDirectionSample, ...],
    interval: StationInterval,
) -> tuple[FaceDirectionSample, ...]:
    span = interval.end_fraction - interval.start_fraction
    return tuple(
        replace(
            sample,
            station_fraction=max(
                0.0,
                min(
                    1.0,
                    (sample.station_fraction - interval.start_fraction) / span,
                ),
            ),
        )
        for sample in samples
        if (
            interval.start_fraction - _GEOMETRY_TOLERANCE
            <= sample.station_fraction
            <= interval.end_fraction + _GEOMETRY_TOLERANCE
        )
    )


def _rebase_profiles(
    profiles: tuple[ProfileTrace, ...],
    *,
    interval: StationInterval,
    sector: WallSector,
    station_index_offset: int,
) -> tuple[ProfileTrace, ...]:
    downstream, downstream_mapping = _terminal_guide_and_mapping(sector)
    assert downstream is not None and downstream_mapping is not None
    upper_offset = sector.upper_guide.length_m * interval.start_fraction
    downstream_offset = downstream_mapping.chainage_at_station(
        interval.start_fraction
    )
    return tuple(
        replace(
            profile,
            station_index=station_index_offset + index,
            upper_chainage_m=upper_offset + profile.upper_chainage_m,
            lower_chainage_m=(
                downstream_offset + profile.lower_chainage_m
                if profile.lower_chainage_m is not None
                else None
            ),
            downstream_chainage_m=(
                downstream_offset + profile.downstream_chainage_m
            ),
        )
        for index, profile in enumerate(profiles)
    )


def place_wall_sector_profiles(
    sector: WallSector,
    assessment_polygon: PlanPolygon,
    *,
    requested_spacing_m: float,
) -> SectorProfilePlacement:
    """Adapt one supported open WallSector into interval-owned Phase-1 calls."""
    if requested_spacing_m <= 0.0 or not isfinite(requested_spacing_m):
        raise ValueError("Requested profile spacing must be positive")

    diagnostics = list(_sector_eligibility_diagnostics(sector))
    if not _eligible(sector):
        return SectorProfilePlacement(
            sector.sector_id,
            (),
            False,
            None,
            tuple(diagnostics),
        )

    interval_results = []
    profile_count = 0
    for interval_index, interval in enumerate(
        sector.assessed_station_intervals
    ):
        samples = _interval_samples(sector.face_direction_samples, interval)
        if not samples:
            interval_results.append(IntervalProfilePlacement(
                interval_index,
                interval,
                (),
                False,
                None,
                (_diagnostic(
                    "missing_interval_face_direction_samples",
                    "Assessed interval carries no local Design Face direction evidence",
                ),),
            ))
            continue
        upper_guide = _cropped_guide(
            sector.upper_guide,
            sector.upper_guide.length_m * interval.start_fraction,
            sector.upper_guide.length_m * interval.end_fraction,
        )
        terminal_guide, terminal_mapping = _terminal_guide_and_mapping(sector)
        assert terminal_guide is not None and terminal_mapping is not None
        terminal_subspan = _cropped_guide(
            terminal_guide,
            terminal_mapping.chainage_at_station(interval.start_fraction),
            terminal_mapping.chainage_at_station(interval.end_fraction),
        )
        lower_guide = (
            terminal_subspan
            if sector.lower_guide is not None
            else None
        )
        downstream_extent = (
            terminal_subspan
            if sector.lower_guide is None
            and sector.downstream_extent is not None
            else None
        )
        try:
            phase1_result = place_profile_traces(
                samples,
                upper_guide,
                assessment_polygon,
                requested_spacing_m=requested_spacing_m,
                lower_guide=lower_guide,
                downstream_extent=downstream_extent,
            )
        except ValueError as exc:
            interval_results.append(IntervalProfilePlacement(
                interval_index,
                interval,
                (),
                False,
                None,
                (_diagnostic(
                    "phase1_contract_mismatch",
                    f"Phase 1 rejected an eligible WallSector interval: {exc}",
                    source="phase1",
                ),),
            ))
            continue

        if not phase1_result.diagnostics.supported:
            interval_results.append(IntervalProfilePlacement(
                interval_index,
                interval,
                (),
                False,
                phase1_result,
                (_diagnostic(
                    "phase1_profile_placement_unsupported",
                    phase1_result.diagnostics.unsupported_reason
                    or "Phase 1 did not support this interval",
                    source="phase1",
                ),),
            ))
            continue
        profiles = _rebase_profiles(
            phase1_result.traces,
            interval=interval,
            sector=sector,
            station_index_offset=profile_count,
        )
        profile_count += len(profiles)
        interval_results.append(IntervalProfilePlacement(
            interval_index,
            interval,
            profiles,
            True,
            phase1_result,
        ))

    interval_results_tuple = tuple(interval_results)
    invariants = (
        validate_horizontal_profile_invariants(
            sector,
            tuple(
                ProfileIntervalEvidence(
                    result.interval,
                    result.profiles,
                    result.phase1_result.diagnostics,
                )
                for result in interval_results_tuple
                if result.supported and result.phase1_result is not None
            ),
            requested_spacing_m,
        )
        if any(result.profiles for result in interval_results_tuple)
        else None
    )
    all_intervals_supported = all(
        result.supported for result in interval_results_tuple
    )
    supported = (
        bool(interval_results_tuple)
        and all_intervals_supported
        and invariants is not None
        and invariants.all_valid
    )
    diagnostics.extend(
        diagnostic
        for result in interval_results_tuple
        for diagnostic in result.diagnostics
    )
    if invariants is not None:
        diagnostics.extend(
            _diagnostic(
                violation.code,
                violation.message,
                source="horizontal_invariants",
            )
            for violation in invariants.violations
        )
    if any(result.supported for result in interval_results_tuple) and not (
        all_intervals_supported
    ):
        diagnostics.append(_diagnostic(
            "partial_assessed_interval_success",
            "Profiles were retained only for the independently supported assessed intervals",
        ))
    if not any(result.profiles for result in interval_results_tuple):
        diagnostics.append(_diagnostic(
            "no_profiles_placed",
            "No valid profile was produced for this wall sector",
        ))
    return SectorProfilePlacement(
        sector.sector_id,
        interval_results_tuple,
        supported,
        invariants,
        tuple(diagnostics),
    )


def place_wall_sector_extraction_profiles(
    extraction_result: WallSectorExtractionResult,
    assessment_polygon: PlanPolygon,
    *,
    requested_spacing_m: float,
) -> WallProfilePlacementResult:
    """Place every eligible sector while preserving per-sector failures."""
    if requested_spacing_m <= 0.0 or not isfinite(requested_spacing_m):
        raise ValueError("Requested profile spacing must be positive")
    extraction_diagnostics = tuple(
        _diagnostic(
            code,
            (
                extraction_result.diagnostics.messages[index]
                if index < len(extraction_result.diagnostics.messages)
                else f"Phase 2B extraction diagnostic: {code}"
            ),
            source="phase2b",
        )
        for index, code in enumerate(extraction_result.diagnostics.codes)
    )
    return WallProfilePlacementResult(
        tuple(
            place_wall_sector_profiles(
                sector,
                assessment_polygon,
                requested_spacing_m=requested_spacing_m,
            )
            for sector in extraction_result.sectors
        ),
        extraction_diagnostics,
    )


def build_wall_profile_placements(
    surface: TriangleSurface,
    topology_index: DesignTopologyIndex,
    assessment_polygon: PlanPolygon,
    *,
    requested_spacing_m: float,
) -> WallProfilePlacementResult:
    """Compose Phase 2B extraction with Phase 1 placement for one Design TIN."""
    extraction_result = extract_wall_sectors(
        surface, topology_index, assessment_polygon
    )
    return place_wall_sector_extraction_profiles(
        extraction_result,
        assessment_polygon,
        requested_spacing_m=requested_spacing_m,
    )


__all__ = [
    "HorizontalInvariantDiagnostics",
    "IntervalProfilePlacement",
    "ProfilePipelineDiagnostic",
    "SectorProfilePlacement",
    "WallProfilePlacementResult",
    "build_wall_profile_placements",
    "place_wall_sector_extraction_profiles",
    "place_wall_sector_profiles",
]
