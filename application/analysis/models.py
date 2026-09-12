"""Framework-free metadata and state for engineering Analysis datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class AnalysisFieldType(StrEnum):
    IDENTIFIER = "identifier"
    TEXT = "text"
    CATEGORICAL = "categorical"
    NUMERIC = "numeric"
    DATE = "date"


class FilterOperator(StrEnum):
    CATEGORICAL_EQUALS = "categorical_equals"
    CATEGORICAL_IN = "categorical_in"
    NUMERIC_RANGE = "numeric_range"
    DATE_RANGE = "date_range"


@dataclass(frozen=True)
class AnalysisField:
    key: str
    label: str
    field_type: AnalysisFieldType
    nullable: bool = False
    unit: str | None = None
    sortable: bool = True
    filterable: bool = False
    default_visible: bool = True
    always_visible: bool = False
    supported_operators: tuple[FilterOperator, ...] = ()
    decimals: int | None = None
    format_hint: str | None = None
    source_role: str | None = None
    common_filter: bool = False
    groupable: bool = False

    @property
    def is_numeric(self) -> bool:
        return self.field_type is AnalysisFieldType.NUMERIC


@dataclass(frozen=True)
class SourceReference:
    entity_type: str
    entity_id: str
    site_id: int | None = None
    domain_id: int | None = None


@dataclass(frozen=True)
class AnalysisRow:
    """One stable statistical observation in an Analysis dataset."""

    identity: str
    values: Mapping[str, object | None]
    source: SourceReference | None = None


@dataclass(frozen=True)
class AnalysisDataset:
    dataset_id: str
    label: str
    row_semantics: str
    fields: tuple[AnalysisField, ...]
    available: bool = True
    unavailable_reason: str | None = None

    def field(self, key: str) -> AnalysisField:
        for item in self.fields:
            if item.key == key:
                return item
        raise KeyError(key)


@dataclass(frozen=True)
class FilterChoice:
    value: object
    label: str


@dataclass(frozen=True)
class FilterCondition:
    field_key: str
    operator: FilterOperator
    value: object


@dataclass(frozen=True)
class FilterSpec:
    dataset_id: str
    conditions: tuple[FilterCondition, ...] = ()

    @property
    def active_count(self) -> int:
        return len(self.conditions)


@dataclass(frozen=True)
class SortSpec:
    field_key: str
    ascending: bool = True


@dataclass(frozen=True)
class FilteredDataset:
    dataset: AnalysisDataset
    rows: tuple[AnalysisRow, ...] = ()
    total_count: int = 0
    filtered_count: int = 0
    truncated: bool = False
    query_metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisPopulationProjection:
    """Complete filtered numeric/category columns used by statistical services.

    This projection is deliberately separate from ``FilteredDataset.rows`` so
    bounded Data-table loading can never become an accidental statistics input.
    """

    dataset: AnalysisDataset
    matching_count: int
    columns: Mapping[str, tuple[object | None, ...]] = field(default_factory=dict)

    def column(self, field_key: str) -> tuple[object | None, ...]:
        try:
            return self.columns[field_key]
        except KeyError as exc:
            raise KeyError(f"Population projection does not include {field_key}") from exc


class DatasetUnavailableError(LookupError):
    """Raised when a staged Analysis dataset is selected before implementation."""


class PopulationLimitExceededError(RuntimeError):
    """Raised before loading a statistical population that is unsafe in memory."""

    def __init__(self, matching_count: int, limit: int):
        self.matching_count = int(matching_count)
        self.limit = int(limit)
        super().__init__(
            f"Filtered population has {self.matching_count} records; "
            f"the safe analysis limit is {self.limit}"
        )
