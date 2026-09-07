from datetime import date
from pathlib import Path
from types import SimpleNamespace
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import pytest

QtWidgets=pytest.importorskip("PySide6.QtWidgets",exc_type=ImportError)
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import QApplication,QDialogButtonBox,QMessageBox,QTabWidget


def app():return QApplication.instance() or QApplication([])


def stub_assessment_domain_repository(monkeypatch,module):
    class DomainRepositoryStub:
        def __init__(self,*_args,**_kwargs):pass
        def get(self,_domain_id):return SimpleNamespace(site=SimpleNamespace(name="Project"))
    monkeypatch.setattr(module,"DomainRepository",DomainRepositoryStub)


def test_assessment_editor_tabs_are_explicitly_detached_and_keep_content():
    app()
    from tests.test_wall_assessment_persistence_ui import make_state,filled_draft
    from ui.editors.assessment_evaluation_editor import AssessmentAreaEvaluationDialog
    state,area=make_state(); evaluation,draft=filled_draft(state,area); evaluation.save_revision(draft,"completed")
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,evaluation.active_revision(),lambda *_:None)
    target=QTabWidget()
    pages=[]
    for title in ("General","Geometry","Face condition","Matrix"):
        page=dialog.take_tab(title); target.addTab(page,title); pages.append(page)
    assert all(page.parent() is not dialog.tabs for page in pages)
    assert all(page.findChildren(QtWidgets.QWidget) for page in pages)
    assert dialog.summary.text() and "DAI: 1.000" in dialog.summary.text()
    dialog._allow_close=True; dialog.close(); target.close()



def test_assessment_page_is_one_continuous_visible_workspace(monkeypatch):
    application=app()
    from tests.test_wall_assessment_persistence_ui import make_state,filled_draft
    from application.services.assessment_event_links import AssessmentEventLinkService
    from domain.assessment.evaluation import AssessmentAreaEvaluationService
    import ui.pages.assessment_area_page as module

    state,area=make_state(); evaluation,draft=filled_draft(state,area)
    evaluation.save_revision(draft,"completed"); state.evaluations.append(evaluation)

    class Attachments:
        def list_for_owner(self,*_args):return []
    class Controller:
        def __init__(self,*_args):
            self.state=state; self.attachments=Attachments(); self.links=AssessmentEventLinkService(state)
        def area(self,_id):return area
        def evaluation_draft(self,_area):return evaluation,evaluation.active_revision()
        def save_evaluation(self,*_args):raise AssertionError("construction must not save")
        def ensure_evaluation_owner(self,*_args):raise AssertionError("opening must not create an owner")
        def save(self):raise AssertionError("construction must not persist")
    monkeypatch.setattr(module,"EntityPageController",Controller)
    stub_assessment_domain_repository(monkeypatch,module)
    context=SimpleNamespace(session_factory=None,current_user=SimpleNamespace(can_edit=True,display_name="Current User"))
    before=len(evaluation.revisions); page=module.AssessmentAreaPage(context,1,"Domain",area.id); page.resize(1920,1080)
    page.show(); page.tabs.setCurrentWidget(page.assessment_tab); application.processEvents()
    splitter=page.assessment_splitter; inputs=page.assessment_inputs; live=page.result
    assert splitter.isVisible() and splitter.count()==2
    assert splitter.widget(0) is inputs and splitter.widget(1) is page.assessment_right
    assert inputs.isVisible() and live.isVisible()
    assert inputs.width()>0 and page.assessment_right.width()>300
    assert page.assessment_tab.findChildren(QtWidgets.QScrollArea)==[]
    assert not hasattr(page,"assessment_sections")
    assert page.geometry_section_title.isVisible() and page.face_condition_section_title.isVisible()
    assert all(editor.isVisible() for editor in page.evaluation_editor.geometry_editors.values())
    assert all(editor.isVisible() for editor in page.evaluation_editor.editors.values())
    assert page.evaluation_editor.date.isVisible() and page.evaluation_editor.date.isEnabled()
    assert page.evaluation_editor.inspector.isVisible() and page.evaluation_editor.inspector.isEnabled()
    assert page.evaluation_editor.inspector.text()=="Иванов"
    assert page.assessment_details_card.isVisible() and inputs.isAncestorOf(page.evaluation_editor.date) and inputs.isAncestorOf(page.evaluation_editor.inspector)
    assert page.assessment_right.width()/(inputs.width()+page.assessment_right.width())>.42
    assert page.assessment_basis_value.text()=="With contour drilling" or page.assessment_basis_value.text()=="Controlled blasting"
    assert page.assessment_basis_detection.text()=="Confirmed contour blast link"
    assert page.face_condition_input_card.isVisible() and page.geometry_input_card.isVisible()
    assert page.face_condition_input_card.objectName() == page.geometry_input_card.objectName() == "CriterionCard"
    assert not page.evaluation_editor.override_reason.isVisible()
    assert page.evaluation_editor.dai_value.isVisible()
    assert page.evaluation_editor.fci_value.isVisible()
    assert page.evaluation_editor.result_value.isVisible()
    assert page.evaluation_editor.plot.isVisible()
    assert page.save_evaluation_button.isVisible()
    assert page.save_evaluation_button.text() == "Save"
    assert [action.text() for action in page.save_evaluation_button.menu().actions()] == ["Complete assessment"]
    input_layout = inputs.layout()
    face_index = input_layout.indexOf(page.face_condition_input_card)
    geometry_index = input_layout.indexOf(page.geometry_input_card)
    assert face_index < geometry_index
    assert not hasattr(page, "face_condition_divider")
    assert page.face_condition_input_card.layout().spacing() == 5
    assert page.geometry_input_card.layout().spacing() == 5
    assert page.evaluation_editor.wall_conformance_import_button.isVisible()
    assert page.evaluation_editor.additional_geometry_widget.isVisible()
    assert page.evaluation_editor.additional_geometry_widget.property("assessmentSection") == "additionalGeometryMetrics"
    assert page.evaluation_editor.additional_geometry_values["mean_backbreak_m"].text() == "N/A"
    metrics_layout = page.evaluation_editor.additional_geometry_widget.layout().itemAt(1).layout()
    assert isinstance(metrics_layout, QtWidgets.QGridLayout)
    assert metrics_layout.verticalSpacing() >= 5 and metrics_layout.contentsMargins().right() >= 7
    assert page.evaluation_editor.additional_geometry_widget.layout().spacing() >= 7
    metric_labels = page.evaluation_editor.additional_geometry_widget.findChildren(
        QtWidgets.QLabel, "AssessmentMetricLabel",
    )
    assert len(metric_labels) == 5 and all(label.font().bold() for label in metric_labels)
    assert all(
        value.font().pointSize() == page.evaluation_editor.shortfall.font().pointSize()
        and value.alignment() & Qt.AlignmentFlag.AlignRight
        for value in page.evaluation_editor.additional_geometry_values.values()
    )
    assert not hasattr(page.evaluation_editor, "additional_geometry_source")
    left_top = page.assessment_details_card.mapTo(page.assessment_tab, QPoint()).y()
    right_top = page.assessment_basis_card.mapTo(page.assessment_tab, QPoint()).y()
    assert left_top == right_top
    assert inputs.layout().contentsMargins().top() == page.assessment_right.layout().contentsMargins().top()
    assert not [
        widget for widget in page.assessment_tab.findChildren(QtWidgets.QWidget)
        if widget.property("assessmentSection") == "measuredWallGeometry"
    ]
    assert not [
        button for button in page.assessment_tab.findChildren(QtWidgets.QPushButton)
        if button.text() == "Calculate from survey…"
    ]
    page.evaluation_editor.draft.measured_wall_geometry.mean_backbreak_m = 1.2
    page.evaluation_editor.draft.measured_wall_geometry.contour_rms_deviation_m = .4
    page.evaluation_editor.draft.measured_wall_geometry.measurement_method = "survey"
    page.evaluation_editor.draft.measured_wall_geometry.design_surface_source = "design.csv"
    page.evaluation_editor.draft.measured_wall_geometry.survey_source = "survey.csv"
    page.evaluation_editor.draft.measured_wall_geometry.survey_point_count = 12
    page.evaluation_editor._refresh_additional_geometry_metrics()
    assert page.evaluation_editor.additional_geometry_values["mean_backbreak_m"].text() == "1.2 m"
    assert page.evaluation_editor.additional_geometry_values["contour_rms_deviation_m"].text() == "0.4 m"
    measured_draft = page.evaluation_editor.collect().measured_wall_geometry
    assert (measured_draft.mean_backbreak_m, measured_draft.contour_rms_deviation_m) == (1.2, .4)
    assert (measured_draft.measurement_method, measured_draft.design_surface_source,
            measured_draft.survey_source, measured_draft.survey_point_count) == (
                "survey", "design.csv", "survey.csv", 12)
    assert page.evaluation_editor.summary.text() and "DAI: 1.000" in page.evaluation_editor.summary.text()
    assert page.evaluation_editor.comments.isVisible() and page.evaluation_editor.recommendations.isVisible()
    assert page.evaluation_editor.comments.height()<=70 and page.evaluation_editor.recommendations.height()<=70
    assert page.assessment_tab.findChildren(QtWidgets.QTextEdit).count(page.evaluation_editor.comments)==1
    assert page.assessment_tab.findChildren(QtWidgets.QTextEdit).count(page.evaluation_editor.recommendations)==1
    assert not hasattr(page.evaluation_editor,"design_table") and not hasattr(page.evaluation_editor,"condition_table")
    page.evaluation_editor.comments.setPlainText("Field note"); page.evaluation_editor.recommendations.setPlainText("Scale loose rock")
    application.processEvents(); collected=page.evaluation_editor.collect()
    assert collected.comments=="Field note" and collected.recommendations=="Scale loose rock"
    assert page.evaluation_editor.override_reason.isHidden()
    page.evaluation_editor.shortfall.set_nullable_value(2.5); application.processEvents()
    assert page.evaluation_editor.collect().design_inputs["bench_angle_shortfall_deg"]==2.5
    assert len(evaluation.revisions)==before
    page.close()
    application.processEvents()

    evaluation.active_revision().controlled_blasting_detection_source="manual_override"
    evaluation.active_revision().change_reason="Engineering review"
    manual=module.AssessmentAreaPage(context,1,"Domain",area.id); manual.resize(1920,1080); manual.show(); manual.tabs.setCurrentWidget(manual.assessment_tab); application.processEvents()
    assert manual.evaluation_editor.override_reason.isVisible()
    assert manual.evaluation_editor.override_reason.text()=="Engineering review"
    assert manual.evaluation_editor.collect().change_reason=="Engineering review"
    manual.close()


@pytest.mark.parametrize("manual_override",[False,True])
def test_assessment_page_opens_real_new_draft_sources(monkeypatch,manual_override):
    application=app()
    from tests.test_wall_assessment_persistence_ui import make_state
    from application.services.assessment_event_links import AssessmentEventLinkService
    from domain.assessment.evaluation import AssessmentAreaEvaluationService
    import ui.pages.assessment_area_page as module

    state,area=make_state(); area.event_links[0].status="excluded"
    service=AssessmentAreaEvaluationService(state)
    if manual_override:
        evaluation,draft=service.new_evaluation(area,"controlled_blasting_v1","Engineering review")
        assert draft.controlled_blasting_detection_source=="manual_override"
    else:
        evaluation,draft=service.new_evaluation(area)
        assert draft.controlled_blasting_detection_source=="no_confirmed_contour_link"

    class Attachments:
        def list_for_owner(self,*_args):return []
    class Controller:
        def __init__(self,*_args):self.state=state; self.attachments=Attachments(); self.links=AssessmentEventLinkService(state)
        def area(self,_id):return area
        def evaluation_draft(self,_area):return evaluation,draft
        def save_evaluation(self,*_args):raise AssertionError("opening and preview must not save")
    monkeypatch.setattr(module,"EntityPageController",Controller)
    stub_assessment_domain_repository(monkeypatch,module)
    context=SimpleNamespace(session_factory=None,current_user=SimpleNamespace(can_edit=True,display_name="Current Inspector"))
    page=module.AssessmentAreaPage(context,1,"Domain",area.id); page.resize(1920,1080); page.show(); page.tabs.setCurrentWidget(page.assessment_tab); application.processEvents()
    assert page.evaluation_editor.inspector.text()=="Current Inspector"
    assert page.assessment_basis_value.text()==("Controlled blasting" if manual_override else "Standard blasting")
    assert page.assessment_basis_detection.text()==("Manual matrix selection" if manual_override else "No confirmed contour blast link")
    assert page.evaluation_editor.override_reason.isVisible() is manual_override
    assert page.evaluation_editor.collect().controlled_blasting_detection_source==draft.controlled_blasting_detection_source
    page.evaluation_editor.toe.set_nullable_value(.5); application.processEvents()
    assert page.evaluation_editor.collect().design_inputs["toe_offset_from_design_m"]==.5
    page.close()


def test_completed_initial_result_uses_stored_values_then_live_edit_recalculates(monkeypatch):
    app()
    from copy import deepcopy
    from tests.test_wall_assessment_persistence_ui import make_state,filled_draft
    from ui.presentation_labels import result_label
    import ui.editors.assessment_evaluation_editor as module
    state,area=make_state(); evaluation,draft=filled_draft(state,area); saved=evaluation.save_revision(draft,"completed"); historical=deepcopy(saved)
    original=module.calculate_revision; calls=[]; allow=False
    def tracked(revision,*args,**kwargs):
        calls.append(revision)
        if not allow:raise AssertionError("completed revision was recalculated during initial display")
        return original(revision,*args,**kwargs)
    monkeypatch.setattr(module,"calculate_revision",tracked)
    dialog=module.AssessmentAreaEvaluationDialog(area,evaluation,saved,lambda *_:None)
    assert calls==[] and "DAI: 1.000" in dialog.summary.text()
    assert f"FCI: {saved.face_condition_index:.3f}" in dialog.summary.text()
    assert result_label(saved.result_label) in dialog.summary.text()
    allow=True; dialog.inspector.setText("Changed inspector")
    assert calls and dialog._preview is not saved
    assert saved.to_dict()==historical.to_dict() and len(evaluation.revisions)==1
    dialog._allow_close=True; dialog.close()


def test_cancelled_attachment_selection_does_not_ensure_owner(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    service=SimpleNamespace(list_for_owner=lambda *_:[]); ensured=[]
    manager=module.EntityAttachmentManagerWidget(service,"assessment_evaluation",None,"document",ensure_owner=lambda:ensured.append(True))
    monkeypatch.setattr(module.QFileDialog,"getOpenFileNames",lambda *_:([],"")); manager.add()
    assert ensured==[] and manager.owner_id is None


def test_cancelled_attachment_metadata_does_not_ensure_owner(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    service=SimpleNamespace(list_for_owner=lambda *_:[]); ensured=[]
    manager=module.EntityAttachmentManagerWidget(service,"assessment_evaluation",None,"document",ensure_owner=lambda:ensured.append(True))
    monkeypatch.setattr(module.QFileDialog,"getOpenFileNames",lambda *_:(["known.pdf"],"Documents (*.pdf *.doc *.docx *.xls *.xlsx *.csv *.txt *.dxf *.dwg *.zip)"))
    monkeypatch.setattr(module.DocumentBatchDialog,"exec",lambda *_:module.QDialog.DialogCode.Rejected)
    manager.add()
    assert ensured==[] and manager.owner_id is None


def test_successful_attachment_ensures_owner_once(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    added=[]
    service=SimpleNamespace(list_for_owner=lambda *_:[],add_files=lambda *args:added.append(args)); owners=[]
    def ensure():owners.append(True); return SimpleNamespace(id="E-1")
    manager=module.EntityAttachmentManagerWidget(service,"assessment_evaluation",None,"document",ensure_owner=ensure)
    monkeypatch.setattr(module.QFileDialog,"getOpenFileNames",lambda *_:(["known.pdf"],"Documents (*.pdf *.doc *.docx *.xls *.xlsx *.csv *.txt *.dxf *.dwg *.zip)"))
    monkeypatch.setattr(module.DocumentBatchDialog,"exec",lambda *_:module.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(module.DocumentBatchDialog,"entries",lambda *_:[("known.pdf",{})])
    manager.add(); assert len(owners)==1 and len(added)==1 and manager.owner_id=="E-1"


def test_unknown_document_warning_can_cancel_without_owner(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    service=SimpleNamespace(list_for_owner=lambda *_:[]); ensured=[]
    manager=module.EntityAttachmentManagerWidget(service,"assessment_evaluation",None,"document",ensure_owner=lambda:ensured.append(True))
    monkeypatch.setattr(module.QFileDialog,"getOpenFileNames",lambda *_:(["unknown.bin"],"All files (*)")); questions=[]; monkeypatch.setattr(module.QMessageBox,"question",lambda *args:questions.append(args[2]) or module.QMessageBox.StandardButton.No)
    manager.add(); assert questions==["SlopeForge may not be able to preview this file. Add it anyway?"] and ensured==[]


def test_failed_add_rolls_back_only_newly_prepared_owner(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    rolled_back=[]
    service=SimpleNamespace(list_for_owner=lambda *_:[],add_files=lambda *_:(_ for _ in ()).throw(RuntimeError("copy failed")))
    owner=SimpleNamespace(id="E-new")
    manager=module.EntityAttachmentManagerWidget(service,"assessment_evaluation",None,"document",
        ensure_owner=lambda:(owner,lambda:rolled_back.append(owner)))
    monkeypatch.setattr(module.QFileDialog,"getOpenFileNames",lambda *_:(['report.pdf'],"Documents (*.pdf *.doc *.docx *.xls *.xlsx *.csv *.txt *.dxf *.dwg *.zip)"))
    monkeypatch.setattr(module.DocumentBatchDialog,"exec",lambda *_:module.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(module.DocumentBatchDialog,"entries",lambda *_:[("report.pdf",{})])
    errors=[]; monkeypatch.setattr(module.QMessageBox,"critical",lambda *args:errors.append(args[2]))
    manager.add()
    assert rolled_back==[owner] and manager.owner_id is None and errors


def test_failed_add_never_rolls_back_existing_owner(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    service=SimpleNamespace(list_for_owner=lambda *_:[],add_files=lambda *_:(_ for _ in ()).throw(RuntimeError("copy failed")))
    ensured=[]
    manager=module.EntityAttachmentManagerWidget(service,"assessment_evaluation","E-existing","document",
        ensure_owner=lambda:ensured.append(True))
    monkeypatch.setattr(module.QFileDialog,"getOpenFileNames",lambda *_:(['report.pdf'],"Documents (*.pdf *.doc *.docx *.xls *.xlsx *.csv *.txt *.dxf *.dwg *.zip)"))
    monkeypatch.setattr(module.DocumentBatchDialog,"exec",lambda *_:module.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(module.DocumentBatchDialog,"entries",lambda *_:[("report.pdf",{})])
    monkeypatch.setattr(module.QMessageBox,"critical",lambda *_:None)
    manager.add()
    assert manager.owner_id=="E-existing" and ensured==[]


def _attachment():
    return SimpleNamespace(id="ATT-1",title="Report",file_date=date(2026,8,1),subtype="other",
        custom_subtype="",original_filename="report.pdf",description="",file_size_bytes=3)


def test_edit_error_is_reported_without_refresh_or_changed(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    item=_attachment(); service=SimpleNamespace(list_for_owner=lambda *_:[item],is_missing=lambda *_:False,
        resolve_path=lambda value:Path(value.original_filename),
        update_metadata=lambda *_args,**_kwargs:(_ for _ in ()).throw(RuntimeError("save failed")))
    manager=module.EntityAttachmentManagerWidget(service,"blast_event","BE-1","document"); manager.table.selectRow(0)
    monkeypatch.setattr(module.AttachmentMetadataDialog,"exec",lambda *_:module.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(module.AttachmentMetadataDialog,"values",lambda *_:{"title":"New","file_date":date.today(),"subtype":"other","description":"","custom_subtype":""})
    refreshed=[]; manager.refresh=lambda:refreshed.append(True); changes=[]; manager.changed.connect(lambda:changes.append(True))
    errors=[]; monkeypatch.setattr(module.QMessageBox,"critical",lambda *args:errors.append(args[2]))
    manager.edit()
    assert errors==["save failed"] and refreshed==[] and changes==[]


def test_delete_error_is_reported_without_refresh_or_changed(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    item=_attachment(); service=SimpleNamespace(list_for_owner=lambda *_:[item],is_missing=lambda *_:False,
        resolve_path=lambda value:Path(value.original_filename),
        delete_attachment=lambda *_:(_ for _ in ()).throw(RuntimeError("delete failed")))
    manager=module.EntityAttachmentManagerWidget(service,"blast_event","BE-1","document"); manager.table.selectRow(0)
    class Box:
        class Icon:Warning=1
        class ButtonRole:DestructiveRole=1; RejectRole=2
        def __init__(self,*_args,**_kwargs):self.delete=None
        def addButton(self,_text,role):
            button=object()
            if role==self.ButtonRole.DestructiveRole:self.delete=button
            return button
        def exec(self):return 0
        def clickedButton(self):return self.delete
        @staticmethod
        def critical(*args):errors.append(args[2])
    errors=[]; monkeypatch.setattr(module,"QMessageBox",Box)
    refreshed=[]; manager.refresh=lambda:refreshed.append(True); changes=[]; manager.changed.connect(lambda:changes.append(True))
    manager.delete()
    assert errors==["delete failed"] and refreshed==[] and changes==[]


def test_delete_cleanup_warning_still_refreshes_and_emits_changed(monkeypatch):
    app(); import ui.dialogs.entity_attachment_dialog as module
    item=_attachment(); items=[item]
    def delete(_id):
        items.clear()
        return SimpleNamespace(cleanup_warning="report.pdf.slopeforge-delete-ID.tmp: locked")
    service=SimpleNamespace(list_for_owner=lambda *_:list(items),is_missing=lambda *_:False,
        resolve_path=lambda value:Path(value.original_filename), delete_attachment=delete)
    manager=module.EntityAttachmentManagerWidget(service,"blast_event","BE-1","document"); manager.table.selectRow(0)
    warnings=[]; critical=[]
    class Box:
        class Icon:Warning=1
        class ButtonRole:DestructiveRole=1; RejectRole=2
        def __init__(self,*_args,**_kwargs):self.delete=None
        def addButton(self,_text,role):
            button=object()
            if role==self.ButtonRole.DestructiveRole:self.delete=button
            return button
        def exec(self):return 0
        def clickedButton(self):return self.delete
        @staticmethod
        def warning(*args):warnings.append(args[2])
        @staticmethod
        def critical(*args):critical.append(args[2])
    monkeypatch.setattr(module,"QMessageBox",Box)
    changes=[]; manager.changed.connect(lambda:changes.append(True))

    manager.delete()

    assert manager.table.rowCount()==0 and changes==[True]
    assert len(warnings)==1 and "temporary file could not be removed" in warnings[0]
    assert critical==[]

def test_russian_standard_buttons_are_never_blank(tmp_path):
    application=app()
    from app.localization import LANGUAGE_KEY,install_selected_translator
    store=QSettings(str(tmp_path/"settings.ini"),QSettings.Format.IniFormat); store.setValue(LANGUAGE_KEY,"ru")
    assert install_selected_translator(application,store)=="ru"
    box=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    assert box.button(QDialogButtonBox.StandardButton.Save).text()
    assert box.button(QDialogButtonBox.StandardButton.Cancel).text()
    message=QMessageBox(QMessageBox.Icon.Question,"Question","Continue?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
    assert message.button(QMessageBox.StandardButton.Yes).text()
    assert message.button(QMessageBox.StandardButton.No).text()
    ok=QMessageBox(QMessageBox.Icon.Information,"Done","Done",QMessageBox.StandardButton.Ok)
    assert ok.button(QMessageBox.StandardButton.Ok).text()


def test_report_saved_file_is_opened_without_success_modal(monkeypatch,tmp_path):
    app(); import ui.dialogs.project_report_dialog as module
    target=tmp_path/"report.xlsx"; context=SimpleNamespace(session_factory=object())
    generator=SimpleNamespace(execute=lambda command: SimpleNamespace(output_path=target.resolve()))
    dialog=module.ProjectReportDialog(generator,1,"Project")
    monkeypatch.setattr(module.QFileDialog,"getSaveFileName",lambda *_:(str(target),"xlsx"))
    generator.execute=lambda command: (target.write_bytes(b"xlsx"), SimpleNamespace(output_path=target.resolve()))[1]
    opened=[]; monkeypatch.setattr(module.QDesktopServices,"openUrl",lambda url:opened.append(url.toLocalFile()) or True)
    monkeypatch.setattr(module.QMessageBox,"information",lambda *_:pytest.fail("success modal is redundant"))
    dialog.generate()
    assert len(opened)==1 and Path(opened[0]).resolve()==target.resolve()
    assert dialog.result()==dialog.DialogCode.Accepted


def test_report_open_failure_warns_but_keeps_saved_file(monkeypatch,tmp_path):
    app(); import ui.dialogs.project_report_dialog as module
    target=tmp_path/"report.xlsx"
    generator=SimpleNamespace(execute=lambda command: (target.write_bytes(b"xlsx"), SimpleNamespace(output_path=target.resolve()))[1])
    dialog=module.ProjectReportDialog(generator,1,"Project")
    monkeypatch.setattr(module.QFileDialog,"getSaveFileName",lambda *_:(str(target),"xlsx")); monkeypatch.setattr(module.QDesktopServices,"openUrl",lambda _url:False)
    warnings=[]; monkeypatch.setattr(module.QMessageBox,"warning",lambda *args:warnings.append(args[2]))
    dialog.generate()
    assert target.exists() and warnings==["The report was saved, but could not be opened automatically."]
