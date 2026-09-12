"""Small desktop composition helpers (not a dependency-injection container)."""
from application.use_cases.create_blast_event import CreateBlastEvent
from infrastructure.db.blast_event_creation import SqlAlchemyBlastEventCreationPersistence
from application.services.entity_editing import AssessmentEditingSession
from application.services.project_surfaces import ProjectSurfaceDatasetService
from application.services.drillhole_datasets import BlastEventDrillholeDatasetService
from infrastructure.db.assessment_state import SqlAlchemyAssessmentStatePersistence
from infrastructure.db.assessment_writes import SqlAlchemyAssessmentWrites
from application.use_cases.create_project import CreateProject
from application.use_cases.create_domain import CreateDomain
from application.use_cases.generate_project_report import GenerateProjectReport
from infrastructure.db.project_creation import SqlAlchemyProjectCreation
from infrastructure.db.project_lines_creation import SqlAlchemyProjectLinesCreationSupport
from infrastructure.db.domain_creation import SqlAlchemyDomainCreation
from infrastructure.db.project_navigation import SqlAlchemyProjectNavigationQueries
from infrastructure.db.project_report import SqlAlchemyProjectReportQuery
from infrastructure.files.project_geometry import ProjectGeometryFileStorage
from infrastructure.files.drillhole_geometry import BlastEventDrillholeFileStorage
from infrastructure.geometry_import.lines import import_line_geometry
from infrastructure.geometry_import.surfaces import import_surface_geometry
from infrastructure.reports.excel_project_report import OpenPyxlProjectReportWriter
from application.use_cases.rename_project import RenameProject
from application.use_cases.rename_domain import RenameDomain
from infrastructure.db.entity_renaming import (
    SqlAlchemyDomainRenaming, SqlAlchemyProjectRenaming,
)
from application.use_cases.explosive_catalogue import ExplosiveCatalogue
from infrastructure.db.explosive_catalogue import SqlAlchemyExplosiveCatalogue
from application.use_cases.charge_presets import ChargePresets
from infrastructure.db.charge_presets import SqlAlchemyChargePresetPersistence
from repositories.assessment_area_context_repository import AssessmentAreaContextRepository
from repositories.project_surface_repository import ProjectSurfaceDatasetRepository
from repositories.drillhole_dataset_repository import BlastEventDrillholeDatasetRepository
from application.services.analysis import AnalysisDatasetService
from infrastructure.db.analysis_dataset_provider import SqlAlchemyAnalysisDatasetProvider


def create_drillhole_dataset_service(context):
    return BlastEventDrillholeDatasetService(
        BlastEventDrillholeDatasetRepository(context.session_factory),
        BlastEventDrillholeFileStorage(context.storage_root),
        import_line_geometry,
    )


def create_blast_event_use_case(context):
    return CreateBlastEvent(
        SqlAlchemyBlastEventCreationPersistence(context.session_factory),
        create_drillhole_dataset_service(context),
    )


def create_entity_editing_session(context, domain_id):
    user = context.current_user
    writes=SqlAlchemyAssessmentWrites(context.session_factory)
    # The composition root supplies the authenticated actor; write methods keep
    # the existing narrow application-port signatures unchanged.
    writes._actor_id = user.id
    return AssessmentEditingSession(
        SqlAlchemyAssessmentStatePersistence(context.session_factory),
        domain_id,
        actor_id=user.id,
        actor_name=getattr(user, "display_name", "") or getattr(user, "full_name", "") or user.username,
        can_edit=user.can_edit,
        writes=writes,
    )


def create_assessment_area_context_queries(context):
    return AssessmentAreaContextRepository(context.session_factory)


def create_project_use_case(context):
    return CreateProject(SqlAlchemyProjectCreation(context.session_factory),
                         SqlAlchemyProjectLinesCreationSupport(context.session_factory))


def create_project_surface_dataset_service(context):
    return ProjectSurfaceDatasetService(
        ProjectSurfaceDatasetRepository(context.session_factory),
        ProjectGeometryFileStorage(context.storage_root),
        import_surface_geometry,
    )


def create_domain_use_case(context):
    return CreateDomain(SqlAlchemyDomainCreation(context.session_factory))


def create_rename_project_use_case(context):
    return RenameProject(SqlAlchemyProjectRenaming(context.session_factory))


def create_rename_domain_use_case(context):
    return RenameDomain(SqlAlchemyDomainRenaming(context.session_factory))


def create_project_navigation_queries(context):
    return SqlAlchemyProjectNavigationQueries(context.session_factory)


def create_generate_project_report_use_case(context):
    return GenerateProjectReport(SqlAlchemyProjectReportQuery(context.session_factory),
                                 OpenPyxlProjectReportWriter())


def create_explosive_catalogue(context):
    adapter = SqlAlchemyExplosiveCatalogue(context.session_factory)
    return ExplosiveCatalogue(adapter, adapter, can_edit=context.current_user.can_edit)


def create_charge_presets(context, site_id=None):
    scope = site_id if site_id is not None else getattr(context, "site_id", None)
    if scope is None: raise ValueError("Project is required for charge presets")
    return ChargePresets(SqlAlchemyChargePresetPersistence(context.session_factory),
                         site_id=scope, can_edit=context.current_user.can_edit)


def create_analysis_dataset_service(context):
    return AnalysisDatasetService(
        SqlAlchemyAnalysisDatasetProvider(context.session_factory)
    )
