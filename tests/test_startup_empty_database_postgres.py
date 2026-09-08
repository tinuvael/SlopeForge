from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres
from alembic import command
from sqlalchemy import create_engine, inspect, text
from tests.postgres_test_database import is_disposable_test_database

from database.base import Base
from database.migrations import alembic_config
from database.schema_compatibility import expected_alembic_head
from database.settings import Settings
from database.startup import StartupError, initialize_database_runtime


def _settings(tmp_path: Path) -> Settings:
    url = os.environ["TEST_DATABASE_URL"]
    if not is_disposable_test_database(url):
        pytest.fail("Refusing startup test outside a test database", pytrace=False)
    return Settings(url, tmp_path / "storage")


def _make_completely_empty(settings: Settings) -> None:
    command.downgrade(alembic_config(settings), "base")
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_explicit_settings_initialize_empty_database_without_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _make_completely_empty(settings)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("STORAGE_ROOT", raising=False)

    first_settings, first_engine, first_sessions = initialize_database_runtime(settings)
    try:
        assert first_settings is settings
        assert set(Base.metadata.tables) <= set(inspect(first_engine).get_table_names())
        with first_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == expected_alembic_head()
        with first_sessions() as session:
            assert session.scalar(text("SELECT 1")) == 1

        second_settings, second_engine, second_sessions = initialize_database_runtime(settings)
        try:
            assert second_settings is settings
            with second_sessions() as session:
                assert session.scalar(text("SELECT 1")) == 1
        finally:
            second_engine.dispose()
    finally:
        first_engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_nonempty_unversioned_database_is_not_initialized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _make_completely_empty(settings)
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE existing_user_data (id integer PRIMARY KEY)")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
    try:
        with pytest.raises(StartupError) as caught:
            initialize_database_runtime(settings)
        assert caught.value.reason == "database_version_incompatible"
        assert set(inspect(engine).get_table_names()) == {"existing_user_data"}
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE existing_user_data")
        engine.dispose()
        command.upgrade(alembic_config(settings), "head")


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_pre_1_0_revision_is_not_initialized_or_stamped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _make_completely_empty(settings)
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        connection.execute(text(
            "INSERT INTO alembic_version (version_num) VALUES ('0007_remove_mine_blastblock')"
        ))
    try:
        with pytest.raises(StartupError) as caught:
            initialize_database_runtime(settings)
        assert caught.value.reason == "database_version_incompatible"
        assert "python -m" not in caught.value.presentation()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0007_remove_mine_blastblock"
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE alembic_version")
        engine.dispose()
        command.upgrade(alembic_config(settings), "head")
