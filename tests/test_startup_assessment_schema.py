from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

import database.startup as startup
from database.base import Base
from database.settings import Settings


CURRENT_HEAD = "3"
KNOWN_OLDER = "2"
PRE_1_0_HEAD = "0003_drillhole_datasets"


class FakeInspector:
    def __init__(self, tables=()):
        self.tables = tables

    def get_table_names(self):
        return list(self.tables)


def test_expected_alembic_head_resolves_real_repository_graph():
    """Exercise the production path/config rather than a mocked head helper."""
    repository_heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert repository_heads == [CURRENT_HEAD]
    assert startup._expected_alembic_head() == repository_heads[0]


def arrange_startup(monkeypatch, *, revision=CURRENT_HEAD, tables=None):
    settings = Settings(
        "postgresql+psycopg://u:secret@db.example:5432/slopeforge",
        Path("/tmp/storage"),
    )
    engine = object()
    state = {
        "revision": revision,
        "tables": Base.metadata.tables if tables is None else tables,
    }

    monkeypatch.setattr(startup.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(startup, "create_database_engine", lambda value: engine)
    monkeypatch.setattr(startup, "create_session_factory", lambda value: "sessions")
    monkeypatch.setattr(startup, "check_connection", lambda value: None)
    monkeypatch.setattr(startup, "_expected_alembic_head", lambda: CURRENT_HEAD)
    monkeypatch.setattr(
        startup,
        "known_alembic_revisions",
        lambda: frozenset({KNOWN_OLDER, CURRENT_HEAD}),
    )
    monkeypatch.setattr(
        startup,
        "_database_alembic_heads",
        lambda value: (() if state["revision"] is None else (state["revision"],)),
    )

    def upgrade(_settings):
        state["revision"] = CURRENT_HEAD
        state["tables"] = Base.metadata.tables

    monkeypatch.setattr(startup, "upgrade_to_head", upgrade)
    monkeypatch.setattr(startup, "configure_mappers", lambda: None)
    monkeypatch.setattr(startup, "inspect", lambda value: FakeInspector(state["tables"]))
    monkeypatch.setattr(
        startup,
        "missing_required_tables",
        lambda value: tuple(
            sorted(set(Base.metadata.tables) - set(state["tables"]))
        ),
    )
    return settings, engine


def test_startup_requires_assessment_tables_and_does_not_run_migrations(monkeypatch):
    arrange_startup(monkeypatch, tables=())

    with pytest.raises(startup.StartupError) as caught:
        startup.initialize_database_runtime()

    message = str(caught.value)
    assert "assessment_" in message or "blast_event_" in message
    assert "assessment_workspaces" not in Base.metadata.tables
    assert "blast_events" in Base.metadata.tables
    assert "assessment_entity_attachments" in Base.metadata.tables
    assert "blast_event_drillhole_datasets" in Base.metadata.tables


def test_startup_accepts_database_at_the_single_current_head(monkeypatch):
    settings, engine = arrange_startup(monkeypatch)
    assert startup.initialize_database_runtime() == (settings, engine, "sessions")


def test_startup_known_older_revision_requires_database_upgrade(monkeypatch):
    arrange_startup(monkeypatch, revision=KNOWN_OLDER)
    with pytest.raises(startup.StartupError) as caught:
        startup.initialize_database_runtime()
    assert caught.value.reason == "database_upgrade_required"
    assert "older SlopeForge schema" in str(caught.value)
    assert "python -m" not in caught.value.presentation()


def test_startup_pre_1_0_revision_is_incompatible(monkeypatch):
    arrange_startup(monkeypatch, revision=PRE_1_0_HEAD)
    with pytest.raises(startup.StartupError) as caught:
        startup.initialize_database_runtime()
    assert caught.value.reason == "database_version_incompatible"
    assert "python -m" not in caught.value.presentation()


def test_startup_unknown_future_revision_requires_application_upgrade(monkeypatch):
    arrange_startup(monkeypatch, revision="4")
    with pytest.raises(startup.StartupError) as caught:
        startup.initialize_database_runtime()
    assert caught.value.reason == "application_upgrade_required"
    assert "newer" in str(caught.value)


def test_real_script_directory_classifies_pre_1_0_revision_as_incompatible(monkeypatch):
    monkeypatch.setattr(startup, "_expected_alembic_head", lambda: CURRENT_HEAD)
    monkeypatch.setattr(
        startup,
        "_database_alembic_heads",
        lambda _engine: (PRE_1_0_HEAD,),
    )
    with pytest.raises(startup.StartupError) as caught:
        startup._verify_alembic_revision(object(), None)
    assert caught.value.reason == "database_version_incompatible"


def test_startup_initializes_a_completely_empty_database(monkeypatch):
    settings, engine = arrange_startup(monkeypatch, revision=None, tables=())
    assert startup.initialize_database_runtime() == (settings, engine, "sessions")


def test_startup_rejects_nonempty_unversioned_database(monkeypatch):
    arrange_startup(monkeypatch, revision=None, tables=("unmanaged_data",))
    with pytest.raises(startup.StartupError) as caught:
        startup.initialize_database_runtime()
    assert caught.value.reason == "database_version_incompatible"
    assert "python -m" not in caught.value.presentation()


def test_runtime_controller_passes_runtime_storage_root_to_app_context():
    runtime_source = Path("app/runtime_controller.py").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")

    assert "storage_root=settings.storage_root" in runtime_source
    assert "startup_error_handler=show_startup_error" in main_source
