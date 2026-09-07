from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from tests.postgres_test_database import is_disposable_test_database

from domain.assessment.entities import AssessmentArea, AssessmentAreaGeometryRevision
from domain.assessment.geometry import (
    AssessmentBoundary, ProjectLineAnchor, ProjectLineSpan, SpatialPoint,
    StraightConnector, derive_elevation_summary, derive_plan_polygon,
)
from domain.geometry.types import DatamineLine, DataminePoint
from domain.project.project_lines import ProjectLinesDataset


def _build_mvp_assessment_fixture() -> tuple[ProjectLinesDataset, AssessmentArea]:
    """Build the canonical Assessment fixture independently of PostgreSQL."""
    source_line = DatamineLine(
        source_id="crest-1",
        points=[
            DataminePoint(x=0.0, y=0.0, z=101.12349, source_row_number=1),
            DataminePoint(x=10.0, y=0.0, z=109.98751, source_row_number=2),
        ],
        import_order=0,
    )
    dataset = ProjectLinesDataset(
        id="LINES-1", name="Survey",
        imported_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        source_file_name="survey.csv", is_active=True, lines=[source_line],
    )
    start_point = SpatialPoint(x=0.0, y=0.0, z=101.12349)
    end_point = SpatialPoint(x=10.0, y=0.0, z=109.98751)
    start_anchor = ProjectLineAnchor(
        source_dataset_id="LINES-1", source_line_id="crest-1",
        source_segment_index=0, interpolation_fraction=0.0,
        frozen_point_xyz=start_point,
    )
    end_anchor = ProjectLineAnchor(
        source_dataset_id="LINES-1", source_line_id="crest-1",
        source_segment_index=0, interpolation_fraction=1.0,
        frozen_point_xyz=end_point,
    )
    corner = SpatialPoint(x=4.0, y=8.0, z=None)
    boundary = AssessmentBoundary(segments=(
        ProjectLineSpan(start_anchor=start_anchor, end_anchor=end_anchor,
                        frozen_trace_xyz=(start_point, end_point)),
        StraightConnector(start_point=end_point, end_point=corner, start_anchor=end_anchor),
        StraightConnector(start_point=corner, end_point=start_point, end_anchor=start_anchor),
    ))
    minimum, maximum = derive_elevation_summary(boundary)
    area = AssessmentArea(
        id="AREA-1", name="North wall", assessment_date=date(2026, 8, 13),
        geometry_revisions=[AssessmentAreaGeometryRevision(
            id="GEOM-1", assessment_area_id="AREA-1", revision_number=1,
            created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            boundary=boundary, final_geometry_frozen=derive_plan_polygon(boundary),
            min_elevation=minimum, max_elevation=maximum,
            change_reason="Initial traced boundary",
        )],
        active_geometry_revision_id="GEOM-1",
    )
    return dataset, area


def test_mvp_assessment_fixture_uses_canonical_domain_serialization() -> None:
    dataset, area = _build_mvp_assessment_fixture()
    source_line = dataset.lines[0]
    assert source_line.source_id == "crest-1"
    assert source_line.import_order == 0
    assert [(point.x, point.y, point.z, point.source_row_number)
            for point in source_line.points] == [
        (0.0, 0.0, 101.12349, 1),
        (10.0, 0.0, 109.98751, 2),
    ]
    revision = area.active_geometry_revision()
    assert derive_plan_polygon(revision.boundary) == revision.final_geometry_frozen
    assert derive_elevation_summary(revision.boundary) == (101.12349, 109.98751)
    assert (revision.min_elevation, revision.max_elevation) == (101.12349, 109.98751)
    assert ProjectLinesDataset.from_dict(dataset.to_dict()).to_dict() == dataset.to_dict()
    restored = AssessmentArea.from_dict(area.to_dict())
    assert restored.to_dict() == area.to_dict()
    assert restored.active_geometry_revision().boundary == revision.boundary


def _require_destructive_test_database(url: str) -> None:
    if not is_disposable_test_database(url):
        pytest.fail("Refusing migration test outside a test database", pytrace=False)


def _alembic_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str):
    from alembic.config import Config
    _require_destructive_test_database(url)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    return Config("alembic.ini")


def test_destructive_migration_guard_rejects_non_test_database() -> None:
    with pytest.raises(pytest.fail.Exception, match="outside a test database"):
        _require_destructive_test_database(
            "postgresql+psycopg://slopeforge@localhost:5432/slopeforge"
        )


def test_alembic_history_keeps_release_1_frozen_and_has_one_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == ["3"]
    assert [revision.revision for revision in script.walk_revisions()] == ["3", "2", "1"]
    assert script.get_current_head() == "3"


def test_every_alembic_revision_fits_standard_version_column() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    revisions = ScriptDirectory.from_config(Config("alembic.ini")).walk_revisions()
    overlong = [revision.revision for revision in revisions if len(revision.revision) > 32]
    assert overlong == [], (
        "Alembic stores revision identifiers in alembic_version.version_num VARCHAR(32): "
        + ", ".join(overlong)
    )


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_wall_alignment_migration_upgrades_and_downgrades_cleanly(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from alembic import command

    url = os.environ["TEST_DATABASE_URL"]
    config = _alembic_config(monkeypatch, tmp_path, url)
    engine = create_engine(url)
    try:
        command.downgrade(config, "2")
        assert "assessment_area_wall_alignments" not in inspect(engine).get_table_names()
        command.upgrade(config, "3")
        assert "assessment_area_wall_alignments" in inspect(engine).get_table_names()
        command.downgrade(config, "2")
        assert "assessment_area_wall_alignments" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "assessment_area_wall_alignments" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_explosive_catalogue_migration_and_postgresql_round_trip(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from alembic import command
    from domain.blasting.charge_design import ExplosiveProduct, ExplosiveProductKind
    from infrastructure.db.explosive_catalogue import SqlAlchemyExplosiveCatalogue

    url = os.environ["TEST_DATABASE_URL"]
    config = _alembic_config(monkeypatch, tmp_path, url)
    engine = create_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    adapter = SqlAlchemyExplosiveCatalogue(sessions)
    try:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        assert "explosive_products" in inspect(engine).get_table_names()
        bulk = adapter.create_product(ExplosiveProduct(
            0, "Bulk PG", ExplosiveProductKind.BULK, "#AA0000", density_kg_m3=1000))
        cartridge = adapter.create_product(ExplosiveProduct(
            0, "Cartridge PG", ExplosiveProductKind.CARTRIDGE, "#00AA00",
            cartridge_diameter_mm=40, cartridge_mass_kg=.5, default_pitch_m=.25))
        assert [item.name for item in adapter.list_products()] == ["Bulk PG", "Cartridge PG"]
        bulk.name = "Bulk PG edited"; bulk.density_kg_m3 = 1200
        assert adapter.update_product(bulk).density_kg_m3 == 1200
        assert adapter.set_product_enabled(cartridge.id, False).enabled is False
        assert [item.id for item in adapter.list_products(enabled_only=True)] == [bulk.id]
        assert adapter.set_product_enabled(cartridge.id, True).enabled is True
        command.downgrade(config, "base")
        assert "explosive_products" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert adapter.list_products() == []
    finally:
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_charge_preset_postgresql_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from alembic import command
    from domain.blasting.charge_design import ChargePresetComponent, ChargeComponentKind
    from infrastructure.db.charge_presets import SqlAlchemyChargePresetPersistence
    url=os.environ["TEST_DATABASE_URL"]; config=_alembic_config(monkeypatch,tmp_path,url)
    engine=create_engine(url); sessions=sessionmaker(engine,expire_on_commit=False)
    try:
        command.downgrade(config,"base"); command.upgrade(config,"head")
        from database.models import Site
        with sessions() as session:
            site=Site(name="Preset project"); session.add(site); session.commit(); site_id=site.id
        adapter=SqlAlchemyChargePresetPersistence(sessions)
        components=(ChargePresetComponent(ChargeComponentKind.STEMMING,0,1),)
        created=adapter.create_preset(site_id,"Standard",components)
        assert adapter.list_presets(site_id)==[created]
        updated=adapter.update_preset(created.id,site_id,"Updated",components); assert updated.name=="Updated"
        adapter.delete_preset(created.id,site_id); assert adapter.list_presets(site_id)==[]
    finally: engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_release_1_baseline_repeats_cleanly_without_leftover_types(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alembic import command

    url = os.environ["TEST_DATABASE_URL"]
    config = _alembic_config(monkeypatch, tmp_path, url)
    engine = create_engine(url)
    try:
        command.downgrade(config, "base")
        for _cycle in range(2):
            command.upgrade(config, "head")
            assert "users" in inspect(engine).get_table_names()
            command.downgrade(config, "base")
            assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
            with engine.connect() as connection:
                assert not connection.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname IN "
                    "('user_role', 'blast_block_status'))"
                ))
        command.upgrade(config, "head")
    finally:
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_release_1_baseline_upgrade_application_smoke_and_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alembic import command
    from database import assessment_models as orm
    from database.models import Domain, Site
    from infrastructure.db.assessment_writes import SqlAlchemyAssessmentWrites
    from repositories.assessment_state_repository import AssessmentStateRepository
    from repositories.project_lines_repository import ProjectLinesRepository

    url = os.environ["TEST_DATABASE_URL"]
    config = _alembic_config(monkeypatch, tmp_path, url)
    engine = create_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def create_core_graph() -> tuple[int, int]:
        with sessions.begin() as session:
            site = Site(name="Project")
            session.add(site); session.flush()
            domain = Domain(site_id=site.id, name="North")
            session.add(domain); session.flush()
            session.add_all([
                orm.BlastEvent(
                    domain_id=domain.id, logical_id="PROD-1", name="Production",
                    event_type="production", elevation_m=Decimal("100.000"),
                    is_archived=False,
                ),
                orm.BlastEvent(
                    domain_id=domain.id, logical_id="CONT-1", name="Contour",
                    event_type="contour", elevation_m=Decimal("105.000"),
                    is_archived=False,
                ),
            ])
            return site.id, domain.id

    try:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        site_id, domain_id = create_core_graph()

        dataset, area = _build_mvp_assessment_fixture()
        ProjectLinesRepository(sessions).import_dataset(site_id, dataset, make_active=True)
        boundary = area.active_geometry_revision().boundary
        minimum, maximum = derive_elevation_summary(boundary)
        result = SqlAlchemyAssessmentWrites(sessions).persist_assessment_area_geometry(
            domain_id, 0, area
        )
        loaded = AssessmentStateRepository(sessions).load_for_domain(domain_id)
        loaded_area = loaded.state.assessment_areas[0]
        loaded_revision = loaded_area.active_geometry_revision()
        assert loaded.expected_version == result.new_version == 1
        assert loaded_area.id == "AREA-1"
        assert loaded_revision.boundary == boundary
        assert loaded_revision.final_geometry_frozen == derive_plan_polygon(boundary)
        assert (loaded_revision.min_elevation, loaded_revision.max_elevation) == (minimum, maximum)
        assert [type(segment).__name__ for segment in loaded_revision.boundary.segments] == [
            "ProjectLineSpan", "StraightConnector", "StraightConnector"
        ]
        assert {event.id for event in loaded.state.blast_events} == {"PROD-1", "CONT-1"}
        assert loaded.state.active_dataset().id == "LINES-1"
        with sessions() as session:
            stored = session.scalar(select(orm.AssessmentAreaGeometryRevision))
            assert stored.min_elevation_m == Decimal("101.123")
            assert stored.max_elevation_m == Decimal("109.988")

        command.downgrade(config, "base")
        assert "users" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        _, rebuilt_domain_id = create_core_graph()
        rebuilt = AssessmentStateRepository(sessions).load_for_domain(rebuilt_domain_id)
        assert {event.id for event in rebuilt.state.blast_events} == {"PROD-1", "CONT-1"}
        assert rebuilt.state.assessment_areas == []
        assert set(inspect(engine).get_table_names()) >= {
            "users", "sites", "domains", "blast_events",
            "project_lines_datasets", "project_surface_datasets",
            "blast_event_drillhole_datasets", "assessment_areas",
            "assessment_area_wall_alignments",
        }
        assert "mines" not in inspect(engine).get_table_names()
        assert "blast_blocks" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
