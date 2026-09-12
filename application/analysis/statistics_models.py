"""Typed, framework-free results for Analysis statistics and plots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NumericSummary:
    field_key: str
    unit: str | None
    matching_count: int
    valid_n: int
    missing_count: int
    mean: float | None
    sample_std_dev: float | None
    sample_variance: float | None
    minimum: float | None
    p5: float | None
    p10: float | None
    q1: float | None
    median: float | None
    q3: float | None
    p90: float | None
    p95: float | None
    maximum: float | None
    iqr: float | None


@dataclass(frozen=True)
class SummaryStatisticsResult:
    matching_count: int
    parameters: tuple[NumericSummary, ...]


@dataclass(frozen=True)
class HistogramResult:
    field_key: str
    valid_n: int
    missing_count: int
    bin_edges: tuple[float, ...]
    counts: tuple[int, ...]
    frequencies_percent: tuple[float, ...]
    mean: float | None
    median: float | None


@dataclass(frozen=True)
class EcdfResult:
    field_key: str
    valid_n: int
    missing_count: int
    observed_values: tuple[float, ...]
    cumulative_proportions: tuple[float, ...]


@dataclass(frozen=True)
class TukeyBoxResult:
    field_key: str
    valid_n: int
    missing_count: int
    q1: float | None
    median: float | None
    q3: float | None
    lower_fence: float | None
    upper_fence: float | None
    lower_whisker: float | None
    upper_whisker: float | None
    outliers: tuple[float, ...]


@dataclass(frozen=True)
class GroupStatistics:
    group_value: object | None
    group_count: int
    valid_n: int
    missing_count: int
    mean: float | None
    median: float | None
    p90: float | None
    sample_std_dev: float | None
    box: TukeyBoxResult


@dataclass(frozen=True)
class GroupedComparisonResult:
    field_key: str
    group_field_key: str
    matching_count: int
    groups: tuple[GroupStatistics, ...]
    total_group_count: int
    excessive_groups: bool = False
