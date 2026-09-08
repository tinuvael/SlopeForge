"""Assessment persistence integration tests for a disposable PostgreSQL DB."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, update
from tests.postgres_test_database import is_disposable_test_database
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL integration tests skipped", allow_module_level=True)
if not is_disposable_test_database(URL):
    pytest.fail("Refusing destructive tests: PostgreSQL database name must contain 'test'", pytrace=False)

from database import assessment_models as orm
from database.models import Domain, Site
from repositories.assessment_state_mapper import (
    AssessmentPersistenceCorruptionError, AssessmentSiteNotFoundError,
)
from repositories.assessment_state_repository import AssessmentStateRepository
from repositories.assessment_area_context_repository import AssessmentAreaContextRepository
from tests.assessment_graph_seeder import AssessmentGraphSeeder
from repositories.project_lines_repository import ProjectLinesRepository
from tests.test_assessment_state_mapper import build_rich_state
from infrastructure.db.assessment_writes import SqlAlchemyAssessmentWrites
from application.services.assessment_areas import AssessmentAreaService
from application.state.assessment_domain_state import AssessmentDomainState
from domain.assessment.geometry import (AssessmentBoundary, ProjectLineAnchor, ProjectLineSpan,
    SpatialPoint, StraightConnector, derive_elevation_summary)
from domain.geometry.types import DatamineLine, DataminePoint, PlanPoint
from domain.project.project_lines import ProjectLinesDataset
from domain.wall_conformance import WallAlignment


@pytest.fixture(scope="session")
def session_factory(tmp_path_factory):
    # Alembic itself reads DATABASE_URL/STORAGE_ROOT; both values originate here,
    # never from the application's DATABASE_URL environment setting.
    old_database = os.environ.get("DATABASE_URL")
    old_storage = os.environ.get("STORAGE_ROOT")
    os.environ["DATABASE_URL"] = URL
    os.environ["STORAGE_ROOT"] = str(tmp_path_factory.mktemp("storage"))
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if old_database is None: os.environ.pop("DATABASE_URL", None)
        else: os.environ["DATABASE_URL"] = old_database
        if old_storage is None: os.environ.pop("STORAGE_ROOT", None)
        else: os.environ["STORAGE_ROOT"] = old_storage
    engine = create_engine(URL)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@dataclass(frozen=True)
class AssessmentContext:
    site_id: int
    domain_id: int


@pytest.fixture
def assessment_context(session_factory):
    with session_factory.begin() as session:
        site = Site(name="Assessment repository integration site")
        session.add(site); session.flush()
        domain = Domain(site_id=site.id, name="North")
        session.add(domain); session.flush()
        context = AssessmentContext(site.id, domain.id)
    yield context


def persist_project_lines(session_factory, site_id, state):
    repository = ProjectLinesRepository(session_factory)
    for dataset in state.datasets:
        repository.add_dataset(site_id, dataset)
    active = state.active_dataset()
    repository.set_active(site_id, active.id if active else None)


def _connector_boundary(offset: float) -> AssessmentBoundary:
    points = (
        SpatialPoint(offset, 0), SpatialPoint(offset + 4, 0),
        SpatialPoint(offset + 4, 4), SpatialPoint(offset, 0),
    )
    return AssessmentBoundary(tuple(
        StraightConnector(start, end) for start, end in zip(points, points[1:])
    ))


def test_project_area_context_real_postgres_path(session_factory):
    with session_factory.begin() as session:
        project_a = Site(name="Assessment context Project A")
        project_b = Site(name="Assessment context Project B")
        session.add_all((project_a, project_b)); session.flush()
        domains = (
            Domain(site_id=project_a.id, name="Context Domain 1"),
            Domain(site_id=project_a.id, name="Context Domain 2"),
            Domain(site_id=project_b.id, name="Context Domain 3"),
        )
        session.add_all(domains); session.flush()
        project_a_id = project_a.id
        domain_ids = tuple(domain.id for domain in domains)

    expected = []
    for index, (domain_id, name) in enumerate(zip(domain_ids, ("Area A", "Area B", "Area C"))):
        state = AssessmentDomainState()
        area = AssessmentAreaService(state).create_area(
            name=name, assessment_date=date.today(), boundary=_connector_boundary(index * 10))
        SqlAlchemyAssessmentWrites(session_factory).persist_assessment_area_geometry(
            domain_id, 0, area)
        expected.append(area.id)

    result = AssessmentAreaContextRepository(session_factory).list_current_boundaries(project_a_id)

    assert len(result) == 2
    assert {(item.domain_id, item.assessment_area_id) for item in result} == set(
        zip(domain_ids[:2], expected[:2])
    )
    assert (domain_ids[2], expected[2]) not in {
        (item.domain_id, item.assessment_area_id) for item in result
    }
    assert all(len(item.ring) == 4 for item in result)


def test_wall_alignment_is_revision_owned_and_survives_session_reload(
        session_factory, assessment_context):
    state = AssessmentDomainState()
    area = AssessmentAreaService(state).create_area(
        name="Wall alignment area", assessment_date=date.today(),
        boundary=_connector_boundary(0),
    )
    writes = SqlAlchemyAssessmentWrites(session_factory)
    version = writes.persist_assessment_area_geometry(
        assessment_context.domain_id, 0, area
    ).new_version
    first_revision = area.active_geometry_revision()
    initial = WallAlignment((PlanPoint(0.0, 0.0), PlanPoint(0.0, 10.0)))
    version = writes.save_wall_alignment(
        assessment_context.domain_id, version, area.id, first_revision.id, initial
    ).new_version

    assert writes.load_wall_alignment(
        assessment_context.domain_id, area.id, first_revision.id
    ) == initial

    AssessmentAreaService(state).revise_area(
        area, boundary=_connector_boundary(20), change_reason="Boundary updated"
    )
    version = writes.persist_assessment_area_geometry(
        assessment_context.domain_id, version, area
    ).new_version
    second_revision = area.active_geometry_revision()
    assert second_revision.id != first_revision.id
    assert writes.load_wall_alignment(
        assessment_context.domain_id, area.id, first_revision.id
    ) == initial
    assert writes.load_wall_alignment(
        assessment_context.domain_id, area.id, second_revision.id
    ) == initial

    replacement = WallAlignment((PlanPoint(1.0, 0.0), PlanPoint(1.0, 12.0)))
    version = writes.save_wall_alignment(
        assessment_context.domain_id, version, area.id, second_revision.id, replacement
    ).new_version
    assert writes.load_wall_alignment(
        assessment_context.domain_id, area.id, first_revision.id
    ) == initial
    assert writes.load_wall_alignment(
        assessment_context.domain_id, area.id, second_revision.id
    ) == replacement
    with pytest.raises(PermissionError, match="Historical"):
        writes.save_wall_alignment(
            assessment_context.domain_id, version, area.id, first_revision.id, replacement
        )

    version = writes.clear_wall_alignment(
        assessment_context.domain_id, version, area.id, second_revision.id
    ).new_version
    assert writes.load_wall_alignment(
        assessment_context.domain_id, area.id, second_revision.id
    ) is None
    assert writes.load_wall_alignment(
        assessment_context.domain_id, area.id, first_revision.id
    ) == initial

    with session_factory.begin() as session:
        old_geometry = session.scalar(select(orm.AssessmentAreaGeometryRevision).join(
            orm.AssessmentArea
        ).where(
            orm.AssessmentArea.domain_id == assessment_context.domain_id,
            orm.AssessmentAreaGeometryRevision.logical_id == first_revision.id,
        ))
        session.delete(old_geometry)
    with session_factory() as session:
        assert session.get(
            orm.AssessmentAreaWallAlignment,
            old_geometry.id,
        ) is None


def test_invalid_persisted_wall_alignment_fails_clearly(
        session_factory, assessment_context):
    state = AssessmentDomainState()
    area = AssessmentAreaService(state).create_area(
        name="Invalid alignment area", assessment_date=date.today(),
        boundary=_connector_boundary(40),
    )
    writes = SqlAlchemyAssessmentWrites(session_factory)
    version = writes.persist_assessment_area_geometry(
        assessment_context.domain_id, 0, area
    ).new_version
    revision = area.active_geometry_revision()
    writes.save_wall_alignment(
        assessment_context.domain_id, version, area.id, revision.id,
        WallAlignment((PlanPoint(0.0, 0.0), PlanPoint(0.0, 10.0))),
    )
    with session_factory.begin() as session:
        row = session.scalar(select(orm.AssessmentAreaWallAlignment))
        row.points_json = [{"x": 0.0}]

    with pytest.raises(ValueError, match="Persisted Wall Alignment.*invalid"):
        writes.load_wall_alignment(assessment_context.domain_id, area.id, revision.id)


def semantic(state):
    """Canonical public payload; JSON arrays/tuples and datetime offsets compare semantically."""
    def normalize(value):
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        if isinstance(value, str) and "T" in value:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return value
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat()
        return value
    # to_dict is the complete domain contract (IDs, parents, geometry, archive
    # fields, revision order, links, card/evaluation payloads and attachments).
    return normalize(state.to_dict())


def test_missing_domain_and_empty_domain(session_factory, assessment_context):
    repository = AssessmentStateRepository(session_factory)
    with pytest.raises(AssessmentSiteNotFoundError): repository.load_for_domain(2_000_000_000)
    loaded = repository.load_for_domain(assessment_context.domain_id)
    assert semantic(loaded.state) == semantic(type(loaded.state)())


def test_read_loads_realistic_seeded_graph(session_factory, assessment_context):
    repository = AssessmentStateRepository(session_factory); expected = build_rich_state()
    persist_project_lines(session_factory, assessment_context.site_id, expected)
    saved = AssessmentGraphSeeder(session_factory).seed_for_domain(assessment_context.domain_id, expected)
    loaded = AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id)
    assert loaded.domain_id == saved.domain_id
    assert semantic(loaded.state) == semantic(expected)
    assert loaded.state.active_dataset().id == "D2"
    assert loaded.state.blast_events[0].active_geometry_revision_id == "BE-P-R2"
    assert loaded.state.assessment_areas[0].active_geometry_revision_id == "AA-R2"
    assert loaded.state.technical_cards[0].active_revision_id.endswith("R002")
    assert loaded.state.evaluations[0].active_revision_id.endswith("R002")
    assert [item.id for item in loaded.state.assessment_areas[0].event_links] == ["LINK-OLD", "LINK-ACTIVE"]
    assert {item.owner_type for item in loaded.state.attachments} == {"blast_event", "assessment_evaluation"}
    assert loaded.state.assessment_areas[0].geometry_revisions[-1].change_reason is None


def test_cross_domain_assessment_event_link_is_rejected(session_factory, assessment_context):
    """A relationally valid active link must still not bridge two owning Domains."""
    state = build_rich_state()
    persist_project_lines(session_factory, assessment_context.site_id, state)
    AssessmentGraphSeeder(session_factory).seed_for_domain(assessment_context.domain_id, state)
    with session_factory.begin() as session:
        foreign = Domain(site_id=assessment_context.site_id, name="Foreign link owner")
        session.add(foreign); session.flush()
        foreign_event = orm.BlastEvent(
            domain_id=foreign.id, logical_id="FOREIGN-EVENT", name="Foreign",
            event_type="contour", elevation_m=100,
        )
        session.add(foreign_event); session.flush()
        foreign_geometry = orm.BlastEventGeometryRevision(
            blast_event=foreign_event, logical_id="FOREIGN-GEOMETRY", revision_number=1,
            imported_at=datetime.now(timezone.utc), source_file_name="foreign.csv",
            source_geometry_json=[], plan_geometry_json={"type": "Point", "coordinates": [0, 0]},
            elevation_m=100, is_active=True,
        )
        session.add(foreign_geometry); session.flush()
        link_id = session.scalar(
            select(orm.AssessmentEventLink.id)
            .join(orm.AssessmentAreaGeometryRevision)
            .where(orm.AssessmentAreaGeometryRevision.is_active.is_(True))
        )
        session.execute(update(orm.AssessmentEventLink).where(
            orm.AssessmentEventLink.id == link_id).values(
                blast_event_geometry_revision_id=foreign_geometry.id))
    with pytest.raises(AssessmentPersistenceCorruptionError, match="different Domains"):
        AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id)


def test_payload_mismatch_is_corruption(session_factory, assessment_context):
    state = build_rich_state()
    persist_project_lines(session_factory, assessment_context.site_id, state)
    AssessmentGraphSeeder(session_factory).seed_for_domain(assessment_context.domain_id, state)
    with session_factory.begin() as session:
        row = session.scalar(select(orm.BlastEventTechnicalCardRevision).order_by(orm.BlastEventTechnicalCardRevision.id))
        payload = dict(row.payload_json); payload["status"] = "completed"
        session.execute(update(orm.BlastEventTechnicalCardRevision).where(
            orm.BlastEventTechnicalCardRevision.id == row.id).values(payload_json=payload))
    with pytest.raises(AssessmentPersistenceCorruptionError, match="payload"):
        AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id)


def test_cross_event_card_geometry_corruption_is_detected(session_factory, assessment_context):
    state = build_rich_state()
    persist_project_lines(session_factory, assessment_context.site_id, state)
    AssessmentGraphSeeder(session_factory).seed_for_domain(assessment_context.domain_id, state)
    with session_factory.begin() as session:
        contour_geometry = session.scalar(select(orm.BlastEventGeometryRevision.id).join(orm.BlastEvent).where(
            orm.BlastEvent.logical_id == "BE-C"))
        card_revision = session.scalar(select(orm.BlastEventTechnicalCardRevision.id))
        session.execute(update(orm.BlastEventTechnicalCardRevision).where(
            orm.BlastEventTechnicalCardRevision.id == card_revision).values(
                blast_event_geometry_revision_id=contour_geometry))
    with pytest.raises(AssessmentPersistenceCorruptionError, match="another BlastEvent"):
        AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id)


def test_cross_area_evaluation_geometry_corruption_is_detected(session_factory, assessment_context):
    # A second area and its revision are produced through a valid replacement,
    # then the relational FK is deliberately pointed at that other area.
    state = build_rich_state(); other = deepcopy(state.assessment_areas[0])
    persist_project_lines(session_factory, assessment_context.site_id, state)
    other.id = "AA-OTHER"; other.event_links = []
    for revision in other.geometry_revisions:
        object.__setattr__(revision, "assessment_area_id", other.id)
        object.__setattr__(revision, "id", "OTHER-" + revision.id)
    other.active_geometry_revision_id = "OTHER-AA-R2"; state.assessment_areas.append(other)
    AssessmentGraphSeeder(session_factory).seed_for_domain(assessment_context.domain_id, state)
    with session_factory.begin() as session:
        other_geometry = session.scalar(select(orm.AssessmentAreaGeometryRevision.id).join(orm.AssessmentArea).where(
            orm.AssessmentArea.logical_id == "AA-OTHER"))
        evaluation_revision = session.scalar(select(orm.AssessmentAreaEvaluationRevision.id))
        session.execute(update(orm.AssessmentAreaEvaluationRevision).where(
            orm.AssessmentAreaEvaluationRevision.id == evaluation_revision).values(
                assessment_area_geometry_revision_id=other_geometry))
    with pytest.raises(AssessmentPersistenceCorruptionError, match="another Assessment Area"):
        AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id)


def test_zero_revision_evaluation_container_round_trips(session_factory, assessment_context):
    from domain.assessment.evaluation import AssessmentAreaEvaluationService

    state = build_rich_state()
    state.evaluations = []
    state.attachments = [item for item in state.attachments if item.owner_type != "assessment_evaluation"]
    area = state.assessment_areas[0]
    owner = AssessmentAreaEvaluationService(state).create_evaluation(area)
    state.evaluations.append(owner)
    persist_project_lines(session_factory, assessment_context.site_id, state)
    repository = AssessmentStateRepository(session_factory)
    AssessmentGraphSeeder(session_factory).seed_for_domain(assessment_context.domain_id, state)

    reloaded = repository.load_for_domain(assessment_context.domain_id).state
    owners = [item for item in reloaded.evaluations if item.assessment_area_id == area.id]
    assert len(owners) == 1
    assert owners[0].id == owner.id
    assert owners[0].revisions == []
    assert owners[0].active_revision_id is None



def _persist_rich_for_focused_write(session_factory, context):
    state = build_rich_state()
    persist_project_lines(session_factory, context.site_id, state)
    AssessmentGraphSeeder(session_factory).seed_for_domain(context.domain_id, state)
    return state


def test_high_precision_sloping_boundary_focused_write_round_trip(session_factory, assessment_context):
    points=[SpatialPoint(0,0,700.1234567),SpatialPoint(5,1,704.8765432),
            SpatialPoint(10,0,711.3337777),SpatialPoint(10,10,718.9991111)]
    source=DatamineLine("SLOPE",[DataminePoint(p.x,p.y,p.z,index+1) for index,p in enumerate(points)])
    dataset=ProjectLinesDataset("HP-DATASET","High precision",datetime.now(timezone.utc),"hp.csv",True,[source])
    state=AssessmentDomainState(datasets=[dataset]); persist_project_lines(session_factory,assessment_context.site_id,state)
    start=ProjectLineAnchor(dataset.id,source.source_id,0,0,points[0]); end=ProjectLineAnchor(dataset.id,source.source_id,2,1,points[3])
    span=ProjectLineSpan(start,end,tuple(points))
    free=SpatialPoint(0,10)
    boundary=AssessmentBoundary((span,StraightConnector(points[3],free,end,None),
        StraightConnector(free,points[0],None,start)))
    area=AssessmentAreaService(state).create_area(name="Sloping",assessment_date=date.today(),boundary=boundary)
    SqlAlchemyAssessmentWrites(session_factory).persist_assessment_area_geometry(
        assessment_context.domain_id,0,area)

    with session_factory() as session:
        row=session.scalar(select(orm.AssessmentAreaGeometryRevision).where(
            orm.AssessmentAreaGeometryRevision.logical_id==area.active_geometry_revision_id))
        assert row.min_elevation_m==Decimal("700.123") and row.max_elevation_m==Decimal("718.999")

    loaded=AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id).state
    restored=loaded.assessment_areas[0].active_geometry_revision()
    assert restored.boundary.to_dict()==boundary.to_dict()
    assert (restored.min_elevation,restored.max_elevation)==derive_elevation_summary(boundary)
    second_boundary=deepcopy(boundary)
    AssessmentAreaService(loaded).create_area(name="Another",assessment_date=date.today(),boundary=second_boundary)

def test_focused_attachment_batch_rolls_back_every_row_on_second_insert(session_factory, assessment_context):
    state = _persist_rich_for_focused_write(session_factory, assessment_context)
    first = deepcopy(state.attachments[0]); first.id = "ATT-BATCH-DUP"
    second = deepcopy(first)
    with pytest.raises(Exception):
        SqlAlchemyAssessmentWrites(session_factory).add_attachment_metadata_batch(
            assessment_context.domain_id, 0, [first, second])
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(orm.AssessmentEntityAttachment).where(
            orm.AssessmentEntityAttachment.logical_id == first.id)) == 0


def test_focused_lazy_owner_and_attachment_batch_roll_back_together(session_factory, assessment_context):
    state = build_rich_state(); state.evaluations = []
    state.attachments = [x for x in state.attachments if x.owner_type == "blast_event"]
    persist_project_lines(session_factory, assessment_context.site_id, state)
    AssessmentGraphSeeder(session_factory).seed_for_domain(assessment_context.domain_id, state)
    owner = deepcopy(build_rich_state().evaluations[0]); owner.id = "EVAL-LAZY"
    owner.revisions = []; owner.active_revision_id = None
    first = deepcopy(build_rich_state().attachments[1]); first.owner_id = owner.id
    first.id = "ATT-LAZY-DUP"; second = deepcopy(first)
    with pytest.raises(Exception):
        SqlAlchemyAssessmentWrites(session_factory).add_attachment_metadata_batch(
            assessment_context.domain_id, 0, [first, second], owner)
    with session_factory() as session:
        assert session.scalar(select(orm.AssessmentAreaEvaluation.id).where(
            orm.AssessmentAreaEvaluation.logical_id == owner.id)) is None
        assert session.scalar(select(orm.AssessmentEntityAttachment.id).where(
            orm.AssessmentEntityAttachment.logical_id == first.id)) is None


def test_active_link_write_does_not_leak_historical_live_mutation(session_factory, assessment_context):
    state = _persist_rich_for_focused_write(session_factory, assessment_context)
    area = state.assessment_areas[0]
    historical = next(x for x in area.event_links if x.assessment_area_geometry_revision_id == "AA-R1")
    active = next(x for x in area.event_links if x.assessment_area_geometry_revision_id == "AA-R2")
    historical_before = historical.status
    historical.status = "excluded" if historical.status != "excluded" else "confirmed"
    active.status = "excluded" if active.status != "excluded" else "confirmed"
    SqlAlchemyAssessmentWrites(session_factory).synchronize_area_links(assessment_context.domain_id, 0, area)
    loaded = AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id).state.assessment_areas[0]
    assert next(x.status for x in loaded.event_links if x.id == historical.id) == historical_before
    assert next(x.status for x in loaded.event_links if x.id == active.id) == active.status


def test_area_geometry_write_does_not_leak_historical_link_mutation(session_factory, assessment_context):
    state = _persist_rich_for_focused_write(session_factory, assessment_context)
    area = state.assessment_areas[0]
    historical = next(x for x in area.event_links if x.assessment_area_geometry_revision_id == "AA-R1")
    historical_before = historical.status
    historical.status = "excluded" if historical.status != "excluded" else "confirmed"
    old = area.geometry_revisions[-1]
    revision = type(old)("AA-R3", area.id, 3, old.created_at, old.boundary,
        old.final_geometry_frozen, old.min_elevation, old.max_elevation, "focused revision")
    area.geometry_revisions.append(revision); area.active_geometry_revision_id = revision.id
    SqlAlchemyAssessmentWrites(session_factory).persist_assessment_area_geometry(
        assessment_context.domain_id, 0, area)
    loaded = AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id).state.assessment_areas[0]
    assert loaded.active_geometry_revision_id == revision.id
    assert next(x.status for x in loaded.event_links if x.id == historical.id) == historical_before


def test_technical_card_identity_mismatch_is_rejected(session_factory, assessment_context):
    state = _persist_rich_for_focused_write(session_factory, assessment_context)
    card = deepcopy(state.technical_cards[0]); card.id = "WRONG-CARD"
    revision = deepcopy(card.revisions[-1]); revision.id = "WRONG-CARD-R3"
    revision.revision_number = 3; card.revisions.append(revision)
    with pytest.raises(ValueError, match="logical ID"):
        SqlAlchemyAssessmentWrites(session_factory).persist_technical_card_revision(
            assessment_context.domain_id, 0, card, revision)
    loaded = AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id).state
    assert len(loaded.technical_cards[0].revisions) == 2


def test_cross_event_link_geometry_is_rejected(session_factory, assessment_context):
    state = _persist_rich_for_focused_write(session_factory, assessment_context)
    area = state.assessment_areas[0]
    active = next(x for x in area.event_links if x.assessment_area_geometry_revision_id == area.active_geometry_revision_id)
    before = active.status
    other = next(event for event in state.blast_events if event.id != active.blast_event_id)
    active.geometry_revision_id = other.active_geometry_revision_id
    active.status = "excluded" if before != "excluded" else "confirmed"
    with pytest.raises(ValueError, match="not persisted"):
        SqlAlchemyAssessmentWrites(session_factory).synchronize_area_links(assessment_context.domain_id, 0, area)
    loaded = AssessmentStateRepository(session_factory).load_for_domain(assessment_context.domain_id).state.assessment_areas[0]
    assert next(x.status for x in loaded.event_links if x.id == active.id) == before


def test_cross_domain_evaluation_and_attachment_mutations_are_rejected(session_factory, assessment_context):
    state = _persist_rich_for_focused_write(session_factory, assessment_context)
    with session_factory.begin() as session:
        foreign_domain = Domain(site_id=assessment_context.site_id, name="Foreign focused guard")
        session.add(foreign_domain); session.flush()
        foreign_domain_id = foreign_domain.id
    writer = SqlAlchemyAssessmentWrites(session_factory)
    attachment = deepcopy(state.attachments[0]); attachment.title = "must not change"
    try:
        with pytest.raises(ValueError, match="another Domain"):
            writer.persist_evaluation_owner(foreign_domain_id, 0, state.evaluations[0])
        with pytest.raises(ValueError, match="another Domain"):
            writer.update_attachment_metadata(foreign_domain_id, 0, attachment)
        with pytest.raises(ValueError, match="another Domain"):
            writer.delete_attachment_metadata(foreign_domain_id, 0, attachment.id)
        loaded = AssessmentStateRepository(session_factory).load_for_domain(
            assessment_context.domain_id).state
        assert next(x.title for x in loaded.attachments if x.id == attachment.id) != "must not change"
    finally:
        with session_factory.begin() as session:
            session.query(Domain).filter_by(id=foreign_domain_id).delete()
