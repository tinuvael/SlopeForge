from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.context import AppContext
from app.localization import tr
from domain.blasting.technical_card import polygon_area_m2
from domain.blasting.workflow import (
    ASSESSMENT_PROGRESS_LABELS,
    WORKFLOW_LABELS,
    BlastWorkflowState,
    assessment_progress_for,
)
from infrastructure.services.production_blast_service import ProductionBlastService
from repositories.domain_repository import DomainRepository
from repositories.entity_history_repository import EntityHistoryRepository
from repositories.production_blast_repository import ProductionBlastRepository, ProductionBlastRow
from ui.block_dialog import BlockDialog
from ui.pages.block_card_widgets import EmptySection, format_datetime, format_decimal
from ui.pages.block_overview_widgets import (
    BlockAttachmentPreview,
    BlockGeometryCard,
    BlockNotesCard,
    BlockRecentActivityCard,
    BlockRelatedEntityList,
    BlockSectionHost,
)
from ui.pages.entity_history_revision_viewer import open_geometry_revision, open_technical_card_revision
from ui.pages.entity_history_widget import EntityHistoryWidget
from ui.pages.entity_overview_widgets import (
    EngineeringSummaryCard,
    EntityHeaderWidget,
    OverviewKeyValueCard,
    RelatedEntityRow,
)
from ui.pages.entity_page_controller import EntityPageController
from ui.pages.entity_tabs import create_attachment_tab_page, create_entity_tabs
from ui.pages.technical_card_widgets import (
    ActualExecutionEditorWidget,
    BlastDesignEditorWidget,
    GeomechanicsEditorWidget,
    TechnicalCardEditorWidget,
)
from ui.presentation_labels import domain_message, format_assessment_elevation_interval


def _fmt_number(value, unit="", digits=2):
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{value}{unit}"
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return f"{text}{unit}"


def _fmt_dateish(value):
    if value in (None, ""):
        return "—"
    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")
    text = str(value)
    try:
        return datetime.fromisoformat(text).strftime("%d.%m.%Y")
    except ValueError:
        return text


def _fmt_history_time(value):
    return value.strftime("%d.%m.%Y %H:%M") if value else "—"


def _pattern(burden, spacing):
    if burden is None and spacing is None:
        return "—"
    return f"{_fmt_number(burden)} × {_fmt_number(spacing)} m"


def _qprime_and_category(geo):
    if geo is None:
        return None, None
    if None in (geo.rqd_percent, geo.jn, geo.jr, geo.ja) or geo.jn == 0 or geo.ja == 0:
        return None, None
    qprime = (float(geo.rqd_percent) / float(geo.jn)) * (float(geo.jr) / float(geo.ja))
    if qprime < 1:
        category = tr("Very unstable")
    elif qprime < 4:
        category = tr("Unstable")
    elif qprime < 10:
        category = tr("Moderately stable")
    else:
        category = tr("Stable")
    return qprime, category


def _history_bounds(entries):
    timed = [entry for entry in entries if entry.timestamp]
    if not timed:
        return None, None, None
    created = min(timed, key=lambda item: item.timestamp)
    updated = max(timed, key=lambda item: item.timestamp)
    return created.actor or "—", created.timestamp, updated.timestamp


class _NullAttachmentService:
    def list_for_owner(self, *args):
        return []


class BlockPage(QWidget):
    data_changed = Signal()
    metadata_saved = Signal(str, int)
    related_assessment_requested = Signal(str, int)

    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.domain_repo = DomainRepository(context.session_factory)
        self.block_repo = ProductionBlastRepository(context.session_factory)
        self.block_service = ProductionBlastService(self.block_repo, self.domain_repo)
        self.history_repo = EntityHistoryRepository(context.session_factory)
        self.filters = {"number_query": None, "domain_id": None, "site_id": None, "status": None}
        self.current_block: ProductionBlastRow | None = None
        self._overview_photo_count = 0
        self._overview_document_count = 0
        self._overview_history_entries = []
        self._related_area_preview_id = None
        self.technical_card_editor = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.header = EntityHeaderWidget("Select a block")
        self.header.edit_button.clicked.connect(self.edit_current_block)
        layout.addWidget(self.header)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.tabs = create_entity_tabs()
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.overview_tab = QWidget()
        overview_layout = QVBoxLayout(self.overview_tab)
        overview_layout.setSpacing(8)
        overview_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.overview_stack_widget = QWidget()
        self.overview_stack_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        overview_stack = QVBoxLayout(self.overview_stack_widget)
        overview_stack.setContentsMargins(0, 0, 0, 0)
        overview_stack.setSpacing(8)
        self.general_info = OverviewKeyValueCard("General information")
        self.general_info.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.related_areas = BlockRelatedEntityList(
            "Related assessment areas",
            expand_viewport=True,
        )
        self.related_areas.entity_activated.connect(self._preview_related_area)
        self.related_areas.entity_action_requested.connect(self._open_related_area)
        self.notes = BlockNotesCard("Notes", expand_editor=True)
        self.notes.save_requested.connect(self._autosave_comment)
        # On taller displays the metadata column shares the plan's row height:
        # the relationship viewport gets the larger portion so an extra area is
        # visible, while General information and Notes remain balanced.
        overview_stack.addWidget(self.general_info, 1)
        overview_stack.addWidget(self.related_areas, 2)
        overview_stack.addWidget(self.notes, 1)
        self.geometry_card = BlockGeometryCard(
            "Plan / geometry",
            action_label="Reimport",
            expand_horizontally=True,
        )
        self.geometry_card.action_requested.connect(self._reimport_current_geometry)
        self.geometry_card.plan.view.escape_requested.connect(self._clear_related_area_preview)
        self._geometry_viewport = self.geometry_card.plan.view.viewport()
        self._geometry_viewport.installEventFilter(self)
        # Keep metadata compact while letting the plan use the larger share of
        # the available Overview width on wide engineering desktops.
        top.addWidget(self.overview_stack_widget, 4)
        top.addWidget(self.geometry_card, 6)
        overview_layout.addLayout(top)

        self.engineering_summary = EngineeringSummaryCard()
        self.engineering_summary.section_open_requested.connect(self._open_engineering_section)
        overview_layout.addWidget(self.engineering_summary)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.engineering_notes = EngineeringSummaryCard("Engineering notes")
        self.engineering_notes.section_open_requested.connect(self._open_engineering_section)
        self.recent_activity = BlockRecentActivityCard()
        self.recent_activity.open_history_requested.connect(lambda: self.tabs.setCurrentWidget(self.history_tab))
        bottom.addWidget(self.engineering_notes, 3)
        bottom.addWidget(self.recent_activity, 2)
        overview_layout.addLayout(bottom)

        self.tabs.addTab(self.overview_tab, tr("General information"))
        self.geomechanics_tab = BlockSectionHost(EmptySection())
        self.design_tab = BlockSectionHost(EmptySection())
        self.execution_tab = BlockSectionHost(EmptySection())
        self.tabs.addTab(self.design_tab, tr("Blast design"))
        self.tabs.addTab(self.geomechanics_tab, tr("Geomechanics"))
        self.tabs.addTab(self.execution_tab, tr("Execution fact"))
        self.photos_tab, self.photos_tab_count, self.manage_photos_button = self._make_attachment_tab("photo")
        self.documents_tab, self.documents_tab_count, self.manage_documents_button = self._make_attachment_tab("document")
        self.tabs.addTab(self.photos_tab, tr("Photos"))
        self.tabs.addTab(self.documents_tab, tr("Documents"))
        self.history_tab = EntityHistoryWidget()
        self.history_tab.entryActivated.connect(self._open_history_entry)
        self.tabs.addTab(self.history_tab, tr("History"))
        left.addWidget(self.tabs)

        self.engineering_actions_widget = QWidget()
        engineering_actions = QHBoxLayout(self.engineering_actions_widget)
        engineering_actions.setContentsMargins(0, 0, 0, 0)
        engineering_actions.addStretch()
        self.engineering_save = QPushButton(tr("Save"))
        self.engineering_save.setObjectName("TechnicalCardSaveButton")
        self.engineering_save.setProperty("role", "primary")
        self.engineering_save.setMinimumSize(108, 32)
        self.engineering_save.clicked.connect(self._save_technical_card_draft)
        self.engineering_save.setEnabled(False)
        engineering_actions.addWidget(self.engineering_save)
        left.addWidget(self.engineering_actions_widget)
        self.tabs.currentChanged.connect(self._sync_engineering_actions_visibility)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        right.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.summary = OverviewKeyValueCard("Summary")
        self.photos = BlockAttachmentPreview("Photos", "photo", max_items=6)
        self.documents = BlockAttachmentPreview("Documents", "document", max_items=7)
        self.photos.add_requested.connect(lambda: self.photo_manager.add())
        self.documents.add_requested.connect(lambda: self.document_manager.add())
        self.photos.open_page_requested.connect(lambda: self._open_attachments("photo"))
        self.documents.open_page_requested.connect(lambda: self._open_attachments("document"))
        for widget in (self.summary, self.photos, self.documents):
            widget.setMinimumWidth(250)
            widget.setMaximumWidth(300)
        right.addWidget(self.summary)
        right.addWidget(self.photos)
        right.addWidget(self.documents)
        right.addStretch()
        self._sidebar_layout = right
        body.addLayout(right, 0)
        layout.addLayout(body)

        self.setStyleSheet(
            """
            #CardFrame { background:#ffffff; border:1px solid #dfe3ea; border-radius:8px; }
            #CardTitle, #EngineeringSectionTitle, #RelatedEntityTitle { font-weight:600; color:#111827; }
            #EntityTitle { font-size:24px; font-weight:700; }
            #EntityContextLine { color:#667085; }
            #MutedText { color:#6b7280; }
            #SummaryValue { color:#111827; font-weight:600; }
            #ActivityTitle { color:#111827; }
            #EngineeringSummaryText { color:#374151; }
            #OverviewDivider { color:#e5e7eb; background:#e5e7eb; max-height:1px; border:0; }
            #StaleBadge { background:#fff1c2; color:#8a5a00; border:1px solid #e5b94d; border-radius:4px; padding:2px 5px; }
            """
        )
        self._sync_engineering_actions_visibility()
        self.refresh()

    def _make_attachment_tab(self, kind):
        page, manager = create_attachment_tab_page(
            _NullAttachmentService(), "blast_event", None, kind, read_only=True
        )
        manager.changed.connect(lambda: self._render_current_block())
        if kind == "photo":
            self.photo_manager = manager
        else:
            self.document_manager = manager
        return page, QLabel(), manager.mutation_buttons[0]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_sidebar_density()

    def showEvent(self, event):
        super().showEvent(event)
        if self._related_area_preview_id is not None:
            self._clear_related_area_preview()
        self._sync_sidebar_density()
        if self._related_area_preview_id is None:
            self.geometry_card.plan.center_on_focus()

    def _sync_sidebar_density(self) -> None:
        if not hasattr(self, "photos") or not hasattr(self, "documents"):
            return
        if not hasattr(self, "tabs") or not self.isVisible() or self.tabs.height() < 100:
            self.photos.set_visible_item_limit(4)
            self.documents.set_visible_item_limit(4)
            return
        available = max(0, self.tabs.height() - 4)
        photo_limit = min(6, self.photos._max_items)
        document_limit = min(7, self.documents._max_items)
        self.photos.set_visible_item_limit(photo_limit)
        self.documents.set_visible_item_limit(document_limit)

        def required_height():
            spacing = self._sidebar_layout.spacing()
            return (
                self.summary.sizeHint().height()
                + self.photos.sizeHint().height()
                + self.documents.sizeHint().height()
                + max(0, spacing) * 2
            )

        while required_height() > available and (document_limit > 4 or photo_limit > 4):
            if document_limit > 4:
                document_limit -= 1
                self.documents.set_visible_item_limit(document_limit)
            elif photo_limit > 4:
                photo_limit -= 2
                self.photos.set_visible_item_limit(photo_limit)

    def _sync_engineering_actions_visibility(self, *_args):
        self.engineering_actions_widget.setVisible(self.tabs.currentIndex() in (1, 2, 3))

    def eventFilter(self, watched, event):
        if (
            watched is getattr(self, "_geometry_viewport", None)
            and self._related_area_preview_id is not None
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._clear_related_area_preview()
        return super().eventFilter(watched, event)

    def _open_engineering_section(self, key):
        target = {
            "geomechanics": self.geomechanics_tab,
            "blast_design": self.design_tab,
            "execution": self.execution_tab,
        }.get(key)
        if target is not None:
            self.tabs.setCurrentWidget(target)

    def _open_related_area(self, area_id):
        if self.current_block is not None:
            self.related_assessment_requested.emit(area_id, self.current_block.domain_id)

    def _preview_related_area(self, area_id):
        if self.current_block is None or self.entity_controller is None:
            return
        event = self.entity_controller.production_event(self.current_block.id)
        geometry = event.active_geometry_revision() if event is not None else None
        area = next(
            (item for item in self.entity_controller.state.assessment_areas if item.id == area_id),
            None,
        )
        area_revision = area.active_geometry_revision() if area is not None else None
        if geometry is None or area_revision is None:
            return
        area_geometry = area_revision.final_geometry_frozen
        if area_geometry is None:
            return
        dataset = self.entity_controller.state.active_dataset()
        lines = dataset.lines if dataset else []
        plan = self.geometry_card.plan
        plan.set_comparison_geometry(
            geometry.plan_geometry,
            area_geometry,
            lines,
            focus_geometry=geometry.plan_geometry,
            recenter=False,
        )
        plan._toggle_lines(self.geometry_card.lines.isChecked())
        block_rect = plan._geometry_path(geometry.plan_geometry).boundingRect()
        area_rect = plan._geometry_path(area_geometry).boundingRect()
        combined = block_rect.united(area_rect)
        if not combined.isNull():
            margin = max(max(combined.width(), combined.height()) * 0.12, 1.0)
            plan.view.fit_to_rect(
                combined.adjusted(-margin, -margin, margin, margin)
            )
        self._related_area_preview_id = area_id
        plan.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _clear_related_area_preview(self):
        if self._related_area_preview_id is None:
            return
        self._related_area_preview_id = None
        self.related_areas.list.clearSelection()
        if self.current_block is None or self.entity_controller is None:
            return
        event = self.entity_controller.production_event(self.current_block.id)
        geometry = event.active_geometry_revision() if event is not None else None
        if geometry is None:
            return
        dataset = self.entity_controller.state.active_dataset()
        lines = dataset.lines if dataset else []
        plan = self.geometry_card.plan
        self.geometry_card.set_geometry(
            geometry.plan_geometry,
            lines,
            focus_geometry=geometry.plan_geometry,
        )
        plan._toggle_lines(self.geometry_card.lines.isChecked())
        plan.center_on_focus()

    def set_filters(self, filters: dict) -> None:
        self.filters = filters
        self.refresh()

    def refresh(self) -> None:
        rows = self.block_service.list_blocks(**self.filters)
        if self.current_block:
            self.current_block = self.block_service.get_block(self.current_block.id)
        if self.current_block is None and rows:
            self.current_block = rows[0]
        self._render_current_block()

    def open_block_id(self, event_id: str) -> None:
        self.current_block = self.block_service.get_block(event_id)
        self._render_current_block()

    def edit_current_block(self) -> None:
        if not self.current_block or not self.context.current_user.can_edit or self.current_block.is_archived:
            return
        dialog = BlockDialog(
            self.block_service,
            self.domain_repo,
            self.context.current_user,
            block=self.current_block,
            expected_version=self.entity_controller.expected_version,
        )
        if dialog.exec():
            self.current_block = self.block_service.get_block(dialog.saved_block_id or self.current_block.id)
            self.refresh()
            self.data_changed.emit()
            self.metadata_saved.emit(self.current_block.id, self.current_block.domain_id)

    def _autosave_comment(self, text):
        block = self.current_block
        if block is None or not self.context.current_user.can_edit or block.is_archived:
            self.notes.restore_saved()
            return
        try:
            self.block_service.update_comment(
                block.id,
                text,
                self.context.current_user,
                expected_version=self.entity_controller.expected_version,
            )
        except Exception as exc:
            self.notes.restore_saved()
            QMessageBox.warning(self, tr("Could not save block"), domain_message(str(exc)))
            return
        self.notes.mark_saved(text)
        self.current_block = self.block_service.get_block(block.id)
        self._render_current_block()
        self.data_changed.emit()

    def edit_comment(self):
        if not self.notes.editor.isReadOnly():
            self.notes.editor.setFocus()

    def _render_current_block(self) -> None:
        self._related_area_preview_id = None
        block = self.current_block
        history_entries = self.history_repo.for_blast_event(block.id) if block else []
        self._overview_history_entries = history_entries
        self.entity_controller = EntityPageController(self.context, block.domain_id) if block else None
        event = self.entity_controller.production_event(block.id) if self.entity_controller else None
        geometry = event.active_geometry_revision() if event else None
        photos = self.entity_controller.attachments.list_for_owner("blast_event", event.id, "photo") if event else []
        documents = self.entity_controller.attachments.list_for_owner("blast_event", event.id, "document") if event else []
        photo_count, document_count = self.entity_controller.attachments.counts("blast_event", event.id) if event else (0, 0)
        self._overview_photo_count = photo_count
        self._overview_document_count = document_count
        attachments_available = event is not None
        for manager in (self.photo_manager, self.document_manager):
            manager.service = self.entity_controller.attachments if self.entity_controller else _NullAttachmentService()
            manager.owner_id = event.id if event else None
            manager.read_only = not self.context.current_user.can_edit or bool(block and block.is_archived)
            for button in manager.mutation_buttons:
                button.setEnabled(attachments_available and not manager.read_only)
            manager.refresh()
        can_add = attachments_available and self.context.current_user.can_edit and not bool(block and block.is_archived)
        self.manage_photos_button.setEnabled(can_add)
        self.manage_documents_button.setEnabled(can_add)
        self.photos_tab_count.setText(f"{photo_count} photo{'s' if photo_count != 1 else ''}" if photo_count else "No photos yet")
        self.documents_tab_count.setText(f"{document_count} document{'s' if document_count != 1 else ''}" if document_count else "No documents yet")

        if block:
            state = BlastWorkflowState(block.status)
            self.header.set_content(
                title=f"{tr('Block')} {block.block_number}",
                status_text=tr(WORKFLOW_LABELS[state]),
                status_state=state,
                archived=block.is_archived,
                can_edit=self.context.current_user.can_edit and not block.is_archived,
                meta_values=(
                    f"{tr('ID')}: {block.id}",
                    f"{tr('Project / Domain')}: {block.site_name} / {block.domain_name}",
                    f"{tr('Horizon')}: {format_decimal(block.horizon_m)} m",
                    f"{tr('Geometry rev.')}: {geometry.revision_number if geometry else '—'}",
                ),
            )
            self.notes.set_value(
                block.comment or "",
                editable=self.context.current_user.can_edit and not block.is_archived,
            )
        else:
            self.header.set_content(title=tr("Select a block"), status_text=tr("—"), status_state="unknown")
            self.notes.set_value("", editable=False)

        service = self.entity_controller.attachments if self.entity_controller else None
        self.photos.set_items(service, photos, "No photos yet", can_add=can_add)
        self.documents.set_items(service, documents, "No documents yet", can_add=can_add)
        self.history_tab.set_entries(history_entries)
        self.recent_activity.set_entries(history_entries)
        self._render_related_areas(event)
        self._render_engineering(block)
        self._sync_sidebar_density()
        self._sync_engineering_actions_visibility()

    def _render_related_areas(self, event):
        if event is None or self.entity_controller is None:
            self.related_areas.set_rows([], empty_text="No linked assessment areas")
            return
        rows = []
        for area in self.entity_controller.state.assessment_areas:
            if not any(
                link.status == "confirmed" and link.blast_event_id == event.id
                for link in area.links_for_revision()
            ):
                continue
            evaluation = next(
                (item for item in self.entity_controller.state.evaluations
                 if item.assessment_area_id == area.id),
                None,
            )
            progress = assessment_progress_for(area, evaluation)
            status = tr(ASSESSMENT_PROGRESS_LABELS[progress])
            rev = area.active_geometry_revision()
            interval = format_assessment_elevation_interval(rev.min_elevation, rev.max_elevation)
            rows.append(RelatedEntityRow(
                area.id,
                area.name,
                f"{area.id} · {interval}",
                status,
                getattr(progress, "value", progress),
                action_text=tr("Go to ›"),
            ))
        self.related_areas.set_rows(rows, empty_text="No linked assessment areas")

    def _open_history_entry(self, entry):
        if not self.current_block or not self.entity_controller:
            return
        event = self.entity_controller.production_event(self.current_block.id)
        if event is None:
            return
        if entry.source_type == "blast_geometry":
            revision = next((item for item in event.geometry_revisions if item.id == entry.source_id), None)
            if revision is not None:
                dataset = self.entity_controller.state.active_dataset()
                open_geometry_revision(self, revision=revision, project_lines=dataset.lines if dataset else [])
            return
        if entry.source_type == "technical_card":
            card = next((item for item in self.entity_controller.state.technical_cards if item.blast_event_id == event.id), None)
            revision = next((item for item in card.revisions if item.id == entry.source_id), None) if card else None
            if card is not None and revision is not None:
                from app.use_case_factory import create_charge_presets, create_explosive_catalogue
                open_technical_card_revision(
                    self,
                    event=event,
                    card=card,
                    revision=revision,
                    domain_name=self.current_block.domain_name,
                    explosive_products=create_explosive_catalogue(self.context).list_enabled_products(),
                    charge_presets=create_charge_presets(self.context, self.entity_controller.site_id),
                )

    def _open_attachments(self, kind):
        self.tabs.setCurrentWidget(self.photos_tab if kind == "photo" else self.documents_tab)

    def _dispose_technical_card_editor(self):
        editor = self.technical_card_editor
        if editor is None:
            return
        self.technical_card_editor = None
        try:
            editor.editor.hide()
            editor.editor.deleteLater()
        except RuntimeError:
            pass
        editor.hide()
        editor.deleteLater()

    def _render_engineering(self, block):
        if block is None:
            self._clear_engineering()
            return
        event = self.entity_controller.production_event(block.id)
        if event is None:
            self._clear_engineering()
            self.geometry_card.set_geometry(None)
            return
        editable = self.context.current_user.can_edit and not block.is_archived
        geometry = event.active_geometry_revision()
        dataset = self.entity_controller.state.active_dataset()
        lines = dataset.lines if dataset else []
        self.geometry_card.set_geometry(
            geometry.plan_geometry if geometry else None,
            lines,
            focus_geometry=geometry.plan_geometry if geometry else None,
        )
        self.geometry_card.set_action_enabled(editable)

        card, draft = self.entity_controller.technical_card_draft(event)
        revision = card.active_revision() or draft
        geo = revision.geomechanical_parameters
        main = next((g for g in revision.drilling_groups if g.included and g.group_type == "main_pattern"), None)
        if main is None:
            main = next((g for g in revision.drilling_groups if g.included), None)
        actual = revision.actual_execution
        actual_main = next((g for g in actual.actual_drilling_groups if g.included and g.group_type == "main_pattern"), None)
        if actual_main is None:
            actual_main = next((g for g in actual.actual_drilling_groups if g.included), None)

        qprime, stability = _qprime_and_category(geo)
        area = polygon_area_m2(geometry.plan_geometry) if geometry else None
        blast_date = actual.actual_blast_date or block.planned_blast_date
        blast_hint = tr("Actual") if actual.actual_blast_date else tr("Planned")
        self.general_info.set_rows((
            ("Blast date", _fmt_dateish(blast_date), blast_hint),
            ("Block area", _fmt_number(area, " m²")),
            ("Bench height", _fmt_number(main.average_depth_m if main else None, " m")),
            ("Stability", stability or tr("Not calculated")),
        ))

        geo_lines = [
            f"{tr('Lithology')}: {geo.lithology or '—'}" if geo else tr("No geomechanics data yet"),
            f"UCS {_fmt_number(geo.ucs_mpa, ' MPa')}" if geo else "",
            f"RQD {_fmt_number(geo.rqd_percent, ' %')}" if geo else "",
            f"Q′ {_fmt_number(qprime)}" if qprime is not None else "",
            f"{tr('Stability')}: {stability}" if stability else "",
        ]
        design_lines = [
            f"Ø {_fmt_number(main.diameter_mm, ' mm')}" if main else tr("No blast-design data yet"),
            f"{tr('Pattern')} {_pattern(main.burden_m, main.spacing_m)}" if main else "",
            f"{tr('Average depth')} {_fmt_number(main.average_depth_m, ' m')}" if main else "",
            f"{tr('Holes')} {_fmt_number(main.hole_count)}" if main else "",
            f"{tr('Explosive')}: {main.explosive_names() or main.explosive_type or '—'}" if main else "",
        ]
        has_fact = bool(actual.actual_drilling_groups or actual.actual_blast_date or actual.completion_status == "completed")
        execution_lines = [
            f"{tr('Blast date')} {_fmt_dateish(actual.actual_blast_date)}" if has_fact else tr("No execution data yet"),
            f"{tr('Pattern')} {_pattern(actual_main.burden_m, actual_main.spacing_m)}" if has_fact and actual_main else "",
            f"{tr('Average depth')} {_fmt_number(actual.actual_average_depth_m, ' m')}" if has_fact else "",
            f"{tr('Holes')} {_fmt_number(actual.actual_total_hole_count)}" if has_fact else "",
            f"{tr('Explosive')} {_fmt_number(actual.actual_total_explosive_mass_kg, ' kg')}" if has_fact else "",
            (
                f"{tr('Rejected')}: {actual.rejected_hole_count or 0} · "
                f"{tr('Wet')}: {actual.wet_hole_count or 0} · "
                f"{tr('Redrilled')}: {actual.redrilled_hole_count or 0} · "
                f"{tr('Uncharged')}: {actual.uncharged_hole_count or 0}"
            ) if has_fact else "",
        ]
        self.engineering_summary.set_sections((
            ("geomechanics", "Geomechanics", geo_lines),
            ("blast_design", "Blast design", design_lines),
            ("execution", "Execution fact", execution_lines),
        ))
        self.engineering_notes.set_sections((
            ("geomechanics", "Geomechanics", [geo.notes if geo and geo.notes else tr("No notes")]),
            ("blast_design", "Blast design", [revision.notes or (main.notes if main else "") or tr("No notes")]),
            ("execution", "Execution fact", [actual.execution_notes or tr("No notes")]),
        ))

        created_actor, created_at, updated_at = _history_bounds(self._overview_history_entries)
        tc_revision = revision.revision_number if revision.revision_number else None
        self.summary.set_rows((
            ("Created by", block.author_name or created_actor or "—"),
            ("Created", format_datetime(block.created_at) if block.created_at else _fmt_history_time(created_at)),
            ("Last updated", format_datetime(block.updated_at) if block.updated_at else _fmt_history_time(updated_at)),
            ("Geometry file", geometry.source_file_name if geometry else "—"),
            ("Geometry revision", f"{tr('Rev.')} {geometry.revision_number}" if geometry else "—"),
            ("Technical Card", f"{tr('Rev.')} {tc_revision}" if tc_revision else "—"),
            ("Photos", self._overview_photo_count),
            ("Documents", self._overview_document_count),
        ))

        from app.use_case_factory import create_charge_presets, create_explosive_catalogue
        old_editor = self.technical_card_editor
        editor = TechnicalCardEditorWidget(
            event,
            card,
            draft,
            self.entity_controller.save_technical_card,
            self,
            not editable,
            domain_name=block.domain_name,
            explosive_products=create_explosive_catalogue(self.context).list_enabled_products(),
            charge_presets=create_charge_presets(self.context, self.entity_controller.site_id),
        )
        self.geomechanics_tab.set_content(
            GeomechanicsEditorWidget(editor.take_tab(tr("Geomechanics")))
        )
        self.design_tab.set_content(
            BlastDesignEditorWidget(editor.take_tab(tr("Drilling and charging")))
        )
        self.execution_tab.set_content(
            ActualExecutionEditorWidget(editor.take_tab(tr("Execution fact")))
        )
        self.technical_card_editor = editor
        if old_editor is not None:
            try:
                old_editor.editor.hide()
                old_editor.editor.deleteLater()
            except RuntimeError:
                pass
            old_editor.hide()
            old_editor.deleteLater()
        self.engineering_save.setEnabled(not editor.editor.read_only)

    def _clear_engineering(self):
        self.engineering_summary.set_sections(())
        self.engineering_notes.set_sections(())
        self.general_info.set_rows(())
        self.summary.set_rows(())
        self.geomechanics_tab.set_content(EmptySection())
        self.design_tab.set_content(EmptySection())
        self.execution_tab.set_content(EmptySection())
        self.engineering_save.setEnabled(False)
        self._dispose_technical_card_editor()
        self.geometry_card.set_action_enabled(False)

    def _reimport_current_geometry(self):
        if not self.current_block or not self.entity_controller:
            return
        event = self.entity_controller.production_event(self.current_block.id)
        if event is not None:
            self._reimport_geometry(event)

    def _save_technical_card_draft(self):
        if self.technical_card_editor is not None and self.context.current_user.can_edit and self.current_block and not self.current_block.is_archived:
            if self.technical_card_editor.save_draft():
                self._refresh_preserving_active_tab()

    def _refresh_preserving_active_tab(self):
        title = self.tabs.tabText(self.tabs.currentIndex())
        self.refresh()
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == title:
                self.tabs.setCurrentIndex(index)
                break

    def _reimport_geometry(self, event):
        if not self.context.current_user.can_edit or self.current_block.is_archived:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Reimport production geometry"),
            "",
            tr("Geometry files (*.dxf *.dm *.dmx);;AutoCAD DXF (*.dxf);;Datamine files (*.dm *.dmx)"),
        )
        if not path:
            return
        try:
            self.entity_controller.reimport_blast_event_geometry(event, path)
        except Exception as exc:
            QMessageBox.warning(self, tr("Geometry import"), domain_message(str(exc)))
        self._render_current_block()
