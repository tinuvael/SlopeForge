"""Site-scoped metadata operations for revisioned Project surface datasets."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import assessment_models as _assessment_models  # noqa: F401
from database.models import Site
from database.project_surface_models import ProjectSurfaceDataset


class ProjectSurfaceDatasetNotFoundError(LookupError):
    pass


class ProjectSurfaceDatasetRepository:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def add_dataset(
        self,
        site_id: int,
        *,
        logical_id: str,
        dataset_kind: str,
        imported_at: datetime,
        imported_by_user_id: int | None,
        source_format: str,
        source_files: list[dict[str, object]],
        vertex_count: int,
        triangle_count: int,
    ) -> ProjectSurfaceDataset:
        if dataset_kind not in {"design", "actual"}:
            raise ValueError(f"Unsupported Project surface kind: {dataset_kind!r}")
        with self._session_factory.begin() as session:
            site = session.scalar(
                select(Site).where(Site.id == site_id).with_for_update()
            )
            if site is None:
                raise ValueError(f"Site {site_id} does not exist")
            current_revision = session.scalar(
                select(func.max(ProjectSurfaceDataset.revision_number)).where(
                    ProjectSurfaceDataset.site_id == site_id,
                    ProjectSurfaceDataset.dataset_kind == dataset_kind,
                )
            )
            inherited_mapping = None
            if dataset_kind == "design":
                previous = session.scalar(
                    select(ProjectSurfaceDataset)
                    .where(
                        ProjectSurfaceDataset.site_id == site_id,
                        ProjectSurfaceDataset.dataset_kind == "design",
                    )
                    .order_by(ProjectSurfaceDataset.revision_number.desc())
                    .limit(1)
                )
                inherited_mapping = (
                    None if previous is None else previous.semantic_mapping_json
                )
            row = ProjectSurfaceDataset(
                site_id=site_id,
                logical_id=logical_id,
                dataset_kind=dataset_kind,
                revision_number=int(current_revision or 0) + 1,
                imported_at=imported_at,
                imported_by_user_id=imported_by_user_id,
                source_format=source_format,
                source_files_json=source_files,
                vertex_count=int(vertex_count),
                triangle_count=int(triangle_count),
                semantic_mapping_json=inherited_mapping,
            )
            session.add(row)
            session.flush()
            row_id = row.id
        return self._get_row(row_id)

    def list_for_site(
        self, site_id: int, *, dataset_kind: str | None = None
    ) -> list[ProjectSurfaceDataset]:
        with self._session_factory() as session:
            statement = select(ProjectSurfaceDataset).where(
                ProjectSurfaceDataset.site_id == site_id
            )
            if dataset_kind is not None:
                statement = statement.where(
                    ProjectSurfaceDataset.dataset_kind == dataset_kind
                )
            statement = statement.order_by(
                ProjectSurfaceDataset.dataset_kind,
                ProjectSurfaceDataset.revision_number.desc(),
            )
            return list(session.scalars(statement))

    def get_current(
        self, site_id: int, dataset_kind: str
    ) -> ProjectSurfaceDataset | None:
        if dataset_kind not in {"design", "actual"}:
            raise ValueError(f"Unsupported Project surface kind: {dataset_kind!r}")
        with self._session_factory() as session:
            return session.scalar(
                select(ProjectSurfaceDataset)
                .where(
                    ProjectSurfaceDataset.site_id == site_id,
                    ProjectSurfaceDataset.dataset_kind == dataset_kind,
                )
                .order_by(ProjectSurfaceDataset.revision_number.desc())
                .limit(1)
            )

    def get_by_logical_id(
        self, site_id: int, logical_id: str
    ) -> ProjectSurfaceDataset:
        with self._session_factory() as session:
            row = session.scalar(
                select(ProjectSurfaceDataset).where(
                    ProjectSurfaceDataset.site_id == site_id,
                    ProjectSurfaceDataset.logical_id == logical_id,
                )
            )
            if row is None:
                raise ProjectSurfaceDatasetNotFoundError(logical_id)
            return row

    def _get_row(self, row_id: int) -> ProjectSurfaceDataset:
        with self._session_factory() as session:
            row = session.get(ProjectSurfaceDataset, row_id)
            if row is None:
                raise ProjectSurfaceDatasetNotFoundError(str(row_id))
            return row

    def update_semantic_mapping(
        self, site_id: int, logical_id: str, mapping: dict[str, object]
    ) -> ProjectSurfaceDataset:
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(ProjectSurfaceDataset)
                .where(
                    ProjectSurfaceDataset.site_id == site_id,
                    ProjectSurfaceDataset.logical_id == logical_id,
                )
                .with_for_update()
            )
            if row is None:
                raise ProjectSurfaceDatasetNotFoundError(logical_id)
            if row.dataset_kind != "design":
                raise ValueError("Surface semantics belong only to Design datasets")
            row.semantic_mapping_json = mapping
            session.flush()
            row_id = row.id
        return self._get_row(row_id)
