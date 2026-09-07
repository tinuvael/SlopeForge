from copy import deepcopy
import json
from math import sqrt
from statistics import pstdev

import pytest

from domain.assessment.evaluation import MeasuredWallGeometry, calculate_revision
from domain.blasting.technical_card import ActualDrillingGroup, GeomechanicalParameters, JointSetOrientation, new_technical_card
from infrastructure.geometry_import.wall_rms import WallSurveyValidationError, calculate_wall_rms_from_csv, load_design_surface
from tests.test_technical_cards import event
from tests.test_wall_assessment import complete_revision


def test_engineering_inputs_and_actual_deviations_round_trip():
    card, draft = new_technical_card(event())
    draft.geomechanical_parameters = GeomechanicalParameters(rock_density_t_m3=2.71, ucs_mpa=80,
        joint_sets=[JointSetOrientation(45, 359, 1.2, 8.5)])
    draft.actual_execution.copy_from_design(draft.drilling_groups, mode="replace")
    actual = draft.actual_execution.actual_drilling_groups[0]
    actual.mean_collar_deviation_m, actual.max_collar_deviation_m = .1, .25
    actual.mean_toe_deviation_m, actual.max_toe_deviation_m = .4, .9
    draft.actual_execution.copy_from_design(draft.drilling_groups, mode="replace")
    saved = card.save_revision(draft)
    restored = type(card).from_dict(json.loads(json.dumps(card.to_dict()))).revisions[0]
    assert restored.geomechanical_parameters.rock_density_t_m3 == 2.71
    assert restored.geomechanical_parameters.joint_sets[0].spacing_m == 1.2
    assert restored.geomechanical_parameters.joint_sets[0].persistence_m == 8.5
    assert restored.actual_execution.actual_drilling_groups[0].design_group_id == saved.drilling_groups[0].id
    assert restored.actual_execution.actual_drilling_groups[0].mean_toe_deviation_m == .4


def test_compact_comparisons_wrap_azimuth_and_ratios_are_safe():
    _, revision = new_technical_card(event()); design = revision.drilling_groups[0]
    design.burden_m, design.spacing_m, design.azimuth_deg = 4, 5, 359
    revision.production_parameters.design_bench_height_m = 12
    revision.actual_execution.copy_from_design(revision.drilling_groups, mode="replace")
    actual = revision.actual_execution.actual_drilling_groups[0]
    actual.azimuth_deg = 1; actual.mean_toe_deviation_m = 1
    rows = {row["parameter"]: row for row in revision.compact_design_actual(design.id)}
    assert rows["Azimuth"]["delta"] == 2
    assert revision.compact_design_actual("missing")[0]["delta"] is None
    assert revision.engineering_ratios(design.id) == {"B/S": .8, "S/B": 1.25, "H/B": 3,
        "mean toe deviation / burden": .25, "mean toe deviation / spacing": .2}
    actual.burden_m = 0
    assert revision.engineering_ratios(design.id)["H/B"] is None


def test_measured_geometry_round_trip_does_not_change_scoring():
    revision = complete_revision(); calculate_revision(revision, True)
    before = (revision.design_achievement_index, revision.face_condition_index, revision.result_quadrant)
    revision.measured_wall_geometry = MeasuredWallGeometry(1, 2, .3, .2, .5, "survey", "design.csv", "actual.csv", 3, "unsigned_point_to_surface_v1")
    restored = type(revision).from_dict(json.loads(json.dumps(revision.to_dict())))
    calculate_revision(restored, True)
    assert restored.measured_wall_geometry.measurement_method == "survey"
    assert (restored.design_achievement_index, restored.face_condition_index, restored.result_quadrant) == before


def test_synthetic_surface_rms_and_invalid_csv(tmp_path):
    design = tmp_path / "design.csv"; survey = tmp_path / "survey.csv"
    design.write_text("PID,X,Y,Z,FID\n1,0,0,0,10\n2,10,0,0,10\n3,0,10,0,10\n")
    survey.write_text("X,Y,Z\n1,1,1\n2,2,2\n3,3,3\n")
    assert load_design_surface(design).faces.shape == (1, 3)
    result = calculate_wall_rms_from_csv(design, survey)
    assert result.rms_m == pytest.approx(sqrt(14 / 3))
    assert result.mean_m == 2 and result.std_m == pytest.approx(pstdev([1, 2, 3]))
    assert (result.min_m, result.max_m, result.point_count) == (1, 3, 3)
    design.write_text("X,Y,Z\n0,0,0\n")
    with pytest.raises(WallSurveyValidationError) as error:
        calculate_wall_rms_from_csv(design, survey)
    assert error.value.code == "missing_columns"


def test_actual_charge_derivatives_use_factual_components():
    from domain.blasting.charge_design import ChargeComponent, ChargeComponentKind

    actual = ActualDrillingGroup(stemming_length_m=99, charge_mass_per_hole_kg=999)
    actual.charge_components = [ChargeComponent(id="stemming", kind=ChargeComponentKind.STEMMING, start_depth_m=0, end_depth_m=2)]
    assert actual.stemming_total_m() == 2
    assert actual.explosive_mass_per_hole_kg() == 0


def test_interleaved_fids_build_their_own_triangles_and_skip_invalid_groups(tmp_path):
    design = tmp_path / "interleaved.csv"
    design.write_text(
        "PID,X,Y,Z,FID\n"
        "1,0,0,0,20\n2,10,0,0,10\n3,0,10,0,20\n"
        "4,11,0,0,10\n5,0,0,1,99\n6,0,0,2,20\n7,10,1,0,10\n"
    )
    mesh = load_design_surface(design)
    triangles = mesh.vertices[mesh.faces]
    assert mesh.faces.shape == (2, 3)
    assert {frozenset(map(tuple, triangle)) for triangle in triangles} == {
        frozenset({(0.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 2.0)}),
        frozenset({(10.0, 0.0, 0.0), (11.0, 0.0, 0.0), (10.0, 1.0, 0.0)}),
    }


@pytest.mark.parametrize(("value", "code"), (("bad", "non_numeric"), ("nan", "non_finite")))
def test_survey_csv_rejects_invalid_numeric_values(tmp_path, value, code):
    design = tmp_path / "design.csv"; survey = tmp_path / "survey.csv"
    design.write_text("PID,X,Y,Z,FID\n1,0,0,0,10\n2,10,0,0,10\n3,0,10,0,10\n")
    survey.write_text(f"X,Y,Z\n1,1,{value}\n")
    with pytest.raises(WallSurveyValidationError) as error:
        calculate_wall_rms_from_csv(design, survey)
    assert error.value.code == code


def test_wall_rms_csv_parser_does_not_import_pandas():
    source = __import__("pathlib").Path("infrastructure/geometry_import/wall_rms.py").read_text()
    assert "import pandas" not in source


def test_missing_rtree_is_reported_as_dependency_error(monkeypatch):
    import builtins
    from infrastructure.geometry_import import wall_rms

    real_import = builtins.__import__
    def without_rtree(name, *args, **kwargs):
        if name == "rtree":
            raise ModuleNotFoundError("No module named 'rtree'")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_rtree)
    with pytest.raises(WallSurveyValidationError) as error:
        wall_rms._dependencies()
    assert error.value.code == "dependencies"


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_positive_geomechanics_measurements_are_validated(value):
    with pytest.raises(ValueError):
        GeomechanicalParameters(rock_density_t_m3=value)
    with pytest.raises(ValueError):
        JointSetOrientation(45, 90, spacing_m=value)
    with pytest.raises(ValueError):
        JointSetOrientation(45, 90, persistence_m=value)


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_non_negative_measured_values_are_validated(value):
    with pytest.raises(ValueError):
        ActualDrillingGroup(mean_toe_deviation_m=value)
    with pytest.raises(ValueError):
        MeasuredWallGeometry(contour_rms_deviation_m=value)


def test_measurement_method_uses_only_canonical_codes():
    assert MeasuredWallGeometry(measurement_method=None).measurement_method is None
    assert MeasuredWallGeometry(measurement_method="laser_scan").measurement_method == "laser_scan"
    with pytest.raises(ValueError, match="Unsupported measurement method"):
        MeasuredWallGeometry(measurement_method="Лазерное сканирование")


def test_legacy_measurement_method_combo_is_not_part_of_active_assessment_ui():
    source = __import__("pathlib").Path("ui/editors/assessment_evaluation_editor.py").read_text()
    assert "self.measurement_method.currentIndexChanged.connect(self._changed)" not in source
    assert "Calculate from survey…" not in source
