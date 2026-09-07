from __future__ import annotations

from tools.diagnose_wall_measurement_test_area import build_report


def test_clean_test_area_landmark_report_is_complete_and_has_no_unexplained_losses():
    report = build_report()

    assert report["profile_count"] == 5
    assert len(report["profiles"]) == report["profile_count"]
    for index, profile in enumerate(report["profiles"]):
        assert profile["profile_index"] == index
        assert profile["actual_segments"]["evaluated_display"] > 0
        assert profile["actual_segments"]["measurement_context"] > 0
        for landmark in profile["landmarks"].values():
            assert landmark["final_status"] == "detected"
            assert landmark["reason"] == "detected"
            assert landmark["expected_design_u_z"] is not None
            assert landmark["final_actual_u_z"] is not None
            assert landmark["preprocessing"] == {
                "input_geometry_present_at_candidate_transition": True,
                "removed_by": None,
            }
            assert landmark["candidates"]
    for summary in report["summary"].values():
        assert summary["final_reason_counts"] == {"detected": 5}
        # Nearby wrong-shape vertices are recorded as rejected alternatives;
        # they must not be confused with a final landmark loss.
        assert set(summary["rejection_gate_counts"]).issubset({
            "transition_topology", "face_resumes_downstream",
        })
    assert report["representative_profiles"]["all_detected"] is not None
    assert report["representative_profiles"]["spatially_displaced"] is None
