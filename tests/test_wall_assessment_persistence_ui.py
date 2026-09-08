from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="Qt unavailable", exc_type=ImportError)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.blasting.entities import BlastEvent
from domain.assessment.entities import AssessmentArea, AssessmentEventLink
from tests.assessment_boundary_fixtures import geometry_revision
from application.state.assessment_domain_state import AssessmentDomainState
from domain.assessment.evaluation import (AssessmentAreaEvaluationService, AssessmentCriterionResult,
 CONDITION, DESIGN, calculate_revision)
from application.services.wall_conformance import assessment_geometry_inputs_from_measurement_summary
from ui.editors.assessment_evaluation_editor import (
    AssessmentAreaEvaluationDialog, DAMAGE_WARNING, NullableDoubleSpinBox,
    QuadrantPlot,
)


def app(): return QApplication.instance() or QApplication([])

def make_state():
    polygon=PlanPolygon((PlanPoint(0,0),PlanPoint(2,0),PlanPoint(2,2),PlanPoint(0,0)))
    geometry=geometry_revision("AA-1-R001","AA-1",1,datetime.now(timezone.utc),polygon,dataset_id="D",minimum=100,maximum=110)
    link=AssessmentEventLink("BE-1","BE-1-R001","confirmed","manual",id="L-1",assessment_area_geometry_revision_id=geometry.id)
    area=AssessmentArea("AA-1","Wall",date.today(),[geometry],geometry.id,[link])
    return AssessmentDomainState(blast_events=[BlastEvent("BE-1","Контур","contour",date.today(),105)],assessment_areas=[area]),area

def filled_draft(state,area):
    evaluation,draft=AssessmentAreaEvaluationService(state).new_evaluation(area)
    draft.inspector="Иванов"
    draft.design_inputs={"design_bench_face_angle_deg":65.0,"actual_bench_face_angle_deg":66.0,"bench_angle_shortfall_deg":0.0,"design_berm_width_m":10.0,"actual_berm_width_m":10.0,"berm_width_deficit_m":0.0,"toe_offset_from_design_m":0.0,"measurement_method":"рулетка","measurement_notes":"контроль"}
    values={"bench_angle":0,"berm_width":0,"toe_position":0,"visible_drillhole_traces":90,"crest_loss":1,"damage":1}
    options={"loose_blocks":"several_small","face_profile":"hard_toe","open_cracks":"closed"}
    results=[]
    template=AssessmentAreaEvaluationService(state).detect_template(area)[0]
    from domain.assessment.evaluation import get_template
    for section in get_template(template).sections:
        for criterion in section.criteria:
            results.append(AssessmentCriterionResult(criterion.id,criterion.name,section.id,
                raw_numeric_value=values.get(criterion.id),selected_option_id=options.get(criterion.id),
                manual_score=8 if criterion.id=="damage" else None,override_reason="Экспертная оценка" if criterion.id=="damage" else None,
                notes="осмотр" if criterion.id=="damage" else "",maximum_score=criterion.maximum_score))
    draft.criterion_results=results
    draft.face_condition_inputs={k:v for k,v in values.items() if k not in {"bench_angle","berm_width","toe_position"}}|options
    calculate_revision(draft,True)
    return evaluation,draft


def wall_conformance_summary(*, angle, berm, toe, additional=None):
    aggregate = lambda value: SimpleNamespace(
        valid_count=0 if value is None else 1,
        total_count=1,
        median=value,
        mean=value,
        minimum=value,
        maximum=value,
    )
    return SimpleNamespace(
        angle_deviation_deg=aggregate(angle),
        upper_berm_width_deviation_m=aggregate(berm),
        toe_signed_offset_u_m=aggregate(toe),
        additional_geometry=additional,
    )


def additional_geometry_summary(*, backbreak=None, overbreak=None, underbreak=None, rms=None):
    aggregate = lambda value: SimpleNamespace(
        valid_count=0 if value is None else 1,
        total_count=1,
        median=value,
        mean=value,
        minimum=value,
        maximum=value,
    )
    return SimpleNamespace(
        backbreak_m=aggregate(backbreak),
        mean_overbreak_m=overbreak,
        mean_underbreak_m=underbreak,
        contour_rms_deviation_m=rms,
    )


def confirm_wall_conformance_preview(monkeypatch, button_text):
    def choose(box):
        next(button for button in box.buttons() if button.text() == button_text).click()
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", choose)

def test_new_evaluation_is_transient_and_empty_legacy_is_safe():
    state,area=make_state(); evaluation,draft=AssessmentAreaEvaluationService(state).new_evaluation(area)
    assert state.evaluations==[] and evaluation.active_revision() is None
    state.evaluations.append(evaluation)
    assert evaluation.active_revision() is None and draft.evaluation_id==evaluation.id

def test_draft_revision_stores_all_inputs_and_is_independent():
    state,area=make_state(); evaluation,draft=filled_draft(state,area); state.evaluations.append(evaluation)
    first=evaluation.save_revision(draft,"draft")
    assert first.design_inputs["design_bench_face_angle_deg"]==65
    assert first.face_condition_inputs["visible_drillhole_traces"]==90
    assert next(r for r in first.criterion_results if r.criterion_id=="loose_blocks").selected_option_id=="several_small"
    damage=next(r for r in first.criterion_results if r.criterion_id=="damage")
    assert (damage.manual_score,damage.override_reason)==(8,"Экспертная оценка")
    edit=deepcopy(first); edit.design_inputs["actual_bench_face_angle_deg"]=60; evaluation.save_revision(edit,"draft")
    assert first.design_inputs["actual_bench_face_angle_deg"]==66

def test_completed_end_to_end_save_load_restores_everything(tmp_path):
    state,area=make_state(); evaluation,draft=filled_draft(state,area); state.evaluations.append(evaluation)
    saved=evaluation.save_revision(draft,"completed")
    restored=AssessmentDomainState.from_dict(state.to_dict()); revision=restored.evaluations[0].active_revision()
    assert revision.status=="completed" and revision.design_inputs==saved.design_inputs
    assert revision.face_condition_inputs==saved.face_condition_inputs
    assert [(r.criterion_id,r.raw_numeric_value,r.selected_option_id,r.manual_score,r.override_reason,r.accepted_score) for r in revision.criterion_results]==[(r.criterion_id,r.raw_numeric_value,r.selected_option_id,r.manual_score,r.override_reason,r.accepted_score) for r in saved.criterion_results]
    assert (revision.design_achievement_index,revision.face_condition_index,revision.result_quadrant)==(1.0,saved.face_condition_index,saved.result_quadrant)

def test_dialog_restores_without_mutating_source_and_nullable_zero():
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area); source=deepcopy(draft); dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    assert dialog.shortfall.nullable_value()==0 and dialog.deficit.nullable_value()==0 and dialog.toe.nullable_value()==0
    assert dialog.editors["visible_drillhole_traces"].input.nullable_value()==90
    assert dialog.editors["loose_blocks"].input.currentData()=="several_small"
    assert dialog.editors["damage"].manual_score.nullable_value()==8 and dialog.editors["damage"].override_reason=="Экспертная оценка"
    assert draft.to_dict()==source.to_dict() and not dialog._dirty
    blank=NullableDoubleSpinBox(); assert blank.nullable_value() is None; blank.set_nullable_value(0); assert blank.nullable_value()==0
    dialog._allow_close=True; dialog.close()

def test_damage_intermediate_range_requires_explicit_score(monkeypatch):
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area); dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    editor=dialog.editors["damage"]; editor.input.set_nullable_value(1); editor.manual_score.clear_value(); editor.manual_score.editingFinished.emit(); dialog.refresh(False)
    assert DAMAGE_WARNING in editor.help_button.toolTip() and dialog._preview.face_condition_index is None
    prompts=[]; monkeypatch.setattr(QtWidgets.QInputDialog,"getText",lambda *_args,**_kwargs:(prompts.append(True) or "Expert review",True))
    editor.manual_score.set_nullable_value(8); editor.manual_score.editingFinished.emit(); dialog.refresh(False)
    assert prompts==[] and dialog._preview.design_achievement_index==1 and dialog._preview.face_condition_index is not None and dialog.plot.design==1
    assert "Several small blocks" in dialog.editors["loose_blocks"].input.currentText()
    assert "Hard toe" in dialog.editors["face_profile"].input.currentText()
    dialog._allow_close=True; dialog.close()

def test_dialog_initialization_does_not_collect_before_restore(monkeypatch):
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area); calls=[]
    original=AssessmentAreaEvaluationDialog.collect
    def checked(self):
        calls.append(self.shortfall.nullable_value()); return original(self)
    monkeypatch.setattr(AssessmentAreaEvaluationDialog,"collect",checked)
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    assert calls and calls[0]==0
    dialog._allow_close=True; dialog.close()

def test_direct_geometry_inputs_are_canonical_and_live_preview_does_not_save():
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area); calls=[]
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:calls.append(True))
    dialog.shortfall.set_nullable_value(3); dialog.deficit.set_nullable_value(1.5); dialog.toe.set_nullable_value(.8)
    collected=dialog.collect()
    assert collected.design_inputs=={
        "bench_angle_shortfall_deg":3.0,"berm_width_deficit_m":1.5,
        "toe_offset_from_design_m":.8,
    }
    assert not ({"design_bench_face_angle_deg","actual_bench_face_angle_deg","design_berm_width_m","actual_berm_width_m"}&collected.design_inputs.keys())
    angle=next(result for result in dialog._preview.criterion_results if result.criterion_id=="bench_angle")
    assert calls==[] and angle.accepted_score is not None
    assert not hasattr(dialog,"design_table") and not hasattr(dialog,"condition_table")
    assert not hasattr(dialog,"scoring_details")
    dialog._allow_close=True; dialog.close()

def test_legacy_geometry_payload_is_presented_without_mutating_history():
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area)
    draft.design_inputs={"design_bench_face_angle_deg":60,"actual_bench_face_angle_deg":57,
                         "design_berm_width_m":8,"actual_berm_width_m":6.5,
                         "toe_offset_from_design_m":.4,"measurement_method":"survey","measurement_notes":"legacy"}
    original=deepcopy(draft)
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    assert dialog.shortfall.nullable_value()==3 and dialog.deficit.nullable_value()==1.5
    assert draft.to_dict()==original.to_dict()
    assert set(dialog.collect().design_inputs)=={"bench_angle_shortfall_deg","berm_width_deficit_m","toe_offset_from_design_m"}
    dialog._allow_close=True; dialog.close()

def test_manual_matrix_reason_is_only_available_for_manual_selection():
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area)
    automatic=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    assert automatic.override_reason.isHidden()
    automatic._allow_close=True; automatic.close()
    draft.controlled_blasting_detection_source="manual_override"; draft.change_reason="Engineering review"
    manual=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    assert not manual.override_reason.isHidden() and manual.override_reason.text()=="Engineering review"
    manual._allow_close=True; manual.close()

def test_compact_integer_manual_score_without_reason_prompt(monkeypatch):
    application=app(); state,area=make_state(); evaluation,draft=filled_draft(state,area); dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None); dialog.show(); application.processEvents()
    editor=dialog.geometry_editors["bench_angle"]
    def rendered(colour):
        image=editor.manual_score.lineEdit().grab().toImage(); target=QColor(colour)
        matches=sum(abs(image.pixelColor(x,y).red()-target.red())<=12 and abs(image.pixelColor(x,y).green()-target.green())<=12 and abs(image.pixelColor(x,y).blue()-target.blue())<=12 for x in range(3,max(4,image.width()-3)) for y in range(3,max(4,image.height()-3)))
        # Subpixel text antialiasing can produce a handful of yellow-ish pixels.
        # A score state is present only when the background contains the colour.
        return matches >= max(10, image.width() * image.height() // 20)
    assert editor.manual_score.objectName()=="AutomaticScore" and editor.manual_score.lineEdit().styleSheet()==""
    assert not rendered("#fff4cc") and not rendered("#fff0f0")
    assert not hasattr(editor,"override_panel") and not hasattr(editor,"override_toggle")
    assert editor.manual_score.maximum()==editor.criterion.maximum_score
    assert editor.score_state_frame.isAncestorOf(editor.manual_score) and editor.help_button.toolTip()
    assert not [button for button in editor.findChildren(QtWidgets.QPushButton) if button.text() in {"Clear","Очистить"}]
    assert not [label for label in editor.findChildren(QtWidgets.QLabel) if label.text() in {"Required","Manual score","Обязательно","Ручной балл"}]
    prompts=[]; monkeypatch.setattr(QtWidgets.QInputDialog,"getText",lambda *_args,**_kwargs:(prompts.append(True) or "Survey review",True))
    editor.manual_score.set_nullable_value(12)
    assert prompts==[] and editor.override_reason is None and not dialog.shortfall.isEnabled()
    assert dialog._preview.criterion_results[0].accepted_score==12
    assert isinstance(editor.manual_score,QtWidgets.QSpinBox) and editor.manual_score.singleStep()==1
    assert editor.manual_score.text().startswith(f"12 / {editor.criterion.maximum_score:g}") and editor.manual_score.styleSheet()==""
    application.processEvents(); assert rendered("#fff4cc")
    editor.manual_score.stepUp(); assert editor.manual_score.value()==13 and editor._manual_value==13
    editor.manual_score.stepDown(); assert editor.manual_score.value()==12 and editor._manual_value==12
    QTest.keyClick(editor.manual_score,Qt.Key.Key_Up); assert editor.manual_score.value()==13
    QTest.keyClick(editor.manual_score,Qt.Key.Key_Down); assert editor.manual_score.value()==12
    editor.manual_score.clear_value(); editor.manual_score.editingFinished.emit()
    assert editor.override_reason is None and dialog.shortfall.isEnabled()
    dialog.shortfall.clear_value(); dialog.refresh(False); assert editor.manual_score.objectName()=="MissingScore" and "Required" in editor.manual_score.toolTip()
    application.processEvents(); assert rendered("#fff0f0")
    editor.manual_score.set_nullable_value(10); editor.manual_score.editingFinished.emit()
    assert editor._manual_value==10 and not dialog.shortfall.isEnabled() and prompts==[]
    dialog._allow_close=True; dialog.close()

def test_storage_failure_does_not_report_success_or_create_revision(monkeypatch):
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area)
    def failure(*_args): raise OSError("disk full")
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,failure)
    monkeypatch.setattr(QtWidgets.QMessageBox,"critical",lambda *_args,**_kwargs: QtWidgets.QMessageBox.StandardButton.Ok)
    assert dialog.save("completed") is False and evaluation.revisions==[] and state.evaluations==[]
    dialog._allow_close=True; dialog.close()


def test_quadrant_axes_use_palette_foreground_and_border_roles():
    app()
    plot = QuadrantPlot()
    palette = QPalette(plot.palette())
    palette.setColor(QPalette.ColorRole.Text, QColor("#e6edf3"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#8b949e"))
    plot.setPalette(palette)
    assert plot._axis_foreground() == QColor("#e6edf3")
    assert plot._axis_border() == QColor("#8b949e")
    source = __import__("pathlib").Path(
        "ui/editors/assessment_evaluation_editor.py"
    ).read_text()
    assert "Qt.GlobalColor.black" not in source


@pytest.mark.parametrize(
    ("angle", "berm", "expected_angle", "expected_berm"),
    ((-2.4, -1.3, 2.4, 1.3), (+1.2, +0.5, 0.0, 0.0)),
)
def test_wall_conformance_q2_mapping_preserves_assessment_semantics(
    angle, berm, expected_angle, expected_berm,
):
    mapped = assessment_geometry_inputs_from_measurement_summary(
        wall_conformance_summary(angle=angle, berm=berm, toe=-0.8)
    )
    assert mapped.bench_angle_shortfall_deg == expected_angle
    assert mapped.berm_width_deficit_m == expected_berm
    assert mapped.toe_offset_from_design_m == -0.8


def test_wall_conformance_apply_is_explicit_dirty_and_unsaved(monkeypatch):
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area); saves=[]
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:saves.append(True))
    dialog.shortfall.set_nullable_value(3.0); dialog.deficit.set_nullable_value(1.5); dialog.toe.set_nullable_value(.4)
    dialog.set_wall_conformance_summary_provider(
        lambda: SimpleNamespace(
            measurement_summary=wall_conformance_summary(
                angle=-2.4, berm=-1.3, toe=1.2,
                additional=additional_geometry_summary(
                    backbreak=.8, overbreak=.6, underbreak=.3, rms=.9,
                ),
            ),
            design_dataset=SimpleNamespace(logical_id="design-current"),
            actual_dataset=SimpleNamespace(logical_id="actual-current"),
        )
    )
    assert dialog.wall_conformance_import_button.isEnabled()
    confirm_wall_conformance_preview(monkeypatch, "Apply")
    dialog._use_wall_conformance_measurements()
    assert (dialog.shortfall.nullable_value(), dialog.deficit.nullable_value(), dialog.toe.nullable_value()) == (2.4, 1.3, 1.2)
    measured = dialog.draft.measured_wall_geometry
    assert (
        measured.mean_backbreak_m, measured.maximum_backbreak_m,
        measured.mean_overbreak_m, measured.mean_underbreak_m,
        measured.contour_rms_deviation_m,
    ) == (.8, .8, .6, .3, .9)
    assert (measured.calculation_method, measured.measurement_method) == (
        "wall_conformance_transverse_profiles_v1", "survey",
    )
    assert (measured.design_surface_source, measured.survey_source) == (
        "design-current", "actual-current",
    )
    assert dialog.additional_geometry_values["contour_rms_deviation_m"].text() == "0.9 m"
    assert dialog._dirty and not saves and evaluation.revisions == []
    assert not dialog.wall_conformance_import_source.isHidden()
    dialog._allow_close=True; dialog.close()


def test_zero_compatible_actual_coverage_does_not_open_import_preview_or_mutate(monkeypatch):
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area)
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    dialog.shortfall.set_nullable_value(3.0); dialog.deficit.set_nullable_value(1.5); dialog.toe.set_nullable_value(.4)
    dialog.draft.measured_wall_geometry.mean_backbreak_m = .9
    dialog._dirty = False
    dialog.set_wall_conformance_summary_provider(
        lambda: SimpleNamespace(measurement_summary=wall_conformance_summary(angle=-2.4, berm=-1.3, toe=1.2))
    )
    import ui.editors.assessment_evaluation_editor as editor_module
    monkeypatch.setattr(editor_module, "compatible_actual_profile_count", lambda _result: 0)
    warnings=[]
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *_args: warnings.append(True))
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda *_args: (_ for _ in ()).throw(AssertionError("preview opened")))
    dialog._use_wall_conformance_measurements()
    assert warnings
    assert (dialog.shortfall.nullable_value(), dialog.deficit.nullable_value(), dialog.toe.nullable_value()) == (3.0, 1.5, .4)
    assert dialog.draft.measured_wall_geometry.mean_backbreak_m == .9
    assert not dialog._dirty
    dialog._allow_close=True; dialog.close()


def test_wall_conformance_cancel_and_missing_kpi_keep_existing_values(monkeypatch):
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area)
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    dialog.shortfall.set_nullable_value(3.0); dialog.deficit.set_nullable_value(1.5); dialog.toe.set_nullable_value(.4)
    dialog.draft.measured_wall_geometry.mean_underbreak_m = .9
    dialog.set_wall_conformance_summary_provider(
        lambda: SimpleNamespace(measurement_summary=wall_conformance_summary(
            angle=-2.4, berm=None, toe=-.8,
            additional=additional_geometry_summary(backbreak=.5, overbreak=.2, rms=.4),
        ))
    )
    confirm_wall_conformance_preview(monkeypatch, "Cancel")
    dialog._use_wall_conformance_measurements()
    assert (dialog.shortfall.nullable_value(), dialog.deficit.nullable_value(), dialog.toe.nullable_value()) == (3.0, 1.5, .4)
    assert dialog.draft.measured_wall_geometry.mean_underbreak_m == .9
    confirm_wall_conformance_preview(monkeypatch, "Apply")
    dialog._use_wall_conformance_measurements()
    assert (dialog.shortfall.nullable_value(), dialog.deficit.nullable_value(), dialog.toe.nullable_value()) == (2.4, 1.5, -.8)
    assert dialog.draft.measured_wall_geometry.mean_underbreak_m == .9
    assert (dialog.draft.measured_wall_geometry.mean_backbreak_m,
            dialog.draft.measured_wall_geometry.mean_overbreak_m,
            dialog.draft.measured_wall_geometry.contour_rms_deviation_m) == (.5, .2, .4)
    dialog._allow_close=True; dialog.close()


def test_wall_conformance_action_is_disabled_for_read_only_or_stale_geometry():
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area)
    read_only=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None,read_only=True)
    read_only.set_wall_conformance_summary_provider(lambda: wall_conformance_summary(angle=0, berm=0, toe=0))
    assert not read_only.wall_conformance_import_button.isEnabled()
    read_only._allow_close=True; read_only.close()
    editable=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    editable.set_wall_conformance_summary_provider(
        lambda: wall_conformance_summary(angle=0, berm=0, toe=0),
        lambda: (False, "Wall Conformance measurements are only available for the active geometry revision."),
    )
    assert not editable.wall_conformance_import_button.isEnabled()
    assert "active geometry revision" in editable.wall_conformance_import_button.toolTip()
    editable._allow_close=True; editable.close()


def test_wall_conformance_import_matches_manual_geometry_scoring(monkeypatch):
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area)
    manual=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    manual.shortfall.set_nullable_value(2.4); manual.deficit.set_nullable_value(1.3); manual.toe.set_nullable_value(-.8)
    manual_result=manual.collect()
    imported=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    imported.set_wall_conformance_summary_provider(
        lambda: wall_conformance_summary(angle=-2.4, berm=-1.3, toe=-.8)
    )
    confirm_wall_conformance_preview(monkeypatch, "Apply")
    imported._use_wall_conformance_measurements()
    imported_result=imported.collect()
    assert (imported_result.design_achievement_index, imported_result.face_condition_index) == (
        manual_result.design_achievement_index, manual_result.face_condition_index
    )
    manual._allow_close=True; manual.close(); imported._allow_close=True; imported.close()


def test_wall_conformance_unavailable_or_empty_result_never_mutates_inputs(monkeypatch):
    app(); state,area=make_state(); evaluation,draft=filled_draft(state,area)
    dialog=AssessmentAreaEvaluationDialog(area,evaluation,draft,lambda *_:None)
    dialog.shortfall.set_nullable_value(3.0); dialog.deficit.set_nullable_value(1.5); dialog.toe.set_nullable_value(.4)
    original = (3.0, 1.5, .4)
    warnings=[]
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *_args: warnings.append(True))
    dialog.set_wall_conformance_summary_provider(
        lambda: (_ for _ in ()).throw(ValueError("No active Actual survey is configured for this Project."))
    )
    dialog._use_wall_conformance_measurements()
    assert warnings and (dialog.shortfall.nullable_value(), dialog.deficit.nullable_value(), dialog.toe.nullable_value()) == original
    messages=[]
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *_args: messages.append(True))
    dialog.set_wall_conformance_summary_provider(
        lambda: wall_conformance_summary(angle=None, berm=None, toe=None)
    )
    dialog._use_wall_conformance_measurements()
    assert messages and (dialog.shortfall.nullable_value(), dialog.deficit.nullable_value(), dialog.toe.nullable_value()) == original
    dialog._allow_close=True; dialog.close()
