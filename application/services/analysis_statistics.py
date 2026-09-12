"""Deterministic statistics over complete filtered Analysis projections."""

from __future__ import annotations

from collections import defaultdict
import math
from typing import Iterable

import numpy as np

from application.analysis.models import (
    AnalysisField,
    AnalysisPopulationProjection,
    FilterSpec,
)
from application.analysis.statistics_models import (
    EcdfResult,
    GroupedComparisonResult,
    GroupStatistics,
    HistogramResult,
    NumericSummary,
    SummaryStatisticsResult,
    TukeyBoxResult,
)
from application.services.analysis import AnalysisDatasetService


_PERCENTILES = (5.0, 10.0, 25.0, 50.0, 75.0, 90.0, 95.0)


class AnalysisStatisticsService:
    """Application service owning all numerical Analysis definitions."""

    DEFAULT_MAX_GROUPS = 24

    def __init__(self, dataset_service: AnalysisDatasetService):
        self.dataset_service = dataset_service

    def load_population(self, filter_spec: FilterSpec) -> AnalysisPopulationProjection:
        dataset = self.dataset_service.dataset(filter_spec.dataset_id)
        field_keys = tuple(
            field.key
            for field in dataset.fields
            if field.is_numeric or field.groupable
        )
        return self.dataset_service.project_population(filter_spec, field_keys)

    def summary(
        self,
        population: AnalysisPopulationProjection,
        field_keys: Iterable[str],
    ) -> SummaryStatisticsResult:
        parameters = tuple(
            self._numeric_summary(population, field_key)
            for field_key in dict.fromkeys(field_keys)
        )
        return SummaryStatisticsResult(population.matching_count, parameters)

    def histogram(
        self, population: AnalysisPopulationProjection, field_key: str
    ) -> HistogramResult:
        self._require_numeric_field(population, field_key)
        values, missing = self._finite_values(population.column(field_key))
        if not values:
            return HistogramResult(field_key, 0, missing, (), (), (), None, None)
        array = np.asarray(values, dtype=float)
        edges = np.histogram_bin_edges(array, bins="auto")
        counts, _ = np.histogram(array, bins=edges)
        frequencies = counts.astype(float) * (100.0 / len(array))
        return HistogramResult(
            field_key=field_key,
            valid_n=len(values),
            missing_count=missing,
            bin_edges=tuple(float(value) for value in edges),
            counts=tuple(int(value) for value in counts),
            frequencies_percent=tuple(float(value) for value in frequencies),
            mean=float(np.mean(array)),
            median=float(np.quantile(array, 0.5, method="linear")),
        )

    def ecdf(
        self, population: AnalysisPopulationProjection, field_key: str
    ) -> EcdfResult:
        self._require_numeric_field(population, field_key)
        values, missing = self._finite_values(population.column(field_key))
        ordered = np.sort(np.asarray(values, dtype=float)) if values else np.asarray([])
        proportions = np.arange(1, len(ordered) + 1, dtype=float) / len(ordered) if len(ordered) else np.asarray([])
        return EcdfResult(
            field_key,
            len(ordered),
            missing,
            tuple(float(value) for value in ordered),
            tuple(float(value) for value in proportions),
        )

    def box(
        self, population: AnalysisPopulationProjection, field_key: str
    ) -> TukeyBoxResult:
        self._require_numeric_field(population, field_key)
        values, missing = self._finite_values(population.column(field_key))
        return self._box_from_values(field_key, values, missing)

    def grouped(
        self,
        population: AnalysisPopulationProjection,
        field_key: str,
        group_field_key: str,
        *,
        max_groups: int | None = None,
    ) -> GroupedComparisonResult:
        self._require_numeric_field(population, field_key)
        try:
            group_field = population.dataset.field(group_field_key)
        except KeyError as exc:
            raise ValueError(
                f"Unknown Analysis grouping field: {group_field_key}"
            ) from exc
        if not group_field.groupable:
            raise ValueError(
                f"Analysis field is not groupable: {group_field_key}"
            )
        numeric = population.column(field_key)
        categories = population.column(group_field_key)
        if len(numeric) != len(categories):
            raise ValueError("Projected Analysis columns have inconsistent lengths")
        grouped_values: dict[object | None, list[object | None]] = defaultdict(list)
        for group_value, value in zip(categories, numeric, strict=True):
            grouped_values[group_value].append(value)
        ordered_keys = sorted(
            grouped_values,
            key=lambda value: (value is None, str(value).casefold() if value is not None else ""),
        )
        limit = self.DEFAULT_MAX_GROUPS if max_groups is None else int(max_groups)
        if limit <= 0:
            raise ValueError("Maximum group count must be positive")
        if len(ordered_keys) > limit:
            return GroupedComparisonResult(
                field_key,
                group_field_key,
                population.matching_count,
                (),
                len(ordered_keys),
                True,
            )
        groups: list[GroupStatistics] = []
        for group_value in ordered_keys:
            raw = grouped_values[group_value]
            values, missing = self._finite_values(raw)
            array = np.asarray(values, dtype=float)
            groups.append(
                GroupStatistics(
                    group_value=group_value,
                    group_count=len(raw),
                    valid_n=len(values),
                    missing_count=missing,
                    mean=float(np.mean(array)) if len(array) else None,
                    median=float(np.quantile(array, 0.5, method="linear")) if len(array) else None,
                    p90=float(np.quantile(array, 0.9, method="linear")) if len(array) else None,
                    sample_std_dev=float(np.std(array, ddof=1)) if len(array) >= 2 else None,
                    box=self._box_from_values(field_key, values, missing),
                )
            )
        return GroupedComparisonResult(
            field_key,
            group_field_key,
            population.matching_count,
            tuple(groups),
            len(groups),
            False,
        )

    def _numeric_summary(
        self, population: AnalysisPopulationProjection, field_key: str
    ) -> NumericSummary:
        field = self._require_numeric_field(population, field_key)
        values, missing = self._finite_values(population.column(field_key))
        if not values:
            return NumericSummary(
                field_key, field.unit, population.matching_count, 0, missing,
                None, None, None, None, None, None, None, None, None, None,
                None, None, None,
            )
        array = np.asarray(values, dtype=float)
        p5, p10, q1, median, q3, p90, p95 = (
            float(value)
            for value in np.quantile(array, np.asarray(_PERCENTILES) / 100.0, method="linear")
        )
        return NumericSummary(
            field_key=field_key,
            unit=field.unit,
            matching_count=population.matching_count,
            valid_n=len(values),
            missing_count=missing,
            mean=float(np.mean(array)),
            sample_std_dev=float(np.std(array, ddof=1)) if len(values) >= 2 else None,
            sample_variance=float(np.var(array, ddof=1)) if len(values) >= 2 else None,
            minimum=float(np.min(array)),
            p5=p5,
            p10=p10,
            q1=q1,
            median=median,
            q3=q3,
            p90=p90,
            p95=p95,
            maximum=float(np.max(array)),
            iqr=q3 - q1,
        )

    @staticmethod
    def _require_numeric_field(
        population: AnalysisPopulationProjection, field_key: str
    ) -> AnalysisField:
        try:
            field = population.dataset.field(field_key)
        except KeyError as exc:
            raise ValueError(
                f"Unknown Analysis statistics field: {field_key}"
            ) from exc
        if not field.is_numeric:
            raise ValueError(
                f"Analysis statistics field is not numeric: {field_key}"
            )
        return field

    @staticmethod
    def _finite_values(values: Iterable[object | None]) -> tuple[list[float], int]:
        valid: list[float] = []
        missing = 0
        for value in values:
            try:
                numeric = float(value) if value is not None else math.nan
            except (TypeError, ValueError, OverflowError):
                numeric = math.nan
            if not math.isfinite(numeric):
                missing += 1
            else:
                valid.append(numeric)
        return valid, missing

    @staticmethod
    def _box_from_values(
        field_key: str, values: list[float], missing: int
    ) -> TukeyBoxResult:
        if not values:
            return TukeyBoxResult(
                field_key, 0, missing, None, None, None, None, None, None, None, ()
            )
        ordered = np.sort(np.asarray(values, dtype=float))
        q1, median, q3 = (
            float(value)
            for value in np.quantile(ordered, (0.25, 0.5, 0.75), method="linear")
        )
        iqr = q3 - q1
        lower_fence = q1 - 1.5 * iqr
        upper_fence = q3 + 1.5 * iqr
        in_fence = ordered[(ordered >= lower_fence) & (ordered <= upper_fence)]
        outliers = ordered[(ordered < lower_fence) | (ordered > upper_fence)]
        return TukeyBoxResult(
            field_key=field_key,
            valid_n=len(ordered),
            missing_count=missing,
            q1=q1,
            median=median,
            q3=q3,
            lower_fence=lower_fence,
            upper_fence=upper_fence,
            lower_whisker=float(in_fence[0]),
            upper_whisker=float(in_fence[-1]),
            outliers=tuple(float(value) for value in outliers),
        )
