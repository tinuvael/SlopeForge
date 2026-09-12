"""Read port used by the Analysis application service."""

from __future__ import annotations

from typing import Mapping, Protocol

from application.analysis.models import (
    AnalysisPopulationProjection,
    FilterChoice,
    FilterSpec,
    FilteredDataset,
    SortSpec,
)


class AnalysisDatasetProvider(Protocol):
    def filter_options(self, dataset_id: str) -> Mapping[str, tuple[FilterChoice, ...]]: ...

    def load(
        self,
        filter_spec: FilterSpec,
        *,
        sort_spec: SortSpec | None,
        limit: int,
    ) -> FilteredDataset: ...

    def project_population(
        self,
        filter_spec: FilterSpec,
        *,
        field_keys: tuple[str, ...],
        max_rows: int,
    ) -> AnalysisPopulationProjection: ...
