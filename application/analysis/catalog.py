"""Canonical dataset and field definitions for the Analysis workspace."""

from __future__ import annotations

from .models import AnalysisDataset, AnalysisField, AnalysisFieldType, FilterOperator


EQUALS = (FilterOperator.CATEGORICAL_EQUALS, FilterOperator.CATEGORICAL_IN)
RANGE = (FilterOperator.NUMERIC_RANGE,)
DATE_RANGE = (FilterOperator.DATE_RANGE,)


def assessment_results_dataset() -> AnalysisDataset:
    return AnalysisDataset(
        dataset_id="assessment_results",
        label="Assessment results",
        row_semantics=(
            "One row is the active stored completed Assessment evaluation revision "
            "for one active Assessment Area and its active geometry revision."
        ),
        fields=(
            AnalysisField("project", "Project", AnalysisFieldType.CATEGORICAL,
                          filterable=True, always_visible=True,
                          supported_operators=EQUALS, source_role="project",
                          common_filter=True, groupable=True),
            AnalysisField("domain", "Domain", AnalysisFieldType.CATEGORICAL,
                          filterable=True, always_visible=True,
                          supported_operators=EQUALS, source_role="domain",
                          common_filter=True, groupable=True),
            AnalysisField("assessment_area", "Assessment Area", AnalysisFieldType.CATEGORICAL,
                          filterable=True, always_visible=True,
                          supported_operators=EQUALS, source_role="entity"),
            AnalysisField("elevation_interval", "Elevation interval", AnalysisFieldType.TEXT,
                          nullable=True, sortable=False),
            AnalysisField("min_elevation_m", "Minimum elevation", AnalysisFieldType.NUMERIC,
                          nullable=True, unit="m", filterable=True, default_visible=False,
                          supported_operators=RANGE, decimals=2),
            AnalysisField("max_elevation_m", "Maximum elevation", AnalysisFieldType.NUMERIC,
                          nullable=True, unit="m", filterable=True, default_visible=False,
                          supported_operators=RANGE, decimals=2),
            AnalysisField("assessment_date", "Evaluation date", AnalysisFieldType.DATE,
                          nullable=True, filterable=True, supported_operators=DATE_RANGE),
            AnalysisField("dai", "DAI", AnalysisFieldType.NUMERIC, nullable=True,
                          filterable=True, supported_operators=RANGE, decimals=3),
            AnalysisField("fci", "FCI", AnalysisFieldType.NUMERIC, nullable=True,
                          filterable=True, supported_operators=RANGE, decimals=3),
            AnalysisField("result_quadrant", "Result", AnalysisFieldType.CATEGORICAL,
                          nullable=True, filterable=True, supported_operators=EQUALS,
                          format_hint="assessment_result", groupable=True),
            AnalysisField("inspector", "Inspector", AnalysisFieldType.CATEGORICAL,
                          nullable=True, filterable=True, default_visible=False,
                          supported_operators=EQUALS, groupable=True),
            AnalysisField("evaluation_revision_id", "Evaluation revision ID",
                          AnalysisFieldType.IDENTIFIER, default_visible=False,
                          source_role="row_identity"),
        ),
    )


def _staged(dataset_id: str, label: str, row_semantics: str) -> AnalysisDataset:
    return AnalysisDataset(
        dataset_id=dataset_id,
        label=label,
        row_semantics=row_semantics,
        fields=(),
        available=False,
        unavailable_reason="This Analysis dataset is not available yet.",
    )


ANALYSIS_DATASETS = (
    assessment_results_dataset(),
    _staged(
        "production_technical_cards",
        "Production blast events / Technical Cards",
        "A future row will represent one explicitly selected current Production event/card observation.",
    ),
    _staged(
        "contour_blast_events",
        "Contour blast events",
        "A future row will represent one current Contour blast observation.",
    ),
    _staged(
        "drillhole_qa",
        "Drillhole QA",
        "A future row will represent one drillhole QA observation.",
    ),
    _staged(
        "wall_conformance_profiles",
        "Wall Conformance profiles",
        "A future row will represent one transverse Wall Conformance profile.",
    ),
)


def dataset_by_id(dataset_id: str) -> AnalysisDataset:
    for dataset in ANALYSIS_DATASETS:
        if dataset.dataset_id == dataset_id:
            return dataset
    raise KeyError(dataset_id)
