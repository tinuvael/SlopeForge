from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres
from alembic import command
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from tests.postgres_test_database import is_disposable_test_database

URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    pytest.skip(
        "TEST_DATABASE_URL is not set; Project surface PostgreSQL tests skipped",
        allow_module_level=True,
    )
if not is_disposable_test_database(URL):
    pytest.fail(
        "Refusing destructive Project surface tests outside a test database",
        pytrace=False,
    )

from database.migrations import alembic_config
from database.models import Site
from database.project_surface_models import ProjectSurfaceDataset
from database.settings import Settings
from repositories.project_surface_repository import ProjectSurfaceDatasetRepository


@pytest.fixture(scope="module")
def factory(tmp_path_factory):
    settings = Settings(
        URL,
        Path(tmp_path_factory.mktemp("project-surface-repository-storage")),
    )
    command.upgrade(alembic_config(settings), "head")
    engine = create_engine(URL)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def _source_file(name: str) -> dict[str, object]:
    return {
        "original_filename": name,
        "stored_filename": name,
        "relative_path": f"files/project_geometry/1/{name}",
        "file_size_bytes": 123,
        "sha256": "a" * 64,
    }


def test_latest_revision_is_implicitly_current_per_project_and_kind(factory) -> None:
    with factory.begin() as session:
        site = Site(name="Project surface revision test")
        session.add(site)
        session.flush()
        site_id = site.id

    repository = ProjectSurfaceDatasetRepository(factory)
    try:
        first = repository.add_dataset(
            site_id,
            logical_id="PG-00000001",
            dataset_kind="actual",
            imported_at=datetime.now(timezone.utc),
            imported_by_user_id=None,
            source_format="dxf",
            source_files=[_source_file("actual_1.dxf")],
            vertex_count=4,
            triangle_count=2,
        )
        second = repository.add_dataset(
            site_id,
            logical_id="PG-00000002",
            dataset_kind="actual",
            imported_at=datetime.now(timezone.utc),
            imported_by_user_id=None,
            source_format="datamine",
            source_files=[_source_file("actual_2tr.dmx"), _source_file("actual_2pt.dmx")],
            vertex_count=6,
            triangle_count=4,
        )
        design = repository.add_dataset(
            site_id,
            logical_id="PG-00000003",
            dataset_kind="design",
            imported_at=datetime.now(timezone.utc),
            imported_by_user_id=None,
            source_format="dxf",
            source_files=[_source_file("design.dxf")],
            vertex_count=3,
            triangle_count=1,
        )

        assert (first.revision_number, second.revision_number) == (1, 2)
        assert design.revision_number == 1
        assert repository.get_current(site_id, "actual").logical_id == second.logical_id
        assert repository.get_current(site_id, "design").logical_id == design.logical_id
        mapping = {
            "attribute_name": "COLOUR",
            "assignments": [{"value": 10, "role": "face"}],
        }
        saved = repository.update_semantic_mapping(site_id, design.logical_id, mapping)
        assert saved.semantic_mapping_json == mapping
        inherited = repository.add_dataset(
            site_id,
            logical_id="PG-00000004",
            dataset_kind="design",
            imported_at=datetime.now(timezone.utc),
            imported_by_user_id=None,
            source_format="dxf",
            source_files=[_source_file("design_2.dxf")],
            vertex_count=3,
            triangle_count=1,
        )
        assert inherited.semantic_mapping_json == mapping
        assert [row.revision_number for row in repository.list_for_site(site_id, dataset_kind="actual")] == [2, 1]
    finally:
        with factory.begin() as session:
            session.execute(
                delete(ProjectSurfaceDataset).where(ProjectSurfaceDataset.site_id == site_id)
            )
            session.execute(delete(Site).where(Site.id == site_id))
