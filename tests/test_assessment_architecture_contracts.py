"""Permanent Assessment concurrency and ownership architecture contracts."""
import ast
import inspect
from pathlib import Path

from application.ports.assessment_state import AssessmentStatePersistence
from application.ports.assessment_writes import AssessmentWrites
from application.services.entity_editing import AssessmentEditingSession


ROOT = Path(__file__).parents[1]
PRODUCTION_ROOTS = ("app", "application", "database", "domain", "infrastructure",
                    "repositories", "services", "ui", "widgets")


def _production_trees():
    for root_name in PRODUCTION_ROOTS:
        for path in (ROOT / root_name).rglob("*.py"):
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_whole_state_save_is_absent_from_application_contracts():
    assert "save" not in AssessmentEditingSession.__dict__
    assert "save" not in AssessmentStatePersistence.__dict__


def test_production_never_calls_retired_whole_state_writers():
    forbidden = {"replace_for_domain", "replace_for_domain_in_session"}
    found = []
    for path, tree in _production_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden:
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert found == []


def test_ui_does_not_import_assessment_state_persistence_adapter():
    forbidden = "infrastructure.db.assessment_state"
    found = []
    for path in (ROOT / "ui").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == forbidden:
                found.append(str(path.relative_to(ROOT)))
    assert found == []


def test_every_focused_assessment_write_requires_expected_version():
    methods = inspect.getmembers(AssessmentWrites, inspect.isfunction)
    assert methods
    for name, method in methods:
        if name.startswith("_") or name.startswith("load_"):
            continue
        parameters = inspect.signature(method).parameters
        assert "expected_version" in parameters, name
        assert parameters["expected_version"].default is inspect.Parameter.empty, name


def test_removed_workspace_container_cannot_return():
    """Assessment roots are owned directly through Domain foreign keys."""
    from database.base import Base
    from database import assessment_models  # noqa: F401
    assert "assessment_workspaces" not in Base.metadata.tables
    for name in ("blast_events", "assessment_areas"):
        table = Base.metadata.tables[name]
        assert table.c.domain_id.type.python_type is int
        assert table.c.logical_id.type.python_type is str
        assert next(iter(table.c.domain_id.foreign_keys)).target_fullname == "domains.id"
        assert "workspace_id" not in table.c
    project_lines = Base.metadata.tables["project_lines_datasets"]
    assert "site_id" in project_lines.c and "domain_id" not in project_lines.c
