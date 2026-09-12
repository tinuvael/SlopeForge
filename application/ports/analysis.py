"""Read port used by the Analysis application service."""

from __future__ import annotations

from typing import Mapping, Protocol

from application.analysis.models import FilterChoice, FilterSpec, FilteredDataset, SortSpec


class AnalysisDatasetProvider(Protocol):
    def filter_options(self, dataset_id: str) -> Mapping[str, tuple[FilterChoice, ...]]: ...

    def load(
        self,
        filter_spec: FilterSpec,
        *,
        sort_spec: SortSpec | None,
        limit: int,
    ) -> FilteredDataset: ...
