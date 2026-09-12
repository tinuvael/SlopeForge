from __future__ import annotations

import math

import pytest

from application.analysis.catalog import assessment_results_dataset
from application.analysis.models import AnalysisPopulationProjection
from application.services.analysis_statistics import AnalysisStatisticsService


def _population(**columns) -> AnalysisPopulationProjection:
    size = len(next(iter(columns.values()))) if columns else 0
    return AnalysisPopulationProjection(
        assessment_results_dataset(),
        size,
        {key: tuple(values) for key, values in columns.items()},
    )


def _service() -> AnalysisStatisticsService:
    return AnalysisStatisticsService(None)  # calculations need no provider


def test_summary_uses_sample_statistics_and_linear_quantiles():
    population = _population(dai=(1.0, 2.0, 3.0, 4.0, None, math.nan, math.inf, "bad"))
    result = _service().summary(population, ("dai",)).parameters[0]

    assert result.matching_count == 8
    assert result.valid_n == 4
    assert result.missing_count == 4
    assert result.mean == pytest.approx(2.5)
    assert result.sample_variance == pytest.approx(5 / 3)
    assert result.sample_std_dev == pytest.approx(math.sqrt(5 / 3))
    assert result.minimum == 1.0
    assert result.p5 == pytest.approx(1.15)
    assert result.p10 == pytest.approx(1.30)
    assert result.q1 == pytest.approx(1.75)
    assert result.median == pytest.approx(2.50)
    assert result.q3 == pytest.approx(3.25)
    assert result.p90 == pytest.approx(3.70)
    assert result.p95 == pytest.approx(3.85)
    assert result.maximum == 4.0
    assert result.iqr == pytest.approx(1.50)


def test_summary_single_value_and_all_missing_are_explicitly_undefined():
    single = _service().summary(_population(fci=(0.6, None)), ("fci",)).parameters[0]
    assert single.valid_n == 1 and single.missing_count == 1
    assert single.mean == single.minimum == single.median == single.maximum == 0.6
    assert single.sample_std_dev is None
    assert single.sample_variance is None

    missing = _service().summary(
        _population(fci=(None, math.nan, math.inf, "invalid")), ("fci",)
    ).parameters[0]
    assert missing.valid_n == 0 and missing.missing_count == 4
    for value in (
        missing.mean, missing.sample_std_dev, missing.sample_variance,
        missing.minimum, missing.p5, missing.p10, missing.q1, missing.median,
        missing.q3, missing.p90, missing.p95, missing.maximum, missing.iqr,
    ):
        assert value is None


def test_histogram_auto_edges_counts_frequencies_and_missing():
    result = _service().histogram(
        _population(dai=(0.0, 1.0, 2.0, 3.0, None, math.nan)), "dai"
    )
    assert result.valid_n == 4 and result.missing_count == 2
    assert result.bin_edges == pytest.approx((0.0, 1.0, 2.0, 3.0))
    assert result.counts == (1, 1, 2)
    assert result.frequencies_percent == pytest.approx((25.0, 25.0, 50.0))
    assert sum(result.frequencies_percent) == pytest.approx(100.0)


def test_ecdf_preserves_duplicates_and_observation_proportions():
    result = _service().ecdf(_population(fci=(3.0, 1.0, 1.0, None)), "fci")
    assert result.valid_n == 3 and result.missing_count == 1
    assert result.observed_values == (1.0, 1.0, 3.0)
    assert result.cumulative_proportions == pytest.approx((1 / 3, 2 / 3, 1.0))


def test_tukey_box_uses_linear_quartiles_observed_whiskers_and_outliers():
    result = _service().box(_population(dai=(1, 2, 3, 4, 100, None)), "dai")
    assert result.valid_n == 5 and result.missing_count == 1
    assert (result.q1, result.median, result.q3) == (2.0, 3.0, 4.0)
    assert (result.lower_fence, result.upper_fence) == (-1.0, 7.0)
    assert (result.lower_whisker, result.upper_whisker) == (1.0, 4.0)
    assert result.outliers == (100.0,)

    no_outlier = _service().box(_population(dai=(1, 2, 3, 4)), "dai")
    assert no_outlier.outliers == ()
    assert (no_outlier.lower_whisker, no_outlier.upper_whisker) == (1.0, 4.0)

    one = _service().box(_population(dai=(7.0,)), "dai")
    assert one.q1 == one.median == one.q3 == 7.0
    assert one.lower_whisker == one.upper_whisker == 7.0
    assert one.outliers == ()


def test_grouped_statistics_have_independent_missing_counts_and_stable_order():
    population = _population(
        fci=(1.0, 2.0, None, 4.0, 5.0),
        domain=("B", "A", "A", None, "B"),
    )
    result = _service().grouped(population, "fci", "domain")
    assert [group.group_value for group in result.groups] == ["A", "B", None]
    alpha, beta, missing_group = result.groups
    assert (alpha.group_count, alpha.valid_n, alpha.missing_count) == (2, 1, 1)
    assert alpha.mean == alpha.median == alpha.p90 == 2.0
    assert alpha.sample_std_dev is None
    assert (beta.group_count, beta.valid_n, beta.missing_count) == (2, 2, 0)
    assert beta.mean == beta.median == 3.0
    assert beta.p90 == pytest.approx(4.6)
    assert beta.sample_std_dev == pytest.approx(math.sqrt(8.0))
    assert (missing_group.group_count, missing_group.valid_n, missing_group.missing_count) == (1, 1, 0)


def test_grouped_comparison_reports_excessive_groups_without_shrinking_plot():
    values = tuple(float(index) for index in range(25))
    groups = tuple(f"Group {index:02d}" for index in range(25))
    result = _service().grouped(
        _population(dai=values, inspector=groups), "dai", "inspector", max_groups=24
    )
    assert result.excessive_groups
    assert result.total_group_count == 25
    assert result.groups == ()


def test_statistics_reject_non_numeric_parameters_and_non_groupable_fields():
    service = AnalysisStatisticsService(None)
    population = _population(
        project=("Alpha", "Beta"),
        assessment_area=("Wall 1", "Wall 2"),
        dai=(0.4, 0.8),
    )
    with pytest.raises(ValueError, match="not numeric"):
        service.histogram(population, "project")
    with pytest.raises(ValueError, match="not groupable"):
        service.grouped(population, "dai", "assessment_area")
