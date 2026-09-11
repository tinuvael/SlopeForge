from __future__ import annotations

import os

import pytest
# Import for its side effect: initialize SVG icon support before Qt UI tests.
from PySide6 import QtSvg  # noqa: F401
from sqlalchemy import create_engine, inspect

from database.env import load_local_env
from tests.postgres_test_database import is_disposable_test_database


load_local_env(".env.test")


def _remove_installed_slopeforge_translator() -> None:
    """Do not let a Russian-localization test leak into later UI tests."""
    try:
        from PySide6.QtCore import QCoreApplication
        import app.localization as localization
    except ImportError:
        return
    app = QCoreApplication.instance()
    if app is not None and localization._translator is not None:
        app.removeTranslator(localization._translator)
    localization._translator = None


@pytest.fixture(autouse=True)
def isolate_installed_translator():
    """Every test starts and ends with canonical English presentation state."""
    _remove_installed_slopeforge_translator()
    yield
    _remove_installed_slopeforge_translator()


def _assert_disposable_database(url: str) -> None:
    if not is_disposable_test_database(url):
        pytest.fail(
            "Refusing destructive PostgreSQL test reset outside an explicitly named test database",
            pytrace=False,
        )


def _truncate_test_data(url: str) -> None:
    """Remove all application rows while keeping the migrated schema intact."""
    _assert_disposable_database(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            tables = [name for name in inspect(connection).get_table_names()
                      if name != "alembic_version"]
            if not tables:
                return
            quoted = ", ".join(connection.dialect.identifier_preparer.quote(name)
                               for name in tables)
            connection.exec_driver_sql(
                f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"
            )
    finally:
        engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def reset_disposable_postgresql_test_database(request, tmp_path_factory):
    """Start integration tests from a clean Alembic head on the dedicated test DB."""
    has_postgres_tests = any(
        item.get_closest_marker("postgres") is not None
        for item in request.session.items
    )
    if not has_postgres_tests:
        yield
        return
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        yield
        return
    _assert_disposable_database(url)
    original_runtime_environment = {
        name: os.environ.get(name) for name in ("DATABASE_URL", "STORAGE_ROOT")
    }

    from alembic import command
    from alembic.config import Config

    old_database = os.environ.get("DATABASE_URL")
    old_storage = os.environ.get("STORAGE_ROOT")
    os.environ["DATABASE_URL"] = url
    os.environ["STORAGE_ROOT"] = str(tmp_path_factory.mktemp("suite-storage"))
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        if old_database is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_database
        if old_storage is None:
            os.environ.pop("STORAGE_ROOT", None)
        else:
            os.environ["STORAGE_ROOT"] = old_storage

    yield

    for name, original in original_runtime_environment.items():
        assert os.environ.get(name) == original, (
            f"PostgreSQL tests leaked process environment variable {name}"
        )


@pytest.fixture(autouse=True)
def isolate_postgresql_integration_test_data(request):
    """Give every explicitly marked PostgreSQL test an isolated empty data set."""
    if request.node.get_closest_marker("postgres") is None:
        yield
        return
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    _truncate_test_data(url)
    yield
    _truncate_test_data(url)
