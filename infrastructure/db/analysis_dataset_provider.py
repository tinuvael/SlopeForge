"""SQLAlchemy read adapter for bounded, non-persisted Analysis datasets."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Select, and_, func, select

from application.analysis.catalog import assessment_results_dataset
from application.analysis.models import (
    AnalysisRow,
    FilterChoice,
    FilterCondition,
    FilterOperator,
    FilterSpec,
    FilteredDataset,
    SourceReference,
    SortSpec,
)
from database import assessment_models as assessment
from database.models import Domain, Site


class SqlAlchemyAnalysisDatasetProvider:
    """Read only the current completed Assessment population in explicit projections."""

    ASSESSMENT_RESULTS = "assessment_results"

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def filter_options(self, dataset_id: str) -> dict[str, tuple[FilterChoice, ...]]:
        self._require_assessment_results(dataset_id)
        with self.session_factory() as session:
            projects = self._choices(
                session,
                Site.id,
                Site.name,
                order_by=(Site.name, Site.id),
            )
            domain_rows = session.execute(
                self._assessment_query(select(Domain.id, Domain.name, Site.name))
                .distinct()
                .order_by(Site.name, Domain.name, Domain.id)
            ).all()
            domains = tuple(
                FilterChoice(domain_id, f"{project_name} / {domain_name}")
                for domain_id, domain_name, project_name in domain_rows
            )
            area_rows = session.execute(
                self._assessment_query(
                    select(
                        assessment.AssessmentArea.id,
                        assessment.AssessmentArea.name,
                        Domain.name,
                        Site.name,
                    )
                )
                .distinct()
                .order_by(
                    Site.name,
                    Domain.name,
                    assessment.AssessmentArea.name,
                    assessment.AssessmentArea.id,
                )
            ).all()
            areas = tuple(
                FilterChoice(area_id, f"{project_name} / {domain_name} / {area_name}")
                for area_id, area_name, domain_name, project_name in area_rows
            )
            quadrants = self._choices(
                session,
                assessment.AssessmentAreaEvaluationRevision.result_quadrant,
                order_by=(assessment.AssessmentAreaEvaluationRevision.result_quadrant,),
                omit_null=True,
            )
            inspectors = self._choices(
                session,
                assessment.AssessmentAreaEvaluationRevision.inspector,
                order_by=(assessment.AssessmentAreaEvaluationRevision.inspector,),
                omit_null=True,
            )
        return {
            "project": projects,
            "domain": domains,
            "assessment_area": areas,
            "result_quadrant": quadrants,
            "inspector": inspectors,
        }

    def load(
        self,
        filter_spec: FilterSpec,
        *,
        sort_spec: SortSpec | None = None,
        limit: int,
    ) -> FilteredDataset:
        self._require_assessment_results(filter_spec.dataset_id)
        conditions = tuple(self._condition(item) for item in filter_spec.conditions)
        count_statement = self._assessment_query(select(func.count()))
        filtered_count_statement = count_statement.where(*conditions)
        row_statement = self._assessment_query(
            select(
                assessment.AssessmentAreaEvaluationRevision.logical_id.label("row_identity"),
                Site.id.label("site_id"),
                Site.name.label("project"),
                Domain.id.label("domain_id"),
                Domain.name.label("domain"),
                assessment.AssessmentArea.logical_id.label("area_logical_id"),
                assessment.AssessmentArea.name.label("assessment_area"),
                assessment.AssessmentAreaGeometryRevision.min_elevation_m.label("min_elevation_m"),
                assessment.AssessmentAreaGeometryRevision.max_elevation_m.label("max_elevation_m"),
                assessment.AssessmentAreaEvaluationRevision.assessment_date.label("assessment_date"),
                assessment.AssessmentAreaEvaluationRevision.design_achievement_index.label("dai"),
                assessment.AssessmentAreaEvaluationRevision.face_condition_index.label("fci"),
                assessment.AssessmentAreaEvaluationRevision.result_quadrant.label("result_quadrant"),
                assessment.AssessmentAreaEvaluationRevision.inspector.label("inspector"),
            )
        ).where(*conditions).order_by(
            *self._order_by(sort_spec)
        ).limit(limit + 1)

        with self.session_factory() as session:
            total_count = int(session.scalar(count_statement) or 0)
            filtered_count = int(session.scalar(filtered_count_statement) or 0)
            raw_rows = session.execute(row_statement).mappings().all()

        truncated = len(raw_rows) > limit
        rows = tuple(self._row(item) for item in raw_rows[:limit])
        return FilteredDataset(
            dataset=assessment_results_dataset(),
            rows=rows,
            total_count=total_count,
            filtered_count=filtered_count,
            truncated=truncated,
            query_metadata={
                "limit": limit,
                "observation": "active_completed_evaluation",
                "sort_field": sort_spec.field_key if sort_spec else None,
                "sort_ascending": sort_spec.ascending if sort_spec else None,
            },
        )

    @staticmethod
    def _order_by(sort_spec: SortSpec | None) -> tuple:
        revision = assessment.AssessmentAreaEvaluationRevision
        geometry = assessment.AssessmentAreaGeometryRevision
        area = assessment.AssessmentArea
        if sort_spec is None:
            return (
                Site.name,
                Domain.name,
                revision.assessment_date.desc().nullslast(),
                area.name,
                revision.logical_id,
            )
        columns = {
            "project": Site.name,
            "domain": Domain.name,
            "assessment_area": area.name,
            "min_elevation_m": geometry.min_elevation_m,
            "max_elevation_m": geometry.max_elevation_m,
            "assessment_date": revision.assessment_date,
            "dai": revision.design_achievement_index,
            "fci": revision.face_condition_index,
            "result_quadrant": revision.result_quadrant,
            "inspector": revision.inspector,
            "evaluation_revision_id": revision.logical_id,
        }
        try:
            column = columns[sort_spec.field_key]
        except KeyError as exc:
            raise ValueError(f"Unsupported Analysis sort field: {sort_spec.field_key}") from exc
        primary = column.asc() if sort_spec.ascending else column.desc()
        return (primary.nullslast(), revision.logical_id.asc())

    @staticmethod
    def _require_assessment_results(dataset_id: str) -> None:
        if dataset_id != SqlAlchemyAnalysisDatasetProvider.ASSESSMENT_RESULTS:
            raise LookupError(f"Unsupported Analysis dataset: {dataset_id}")

    @staticmethod
    def _assessment_query(statement: Select) -> Select:
        revision = assessment.AssessmentAreaEvaluationRevision
        evaluation = assessment.AssessmentAreaEvaluation
        area = assessment.AssessmentArea
        geometry = assessment.AssessmentAreaGeometryRevision
        return (
            statement.select_from(revision)
            .join(evaluation, revision.evaluation_id == evaluation.id)
            .join(area, evaluation.assessment_area_id == area.id)
            .join(Domain, area.domain_id == Domain.id)
            .join(Site, Domain.site_id == Site.id)
            .join(geometry, revision.assessment_area_geometry_revision_id == geometry.id)
            .where(
                area.is_archived.is_(False),
                evaluation.is_archived.is_(False),
                revision.is_active.is_(True),
                revision.status == "completed",
                geometry.is_active.is_(True),
            )
        )

    def _choices(
        self,
        session,
        value_column,
        label_column=None,
        *,
        order_by: tuple,
        omit_null: bool = False,
    ) -> tuple[FilterChoice, ...]:
        label_column = label_column if label_column is not None else value_column
        statement = self._assessment_query(select(value_column, label_column)).distinct()
        if omit_null:
            statement = statement.where(value_column.is_not(None))
        rows = session.execute(statement.order_by(*order_by)).all()
        return tuple(FilterChoice(value, str(label)) for value, label in rows)

    @staticmethod
    def _condition(condition: FilterCondition):
        revision = assessment.AssessmentAreaEvaluationRevision
        geometry = assessment.AssessmentAreaGeometryRevision
        columns = {
            "project": Site.id,
            "domain": Domain.id,
            "assessment_area": assessment.AssessmentArea.id,
            "assessment_date": revision.assessment_date,
            "min_elevation_m": geometry.min_elevation_m,
            "max_elevation_m": geometry.max_elevation_m,
            "dai": revision.design_achievement_index,
            "fci": revision.face_condition_index,
            "result_quadrant": revision.result_quadrant,
            "inspector": revision.inspector,
        }
        try:
            column = columns[condition.field_key]
        except KeyError as exc:
            raise ValueError(f"Unsupported Analysis filter field: {condition.field_key}") from exc

        if condition.operator is FilterOperator.CATEGORICAL_EQUALS:
            return column == condition.value
        if condition.operator is FilterOperator.CATEGORICAL_IN:
            return column.in_(tuple(condition.value))
        if condition.operator in {FilterOperator.NUMERIC_RANGE, FilterOperator.DATE_RANGE}:
            lower, upper = condition.value
            parts = []
            if lower is not None:
                parts.append(column >= lower)
            if upper is not None:
                parts.append(column <= upper)
            return and_(*parts)
        raise ValueError(f"Unsupported Analysis filter operator: {condition.operator}")

    @staticmethod
    def _row(item) -> AnalysisRow:
        minimum = item["min_elevation_m"]
        maximum = item["max_elevation_m"]
        interval = None
        if minimum is not None and maximum is not None:
            interval = f"{_number(minimum)}–{_number(maximum)}"
        identity = str(item["row_identity"])
        return AnalysisRow(
            identity=identity,
            values={
                "project": item["project"],
                "domain": item["domain"],
                "assessment_area": item["assessment_area"],
                "elevation_interval": interval,
                "min_elevation_m": _float_or_none(minimum),
                "max_elevation_m": _float_or_none(maximum),
                "assessment_date": item["assessment_date"],
                "dai": _float_or_none(item["dai"]),
                "fci": _float_or_none(item["fci"]),
                "result_quadrant": item["result_quadrant"],
                "inspector": item["inspector"] or None,
                "evaluation_revision_id": identity,
            },
            source=SourceReference(
                entity_type="assessment_area",
                entity_id=str(item["area_logical_id"]),
                site_id=int(item["site_id"]),
                domain_id=int(item["domain_id"]),
            ),
        )


def _float_or_none(value) -> float | None:
    return float(value) if value is not None else None


def _number(value: Decimal) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"
