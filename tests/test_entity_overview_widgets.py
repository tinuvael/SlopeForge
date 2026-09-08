from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_overview_geometry_focus_uses_two_span_context():
    text = Path("ui/pages/plan_geometry_widget.py").read_text(encoding="utf-8")
    assert "factor = 2.0" in text
    assert "sqrt(2.0)" not in text


def test_operational_pages_use_shared_overview_primitives():
    block = Path("ui/pages/block_page.py").read_text(encoding="utf-8")
    contour = Path("ui/pages/contour_event_page.py").read_text(encoding="utf-8")
    assessment = Path("ui/pages/assessment_area_page.py").read_text(encoding="utf-8")
    for text in (block, contour, assessment):
        assert "EntityHeaderWidget" in text
        assert "OverviewKeyValueCard" in text
    assert "BlockRecentActivityCard" in block
    assert "ContourRecentActivityCard" in contour
    assert "AssessmentRecentActivityCard" in assessment
    assert "AssessmentAttachmentPreview" in assessment
    assert "AssessmentGeometryCard" in assessment
    assert "AssessmentRelatedEventList" in assessment
    assert "AssessmentMatrixCard" in assessment
    assert "BlockAttachmentPreview" in block
    assert "BlockGeometryCard" in block
    assert "BlockRelatedEntityList" in block
    assert "BlockNotesCard" in block
    assert "ContourAttachmentPreview" in contour
    assert "ContourGeometryCard" in contour
    assert "ContourRelatedEntityList" in contour
    assert "ContourNotesCard" in contour
    assert "focus_geometry=geometry.plan_geometry" in block
    assert "focus_geometry=self.rev.plan_geometry" in contour
    assert "focus_geometry=rev.final_geometry_frozen" in assessment


def test_shared_square_geometry_card_is_large_and_near_square():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import SquareGeometryCard

    app = widgets.QApplication.instance() or widgets.QApplication([])
    card = SquareGeometryCard()
    assert card.hasHeightForWidth()
    assert card.heightForWidth(540) == 540
    assert card.sizeHint().width() == card.sizeHint().height() == 540
    assert card.minimumWidth() >= 470
    assert card.maximumWidth() == 620
    assert not card.plan.lines.isVisible()
    assert not card.plan.frame_button.isVisible()
    assert not card.plan.reimport_button.isVisible()
    card.close(); app.processEvents()


def test_status_badges_have_distinct_semantic_workflow_colors():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import EntityHeaderWidget

    app = widgets.QApplication.instance() or widgets.QApplication([])
    header = EntityHeaderWidget()
    roles = {}
    for state in ("in_preparation", "planned", "blasted", "assessed", "in_progress", "completed"):
        header.set_content(title="X", status_text=state, status_state=state)
        roles[state] = header.status.property("statusRole")
    assert roles["in_preparation"] == "neutral"
    assert roles["planned"] == roles["in_progress"] == "info"
    assert roles["blasted"] == "warning"
    assert roles["assessed"] == roles["completed"] == "success"
    assert roles["planned"] != roles["blasted"]
    assert roles["completed"] != roles["blasted"]
    header.set_content(title="X", status_text="Completed", status_state="completed", archived=True)
    assert header.archive.property("statusRole") == "archived"
    assert header.archive.isVisible() or not header.isVisible()
    header.close(); app.processEvents()


def test_header_uses_one_context_line_instead_of_duplicate_meta_cards():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import EntityHeaderWidget

    app = widgets.QApplication.instance() or widgets.QApplication([])
    header = EntityHeaderWidget()
    header.set_content(
        title="Block X", status_text="Blasted", status_state="blasted",
        meta_values=("ID: BL-X", "Project / Domain: P / D", "Horizon: 630 m", "Geometry rev.: 2"),
    )
    assert header.context.text() == "ID: BL-X  ·  Project / Domain: P / D  ·  Horizon: 630 m  ·  Geometry rev.: 2"
    header.close(); app.processEvents()


def test_inline_notes_requests_save_only_after_dirty_focus_out():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import InlineAutosaveNotes

    app = widgets.QApplication.instance() or widgets.QApplication([])
    notes = InlineAutosaveNotes()
    saved = []
    notes.save_requested.connect(saved.append)
    notes.set_value("old", editable=True)
    notes.editor.focus_lost.emit()
    assert saved == []
    notes.editor.setPlainText("new")
    notes.editor.focus_lost.emit()
    assert saved == ["new"]
    notes.mark_saved("new")
    notes.editor.focus_lost.emit()
    assert saved == ["new"]
    notes.close(); app.processEvents()


def test_quick_attachment_preview_exposes_add_and_open_actions():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import QuickAttachmentPreview

    app = widgets.QApplication.instance() or widgets.QApplication([])
    preview = QuickAttachmentPreview("Photos", "photo")
    added=[]; opened=[]
    preview.add_requested.connect(lambda: added.append(True))
    preview.open_page_requested.connect(lambda: opened.append(True))
    preview.set_items(None, [], "No photos yet", can_add=False)
    assert not preview.add_button.isEnabled()
    assert preview.open_button.isEnabled()
    preview.set_items(None, [], "No photos yet", can_add=True)
    preview.add_button.click(); preview.open_button.click(); app.processEvents()
    assert added == [True]
    assert opened == [True]
    preview.close(); app.processEvents()


def test_quick_attachment_preview_extra_items_collapse_without_rebuild_or_overflow():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import QuickAttachmentPreview

    app = widgets.QApplication.instance() or widgets.QApplication([])
    photos = QuickAttachmentPreview("Photos", "photo", max_items=6)
    documents = QuickAttachmentPreview("Documents", "document", max_items=7)
    fake_photos = [SimpleNamespace(id=f"p{i}", title="", original_filename=f"p{i}.jpg") for i in range(6)]
    fake_docs = [SimpleNamespace(id=f"d{i}", title="", original_filename=f"d{i}.pdf") for i in range(7)]
    photos.set_items(None, fake_photos, "No photos yet")
    documents.set_items(None, fake_docs, "No documents yet")

    assert len(photos._item_rows) == 3
    assert len(documents._item_rows) == 7
    photos.set_visible_item_limit(6)
    assert sum(row.isHidden() for row in photos._item_rows) == 0
    photos.set_visible_item_limit(4)
    assert [row.isHidden() for row in photos._item_rows] == [False, False, True]
    documents.set_visible_item_limit(5)
    assert [row.isHidden() for row in documents._item_rows] == [False, False, False, False, False, True, True]

    photos.resize(250, 180)
    margins = photos.layout.contentsMargins()
    available = photos.width() - margins.left() - margins.right()
    assert photos.PHOTO_TILE_WIDTH * 2 + 6 <= available
    photos.close(); documents.close(); app.processEvents()


def test_related_entity_list_empty_state_is_compact_and_unframed():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import RelatedEntityList

    app = widgets.QApplication.instance() or widgets.QApplication([])
    related = RelatedEntityList("Related assessment areas")
    related.set_rows([], empty_text="No linked assessment areas")
    assert related.list.minimumHeight() == 38
    assert related.list.maximumHeight() == 38
    assert related.list.frameShape() == widgets.QFrame.Shape.NoFrame
    related.close(); app.processEvents()


def test_related_entity_list_separates_preview_click_from_go_to_action():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import (
        OverviewLinkButton,
        RelatedEntityList,
        RelatedEntityRow,
    )

    app = widgets.QApplication.instance() or widgets.QApplication([])
    related = RelatedEntityList("Related assessment areas")
    activated = []
    navigated = []
    related.entity_activated.connect(activated.append)
    related.entity_action_requested.connect(navigated.append)
    related.set_rows([
        RelatedEntityRow("AA-1", "Area 1", "AA-1 · 600–630 m", action_text="Go to ›")
    ])
    item = related.list.item(0)
    related._emit_item(item)
    assert activated == ["AA-1"]
    holder = related.list.itemWidget(item)
    button = holder.findChild(OverviewLinkButton)
    assert button is not None
    button.click(); app.processEvents()
    assert navigated == ["AA-1"]
    related.close(); app.processEvents()


def test_assessment_matrix_preview_only_accepts_stored_indices_and_is_large():
    widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ui.pages.entity_overview_widgets import AssessmentMatrixPreview

    app = widgets.QApplication.instance() or widgets.QApplication([])
    preview = AssessmentMatrixPreview()
    preview.set_result(dai=.71, fci=.58, dai_threshold=.65, fci_threshold=.60)
    assert preview.dai == pytest.approx(.71)
    assert preview.fci == pytest.approx(.58)
    assert preview.dai_threshold == pytest.approx(.65)
    assert preview.fci_threshold == pytest.approx(.60)
    assert preview.minimumWidth() >= 300
    assert preview.minimumHeight() >= 300
    preview.close(); app.processEvents()


def test_assessment_overview_uses_saved_results_once_and_real_related_events():
    text = Path("ui/pages/assessment_area_page.py").read_text(encoding="utf-8")
    assert "active.design_achievement_index" in text
    assert "active.face_condition_index" in text
    assert "matrix_template_snapshot" in text
    assert "calculate_revision(" not in text
    assert "photo_manager.add()" in text
    assert "document_manager.add()" in text
    assert "Related blast events" in text
    assert 'item.status != "excluded" and item.blast_event_id == event_id' in text
    assert 'visible_links = [x for x in self.area.links_for_revision() if x.status != "excluded"]' in text
    assert 'if link.status == "suggested"' in text
    assert "self.controller.links.is_stale(link)" in text
    assert "self.controller.links.linked_revision(event, link)" in text
    assert "related_blast_event_requested" in text
    assert '("Area height", _number(area_height, " m"))' in text
    for field in ("bench_angle_shortfall_deg", "berm_width_deficit_m", "toe_offset_from_design_m"):
        assert field in text
    assert '("DAI", dai)' in text
    assert '("FCI", fci)' in text
    assert text.count('(\"DAI\", dai)') == 1
    assert text.count('(\"FCI\", fci)') == 1
    assert 'action_text=tr("Go to ›")' in text
    assert "set_comparison_geometry(" in text
    assert "event_rect" in text


def test_block_overview_uses_main_pattern_depth_and_execution_exceptions():
    page = Path("ui/pages/block_page.py").read_text(encoding="utf-8")
    assert "polygon_area_m2" in page
    assert "_qprime_and_category" in page
    for label in ("Very unstable", "Unstable", "Moderately stable", "Stable"):
        assert label in page
    assert '("Blast date",' in page
    assert '("Block area",' in page
    assert '("Bench height", _fmt_number(main.average_depth_m' in page
    assert '("Stability",' in page
    assert "Related assessment areas" in page
    assert 'link.status == "confirmed"' in page
    for field in ("rejected_hole_count", "wet_hole_count", "redrilled_hole_count", "uncharged_hole_count"):
        assert field in page
    assert "BlockNotesCard" in page
    assert "GeneralInfoCard" not in page
    assert "Created" not in page.split("meta_values=(", 1)[1].split("),", 1)[0]
    assert "Updated" not in page.split("meta_values=(", 1)[1].split("),", 1)[0]


def test_block_overview_refinements_keep_preview_and_navigation_distinct():
    page = Path("ui/pages/block_page.py").read_text(encoding="utf-8")
    helpers = Path("ui/pages/block_overview_widgets.py").read_text(encoding="utf-8")
    general = page.index("overview_stack.addWidget(self.general_info")
    related = page.index("overview_stack.addWidget(self.related_areas")
    notes = page.index("overview_stack.addWidget(self.notes")
    assert general < related < notes
    assert 'BlockAttachmentPreview("Photos", "photo", max_items=6)' in page
    assert 'BlockAttachmentPreview("Documents", "document", max_items=7)' in page
    assert "BlockGeometryCard" in page
    assert '"Plan / geometry"' in page
    assert 'action_label="Reimport"' in page
    assert "expand_horizontally=True" in page
    assert "BlockNotesCard" in page
    assert '"Notes"' in page
    assert "set_visible_item_limit(photo_limit)" in page
    assert "set_visible_item_limit(document_limit)" in page
    assert "entity_activated.connect(self._preview_related_area)" in page
    assert "entity_action_requested.connect(self._open_related_area)" in page
    assert "escape_requested.connect(self._clear_related_area_preview)" in page
    assert 'action_text=tr("Go to ›")' in page
    assert "set_comparison_geometry(" in page
    assert "block_rect.united(area_rect)" in page
    assert "plan.center_on_focus()" in page
    assert "QEvent.Type.MouseButtonPress" in page
    assert "ScrollBarAlwaysOff" in helpers
    assert "BlockRelatedEntityItem" in helpers
    assert "revision=geometry.revision_number" not in page.split("def _render_engineering", 1)[1].split("card, draft", 1)[0]
    assert "source=geometry.source_file_name" not in page.split("def _render_engineering", 1)[1].split("card, draft", 1)[0]


def test_contour_overview_has_safe_metadata_and_related_assessment_access():
    text = Path("ui/pages/contour_event_page.py").read_text(encoding="utf-8")
    for label in ("Average depth", "Azimuth", "Inclination", "Spacing"):
        assert label in text
    assert "_primary_contour_group" in text
    assert "actual.actual_average_depth_m" in text
    assert "actual_group.spacing_m" in text
    assert '("Method",' in text
    assert "Related assessment areas" in text
    assert "related_assessment_requested" in text
    assert "update_contour_comment" in text
    assert "self.blast_event.created_at" not in text
    assert "GeomechanicsEditorWidget" not in text
    for field in ("rejected_hole_count", "wet_hole_count", "redrilled_hole_count", "uncharged_hole_count"):
        assert field in text


def test_contour_overview_matches_stabilized_block_presentation_contract():
    page = Path("ui/pages/contour_event_page.py").read_text(encoding="utf-8")
    helpers = Path("ui/pages/contour_overview_widgets.py").read_text(encoding="utf-8")
    general = page.index("overview_stack.addWidget(self.general_info)")
    related = page.index("overview_stack.addWidget(self.related_areas)")
    notes = page.index("overview_stack.addWidget(self.notes)")
    assert general < related < notes
    assert 'ContourAttachmentPreview("Photos", "photo", max_items=6)' in page
    assert 'ContourAttachmentPreview("Documents", "document", max_items=7)' in page
    assert 'ContourGeometryCard("Plan / geometry", action_label="Reimport")' in page
    assert 'ContourNotesCard("Notes")' in page
    assert "ContourRecentActivityCard" in page
    assert "self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)" in page
    assert "set_visible_item_limit(photo_limit)" in page
    assert "set_visible_item_limit(document_limit)" in page
    assert "entity_activated.connect(self._preview_related_area)" in page
    assert "entity_action_requested.connect(self._open_related_area)" in page
    assert "escape_requested.connect(self._clear_related_area_preview)" in page
    assert 'action_text=tr("Go to ›")' in page
    assert "set_comparison_geometry(" in page
    assert "contour_rect.united(area_rect)" in page
    assert "plan.center_on_focus()" in page
    assert "QEvent.Type.MouseButtonPress" in page
    assert "revision=self.rev.revision_number" not in page
    assert "source=self.rev.source_file_name" not in page
    assert "BlockGeometryCard" in helpers
    assert "BlockRelatedEntityList" in helpers
    assert "BlockAttachmentPreview" in helpers


def test_main_window_wires_related_entities_into_existing_navigation():
    text = Path("ui/main_window.py").read_text(encoding="utf-8")
    assert "self.block_page.related_assessment_requested.connect(self._open_related_assessment)" in text
    assert "page.related_assessment_requested.connect(self._open_related_assessment)" in text
    assert "page.related_blast_event_requested.connect(self._open_related_blast)" in text
    assert 'if event_type=="production"' in text
    assert "open_contour_from_tree(event_id" in text


def test_block_and_contour_note_persistence_are_focused_versioned_writes():
    block_service = Path("infrastructure/services/production_blast_service.py").read_text(encoding="utf-8")
    contour_service = Path("infrastructure/services/contour_blast_service.py").read_text(encoding="utf-8")
    controller = Path("ui/pages/entity_page_controller.py").read_text(encoding="utf-8")
    assert "guard_domain_versions" in block_service
    assert "Changed field: Comment" in block_service
    assert "guard_domain_versions" in contour_service
    assert "Changed field: Comment" in contour_service
    assert "self.editing.expected_version = new_version" in controller
