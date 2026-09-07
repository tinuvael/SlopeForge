from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateIndex, CreateTable

from database.base import Base
from database import assessment_models, drillhole_models, models  # noqa: F401

EXPECTED = {
    "project_lines_datasets", "blast_events",
    "blast_event_geometry_revisions", "blast_event_technical_cards",
    "blast_event_technical_card_revisions", "blast_event_drillhole_datasets",
    "assessment_areas", "assessment_area_geometry_revisions", "assessment_area_wall_alignments", "assessment_event_links",
    "assessment_area_evaluations", "assessment_area_evaluation_revisions",
    "assessment_entity_attachments",
}


def table(name): return Base.metadata.tables[name]
def checks(name): return " ".join(str(c.sqltext) for c in table(name).constraints if isinstance(c, CheckConstraint))
def uniques(name): return {tuple(c.name for c in u.columns) for u in table(name).constraints if isinstance(u, UniqueConstraint)}
def fk(name, column): return next(iter(table(name).c[column].foreign_keys))


def test_import_is_declarative_only_in_clean_subprocess():
    code = r'''
import sys
import sqlalchemy
import sqlalchemy.engine

def forbidden(*args, **kwargs):
    raise AssertionError("assessment_models attempted to create an engine or connection")

sqlalchemy.create_engine = forbidden
sqlalchemy.engine.create_engine = forbidden
sqlalchemy.engine.Engine.connect = forbidden
import database.models
import database.assessment_models as module
import database.drillhole_models
assert "assessment_workspaces" not in module.Base.metadata.tables
assert "mines" not in module.Base.metadata.tables
assert "blast_blocks" not in module.Base.metadata.tables
assert module.Base.metadata.tables["blast_events"].c.domain_id is not None
assert "blast_event_drillhole_datasets" in module.Base.metadata.tables
for prefix in ("PySide6", "PyQt6", "ui", "widgets"):
    assert not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules), prefix
'''
    environment = os.environ.copy(); environment["PYTHONPATH"] = str(Path.cwd())
    result = subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), env=environment,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_canonical_tables_exist_and_legacy_persistence_cannot_return():
    assert EXPECTED <= set(Base.metadata.tables)
    retired = {
        "mines", "blast_blocks", "rock_mass_profiles", "rock_structures", "blast_designs",
        "drilling_patterns", "charge_segments", "blast_executions",
        "wall_assessments", "attachments", "explosive_types", "lithologies",
    }
    assert retired.isdisjoint(Base.metadata.tables)
    assert "assessment_entity_attachments" in Base.metadata.tables
    assert "blast_event_technical_card_revisions" in Base.metadata.tables
    assert "blast_event_drillhole_datasets" in Base.metadata.tables
    assert "project_lines_datasets" in Base.metadata.tables
    assert not hasattr(models, "Mine")
    assert not hasattr(models, "BlastBlock")


def test_direct_project_domain_ownership_and_logical_identity():
    assert "assessment_workspaces" not in Base.metadata.tables
    assert "mine_id" not in table("sites").c
    assert ("site_id", "logical_id") in uniques("project_lines_datasets")
    assert ("domain_id", "logical_id") in uniques("blast_events")
    assert ("domain_id", "logical_id") in uniques("assessment_areas")
    assert ("logical_id",) in uniques("assessment_area_evaluations")
    for name in ("blast_events", "assessment_areas"):
        assert table(name).c.domain_id.type.python_type is int
        assert table(name).c.logical_id.type.python_type is str
        assert fk(name, "domain_id").target_fullname == "domains.id"
    assert "domain_id" not in table("project_lines_datasets").c
    assert fk("project_lines_datasets", "site_id").target_fullname == "sites.id"


def test_blast_event_drillhole_dataset_is_event_owned_and_revisioned():
    dataset = table("blast_event_drillhole_datasets")
    assert fk("blast_event_drillhole_datasets", "blast_event_id").target_fullname == "blast_events.id"
    assert fk("blast_event_drillhole_datasets", "blast_event_id").ondelete == "CASCADE"
    assert fk("blast_event_drillhole_datasets", "matched_design_dataset_id").target_fullname == "blast_event_drillhole_datasets.id"
    assert ("blast_event_id", "logical_id") in uniques("blast_event_drillhole_datasets")
    assert ("blast_event_id", "dataset_kind", "revision_number") in uniques("blast_event_drillhole_datasets")
    sql = checks("blast_event_drillhole_datasets")
    assert "design" in sql and "actual" in sql
    assert "revision_number > 0" in sql
    for name in ("source_files_json", "holes_json", "summary_json", "matches_json"):
        assert isinstance(dataset.c[name].type, JSONB)


def test_optional_frozen_link_geometry_uses_sql_null():
    assert table("assessment_event_links").c.frozen_intersection_geometry_json.type.none_as_null is True


def test_domain_and_site_scoped_project_lines_foundation():
    assert ("site_id", "name") in uniques("domains")
    assert fk("domains", "site_id").column.table.name == "sites"
    assert fk("project_lines_datasets", "site_id").column.table.name == "sites"
    assert "is_archived" in table("project_lines_datasets").c
    assert "archived_at" in table("project_lines_datasets").c
    assert "NOT (is_archived AND is_active)" in checks("project_lines_datasets")
    assert "horizons" not in Base.metadata.tables


def test_blast_event_is_the_only_production_and_contour_persistence_entity():
    event = table("blast_events")
    assert "blast_block_id" not in event.c
    assert "comment" in event.c
    assert "created_by_user_id" in event.c
    assert fk("blast_events", "created_by_user_id").target_fullname == "users.id"
    assert fk("blast_events", "created_by_user_id").ondelete == "SET NULL"
    sql = checks("blast_events")
    assert "production" in sql and "contour" in sql


def test_revision_identity_numbers_and_active_partial_indexes():
    revisions = {
        "blast_event_geometry_revisions": "blast_event_id",
        "blast_event_technical_card_revisions": "technical_card_id",
        "assessment_area_geometry_revisions": "assessment_area_id",
        "assessment_area_evaluation_revisions": "evaluation_id",
    }
    for name, parent in revisions.items():
        assert (parent, "logical_id") in uniques(name)
        assert (parent, "revision_number") in uniques(name)
        assert "revision_number > 0" in checks(name)
        partial = [i for i in table(name).indexes if i.unique and i.dialect_options["postgresql"]["where"] is not None]
        assert len(partial) == 1
        assert str(partial[0].dialect_options["postgresql"]["where"]) == "is_active"
    dataset_partial = [i for i in table("project_lines_datasets").indexes if i.unique]
    assert len(dataset_partial) == 1
    assert tuple(c.name for c in dataset_partial[0].columns) == ("site_id",)


def test_geometry_elevation_json_and_exact_revision_links():
    geometry = table("assessment_area_geometry_revisions")
    assert "boundary_json" in geometry.c and "selection_polygon_json" not in geometry.c
    assert geometry.c.min_elevation_m.nullable and geometry.c.max_elevation_m.nullable
    assert fk("blast_event_technical_card_revisions", "blast_event_geometry_revision_id").target_fullname == "blast_event_geometry_revisions.id"
    assert fk("assessment_area_evaluation_revisions", "assessment_area_geometry_revision_id").target_fullname == "assessment_area_geometry_revisions.id"
    links = table("assessment_event_links")
    wall_alignment = table("assessment_area_wall_alignments")
    assert tuple(wall_alignment.primary_key.columns.keys()) == (
        "assessment_area_geometry_revision_id",
    )
    assert fk(wall_alignment.name, "assessment_area_geometry_revision_id").target_fullname == "assessment_area_geometry_revisions.id"
    assert "jsonb_typeof(points_json) = 'array'" in checks(wall_alignment.name)
    assert "blast_event_id" not in links.c
    assert fk("assessment_event_links", "assessment_area_geometry_revision_id").target_fullname == "assessment_area_geometry_revisions.id"
    assert fk("assessment_event_links", "blast_event_geometry_revision_id").target_fullname == "blast_event_geometry_revisions.id"
    for t in EXPECTED:
        for column in table(t).c:
            if column.name.endswith("_json"): assert isinstance(column.type, JSONB)


def test_required_domain_fields_are_not_nullable():
    required = {
        "blast_events": ("elevation_m",),
        "blast_event_geometry_revisions": ("elevation_m",),
        "blast_event_drillhole_datasets": ("dataset_kind", "revision_number", "hole_count", "total_drilling_length_m"),
        "assessment_areas": ("assessment_date",),
        "assessment_entity_attachments": (
            "subtype", "custom_subtype", "title", "file_date", "description", "mime_type", "file_size_bytes"),
    }
    for table_name, columns in required.items():
        assert all(not table(table_name).c[column].nullable for column in columns)


def test_attachment_owner_and_other_checks():
    sql = checks("assessment_entity_attachments")
    assert "owner_type = 'blast_event'" in sql
    assert "owner_type = 'assessment_evaluation'" in sql
    assert "file_size_bytes >= 0" in sql
    assert "btrim(relative_path)" in sql
    assert ("logical_id",) in uniques("assessment_entity_attachments")


def test_all_foreign_key_delete_actions():
    expected = {
        ("project_lines_datasets", "site_id"): "RESTRICT",
        ("blast_events", "domain_id"): "RESTRICT",
        ("blast_events", "created_by_user_id"): "SET NULL",
        ("blast_event_geometry_revisions", "blast_event_id"): "CASCADE",
        ("blast_event_technical_cards", "blast_event_id"): "CASCADE",
        ("blast_event_technical_card_revisions", "technical_card_id"): "CASCADE",
        ("blast_event_technical_card_revisions", "blast_event_geometry_revision_id"): "RESTRICT",
        ("blast_event_drillhole_datasets", "blast_event_id"): "CASCADE",
        ("blast_event_drillhole_datasets", "matched_design_dataset_id"): "CASCADE",
        ("blast_event_drillhole_datasets", "imported_by_user_id"): "SET NULL",
        ("assessment_areas", "domain_id"): "RESTRICT",
        ("assessment_area_geometry_revisions", "assessment_area_id"): "CASCADE",
        ("assessment_area_wall_alignments", "assessment_area_geometry_revision_id"): "CASCADE",
        ("assessment_event_links", "assessment_area_geometry_revision_id"): "CASCADE",
        ("assessment_event_links", "blast_event_geometry_revision_id"): "RESTRICT",
        ("assessment_area_evaluations", "assessment_area_id"): "CASCADE",
        ("assessment_area_evaluation_revisions", "evaluation_id"): "CASCADE",
        ("assessment_area_evaluation_revisions", "assessment_area_geometry_revision_id"): "RESTRICT",
        ("assessment_entity_attachments", "blast_event_id"): "CASCADE",
        ("assessment_entity_attachments", "assessment_area_evaluation_id"): "CASCADE",
    }
    assert {(t, c): fk(t, c).ondelete for t, c in expected} == expected


def test_metadata_and_indexes_compile_with_postgresql():
    dialect = postgresql.dialect()
    for name in EXPECTED:
        assert "CREATE TABLE" in str(CreateTable(table(name)).compile(dialect=dialect))
        for index in table(name).indexes: assert "CREATE" in str(CreateIndex(index).compile(dialect=dialect))


def test_project_surface_semantics_metadata_is_nullable_jsonb():
    column = table("project_surface_datasets").c.semantic_mapping_json
    assert isinstance(column.type, postgresql.JSONB)
    assert column.nullable is True


def test_release_1_baseline_is_frozen_and_later_migrations_are_appended():
    versions = sorted(Path("alembic/versions").glob("*.py"))
    assert [path.name for path in versions] == [
        "0001_slopeforge_1.py", "0002_project_surface_semantics.py",
        "0003_assessment_area_wall_alignments.py",
    ]
    baseline = versions[0].read_text()
    assert 'revision = "1"' in baseline
    assert "down_revision = None" in baseline
    assert '_run_component("core", "upgrade")' in baseline
    assert '_run_component("project_surfaces", "upgrade")' in baseline
    assert '_run_component("drillhole_datasets", "upgrade")' in baseline
    assert sorted(path.name for path in Path("alembic/schema_v1").glob("*.py")) == [
        "core.py", "drillhole_datasets.py", "project_surfaces.py",
    ]
    semantics = versions[1].read_text()
    assert 'revision = "2"' in semantics
    assert 'down_revision = "1"' in semantics
    assert "semantic_mapping_json" in semantics
    alignments = versions[2].read_text()
    assert 'revision = "3"' in alignments
    assert 'down_revision = "2"' in alignments
    assert "assessment_area_wall_alignments" in alignments
