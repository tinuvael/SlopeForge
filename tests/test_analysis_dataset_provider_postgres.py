"""Assessment Analysis read-model integration against a disposable PostgreSQL DB."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import os

import pytest

pytestmark = pytest.mark.postgres

URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    pytest.skip("TEST_DATABASE_URL is not set; Analysis integration tests skipped", allow_module_level=True)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from application.analysis.models import (
    FilterCondition,
    FilterOperator,
    FilterSpec,
    SortSpec,
)
from database import assessment_models as orm
from database.models import Domain, Site
from infrastructure.db.analysis_dataset_provider import SqlAlchemyAnalysisDatasetProvider
from tests.postgres_test_database import is_disposable_test_database


if not is_disposable_test_database(URL):
    pytest.fail("Refusing Analysis tests outside a disposable test database", pytrace=False)


@pytest.fixture(scope="module")
def factory():
    engine = create_engine(URL)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def _area(session, domain, *, suffix, status, assessment_date, dai, fci, active=True):
    area = orm.AssessmentArea(
        domain=domain,
        logical_id=f"AA-{suffix}",
        name=f"Wall {suffix}",
        assessment_date=assessment_date,
        is_archived=False,
    )
    geometry = orm.AssessmentAreaGeometryRevision(
        assessment_area=area,
        logical_id=f"AA-{suffix}-R001",
        revision_number=1,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        boundary_json={"segments": []},
        final_geometry_json={"type": "Polygon", "coordinates": []},
        min_elevation_m=Decimal("600.000"),
        max_elevation_m=Decimal("620.000"),
        is_active=True,
    )
    evaluation = orm.AssessmentAreaEvaluation(
        assessment_area=area,
        logical_id=f"E-{suffix}",
        is_archived=False,
    )
    session.add_all((area, geometry, evaluation))
    session.flush()
    revision = orm.AssessmentAreaEvaluationRevision(
        evaluation=evaluation,
        logical_id=f"ER-{suffix}",
        revision_number=2 if suffix == "1" else 1,
        created_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        geometry_revision=geometry,
        assessment_date=assessment_date,
        inspector="Inspector",
        status=status,
        matrix_template_id="standard",
        matrix_template_version=1,
        design_achievement_index=Decimal(str(dai)) if dai is not None else None,
        face_condition_index=Decimal(str(fci)) if fci is not None else None,
        result_quadrant="good_results" if status == "completed" else None,
        payload_json={"stored": True},
        is_active=active,
    )
    session.add(revision)
    return area, geometry, evaluation, revision


def test_assessment_provider_uses_one_current_stored_completed_result_and_filters(factory):
    with factory.begin() as session:
        alpha = Site(name="Alpha")
        beta = Site(name="Beta")
        north = Domain(site=alpha, name="North")
        east = Domain(site=beta, name="East")
        session.add_all((alpha, beta, north, east))
        session.flush()
        _area(
            session, north, suffix="1", status="completed",
            assessment_date=date(2026, 8, 2), dai="0.82", fci="0.61",
        )
        first_evaluation = session.query(orm.AssessmentAreaEvaluation).filter_by(logical_id="E-1").one()
        first_geometry = session.query(orm.AssessmentAreaGeometryRevision).filter_by(logical_id="AA-1-R001").one()
        session.add(orm.AssessmentAreaEvaluationRevision(
            evaluation=first_evaluation,
            logical_id="ER-1-HISTORICAL",
            revision_number=1,
            created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            geometry_revision=first_geometry,
            assessment_date=date(2026, 8, 1),
            inspector="Inspector",
            status="completed",
            matrix_template_id="standard",
            matrix_template_version=1,
            design_achievement_index=Decimal("0.11"),
            face_condition_index=Decimal("0.22"),
            result_quadrant="unacceptable",
            payload_json={"stored": True},
            is_active=False,
        ))
        _area(
            session, north, suffix="2", status="draft",
            assessment_date=date(2026, 8, 3), dai="0.99", fci="0.99",
        )
        _area(
            session, east, suffix="3", status="completed",
            assessment_date=date(2026, 8, 5), dai="0.30", fci="0.90",
        )
        alpha_id, north_id = alpha.id, north.id

    provider = SqlAlchemyAnalysisDatasetProvider(factory)
    result = provider.load(FilterSpec("assessment_results"), limit=100)
    assert result.total_count == result.filtered_count == 2
    assert [row.identity for row in result.rows] == ["ER-1", "ER-3"]
    first = result.rows[0]
    assert first.values["dai"] == pytest.approx(.82)
    assert first.values["fci"] == pytest.approx(.61)
    assert first.values["dai"] != first.values["fci"]
    assert first.source.entity_id == "AA-1"
    assert first.source.domain_id == north_id

    project = provider.load(FilterSpec("assessment_results", (
        FilterCondition("project", FilterOperator.CATEGORICAL_EQUALS, alpha_id),
    )), limit=100)
    assert project.total_count == 2 and project.filtered_count == 1
    assert project.rows[0].values["project"] == "Alpha"

    numeric = provider.load(FilterSpec("assessment_results", (
        FilterCondition("dai", FilterOperator.NUMERIC_RANGE, (.8, .9)),
        FilterCondition("fci", FilterOperator.NUMERIC_RANGE, (.6, .7)),
    )), limit=100)
    assert numeric.filtered_count == 1 and numeric.rows[0].identity == "ER-1"

    dated = provider.load(FilterSpec("assessment_results", (
        FilterCondition("assessment_date", FilterOperator.DATE_RANGE,
                        (date(2026, 8, 4), date(2026, 8, 6))),
    )), limit=100)
    assert dated.filtered_count == 1 and dated.rows[0].identity == "ER-3"

    options = provider.filter_options("assessment_results")
    assert "status" not in options
    assert {item.label for item in options["project"]} == {"Alpha", "Beta"}
    assert any(item.value == north_id for item in options["domain"])


def test_assessment_provider_sorts_full_filtered_population_before_limit(factory):
    with factory.begin() as session:
        project = Site(name="Analysis Sort Project")
        domain = Domain(site=project, name="Sort Domain")
        session.add_all((project, domain))
        session.flush()
        for suffix, dai in (
            ("SORT-HIGH", "0.90"),
            ("SORT-NULL", None),
            ("SORT-LOW", "0.10"),
            ("SORT-MID", "0.50"),
        ):
            _area(
                session,
                domain,
                suffix=suffix,
                status="completed",
                assessment_date=date(2026, 8, 10),
                dai=dai,
                fci="0.50",
            )
        project_id = project.id

    provider = SqlAlchemyAnalysisDatasetProvider(factory)
    filters = FilterSpec(
        "assessment_results",
        (
            FilterCondition(
                "project", FilterOperator.CATEGORICAL_EQUALS, project_id
            ),
        ),
    )
    ascending = provider.load(
        filters, sort_spec=SortSpec("dai", ascending=True), limit=2
    )
    assert ascending.filtered_count == 4
    assert ascending.truncated
    assert [row.identity for row in ascending.rows] == [
        "ER-SORT-LOW",
        "ER-SORT-MID",
    ]

    descending = provider.load(
        filters, sort_spec=SortSpec("dai", ascending=False), limit=4
    )
    assert [row.identity for row in descending.rows] == [
        "ER-SORT-HIGH",
        "ER-SORT-MID",
        "ER-SORT-LOW",
        "ER-SORT-NULL",
    ]
