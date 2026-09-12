"""Application boundary for dataset discovery, typed filtering, and row loading."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from numbers import Real

from application.analysis.catalog import ANALYSIS_DATASETS, dataset_by_id
from application.analysis.models import (
    AnalysisDataset,
    DatasetUnavailableError,
    FilterChoice,
    FilterCondition,
    FilterOperator,
    FilterSpec,
    FilteredDataset,
    SortSpec,
)
from application.ports.analysis import AnalysisDatasetProvider


class AnalysisDatasetService:
    DEFAULT_ROW_LIMIT = 5000

    def __init__(self, provider: AnalysisDatasetProvider):
        self.provider = provider

    def datasets(self) -> tuple[AnalysisDataset, ...]:
        return ANALYSIS_DATASETS

    def dataset(self, dataset_id: str) -> AnalysisDataset:
        try:
            return dataset_by_id(dataset_id)
        except KeyError as exc:
            raise LookupError(f"Unknown Analysis dataset: {dataset_id}") from exc

    def filter_options(self, dataset_id: str) -> dict[str, tuple[FilterChoice, ...]]:
        dataset = self.dataset(dataset_id)
        if not dataset.available:
            raise DatasetUnavailableError(dataset.unavailable_reason or dataset.label)
        return dict(self.provider.filter_options(dataset_id))

    def load(
        self,
        filter_spec: FilterSpec,
        *,
        sort_spec: SortSpec | None = None,
        limit: int | None = None,
    ) -> FilteredDataset:
        dataset = self.dataset(filter_spec.dataset_id)
        if not dataset.available:
            raise DatasetUnavailableError(dataset.unavailable_reason or dataset.label)
        for condition in filter_spec.conditions:
            self._validate_condition(dataset, condition)
        if sort_spec is not None:
            self._validate_sort(dataset, sort_spec)
        row_limit = self.DEFAULT_ROW_LIMIT if limit is None else int(limit)
        if row_limit <= 0:
            raise ValueError("Analysis row limit must be positive")
        return self.provider.load(filter_spec, sort_spec=sort_spec, limit=row_limit)

    @staticmethod
    def _validate_sort(dataset: AnalysisDataset, sort_spec: SortSpec) -> None:
        if not isinstance(sort_spec.ascending, bool):
            raise ValueError("Analysis sort direction must be boolean")
        try:
            field = dataset.field(sort_spec.field_key)
        except KeyError as exc:
            raise ValueError(f"Unknown Analysis sort field: {sort_spec.field_key}") from exc
        if not field.sortable:
            raise ValueError(f"Analysis field is not sortable: {sort_spec.field_key}")

    @staticmethod
    def _validate_condition(dataset: AnalysisDataset, condition: FilterCondition) -> None:
        try:
            field = dataset.field(condition.field_key)
        except KeyError as exc:
            raise ValueError(f"Unknown Analysis field: {condition.field_key}") from exc
        if not field.filterable or condition.operator not in field.supported_operators:
            raise ValueError(
                f"Filter operator {condition.operator} is not supported for {field.key}"
            )
        value = condition.value
        if condition.operator is FilterOperator.CATEGORICAL_IN:
            if not isinstance(value, (tuple, list, set, frozenset)) or not value:
                raise ValueError("Categorical IN filters require at least one value")
        elif condition.operator is FilterOperator.NUMERIC_RANGE:
            AnalysisDatasetService._validate_range(value, (Real, Decimal), "numeric")
        elif condition.operator is FilterOperator.DATE_RANGE:
            AnalysisDatasetService._validate_range(value, date, "date")

    @staticmethod
    def _validate_range(value: object, expected_type: type | tuple[type, ...], label: str) -> None:
        if not isinstance(value, tuple) or len(value) != 2:
            raise ValueError(f"{label.title()} range filters require a (minimum, maximum) tuple")
        lower, upper = value
        if lower is None and upper is None:
            raise ValueError(f"{label.title()} range filters cannot be empty")
        for item in (lower, upper):
            if item is not None and not isinstance(item, expected_type):
                raise ValueError(f"Invalid {label} range value")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError(f"{label.title()} range minimum cannot exceed maximum")
