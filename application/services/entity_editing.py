"""Application coordinator for editing entities in one Assessment Domain."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from application.ports.assessment_state import AssessmentStatePersistence
from application.ports.assessment_writes import AssessmentWrites
from application.services.assessment_event_links import AssessmentEventLinkService
from application.services.assessment_areas import AssessmentAreaService
from application.services.blast_events import BlastEventService
from domain.assessment.evaluation import AssessmentAreaEvaluationService
from domain.blasting.technical_card import TechnicalCardService
from domain.wall_conformance import WallAlignment


@dataclass(frozen=True)
class AssessmentGeometryCommitResult:
    area_id: str
    created: bool
    link_refresh_result: object | None
    link_refresh_warning: str | None


class AssessmentEditingSession:
    """Owns the stable live graph and coordinates focused write workflows."""

    def __init__(self, persistence: AssessmentStatePersistence, domain_id: int, *,
                 actor_id: int, can_edit: bool, writes: AssessmentWrites,
                 actor_name: str | None = None):
        snapshot = persistence.load(domain_id)
        self._persistence = persistence
        self._writes = writes
        self.domain_id = snapshot.domain_id
        self.site_id = snapshot.site_id
        self.expected_version = snapshot.expected_version
        self.state = snapshot.state
        self.actor_id = actor_id
        self.actor_name = actor_name
        self.can_edit = can_edit
        self.technical_cards = TechnicalCardService(self.state)
        self.evaluations = AssessmentAreaEvaluationService(self.state)
        self.links = AssessmentEventLinkService(self.state)
        self.areas = AssessmentAreaService(self.state)

    def _require_edit(self) -> None:
        if not self.can_edit:
            raise PermissionError("2D Assessment is read-only for the current user")

    def _write(self, operation: str, *args):
        result = getattr(self._writes, operation)(
            self.domain_id, self.expected_version, *args
        )
        self.expected_version = result.new_version
        return result.new_version

    def _save_archive_change(self, entity, archived: bool, operation: str) -> None:
        self._require_edit()
        previous = (entity.is_archived, entity.archived_at, entity.archive_reason)
        try:
            entity.archive() if archived else entity.restore()
            self._write(operation, entity)
        except Exception:
            entity.is_archived, entity.archived_at, entity.archive_reason = previous
            raise

    def set_assessment_area_archived(self, area, archived: bool) -> None:
        if area not in self.state.assessment_areas:
            raise ValueError("Assessment Area not found in this Domain")
        self._save_archive_change(area, archived, "persist_area_archive")

    def set_contour_event_archived(self, event, archived: bool) -> None:
        if event not in self.state.blast_events:
            raise ValueError("BlastEvent not found in this Domain")
        if event.event_type != "contour":
            raise ValueError("Only contour BlastEvents can use contour archive")
        self._save_archive_change(event, archived, "persist_contour_archive")

    def update_contour_metadata(self, event, *, name, elevation,
                                target_domain_id, target_expected_version):
        self._require_edit()
        if event not in self.state.blast_events or event.event_type != "contour":
            raise ValueError("Contour BlastEvent not found in this Domain")
        name = name.strip()
        if not name: raise ValueError("Name is required")
        result = self._writes.update_contour_metadata(
            self.domain_id, self.expected_version, target_domain_id,
            target_expected_version, event.id, name, float(elevation))
        event.name, event.elevation = name, float(elevation)
        self.expected_version = result.new_version
        return result

    def update_assessment_area_metadata(self, area, *, name, target_domain_id,
                                        target_expected_version):
        self._require_edit()
        if area not in self.state.assessment_areas:
            raise ValueError("Assessment Area not found in this Domain")
        name = name.strip()
        if not name: raise ValueError("Name is required")
        result = self._writes.update_assessment_area_metadata(
            self.domain_id, self.expected_version, target_domain_id,
            target_expected_version, area.id, name)
        area.name = name
        self.expected_version = result.new_version
        return result

    def reimport_blast_event_geometry(self, event, path):
        self._require_edit()
        if event not in self.state.blast_events:
            raise ValueError("BlastEvent not found in this Domain")
        count = len(event.geometry_revisions)
        active_id = event.active_geometry_revision_id
        active_flags = [revision.is_active for revision in event.geometry_revisions]
        try:
            revision = BlastEventService(self.state).reimport_geometry(event, path)
            self._write("append_blast_geometry_revision", event.id, revision)
            return revision
        except Exception:
            del event.geometry_revisions[count:]
            event.active_geometry_revision_id = active_id
            for existing, is_active in zip(event.geometry_revisions, active_flags):
                existing.is_active = is_active
            raise

    def _mutate_links(self, area, operation, *args):
        self._require_edit()
        if area not in self.state.assessment_areas:
            raise ValueError("Assessment Area not found in this Domain")
        original_links = list(area.event_links)
        original_statuses = [(link, link.status) for link in original_links]
        try:
            result = operation(area, *args)
            self._write("synchronize_area_links", area)
            return result
        except Exception:
            area.event_links[:] = original_links
            for link, status in original_statuses:
                link.status = status
            raise

    def confirm_event_link(self, area, link_id):
        return self._mutate_links(area, self.links.confirm_link, link_id)

    def exclude_event_link(self, area, link_id):
        return self._mutate_links(area, self.links.exclude_link, link_id)

    def restore_event_link(self, area, link_id):
        return self._mutate_links(area, self.links.restore_suggestion, link_id)

    def add_manual_event_link(self, area, blast_event_id):
        return self._mutate_links(area, self.links.add_manual_link, blast_event_id)

    def refresh_event_link_suggestions(self, area):
        return self._mutate_links(area, self.links.refresh_suggestions)

    def preview_assessment_event_links(self, boundary):
        """Read-only advisory match using the same matcher as final refresh."""
        return self.links.preview(boundary)

    def save_assessment_area_geometry(self, *, assessment_area_id=None, name=None,
                                      assessment_date=None, boundary=None, change_reason=None):
        """Create/revise geometry, refresh links, and persist with in-place rollback."""
        self._require_edit()
        created = assessment_area_id is None
        area = None
        old_revision_count = old_active_id = None
        old_links = []
        old_statuses = []
        if not created:
            area = next((item for item in self.state.assessment_areas
                         if item.id == assessment_area_id), None)
            if area is None:
                raise ValueError("Assessment Area not found in this Domain")
            old_revision_count = len(area.geometry_revisions)
            old_active_id = area.active_geometry_revision_id
            old_links = list(area.event_links)
            old_statuses = [(link, link.status) for link in old_links]
        try:
            if created:
                area = self.areas.create_area(
                    name=name or "", assessment_date=assessment_date,
                    boundary=boundary)
                old_links = []
            else:
                self.areas.revise_area(area, boundary=boundary, change_reason=change_reason)
            links_before_refresh = list(area.event_links)
            statuses_before_refresh = [(link, link.status) for link in links_before_refresh]
            try:
                link_result = self.links.refresh_suggestions(area)
                warning = None
            except Exception as exc:
                area.event_links[:] = links_before_refresh
                for link, status in statuses_before_refresh:
                    link.status = status
                link_result = None
                warning = str(exc)
            self._write("persist_assessment_area_geometry", area)
            return AssessmentGeometryCommitResult(area.id, created, link_result, warning)
        except Exception:
            if created and area in self.state.assessment_areas:
                self.state.assessment_areas.remove(area)
            elif area is not None:
                del area.geometry_revisions[old_revision_count:]
                area.active_geometry_revision_id = old_active_id
                area.event_links[:] = old_links
                for link, status in old_statuses:
                    link.status = status
            raise

    def load_wall_alignment(self, area, geometry_revision=None) -> WallAlignment | None:
        """Read the alignment saved for this exact Assessment geometry revision."""
        if area not in self.state.assessment_areas:
            raise ValueError("Assessment Area not found in this Domain")
        revision = geometry_revision or area.active_geometry_revision()
        if revision not in area.geometry_revisions:
            raise ValueError("Assessment geometry revision does not belong to this Assessment Area")
        return self._writes.load_wall_alignment(self.domain_id, area.id, revision.id)

    def save_wall_alignment(self, area, alignment: WallAlignment) -> None:
        """Replace the active revision's manual stationing polyline transactionally."""
        self._require_edit()
        if area not in self.state.assessment_areas:
            raise ValueError("Assessment Area not found in this Domain")
        if area.is_archived:
            raise PermissionError("Archived Assessment Areas are read-only")
        revision = area.active_geometry_revision()
        result = self._writes.save_wall_alignment(
            self.domain_id, self.expected_version, area.id, revision.id, alignment
        )
        self.expected_version = result.new_version

    def clear_wall_alignment(self, area) -> None:
        """Delete only the active revision's manual stationing polyline."""
        self._require_edit()
        if area not in self.state.assessment_areas:
            raise ValueError("Assessment Area not found in this Domain")
        if area.is_archived:
            raise PermissionError("Archived Assessment Areas are read-only")
        revision = area.active_geometry_revision()
        result = self._writes.clear_wall_alignment(
            self.domain_id, self.expected_version, area.id, revision.id
        )
        self.expected_version = result.new_version

    def technical_card_draft(self, event):
        return self.technical_cards.edit_or_create(event)

    def save_technical_card(self, card, revision, status, planned_date=...):
        self._require_edit()
        count = len(card.revisions)
        active = card.active_revision_id
        event = next(item for item in self.state.blast_events if item.id == card.blast_event_id)
        previous_date = event.event_date
        if planned_date is not ...:
            event.event_date = planned_date
        try:
            card.save_revision(revision, status=status)
            saved = card.revisions[-1]
            if self.actor_name:
                saved.author = self.actor_name
            self._write("persist_technical_card_revision", card, saved, event.event_date)
        except Exception:
            del card.revisions[count:]
            card.active_revision_id = active
            event.event_date = previous_date
            raise

    def evaluation_draft(self, area):
        existing = next(
            (item for item in reversed(self.state.evaluations)
             if item.assessment_area_id == area.id), None,
        )
        if existing and existing.active_revision():
            draft = deepcopy(existing.active_revision())
            return existing, self.evaluations.refresh_auto_draft(draft, area)
        if existing:
            return self.evaluations.new_draft(existing, area)
        return self.evaluations.new_evaluation(area)

    def ensure_evaluation_owner(self, area, evaluation=None):
        """Persist an empty owner when an explicit non-attachment caller needs it."""
        self._require_edit()
        owner, rollback = self.prepare_evaluation_attachment_owner(area, evaluation)
        if rollback is None:
            return owner
        try:
            self._write("persist_evaluation_owner", owner)
        except Exception:
            rollback()
            raise
        return owner

    def prepare_evaluation_attachment_owner(self, area, evaluation=None):
        """Prepare a lazy owner; the attachment save persists owner and file metadata."""
        self._require_edit()
        existing = next(
            (item for item in self.state.evaluations
             if item.assessment_area_id == area.id), None,
        )
        if existing:
            return existing, None
        owner = evaluation or self.evaluations.create_evaluation(area)
        if owner.assessment_area_id != area.id:
            raise ValueError("evaluation owner belongs to another Assessment Area")
        self.state.evaluations.append(owner)

        def rollback():
            if owner in self.state.evaluations and not owner.revisions:
                self.state.evaluations.remove(owner)

        return owner, rollback

    def save_evaluation(self, evaluation, revision, status):
        self._require_edit()
        present = evaluation in self.state.evaluations
        count = len(evaluation.revisions)
        active = evaluation.active_revision_id
        try:
            evaluation.save_revision(revision, status)
            if not present:
                self.state.evaluations.append(evaluation)
            saved = evaluation.revisions[-1]
            self._write("persist_evaluation_revision", evaluation, saved)
        except Exception:
            del evaluation.revisions[count:]
            evaluation.active_revision_id = active
            if not present and evaluation in self.state.evaluations:
                self.state.evaluations.remove(evaluation)
            raise

    def add_attachment_metadata_batch(self, attachments, evaluation_owner=None):
        self._require_edit()
        return self._write("add_attachment_metadata_batch", attachments, evaluation_owner)

    def update_attachment_metadata(self, attachment):
        self._require_edit()
        return self._write("update_attachment_metadata", attachment)

    def delete_attachment_metadata(self, attachment):
        self._require_edit()
        return self._write("delete_attachment_metadata", attachment.id)
