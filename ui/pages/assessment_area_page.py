from __future__ import annotations

from app.localization import tr
from domain.blasting.workflow import (
    ASSESSMENT_PROGRESS_LABELS,
    WORKFLOW_LABELS,
    assessment_progress_for,
    blast_workflow_for,
)
from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSizePolicy,
    QSplitter, QVBoxLayout, QWidget,
)
from repositories.domain_repository import DomainRepository
from repositories.entity_history_repository import EntityHistoryRepository
from ui.pages.assessment_overview_widgets import (
    AssessmentAttachmentPreview,
    AssessmentCommentsCard,
    AssessmentGeometryCard,
    AssessmentMatrixCard,
    AssessmentRecentActivityCard,
    AssessmentRelatedEventList,
    AssessmentStateSummaryCard,
)
from ui.pages.entity_history_widget import EntityHistoryWidget
from ui.pages.entity_history_revision_viewer import open_assessment_revision, open_geometry_revision
from ui.pages.entity_overview_widgets import (
    EntityHeaderWidget,
    OverviewKeyValueCard,
    RelatedEntityRow,
)
from ui.pages.entity_page_controller import EntityPageController
from ui.pages.entity_tabs import create_attachment_tab_page, create_entity_tabs
from ui.pages.plan_geometry_widget import PlanGeometryWidget
from ui.editors.assessment_evaluation_editor import AssessmentAreaEvaluationDialog
from ui.presentation_labels import format_assessment_elevation_interval, result_label
from ui.widgets.design_system import SplitSaveButton


def _value(value):
    return "—" if value in (None, "") else str(value)


def _date(value):
    return value.strftime("%d.%m.%Y") if hasattr(value, "strftime") else _value(value)


def _datetime_text(value):
    return value.strftime("%d.%m.%Y %H:%M") if value else "—"


def _number(value, unit=""):
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{value}{unit}"
    return f"{number:g}{unit}"


def _history_bounds(entries):
    timed = [entry for entry in entries if entry.timestamp]
    if not timed:
        return None, None, None
    created = min(timed, key=lambda item: item.timestamp)
    updated = max(timed, key=lambda item: item.timestamp)
    return created.actor or "—", created.timestamp, updated.timestamp


def _face_condition_value(revision, criterion_id, unit=""):
    if revision is None:
        return "—"
    result = next((item for item in revision.criterion_results if item.criterion_id == criterion_id), None)
    if result is None:
        return "—"
    if result.raw_numeric_value is not None:
        return f"{result.raw_numeric_value:g}{unit}"
    if result.raw_text_value:
        return str(result.raw_text_value)
    option_id = result.selected_option_id
    if not option_id:
        return "—"
    option_labels = {
        "none": "None", "several_small": "Several small blocks", "large": "Large blocks",
        "many": "Many blocks", "straight": "Straight", "hard_toe": "Hard toe",
        "hanging_face": "Hanging face", "hanging_crest": "Hanging crest",
        "irregular": "Irregular", "closed": "None / closed", "many_open": "Many open cracks",
    }
    return tr(option_labels.get(option_id, option_id.replace("_", " ").title()))


class AssessmentLinkListItem(QFrame):
    STATUS_COLORS = {
        "suggested": ("#fff8e6", "#d6a632"),
        "confirmed": ("#edf8f0", "#58a66a"),
        "excluded": ("#f3f4f6", "#9ca3af"),
    }

    def __init__(self, event, link, stale, parent=None):
        super().__init__(parent)
        self.workflow_status = link.status
        self.is_stale = stale
        self.setObjectName("AssessmentLinkItem")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(3)
        title = QLabel(event.name)
        title.setStyleSheet("font-weight:600")
        layout.addWidget(title)
        detail = QLabel(f"{tr(event.event_type.title())} · {event.elevation:g} m")
        detail.setObjectName("MutedText")
        layout.addWidget(detail)
        badges = QHBoxLayout()
        status = QLabel(tr(link.status.title()))
        status.setObjectName("LinkStatusBadge")
        badges.addWidget(status)
        if stale:
            stale_badge = QLabel(tr("Stale"))
            stale_badge.setObjectName("StaleBadge")
            badges.addWidget(stale_badge)
        badges.addStretch()
        source = QLabel(f"{tr(link.source.title())} · {link.geometry_revision_id}")
        source.setObjectName("MutedText")
        badges.addWidget(source)
        layout.addLayout(badges)
        self.set_selected(False)

    def set_selected(self, selected):
        background, accent = self.STATUS_COLORS[self.workflow_status]
        border = "#2563a6" if selected else accent
        width = 2 if selected else 1
        self.setStyleSheet(
            f"QFrame#AssessmentLinkItem{{background:{background};border:{width}px solid {border};border-radius:5px}}"
            "QLabel#LinkStatusBadge{font-weight:600;color:#374151}"
            "QLabel#StaleBadge{background:#fff1c2;color:#8a5a00;border:1px solid #e5b94d;border-radius:4px;padding:1px 4px}"
        )


class AssessmentAreaPage(QWidget):
    edit_boundaries_requested = Signal(str)
    metadata_saved = Signal(str, int)
    related_blast_event_requested = Signal(str, str, int)

    def __init__(self, context, domain_id, domain_name, area_id, parent=None):
        super().__init__(parent)
        self.context = context
        self.domain_id = domain_id
        self.domain_name = domain_name
        self.area_id = area_id
        self.controller = EntityPageController(context, domain_id)
        domain_row = DomainRepository(context.session_factory).get(domain_id)
        self.project_name = domain_row.site.name if domain_row is not None and domain_row.site is not None else "—"
        factory = getattr(context, "session_factory", None)
        self.history_repo = EntityHistoryRepository(factory) if callable(factory) else None
        self.area = self.controller.area(area_id)
        self.read_only = not context.current_user.can_edit or self.area.is_archived
        self._related_event_preview_id = None
        self._build_editor()

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        self._header(root)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.tabs = create_entity_tabs()
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        left.addWidget(self.tabs)
        body.addLayout(left, 1)
        self._sidebar(body)
        root.addLayout(body)

        self._overview()
        self.tabs.addTab(self.assessment_tab, tr("Assessment"))
        self._linked_events()
        self._attachment_tab("Photos")
        self._attachment_tab("Documents")
        self.tabs.addTab(self.history, tr("History"))
        self._wire_sidebar_actions()
        self._refresh_overview_and_sidebar()
        self._refresh_history()
        self.setStyleSheet(
            "#CardFrame,#CriterionCard,#ResultCard{background:white;border:1px solid #dfe3ea;border-radius:6px}"
            "#CardTitle,#EngineeringSectionTitle,#RelatedEntityTitle{font-weight:600;color:#111827}#EntityTitle{font-size:24px;font-weight:700}"
            "#EntityContextLine{color:#667085}#MutedText{color:#6b7280}#SummaryValue{color:#111827;font-weight:600}"
            "#ActivityTitle{color:#111827}#EngineeringSummaryText{color:#374151}#OverviewDivider{color:#e5e7eb;background:#e5e7eb;max-height:1px;border:0}"
            "#StaleBadge{background:#fff1c2;color:#8a5a00;border:1px solid #e5b94d;border-radius:4px;padding:2px 5px}"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_sidebar_density()

    def showEvent(self, event):
        super().showEvent(event)
        if self._related_event_preview_id is not None:
            self._clear_related_event_preview()
        self._sync_sidebar_density()
        if self._related_event_preview_id is None and hasattr(self, "geometry_card"):
            self.geometry_card.plan.center_on_focus()

    def eventFilter(self, watched, event):
        if (
            watched is getattr(self, "_geometry_viewport", None)
            and self._related_event_preview_id is not None
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._clear_related_event_preview()
        return super().eventFilter(watched, event)

    def _header(self, root):
        self.header = EntityHeaderWidget()
        self.header.edit_button.clicked.connect(self.edit_metadata)
        root.addWidget(self.header)

    def _wire_sidebar_actions(self):
        self.photo_preview.add_requested.connect(lambda: self.photo_manager.add())
        self.document_preview.add_requested.connect(lambda: self.document_manager.add())
        self.photo_preview.open_page_requested.connect(lambda: self.tabs.setCurrentWidget(self.photos_tab))
        self.document_preview.open_page_requested.connect(lambda: self.tabs.setCurrentWidget(self.documents_tab))

    def _sync_sidebar_density(self) -> None:
        if not hasattr(self, "photo_preview") or not hasattr(self, "document_preview"):
            return
        if not hasattr(self, "tabs") or not self.isVisible() or self.tabs.height() < 100:
            self.photo_preview.set_visible_item_limit(4)
            self.document_preview.set_visible_item_limit(4)
            return

        available = max(0, self.tabs.height() - 4)
        photo_limit = min(6, self.photo_preview._max_items)
        document_limit = min(7, self.document_preview._max_items)
        self.photo_preview.set_visible_item_limit(photo_limit)
        self.document_preview.set_visible_item_limit(document_limit)

        def required_height():
            spacing = self._sidebar_layout.spacing()
            return (
                self.summary_card.sizeHint().height()
                + self.photo_preview.sizeHint().height()
                + self.document_preview.sizeHint().height()
                + max(0, spacing) * 2
            )

        while required_height() > available and (document_limit > 4 or photo_limit > 4):
            if document_limit > 4:
                document_limit -= 1
                self.document_preview.set_visible_item_limit(document_limit)
            elif photo_limit > 4:
                photo_limit -= 2
                self.photo_preview.set_visible_item_limit(photo_limit)

    def edit_metadata(self):
        from ui.dialogs.entity_metadata_dialogs import AssessmentAreaMetadataDialog
        repo = DomainRepository(self.context.session_factory)
        domains = repo.selectable_for_site(self.controller.site_id)
        dialog = AssessmentAreaMetadataDialog(domains, self.controller.domain_id, self.area.name, self)
        if not dialog.exec():
            return
        name = dialog.name.text().strip()
        target_id, target_version = dialog.selected_domain
        if not name:
            QMessageBox.warning(self, tr("Could not save"), tr("Name is required"))
            return
        try:
            self.controller.update_assessment_area_metadata(
                self.area, name=name, target_domain_id=target_id, target_expected_version=target_version
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not save"), str(exc))
            return
        self.metadata_saved.emit(self.area.id, target_id)

    def _build_editor(self):
        evaluation, draft = self.controller.evaluation_draft(self.area)
        self.evaluation = evaluation
        self.evaluation_editor = AssessmentAreaEvaluationDialog(
            self.area, evaluation, draft, self.controller.save_evaluation, None, read_only=self.read_only
        )
        obsolete = [self.evaluation_editor.take_tab(tr(title)) for title in ("General", "Geometry", "Face condition")]
        self.assessment_tab = QWidget()
        layout = QVBoxLayout(self.assessment_tab)
        layout.setContentsMargins(4, 4, 4, 4)
        self.assessment_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.assessment_splitter.setChildrenCollapsible(False)
        self.assessment_inputs = QWidget()
        self.assessment_inputs.setMinimumWidth(500)
        inputs = QVBoxLayout(self.assessment_inputs)
        inputs.setContentsMargins(4, 2, 4, 2)
        inputs.setSpacing(6)
        if not self.read_only and not self.evaluation_editor.inspector.text().strip():
            self.evaluation_editor.inspector.setText(getattr(self.context.current_user, "display_name", "") or "")
        self.assessment_details_card = QFrame()
        self.assessment_details_card.setObjectName("CriterionCard")
        details = QVBoxLayout(self.assessment_details_card)
        details.setContentsMargins(8, 5, 8, 5)
        details.setSpacing(5)
        details.addWidget(QLabel(f"<b>{tr('Assessment details')}</b>"))
        metadata = QHBoxLayout()
        metadata.addWidget(QLabel(tr("Assessment date")))
        metadata.addWidget(self.evaluation_editor.date)
        metadata.addSpacing(12)
        metadata.addWidget(QLabel(tr("Inspector")))
        metadata.addWidget(self.evaluation_editor.inspector, 1)
        details.addLayout(metadata)
        inputs.addWidget(self.assessment_details_card)
        def section_card(title, editors, object_name):
            card = QFrame(); card.setObjectName("CriterionCard"); card.setProperty("assessmentSection", object_name)
            card_layout = QVBoxLayout(card); card_layout.setContentsMargins(8, 5, 8, 5); card_layout.setSpacing(5)
            heading = QLabel(f"<b>{tr(title)}</b>"); heading.setObjectName("EngineeringSectionTitle"); card_layout.addWidget(heading)
            for editor in editors:
                editor.setObjectName("AssessmentCriterionRow"); card_layout.addWidget(editor)
            return card, heading

        self.face_condition_input_card, self.face_condition_section_title = section_card(
            "Face condition", self.evaluation_editor.editors.values(), "faceCondition")
        inputs.addWidget(self.face_condition_input_card)
        self.geometry_input_card, self.geometry_section_title = section_card(
            "Geometry", self.evaluation_editor.geometry_editors.values(), "geometry")
        self.evaluation_editor.wall_conformance_import_widget.setParent(self.geometry_input_card)
        self.geometry_input_card.layout().insertWidget(
            1, self.evaluation_editor.wall_conformance_import_widget
        )
        self.evaluation_editor.additional_geometry_widget.setParent(self.geometry_input_card)
        self.geometry_input_card.layout().addWidget(
            self.evaluation_editor.additional_geometry_widget
        )
        inputs.addWidget(self.geometry_input_card)
        inputs.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.assessment_splitter.addWidget(self.assessment_inputs)
        self.assessment_right = QWidget()
        self.assessment_right.setMinimumWidth(360)
        self.assessment_right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right = QVBoxLayout(self.assessment_right)
        right.setContentsMargins(0, 2, 0, 0)
        right.setSpacing(7)
        matrix_context_card = QFrame()
        self.assessment_basis_card = matrix_context_card
        matrix_context_card.setObjectName("CriterionCard")
        basis = QVBoxLayout(matrix_context_card)
        basis.setContentsMargins(10, 6, 10, 6)
        basis.addWidget(QLabel(f"<b>{tr('Assessment basis')}</b>"))
        controlled = draft.matrix_template_id == "controlled_blasting_v1"
        self.assessment_basis_value = QLabel(tr("Controlled blasting") if controlled else tr("Standard blasting"))
        self.assessment_basis_value.setObjectName("MetaBadge")
        basis.addWidget(self.assessment_basis_value, 0, Qt.AlignmentFlag.AlignLeft)
        source = draft.controlled_blasting_detection_source
        detection = {
            "confirmed_link": tr("Confirmed contour blast link"),
            "no_confirmed_contour_link": tr("No confirmed contour blast link"),
            "manual_override": tr("Manual matrix selection"),
        }.get(source, source)
        self.assessment_basis_detection = QLabel(detection)
        self.assessment_basis_detection.setObjectName("MutedText")
        basis.addWidget(self.assessment_basis_detection)
        self.override_reason_label = QLabel(tr("Manual matrix selection reason"))
        basis.addWidget(self.override_reason_label)
        basis.addWidget(self.evaluation_editor.override_reason)
        manual_matrix = source == "manual_override"
        self.override_reason_label.setVisible(manual_matrix)
        self.evaluation_editor.override_reason.setVisible(manual_matrix)
        right.addWidget(matrix_context_card)
        self.result = self.evaluation_editor.take_tab(tr("Matrix"), self.assessment_right)
        self.result.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right.addWidget(self.result, 1)
        self.result.show()
        self.assessment_splitter.addWidget(self.assessment_right)
        self.assessment_splitter.setStretchFactor(0, 55)
        self.assessment_splitter.setStretchFactor(1, 45)
        self.assessment_splitter.setSizes([550, 450])
        layout.addWidget(self.assessment_splitter, 1)
        self.evaluation_editor.comments.setMaximumHeight(65)
        self.evaluation_editor.recommendations.setMaximumHeight(65)
        notes = QFormLayout()
        notes.setVerticalSpacing(3)
        self.comments_label = QLabel(tr("Comments"))
        self.recommendations_label = QLabel(tr("Recommendations"))
        notes.addRow(self.comments_label, self.evaluation_editor.comments)
        notes.addRow(self.recommendations_label, self.evaluation_editor.recommendations)
        layout.addLayout(notes)
        controls = QHBoxLayout()
        controls.addStretch()
        self.save_evaluation_button = SplitSaveButton(
            lambda: self._save_evaluation("draft"), lambda: self._save_evaluation("completed"),
            completion_text=tr("Complete assessment"),
        )
        self.save_evaluation_button.setEnabled(not self.read_only)
        controls.addWidget(self.save_evaluation_button)
        layout.addLayout(controls)
        for page in obsolete:
            page.deleteLater()
        old_history = self.evaluation_editor.take_tab(tr("History"))
        old_history.deleteLater()
        self.history = EntityHistoryWidget()
        self.history.entryActivated.connect(self._open_history_entry)

    def configure_wall_conformance_import(self, wall_conformance_tab):
        """Connect Geometry's explicit import to the active-revision diagnostic path."""
        self.wall_conformance_tab = wall_conformance_tab
        self.evaluation_editor.set_wall_conformance_summary_provider(
            self._current_wall_conformance_result,
            self._wall_conformance_import_available,
        )
        changed = getattr(wall_conformance_tab, "wall_conformance_state_changed", None)
        if changed is not None:
            changed.connect(self.evaluation_editor.refresh_wall_conformance_import_availability)

    def _wall_conformance_import_available(self):
        active_revision = self.area.active_geometry_revision()
        if self.read_only:
            return False, tr("Archived Assessment Areas and Viewer accounts are read-only.")
        if self.evaluation_editor.draft.assessment_area_geometry_revision_id != active_revision.id:
            return False, tr("Wall Conformance measurements are only available for the active geometry revision.")
        tab = getattr(self, "wall_conformance_tab", None)
        if tab is None or getattr(getattr(tab, "geometry_revision", None), "id", None) != active_revision.id:
            return False, tr("Wall Conformance is not available for the active geometry revision.")
        try:
            alignment = self.controller.load_wall_alignment(self.area, active_revision)
            if alignment is None:
                return False, tr("Wall Alignment is not defined.")
            design, actual = tab.service.current_datasets(self.controller.site_id)
        except Exception as exc:
            return False, str(exc)
        if not bool(getattr(tab.service.surface_service, "storage_available", True)):
            return False, tr("Shared file storage is unavailable for this connection.")
        if design is None or actual is None:
            missing = tr("Design surface") if design is None else tr("Actual survey")
            return False, tr("%1 is not configured for this Project.").replace("%1", missing)
        return True, ""

    def _current_wall_conformance_result(self):
        available, reason = self._wall_conformance_import_available()
        if not available:
            raise ValueError(reason)
        active_revision = self.area.active_geometry_revision()
        tab = self.wall_conformance_tab
        alignment = self.controller.load_wall_alignment(self.area, active_revision)
        current_result = getattr(tab, "result", None)
        design_dataset, actual_dataset = tab.service.current_datasets(self.controller.site_id)
        if (
            current_result is not None
            and getattr(current_result, "wall_alignment", None) == alignment
            and getattr(getattr(current_result, "design_dataset", None), "logical_id", None)
            == getattr(design_dataset, "logical_id", None)
            and getattr(getattr(current_result, "actual_dataset", None), "logical_id", None)
            == getattr(actual_dataset, "logical_id", None)
        ):
            return current_result
        return tab.service.calculate_current(
            self.controller.site_id,
            active_revision.final_geometry_frozen,
            alignment,
            tab._settings(),
        )

    def _sidebar(self, body):
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.summary_card = OverviewKeyValueCard("Summary")
        self.photo_preview = AssessmentAttachmentPreview("Photos", "photo", max_items=6)
        self.document_preview = AssessmentAttachmentPreview("Documents", "document", max_items=7)
        for widget in (self.summary_card, self.photo_preview, self.document_preview):
            widget.setMinimumWidth(250)
            widget.setMaximumWidth(300)
        right.addWidget(self.summary_card)
        right.addWidget(self.photo_preview)
        right.addWidget(self.document_preview)
        right.addStretch()
        self._sidebar_layout = right
        body.addLayout(right, 0)

    def _overview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        workspace = QGridLayout()
        workspace.setHorizontalSpacing(8)
        workspace.setVerticalSpacing(8)
        workspace.setColumnStretch(0, 1)
        workspace.setColumnStretch(1, 0)

        self.assessment_result = OverviewKeyValueCard("Assessment result", open_label="Open ›")
        self.assessment_result.open_requested.connect(lambda: self.tabs.setCurrentWidget(self.assessment_tab))
        workspace.addWidget(self.assessment_result, 0, 0)

        self.related_events = AssessmentRelatedEventList("Related blast events")
        self.related_events.entity_activated.connect(self._preview_related_event)
        self.related_events.entity_action_requested.connect(self._open_related_event)
        workspace.addWidget(self.related_events, 1, 0, 2, 1)

        self.geometry_card = AssessmentGeometryCard("Plan / geometry", action_label="Edit boundaries")
        self.geometry_card.action_requested.connect(self._request_edit_boundaries)
        self.geometry_card.plan.view.escape_requested.connect(self._clear_related_event_preview)
        self._geometry_viewport = self.geometry_card.plan.view.viewport()
        self._geometry_viewport.installEventFilter(self)
        workspace.addWidget(self.geometry_card, 0, 1, 2, 1)
        workspace.setRowMinimumHeight(1, self.related_events.LIST_HEIGHT)

        state_row = QWidget()
        state_layout = QHBoxLayout(state_row)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.setSpacing(8)
        self.state_summary_card = AssessmentStateSummaryCard("Geometry / face condition")
        self.state_summary_card.open_requested.connect(lambda: self.tabs.setCurrentWidget(self.assessment_tab))
        self.matrix_card = AssessmentMatrixCard("Assessment matrix")
        self.matrix_preview = self.matrix_card.preview
        self.matrix_basis_text = QLabel()
        self.matrix_basis_text.setObjectName("MutedText")
        self.matrix_card.layout.addWidget(self.matrix_basis_text)
        state_layout.addWidget(self.state_summary_card, 1)
        state_layout.addWidget(self.matrix_card, 0)
        workspace.addWidget(state_row, 2, 1)
        layout.addLayout(workspace)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.comments_card = AssessmentCommentsCard("Comments / recommendations")
        self.comments_card.section_open_requested.connect(self._open_overview_section)
        self.recent_activity = AssessmentRecentActivityCard()
        self.recent_activity.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.recent_activity.open_history_requested.connect(lambda: self.tabs.setCurrentWidget(self.history))
        bottom.addWidget(self.comments_card, 3)
        bottom.addWidget(self.recent_activity, 2)
        layout.addLayout(bottom)
        self.tabs.addTab(page, tr("Overview"))

    def _open_related_event(self, event_id):
        event = self.controller.event(event_id)
        if event is not None:
            self.related_blast_event_requested.emit(event.id, event.event_type, self.controller.domain_id)

    def _preview_related_event(self, event_id):
        area_revision = self.area.active_geometry_revision()
        link = next(
            (
                item for item in self.area.links_for_revision()
                if item.status != "excluded" and item.blast_event_id == event_id
            ),
            None,
        )
        if link is None:
            return
        event = self.controller.links.event(link.blast_event_id)
        linked_revision = self.controller.links.linked_revision(event, link)
        if linked_revision is None:
            return
        dataset = next(
            (
                item for item in self.controller.state.datasets
                if item.id == (area_revision.source_dataset_ids[0] if area_revision.source_dataset_ids else None)
            ),
            None,
        )
        project_lines = dataset.lines if dataset else []
        plan = self.geometry_card.plan
        plan.set_comparison_geometry(
            area_revision.final_geometry_frozen,
            linked_revision.plan_geometry,
            project_lines,
            focus_geometry=area_revision.final_geometry_frozen,
            recenter=False,
        )
        plan._toggle_lines(self.geometry_card.lines.isChecked())
        area_rect = plan._geometry_path(area_revision.final_geometry_frozen).boundingRect()
        event_rect = plan._geometry_path(linked_revision.plan_geometry).boundingRect()
        combined = area_rect.united(event_rect)
        if not combined.isNull():
            margin = max(max(combined.width(), combined.height()) * 0.12, 1.0)
            plan.view.fit_to_rect(combined.adjusted(-margin, -margin, margin, margin))
        self._related_event_preview_id = event_id
        plan.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _clear_related_event_preview(self):
        if self._related_event_preview_id is None:
            return
        self._related_event_preview_id = None
        self.related_events.list.clearSelection()
        rev = self.area.active_geometry_revision()
        dataset = next(
            (
                item for item in self.controller.state.datasets
                if item.id == (rev.source_dataset_ids[0] if rev.source_dataset_ids else None)
            ),
            None,
        )
        self.geometry_card.set_geometry(
            rev.final_geometry_frozen,
            dataset.lines if dataset else [],
            focus_geometry=rev.final_geometry_frozen,
        )
        self.geometry_card.plan._toggle_lines(self.geometry_card.lines.isChecked())
        self.geometry_card.plan.center_on_focus()

    def _open_overview_section(self, key):
        if key == "linked_events" and hasattr(self, "linked_events_tab"):
            self.tabs.setCurrentWidget(self.linked_events_tab)
        elif key in {"assessment", "result", "geometry", "face_condition", "comments", "recommendations"}:
            self.tabs.setCurrentWidget(self.assessment_tab)

    def _history_entries(self):
        return self.history_repo.for_assessment_area(self.area.id) if self.history_repo is not None else []

    def _refresh_history(self):
        entries = self._history_entries()
        self.history.set_entries(entries)
        if hasattr(self, "recent_activity"):
            self.recent_activity.set_entries(entries)
        return entries

    def _open_history_entry(self, entry):
        if entry.source_type == "assessment_geometry":
            revision = next((item for item in self.area.geometry_revisions if item.id == entry.source_id), None)
            if revision is not None:
                dataset = next((d for d in self.controller.state.datasets if d.id == (revision.source_dataset_ids[0] if revision.source_dataset_ids else None)), None)
                open_geometry_revision(self, revision=revision, project_lines=dataset.lines if dataset else [], assessment=True)
            return
        if entry.source_type == "assessment_evaluation":
            revision = next((item for item in self.evaluation.revisions if item.id == entry.source_id), None)
            if revision is not None:
                open_assessment_revision(
                    self, area=self.area, evaluation=self.evaluation, revision=revision,
                    attachment_service=self.controller.attachments,
                )

    def _refresh_overview_and_sidebar(self):
        self._related_event_preview_id = None
        rev = self.area.active_geometry_revision()
        active = self.evaluation.active_revision()
        visible_links = [x for x in self.area.links_for_revision() if x.status != "excluded"]
        progress = assessment_progress_for(self.area, self.evaluation)
        status = tr(ASSESSMENT_PROGRESS_LABELS[progress])
        interval = format_assessment_elevation_interval(rev.min_elevation, rev.max_elevation)
        history_entries = self._history_entries()
        self.header.set_content(
            title=self.area.name,
            status_text=status,
            status_state=progress,
            archived=self.area.is_archived,
            can_edit=not self.read_only,
            meta_values=(
                f"{tr('ID')}: {self.area.id}",
                f"{tr('Project / Domain')}: {self.project_name} / {self.domain_name}",
                f"{tr('Interval')}: {interval}",
                f"{tr('Geometry rev.')}: {rev.revision_number}",
            ),
        )

        dataset = next((d for d in self.controller.state.datasets if d.id == (rev.source_dataset_ids[0] if rev.source_dataset_ids else None)), None)
        geometry_source = dataset.source_file_name if dataset is not None else tr("Free boundary")
        self.geometry_card.set_geometry(
            rev.final_geometry_frozen,
            dataset.lines if dataset else [],
            focus_geometry=rev.final_geometry_frozen,
        )
        self.geometry_card.set_action_enabled(not self.read_only)

        dai = f"{active.design_achievement_index:.3f}" if active and active.design_achievement_index is not None else "—"
        fci = f"{active.face_condition_index:.3f}" if active and active.face_condition_index is not None else "—"
        quadrant = result_label(active.result_label) if active else "—"
        inspector = active.inspector if active and active.inspector else "—"
        assessment_date = active.assessment_date if active and active.assessment_date else self.area.assessment_date
        area_height = None
        if rev.min_elevation is not None and rev.max_elevation is not None:
            area_height = abs(float(rev.max_elevation) - float(rev.min_elevation))
        self.assessment_result.set_rows((
            ("Assessment date", _date(assessment_date)),
            ("Inspector", inspector),
            ("Area height", _number(area_height, " m")),
            ("DAI", dai),
            ("FCI", fci),
            ("Result", _value(quadrant)),
        ))

        design_inputs = active.design_inputs if active and active.design_inputs else {}
        geometry_lines = []
        if active:
            geometry_lines = [
                f"{tr('Angle deviation')}: {_number(design_inputs.get('bench_angle_shortfall_deg'), '°')}",
                f"{tr('Berm deviation')}: {_number(design_inputs.get('berm_width_deficit_m'), ' m')}",
                f"{tr('Toe deviation')}: {_number(design_inputs.get('toe_offset_from_design_m'), ' m')}",
            ]

        face_rows = (
            ("Contour hole traces", _face_condition_value(active, "visible_drillhole_traces", " %")),
            ("Loose blocks", _face_condition_value(active, "loose_blocks")),
            ("Face profile", _face_condition_value(active, "face_profile")),
            ("Crest loss", _face_condition_value(active, "crest_loss", " m")),
            ("Blast damage", _face_condition_value(active, "damage", " /m²")),
            ("Blast cracks", _face_condition_value(active, "open_cracks")),
        )
        visible_face = [f"{tr(name)}: {value}" for name, value in face_rows if value != "—"]
        self.state_summary_card.set_sections((
            ("Geometry", geometry_lines or [tr("No geometry assessment data yet")]),
            ("Face condition", visible_face or [tr("No face-condition data yet")]),
        ))

        snapshot = active.matrix_template_snapshot if active else {}
        dai_threshold = snapshot.get("design_achievement_threshold", .65) if snapshot else .65
        fci_threshold = snapshot.get("face_condition_threshold", .60) if snapshot else .60
        self.matrix_preview.set_result(
            dai=active.design_achievement_index if active else None,
            fci=active.face_condition_index if active else None,
            dai_threshold=dai_threshold,
            fci_threshold=fci_threshold,
        )
        controlled = bool(active and active.controlled_blasting_present)
        basis = tr("Controlled blasting") if controlled else tr("Standard blasting")
        self.matrix_basis_text.setText(f"{basis} · {_value(quadrant)}")

        related_rows = []
        for link in visible_links:
            event = self.controller.links.event(link.blast_event_id)
            workflow = blast_workflow_for(self.controller.state, event)
            title = f"{tr('Block')} {event.name}" if event.event_type == "production" else f"{tr('Contour blast')} {event.name}"
            subtitle = f"{event.id} · {tr('Horizon')} {_value(f'{event.elevation:g}')} m"
            if link.status == "suggested":
                subtitle += f" · {tr('Suggested')}"
            related_rows.append(RelatedEntityRow(
                event.id,
                title,
                subtitle,
                tr(WORKFLOW_LABELS[workflow]),
                getattr(workflow, "value", workflow),
                self.controller.links.is_stale(link),
                action_text=tr("Go to ›"),
            ))
        self.related_events.set_rows(related_rows, empty_text="No linked blast events")

        self.comments_card.set_sections((
            ("comments", "Comments", [active.comments if active and active.comments else tr("No comments")]),
            ("recommendations", "Recommendations", [active.recommendations if active and active.recommendations else tr("No recommendations")]),
        ))
        self.recent_activity.set_entries(history_entries)

        persisted = self.evaluation in self.controller.state.evaluations
        photos = self.controller.attachments.list_for_owner("assessment_evaluation", self.evaluation.id, "photo") if persisted else []
        documents = self.controller.attachments.list_for_owner("assessment_evaluation", self.evaluation.id, "document") if persisted else []
        created_actor, created_at, updated_at = _history_bounds(history_entries)
        if created_at is None:
            created_at = min((item.created_at for item in self.area.geometry_revisions), default=rev.created_at)
        fallback_updates = [rev.created_at]
        if active is not None:
            fallback_updates.append(active.created_at)
        if updated_at is None:
            updated_at = max(fallback_updates)
        self.summary_card.set_rows((
            ("Created by", created_actor or "—"),
            ("Created", _datetime_text(created_at)),
            ("Last updated", _datetime_text(updated_at)),
            ("Geometry file", geometry_source),
            ("Geometry revision", f"{tr('Rev.')} {rev.revision_number}"),
            ("Evaluation", f"{tr('Rev.')} {active.revision_number}" if active else "—"),
            ("Photos", len(photos)),
            ("Documents", len(documents)),
        ))
        self.photo_preview.set_items(self.controller.attachments, photos, "No photos yet", can_add=not self.read_only)
        self.document_preview.set_items(self.controller.attachments, documents, "No documents yet", can_add=not self.read_only)
        self._sync_sidebar_density()

    def _linked_events(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.links_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.links_splitter.setChildrenCollapsible(False)
        left = QWidget()
        left.setMinimumWidth(300)
        left.setMaximumWidth(380)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(6)
        self.links_list = QListWidget()
        self.links_list.setSpacing(5)
        self.links_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.links_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.links_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_layout.addWidget(self.links_list, 1)
        selected_actions = QHBoxLayout()
        global_actions = QHBoxLayout()
        self.link_action_buttons = {}
        for label, translated, callback in (("Confirm", tr("Confirm"), self.confirm_link), ("Exclude", tr("Exclude"), self.exclude_link), ("Restore suggestion", tr("Restore suggestion"), self.restore_link)):
            button = QPushButton(translated)
            button.setEnabled(not self.read_only)
            button.clicked.connect(callback)
            selected_actions.addWidget(button)
            self.link_action_buttons[label] = button
        for label, translated, callback in (("Add manually", tr("Add manually"), self.add_manual_link), ("Recalculate links", tr("Recalculate links"), self.recalculate_links)):
            button = QPushButton(translated)
            button.setEnabled(not self.read_only)
            button.clicked.connect(callback)
            global_actions.addWidget(button)
            self.link_action_buttons[label] = button
        left_layout.addLayout(selected_actions)
        left_layout.addLayout(global_actions)
        self.links_splitter.addWidget(left)
        detail = QFrame()
        detail.setObjectName("CriterionCard")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(8, 6, 8, 8)
        detail_layout.setSpacing(5)
        header = QHBoxLayout()
        names = QVBoxLayout()
        self.link_event_name = QLabel(tr("Select a linked event"))
        self.link_event_name.setObjectName("CardTitle")
        self.link_event_detail = QLabel()
        self.link_event_detail.setObjectName("MutedText")
        names.addWidget(self.link_event_name)
        names.addWidget(self.link_event_detail)
        header.addLayout(names, 1)
        self.link_event_type = QLabel()
        self.link_event_type.setObjectName("MetaBadge")
        header.addWidget(self.link_event_type)
        detail_layout.addLayout(header)
        self.link_status_line = QLabel()
        self.link_status_line.setTextFormat(Qt.TextFormat.RichText)
        detail_layout.addWidget(self.link_status_line)
        self.link_warning = QLabel()
        self.link_warning.setWordWrap(True)
        self.link_warning.setStyleSheet("background:#fff8e6;color:#8a5a00;border:1px solid #e5b94d;border-radius:4px;padding:5px")
        self.link_warning.hide()
        detail_layout.addWidget(self.link_warning)
        legend = f"<span style='color:#1261a0'>■</span> {tr('Assessment area')} &nbsp;&nbsp; <span style='color:#d97706'>■</span> {tr('Blast event')} &nbsp;&nbsp; <span style='color:#9ca3af'>━</span> {tr('Project Lines')}"
        self.link_preview = PlanGeometryWidget()
        self.link_preview.reimport_button.hide()
        self.link_preview.set_context(legend)
        self.link_preview.use_center_control()
        detail_layout.addWidget(self.link_preview, 1)
        self.links_splitter.addWidget(detail)
        self.links_splitter.setStretchFactor(0, 0)
        self.links_splitter.setStretchFactor(1, 1)
        layout.addWidget(self.links_splitter)
        self.linked_events_tab = page
        self._links_splitter_initialized = False
        self.links_list.currentRowChanged.connect(self._link_selection_changed)
        self.tabs.currentChanged.connect(self._linked_tab_changed)
        self.tabs.addTab(page, tr("Linked events"))
        self._link_preview_initialized = False
        self.refresh_links()

    def _linked_tab_changed(self, index):
        if self.tabs.widget(index) is self.linked_events_tab and not self._links_splitter_initialized:
            QTimer.singleShot(0, self._initialize_links_splitter)

    def _initialize_links_splitter(self):
        if self._links_splitter_initialized or not self.links_splitter.isVisible():
            return
        total = self.links_splitter.width()
        if total <= 0:
            QTimer.singleShot(0, self._initialize_links_splitter)
            return
        left_width = max(300, min(340, total // 3))
        right_width = max(1, total - left_width - self.links_splitter.handleWidth())
        self.links_splitter.setSizes([left_width, right_width])
        self._links_splitter_initialized = True
        QTimer.singleShot(0, self.link_preview.center_on_focus)

    def refresh_links(self):
        selected = self._selected_link()
        selected_id = selected.id if selected else None
        links = self.area.links_for_revision()
        self.links_list.clear()
        self._link_item_widgets = []
        for link in links:
            event = self.controller.links.event(link.blast_event_id)
            widget = AssessmentLinkListItem(event, link, self.controller.links.is_stale(link))
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, link.id)
            self.links_list.addItem(item)
            self.links_list.setItemWidget(item, widget)
            self._link_item_widgets.append(widget)
        row = next((index for index, item in enumerate(links) if item.id == selected_id), 0 if links else -1)
        if row >= 0:
            self.links_list.setCurrentRow(row)
        else:
            self.refresh_link_preview()

    def _link_selection_changed(self, _row):
        for row, widget in enumerate(self._link_item_widgets):
            widget.set_selected(row == self.links_list.currentRow())
        self.refresh_link_preview()

    def _selected_link(self):
        row = self.links_list.currentRow() if hasattr(self, "links_list") else -1
        links = self.area.links_for_revision()
        return links[row] if 0 <= row < len(links) else None

    def _change_link(self, method):
        if not self._ensure_editable():
            return
        link = self._selected_link()
        if link:
            method(self.area, link.id)
            self.refresh_links()
            self._refresh_overview_and_sidebar()
            self._refresh_history()

    def confirm_link(self):
        self._change_link(self.controller.confirm_event_link)

    def exclude_link(self):
        self._change_link(self.controller.exclude_event_link)

    def restore_link(self):
        self._change_link(self.controller.restore_event_link)

    def recalculate_links(self):
        if self._ensure_editable():
            self.controller.refresh_event_link_suggestions(self.area)
            self.refresh_links()
            self._refresh_overview_and_sidebar()

    def add_manual_link(self):
        if not self._ensure_editable():
            return
        events = [e for e in self.controller.state.blast_events if not e.is_archived]
        labels = [f"{e.name} ({e.event_type}, {e.elevation:g})" for e in events]
        selected, ok = QInputDialog.getItem(self, tr("Add linked event"), tr("BlastEvent"), labels, 0, False)
        if ok and selected:
            self.controller.add_manual_event_link(self.area, events[labels.index(selected)].id)
            self.refresh_links()
            self._refresh_overview_and_sidebar()
            self._refresh_history()

    def refresh_link_preview(self):
        area_revision = self.area.active_geometry_revision()
        dataset = next((d for d in self.controller.state.datasets if d.id == (area_revision.source_dataset_ids[0] if area_revision.source_dataset_ids else None)), None)
        project_lines = dataset.lines if dataset else []
        link = self._selected_link()
        if not link:
            self.link_event_name.setText(tr("Select a linked event"))
            self.link_event_detail.clear()
            self.link_event_type.clear()
            self.link_status_line.clear()
            self.link_warning.hide()
            self.link_preview.set_comparison_geometry(
                area_revision.final_geometry_frozen, None, project_lines,
                focus_geometry=area_revision.final_geometry_frozen,
                recenter=not self._link_preview_initialized,
            )
            self._link_preview_initialized = True
            return
        event = self.controller.links.event(link.blast_event_id)
        revision = self.controller.links.linked_revision(event, link)
        stale = self.controller.links.is_stale(link)
        candidate = self.controller.links.evaluate_event(self.area, event)
        self.link_event_name.setText(event.name)
        self.link_event_type.setText(tr(event.event_type.title()))
        self.link_event_detail.setText(f"{event.elevation:g} m · {tr('Revision')} {link.geometry_revision_id}")
        badges = [tr(link.status.title()), tr("Stale") if stale else tr("Current")]
        if candidate.spatial_matches:
            badges.append(tr("Spatial match"))
        self.link_status_line.setText(" &nbsp; ".join(
            f"<span style='background:#eef2f7;border:1px solid #d5dbe3;border-radius:4px;padding:2px 5px'><b>{value}</b></span>"
            for value in badges
        ))
        self.link_warning.setText(tr("Referenced geometry revision is unavailable. Current event geometry was not substituted."))
        self.link_warning.setVisible(revision is None)
        self.link_preview.set_comparison_geometry(
            area_revision.final_geometry_frozen,
            revision.plan_geometry if revision else None,
            project_lines,
            focus_geometry=area_revision.final_geometry_frozen,
            recenter=not self._link_preview_initialized,
        )
        self._link_preview_initialized = True

    def _attachment_tab(self, title):
        kind = "photo" if title == "Photos" else "document"
        persisted = self.evaluation in self.controller.state.evaluations
        owner_id = self.evaluation.id if persisted else None

        def ensure_owner():
            owner, rollback = self.controller.prepare_evaluation_attachment_owner(self.area, self.evaluation)
            self.evaluation = owner
            return owner, rollback

        page, manager = create_attachment_tab_page(
            self.controller.attachments,
            "assessment_evaluation",
            owner_id,
            kind,
            read_only=self.read_only,
            ensure_owner=ensure_owner,
        )
        manager.changed.connect(self._after_attachment_change)
        self.attachment_controls = getattr(self, "attachment_controls", [])
        self.attachment_controls.append((kind, manager))
        self.tabs.addTab(page, tr(title))
        if kind == "photo":
            self.photos_tab = page
            self.photo_manager = manager
        else:
            self.documents_tab = page
            self.document_manager = manager

    def _after_attachment_change(self):
        self._refresh_overview_and_sidebar()
        self._refresh_history()

    def _save_evaluation(self, status):
        if not self._ensure_editable():
            return
        if self.evaluation_editor.save(status):
            self._refresh_attachment_controls()
            self._refresh_overview_and_sidebar()
            if hasattr(self, "_refresh_history"):
                self._refresh_history()

    def _refresh_attachment_controls(self):
        persisted = self.evaluation in self.controller.state.evaluations
        for _kind, manager in getattr(self, "attachment_controls", []):
            manager.owner_id = self.evaluation.id if persisted else None
            manager.refresh()

    def _ensure_editable(self):
        if self.read_only:
            QMessageBox.warning(self, tr("Read only"), tr("Archived Assessment Areas and Viewer accounts are read-only."))
            return False
        return True

    def _request_edit_boundaries(self):
        if self._ensure_editable():
            self.edit_boundaries_requested.emit(self.area.id)
