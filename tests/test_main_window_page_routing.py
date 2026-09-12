"""Architecture-level routing regressions for the tree-driven MainWindow."""
from pathlib import Path


def source(path): return Path(path).read_text(encoding="utf-8")
def compact(text): return "".join(text.split())

def test_tree_is_primary_navigation_without_project_lines_branch():
    tree=source("ui/widgets/project_tree.py")
    assert '"Project Lines"' not in tree
    assert '"type":"horizon"' in tree and '"type":"interval"' in tree
    assert "class ProjectTreeWidget(QTreeWidget)" in tree
    assert 'VIRTUAL_SECTION_TYPES = {"horizon", "interval"}' in tree
    assert "def drawBranches" in tree and "self.VIRTUAL_SECTION_TYPES" in tree
    assert "_keep_virtual_section_expanded" in tree and "item.setExpanded(True)" in tree
    assert "DontShowIndicator" not in tree

def test_project_and_domain_filters_are_user_facing():
    tree=source("ui/widgets/project_tree.py")
    assert "project_filter" in tree and "domain_filter" in tree and "status_filter" in tree
    assert "mine_filter" not in tree and "site_filter" not in tree
    assert "Show archived" in tree

def test_site_domain_block_and_area_routes_exist():
    main=source("ui/main_window.py")
    for method in ("select_site", "select_domain", "open_block_from_tree", "open_area_from_tree"):
        assert f"def {method}" in main
    assert "AssessmentAreaPage" in main

def test_cancel_and_discard_share_one_leave_guard():
    main=source("ui/main_window.py")
    assert main.count("def _guard_leave") == 1
    guard=main[main.index("def _guard_leave"):main.index("def _set_context")]
    assert "StandardButton.Discard" in guard
    assert "cancel_active_workflow" in guard
    assert "return False" in guard

def test_missing_project_lines_warning_and_no_drawing_before_check():
    main=source("ui/main_window.py")
    area=main[main.index("def _add_area"):main.index("def _archive_selected")]
    assert "Load Project Lines for the project first." in area
    assert area.index("project_has_active_lines") < area.index("AssessmentAreaCreationPage")

def test_block_creation_reuses_blast_event_dialog_and_single_event_identity():
    main=source("ui/main_window.py")
    block=main[main.index("def _add_blast_event"):main.index("def _add_area")]
    assert "BlastEventDialog" in block
    assert "CreateBlastEventCommand" in block
    assert "self.create_blast_event.execute" in block
    assert "result.event_type==\"contour\"" in block
    assert "open_block_from_tree(result.event_id" in block
    for forbidden in ("EntityPageController", "BlastEventService", "create_block", "controller.save", "session_factory", "BlastBlock"):
        assert forbidden not in block

def test_site_dashboard_owns_project_lines_management():
    page=source("ui/pages/dashboards/site_dashboard.py")
    assert "class SiteDashboardPage" in page
    assert 'primary_action_label="Project Lines"' in page
    assert 'tr("Import lines")' in page and 'tr("Update lines")' in page
    assert "ProjectLinesCard" in page
    assert "ProjectLinesRepository" in page
    assert "QTabWidget" not in page and "QScrollArea" not in page

def test_project_dashboard_attention_routes_to_canonical_area_page():
    page=source("ui/pages/dashboards/site_dashboard.py")
    main=source("ui/main_window.py")
    assert "assessment_area_requested = Signal(str, int)" in page
    assert "page.assessment_area_requested.connect" in main
    assert "def _open_project_dashboard_area" in main
    route=main[main.index("def _open_project_dashboard_area"):main.index("def _open_domain_dashboard")]
    assert "open_area_from_tree" in route and "get_domain_context" in route

def test_archive_button_and_production_event_service_are_connected():
    assert "archive_button" in source("ui/header.py")
    main=source("ui/main_window.py")
    assert "archive_requested.connect(self._archive_selected)" in main
    assert "set_archived" in source("infrastructure/services/production_blast_service.py")

def test_block_page_embeds_geometry_and_revision_safe_technical_card_tabs():
    block=source("ui/pages/block_page.py")
    assert "production_event" in block and "active_geometry_revision" in block
    assert "TechnicalCardEditorWidget" in block
    assert 'take_tab(tr("Geomechanics"))' in block
    assert 'take_tab(tr("Drilling and charging"))' in block
    assert 'take_tab(tr("Execution fact"))' in block

def test_area_page_is_focused_without_legacy_mode_switch():
    area=source("ui/pages/assessment_area_page.py")
    assert "class AssessmentAreaPage" in area
    assert '"Overview"' in area and '"Assessment"' in area
    assert 'addTab(self.result,tr("Result"))' not in area
    assert '"Linked events"' in area
    assert "Blast Events / Assessment Areas" not in area
    assert "AssessmentAreaEvaluationDialog" in area and "Matrix" in area and "assessment_splitter" in area
    assert "Design Achievement Index" not in area

def test_assessment_geometry_uses_direct_compact_inputs():
    editor=source("ui/editors/assessment_evaluation_editor.py")
    for label in ("Angle deviation, °","Berm deviation, m","Toe deviation, m"):
        assert label in editor
    geometry=editor[editor.index("    def _geometry(self):"):editor.index("    def _geometry_rules(self):")]
    for legacy in ("Design bench face angle", "Actual bench face angle", "Design berm width", "Actual berm width"):
        assert legacy not in geometry
    assert "Scoring guide" not in geometry and "set_help" in geometry

def test_area_links_and_focused_creation_are_reused():
    area=source("ui/pages/assessment_area_page.py")
    for action in ("confirm_event_link","exclude_event_link","restore_event_link","refresh_event_link_suggestions"):
        assert action in area
    creation=source("ui/pages/assessment_area_creation_page.py")
    main=source("ui/main_window.py")
    assert "AssessmentAreaCreationPage" in creation and "AssessmentGeometryEditorWidget" in creation
    assert "Blast Events" not in creation and "TechnicalCard" not in creation
    assert "AssessmentAreaCreationPage" in main and "_area_created" in main

def test_entity_page_integration_corrections_are_visible():
    main=source("ui/main_window.py")
    block=source("ui/pages/block_page.py")
    assert 'QPushButton(tr("Save"))' in block
    assert "save_draft()" in block and "complete()" not in block
    area=source("ui/pages/assessment_area_page.py")
    area_compact=compact(area)
    assert "self.assessment_sections" not in area
    assert "self.assessment_inputs=QWidget()" in area_compact
    assert "self.assessment_inputs=QScrollArea()" not in area_compact
    assert "geometry_section_title" in area and "face_condition_section_title" in area
    assert "page.setVisible(True)" not in area
    assert "Save an assessment draft first" not in area
    assert "prepare_evaluation_attachment_owner" in area
    creation=source("ui/pages/assessment_area_creation_page.py")
    for label in ("Fit","Project Lines","Undo","Finish boundary","Create Assessment Area","Cancel"):
        assert label in creation
    assert "self._start_drawing()" not in creation[creation.index("def __init__"):creation.index("def _start_drawing")]
    assert "page.area_created.connect" in main and "open_area_from_tree" in main

def test_refresh_reloads_filters_and_area_construction_is_guarded():
    main=source("ui/main_window.py")
    refresh=main[main.index("def refresh_project_data"):main.index("def closeEvent")]
    assert refresh.index("reload_filters") < refresh.index("load_data")
    assert main.count("Could not open the assessment area") == 1
    assert "Could not start assessment area creation" in main
    assert "Could not open boundary editing" in main

def test_existing_block_dialog_uses_required_horizon_and_allows_versioned_domain_move():
    dialog=source("ui/block_dialog.py")
    assert "self.horizon.setText(str(block.horizon_m))" in dialog
    assert "planned_date" not in dialog and "self.status" not in dialog
    assert "target_expected_version=target_version" in dialog
    assert "self.domain.setEnabled(self.domain.count()>1" in dialog

def test_contour_event_ui_and_tree_architecture():
    tree=source("ui/widgets/project_tree.py")
    assert '"Blast events"' in tree and '"Blast blocks"' not in tree
    assert "list_contour_events" in tree and '"type":"contour"' in tree
    header=source("ui/header.py")
    for label in ("Add blast event","Add assessment area"):
        assert label in header
    main=source("ui/main_window.py")
    assert "def _add_blast_event" in main and "open_contour_from_tree" in main
    assert "result.event_type==\"contour\"" in main
    page=source("ui/pages/contour_event_page.py")
    assert "ContourEventPage" in page and "Geomechanics" not in page
    assert '"Blast design"' in page and '"Execution fact"' in page

def test_area_creation_cancel_drawing_is_not_page_cancel():
    creation=source("ui/pages/assessment_area_creation_page.py")
    assert "cancel_drawing.clicked.connect(self.editor.cancel_workflow)" in creation
    assert "def _close_page" in creation and "self.cancelled.emit()" in creation
    assert "def _start_drawing" in creation and "start_new_area()" in creation


def test_stale_block_engineering_is_cleared_and_read_only_is_defensive():
    block=source("ui/pages/block_page.py")
    render=block[block.index("def _render_engineering"):block.index("def _reimport_current_geometry")]
    assert "self._clear_engineering()" in render
    assert "self._dispose_technical_card_editor()" in block
    assert "self.design_tab.set_content(EmptySection())" in block
    assert "self.execution_tab.set_content(EmptySection())" in block
    assert "self.geometry_card.set_action_enabled(False)" in block
    assert "not editable" in render
    reimport=block[block.index("def _reimport_geometry"):]
    assert "not self.context.current_user.can_edit" in reimport and "self.current_block.is_archived" in reimport

def test_area_and_contour_mutations_are_defensively_read_only():
    area=source("ui/pages/assessment_area_page.py")
    contour=source("ui/pages/contour_event_page.py")
    assert "self.read_only=notcontext.current_user.can_editor self.area.is_archived".replace(" ","") in compact(area)
    assert "def _ensure_editable" in area
    for method in ("_change_link","recalculate_links","add_manual_link","_save_evaluation","_request_edit_boundaries"):
        section=area[area.index(f"def {method}"):]
        assert "_ensure_editable" in section[:500]
    assert "self.read_only=notcontext.current_user.can_editor self.blast_event.is_archived".replace(" ","") in compact(contour)
    assert "if self.read_only" in contour
    assert "self.geometry_card.set_action_enabled(not self.read_only)" in contour

def test_restart_drawing_distinguishes_create_and_edit_modes():
    creation=source("ui/pages/assessment_area_creation_page.py")
    restart=creation[creation.index("def _start_drawing"):creation.index("def _confirm")]
    assert "if self.edit_area_id" in restart
    assert "self.editor.start_edit(self.edit_area_id)" in restart
    assert "self.editor.start_new_area()" in restart


def test_area_completion_is_not_driven_by_generic_state_saved():
    creation=source("ui/pages/assessment_area_creation_page.py")
    assert "state_saved.connect" not in creation
    assert "self.editor.area_created.connect(self.area_created)" in creation
    assert "self.editor.area_revised.connect(self.area_created)" in creation


def test_successful_area_edit_has_dedicated_unguarded_completion_path():
    main=source("ui/main_window.py")
    finish=main[main.index("def _finish_area_boundary_edit"):main.index("def _cancel_area_boundary_edit")]
    assert "self.assessment_page=None" in finish
    assert "refresh_project_data()" in finish and "open_area_from_tree" in finish
    assert "save_now" not in finish and "_guard_leave" not in finish
    assert "removeWidget(edit_page)" in finish and "deleteLater()" in finish
    cancel=main[main.index("def _cancel_area_boundary_edit"):main.index("def _edit_area_boundaries")]
    assert "open_area_from_tree" in cancel


def test_analysis_button_toggles_persistent_full_width_workspace_before_report():
    header=source("ui/header.py")
    main=source("ui/main_window.py")
    page=source("ui/pages/analysis_page.py")
    filters=source("ui/analysis/filters.py")
    data_table=source("ui/analysis/data_table.py")
    assert "analysis_requested" in header and "Signal()" in header
    assert 'QPushButton(tr("Analysis"))' in header
    assert 'self.analysis_button.setIcon(ui_icon("analytics"))' in header
    assert header.index("layout.addWidget(self.analysis_button)") < header.index("layout.addWidget(self.report_button)")
    assert "AnalysisPage" in main
    assert "analysis_requested.connect(self._open_analysis)" in main
    assert "self.workspace_stack.addWidget(self.normal_workspace)" in main
    assert "self.workspace_stack.addWidget(self.analysis_page)" in main
    assert "self._set_analysis_mode(False)" in main
    assert "self._set_analysis_mode(True)" in main
    assert "AnalysisDataTable" in page
    assert "FilterSpec" in page
    assert "AnalysisFilterPanel" in page
    assert 'setObjectName("AnalysisDataTable")' in data_table
    assert 'tr("+ Add filter")' in filters
