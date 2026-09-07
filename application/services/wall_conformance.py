from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from domain.geometry.types import PlanPolygon
from domain.wall_conformance import (
    AlignmentPlacementDiagnostic,
    AlignmentProfileSectionResult,
    PROTOTYPE_DESIGN_ROLE_MAPPING,
    SurfaceRoleMapping,
    WallAlignment,
    WallMeasurementSummary,
    WallProfileMeasurements,
    aggregate_measurements,
    build_alignment_profile_sections,
    has_compatible_actual_wall_section,
    measure_profiles,
    semantic_value_token,
)


class WallConformanceUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssessmentGeometryMeasurementInputs:
    """Mapped Wall Conformance Q2 values for the Assessment Geometry form.

    ``None`` deliberately means the corresponding independently aggregated KPI
    is unavailable.  It must not be converted to a zero in the UI because a
    zero is a meaningful "design met" assessment input.
    """

    bench_angle_shortfall_deg: float | None
    berm_width_deficit_m: float | None
    toe_offset_from_design_m: float | None
    mean_backbreak_m: float | None = None
    maximum_backbreak_m: float | None = None
    mean_overbreak_m: float | None = None
    mean_underbreak_m: float | None = None
    contour_rms_deviation_m: float | None = None

    @property
    def has_available_value(self) -> bool:
        return any(value is not None for value in (
            self.bench_angle_shortfall_deg,
            self.berm_width_deficit_m,
            self.toe_offset_from_design_m,
            self.mean_backbreak_m,
            self.maximum_backbreak_m,
            self.mean_overbreak_m,
            self.mean_underbreak_m,
            self.contour_rms_deviation_m,
        ))


def assessment_geometry_inputs_from_measurement_summary(
    measurement_summary: WallMeasurementSummary | None,
) -> AssessmentGeometryMeasurementInputs:
    """Map the independent Wall Conformance Q2 aggregates to Assessment inputs.

    Wall Conformance reports signed Actual-minus-Design angle and upper-berm
    deviations, whereas Assessment scores positive shortfalls/deficits.  Toe
    remains signed because Assessment applies its existing absolute-value
    scoring rule later.
    """
    if measurement_summary is None:
        return AssessmentGeometryMeasurementInputs(None, None, None)

    def median(name: str) -> float | None:
        aggregate = getattr(measurement_summary, name, None)
        value = getattr(aggregate, "median", None)
        return None if value is None else float(value)

    angle_deviation = median("angle_deviation_deg")
    berm_deviation = median("upper_berm_width_deviation_m")
    toe_offset = median("toe_signed_offset_u_m")
    additional = getattr(measurement_summary, "additional_geometry", None)
    backbreak = getattr(additional, "backbreak_m", None)
    return AssessmentGeometryMeasurementInputs(
        bench_angle_shortfall_deg=(
            None if angle_deviation is None else max(0.0, -angle_deviation)
        ),
        berm_width_deficit_m=(
            None if berm_deviation is None else max(0.0, -berm_deviation)
        ),
        toe_offset_from_design_m=toe_offset,
        mean_backbreak_m=getattr(backbreak, "mean", None),
        maximum_backbreak_m=getattr(backbreak, "maximum", None),
        mean_overbreak_m=getattr(additional, "mean_overbreak_m", None),
        mean_underbreak_m=getattr(additional, "mean_underbreak_m", None),
        contour_rms_deviation_m=getattr(additional, "contour_rms_deviation_m", None),
    )


def compatible_actual_profile_count(result) -> int | None:
    """Return existing Wall Conformance coverage, when a full result is available."""
    profiles = getattr(getattr(result, "profile_sections", None), "profiles", None)
    measurements = getattr(result, "measurements", None)
    if profiles is None or measurements is None or len(profiles) != len(measurements):
        return None
    return sum(
        has_compatible_actual_wall_section(profile, measurement)
        for profile, measurement in zip(profiles, measurements)
    )


@dataclass(frozen=True)
class WallConformanceDiagnosticSettings:
    spacing_m: float = 3.0


@dataclass(frozen=True)
class WallConformanceDiagnosticResult:
    design_dataset: Any
    actual_dataset: Any
    wall_alignment: WallAlignment
    profile_sections: AlignmentProfileSectionResult
    settings: WallConformanceDiagnosticSettings
    role_mapping: SurfaceRoleMapping
    mapping_is_fallback: bool
    diagnostics: tuple[AlignmentPlacementDiagnostic, ...] = ()
    measurements: tuple[WallProfileMeasurements, ...] = ()
    measurement_summary: WallMeasurementSummary | None = None


@dataclass(frozen=True)
class SurfaceAttributeValueCount:
    value: object
    triangle_count: int


@dataclass(frozen=True)
class DesignSemanticInspection:
    dataset: Any
    attribute_values: dict[str, tuple[SurfaceAttributeValueCount, ...]]
    mapping: SurfaceRoleMapping
    is_fallback: bool


class WallConformanceDiagnosticService:
    """Orchestrate diagnostic geometry and Design-revision semantics.

    It persists only source-semantic metadata. Diagnostic results remain
    read-only and deterministic geometry stays in ``domain.wall_conformance``.
    """

    def __init__(self, surface_service):
        self.surface_service = surface_service

    def current_datasets(self, site_id: int) -> tuple[Any | None, Any | None]:
        return (
            self.surface_service.current(site_id, "design"),
            self.surface_service.current(site_id, "actual"),
        )

    @staticmethod
    def mapping_for_dataset(dataset) -> tuple[SurfaceRoleMapping, bool]:
        payload = getattr(dataset, "semantic_mapping_json", None)
        if payload:
            return SurfaceRoleMapping.from_dict(payload), False
        return PROTOTYPE_DESIGN_ROLE_MAPPING, True

    def inspect_design_semantics(self, site_id: int) -> DesignSemanticInspection:
        loaded = self.surface_service.load_current(site_id, "design")
        if loaded is None:
            raise WallConformanceUnavailableError(
                "No active Design surface is configured for this Project."
            )
        dataset, imported = loaded
        surface = getattr(imported, "surface", None)
        if surface is None:
            raise WallConformanceUnavailableError(
                "The active Design surface does not contain triangulated geometry."
            )
        attribute_names = sorted({
            str(key)
            for triangle in surface.triangles
            for key in triangle.source_attributes
        })
        counts: dict[str, dict[str, list[object]]] = {name: {} for name in attribute_names}
        for triangle in surface.triangles:
            attributes = {str(key): value for key, value in triangle.source_attributes.items()}
            for name in attribute_names:
                value = attributes.get(name, "<missing>")
                token = semantic_value_token(value)
                entry = counts.setdefault(name, {}).setdefault(token, [value, 0])
                entry[1] = int(entry[1]) + 1
        values = {
            name: tuple(
                SurfaceAttributeValueCount(item[0], int(item[1]))
                for _, item in sorted(grouped.items())
            )
            for name, grouped in sorted(counts.items())
        }
        mapping, fallback = self.mapping_for_dataset(dataset)
        return DesignSemanticInspection(dataset, values, mapping, fallback)

    def save_design_semantics(
        self, site_id: int, logical_id: str, mapping: SurfaceRoleMapping
    ):
        if not any(role == "face" for _, role in mapping.assignments):
            raise ValueError("Map at least one source value to Face before saving.")
        return self.surface_service.save_design_semantic_mapping(
            site_id, logical_id, mapping
        )

    def calculate_current(
        self,
        site_id: int,
        assessment_polygon: PlanPolygon,
        wall_alignment: WallAlignment,
        settings: WallConformanceDiagnosticSettings | None = None,
    ) -> WallConformanceDiagnosticResult:
        settings = settings or WallConformanceDiagnosticSettings()
        if wall_alignment is None:
            raise WallConformanceUnavailableError(
                "Define a Wall Alignment before calculating profiles."
            )
        design_dataset, actual_dataset = self.current_datasets(site_id)
        if design_dataset is None:
            raise WallConformanceUnavailableError(
                "No active Design surface is configured for this Project."
            )
        if actual_dataset is None:
            raise WallConformanceUnavailableError(
                "No active Actual survey is configured for this Project."
            )
        if not bool(getattr(self.surface_service, "storage_available", True)):
            raise WallConformanceUnavailableError(
                "Shared file storage is unavailable for this connection. "
                "Surface metadata can be viewed, but wall conformance cannot be calculated."
            )

        _, design_import = self.surface_service.load_dataset(
            site_id, design_dataset.logical_id
        )
        _, actual_import = self.surface_service.load_dataset(
            site_id, actual_dataset.logical_id
        )
        design_surface = getattr(design_import, "surface", None)
        actual_surface = getattr(actual_import, "surface", None)
        if design_surface is None or actual_surface is None:
            raise WallConformanceUnavailableError(
                "The active Project surface datasets do not contain triangulated geometry."
            )

        role_mapping, mapping_is_fallback = self.mapping_for_dataset(design_dataset)
        try:
            assembly_result = build_alignment_profile_sections(
                alignment=wall_alignment,
                design_surface=design_surface,
                actual_surface=actual_surface,
                assessment_polygon=assessment_polygon,
                role_mapping=role_mapping,
                spacing_m=settings.spacing_m,
            )
        except ValueError as exc:
            raise WallConformanceUnavailableError(
                "No usable Design wall profiles could be assembled for this "
                "Assessment Area. Check the Wall Alignment coverage and Design "
                "surface semantics."
            ) from exc
        if not assembly_result.profiles:
            raise WallConformanceUnavailableError(
                "No usable Design wall profiles could be assembled for this "
                "Wall Alignment. Check its coverage and Design surface semantics."
            )
        measurements = measure_profiles(assembly_result.profiles)
        return WallConformanceDiagnosticResult(
            design_dataset=design_dataset,
            actual_dataset=actual_dataset,
            wall_alignment=wall_alignment,
            profile_sections=assembly_result,
            settings=settings,
            role_mapping=role_mapping,
            mapping_is_fallback=mapping_is_fallback,
            diagnostics=assembly_result.diagnostics,
            measurements=measurements,
            measurement_summary=aggregate_measurements(
                measurements, assembly_result.profiles,
            ),
        )
