from __future__ import annotations

import os

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from database.migrations import alembic_config
from database.settings import Settings


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_release_1_database_records_alembic_revision_one(tmp_path) -> None:
    """The frozen SlopeForge 1.0 baseline must remain Alembic revision ``1``.

    The application's current migration head may advance beyond revision 1.
    This regression therefore checks the actual historical baseline by
    downgrading the disposable test database to revision 1, then restores head
    for the rest of the PostgreSQL suite.
    """
    settings = Settings(os.environ["TEST_DATABASE_URL"], tmp_path / "storage")
    engine = create_engine(settings.database_url)
    try:
        command.downgrade(alembic_config(settings), "1")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "1"
    finally:
        engine.dispose()
        command.upgrade(alembic_config(settings), "head")
