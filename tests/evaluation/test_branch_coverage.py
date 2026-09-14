"""Coverage arithmetic uses invented metadata and predictions, never footage."""

import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("coverage_evaluator", Path(__file__).resolve().parents[2] / "scripts" / "evaluate-interactions.py")
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def dataset():
    # Deliberately tiny calculation fixture: these counts are not rollout targets.
    session = {
        "id": "staged", "site_id": "site-1", "split": "test", "camera_id": "camera-1", "day_id": "day-1",
        "recording_sha256": "a" * 64, "source_kind": "STAGED", "duration_seconds": 90,
        "normal_duration_measured": False,
    }
    normal = {**session, "id": "ordinary", "recording_sha256": "b" * 64, "source_kind": "NORMAL_OBSERVATION", "duration_seconds": 10, "normal_duration_measured": True}
    labels = ("NORMAL_SHOPPING", "TAKE_PRODUCT", "RETURN_PRODUCT", "PLACE_IN_BASKET", "POSSIBLE_CONCEALMENT", "NORMAL_SHOPPING", "RETURN_PRODUCT", "UNCLEAR", "UNCLEAR")
    windows = []
    for i, (scenario, label) in enumerate(zip(evaluator.SCENARIOS, labels)):
        windows.append({
            "id": "w" + str(i), "session_id": "staged", "start_seconds": i * 10, "end_seconds": i * 10 + 10,
            "label": label, "scenario": scenario, "person_ids": ["participant-1"],
            "frames": [{"path": f"frames/w{i}-{n}.jpg", "offset_seconds": i * 10 + offset} for n, offset in enumerate((0, 5, 10))],
        })
    windows.append({**copy.deepcopy(windows[0]), "id": "normal", "session_id": "ordinary", "frames": [{"path": f"frames/ordinary-{n}.jpg", "offset_seconds": offset} for n, offset in enumerate((0, 5, 10))]})
    manifest = {
        "schema_version": "1.0", "dataset_id": "synthetic-coverage-arithmetic", "split_policy": "DISJOINT_CAMERA_DAY_PERSON",
        "authorization": {"confirmed": True, "reference": "synthetic-no-media"}, "sessions": [session, normal], "windows": windows,
    }
    predictions = {"provenance": {"model": "not-a-model", "prompt_version": "none", "origin": "SYNTHETIC_METRICS_TEST"}, "results": [
        {"window_id": w["id"], "action": w["label"], "alarm_eligible": w["label"] == "POSSIBLE_CONCEALMENT", "inference_ms": 10} for w in windows
    ]}
    branch = {
        "site_id": "site-1", "minimum_successful_windows_per_class": {label: 1 for label in evaluator.CLASSES},
        "minimum_successful_windows_per_scenario": {scenario: 1 for scenario in evaluator.SCENARIOS},
        "minimum_recordings": 2, "minimum_cameras": 1, "minimum_days": 1, "minimum_normal_camera_hours": 0.001,
    }
    plan = {"schema_version": "1.0", "plan_id": "synthetic-plan", "declared_before_test": True, "owner_reference": "synthetic-owner", "branches": [branch]}
    return manifest, predictions, plan


def test_one_branch_cannot_satisfy_other_five_branches(tmp_path):
    manifest, predictions, plan = dataset()
    plan["branches"] = [{**copy.deepcopy(plan["branches"][0]), "site_id": f"site-{i}"} for i in range(1, 7)]
    report = evaluator.evaluate(manifest, predictions, tmp_path, coverage_plan=plan)
    coverage = report["branch_coverage"]
    assert coverage["status"] == "INCOMPLETE"
    assert coverage["branches"][0]["status"] == "SUFFICIENT_FOR_REVIEW"
    assert [b["status"] for b in coverage["branches"][1:]] == ["NOT_RUN"] * 5
    assert all(b["site_acceptance_established"] is False for b in coverage["branches"])
    assert coverage["plan_sha256"] == evaluator.digest(plan)
    assert coverage["declaration_independently_verified"] is False


def test_errors_do_not_count_as_successful_scenario_or_class_coverage(tmp_path):
    manifest, predictions, plan = dataset()
    predictions["results"][4].update(status="ERROR", action="UNCLEAR", alarm_eligible=False)
    branch = evaluator.evaluate(manifest, predictions, tmp_path, coverage_plan=plan)["branch_coverage"]["branches"][0]
    assert branch["status"] == "INSUFFICIENT"
    assert branch["successful_class_support"]["POSSIBLE_CONCEALMENT"] == 0
    assert branch["successful_scenario_support"]["STAGED_CONCEALMENT"] == 0
    assert {m["check"] for m in branch["missing"]} >= {"class:POSSIBLE_CONCEALMENT", "scenario:STAGED_CONCEALMENT", "inference_errors"}


def test_correct_action_label_does_not_inflate_alarm_recall(tmp_path):
    manifest, predictions, plan = dataset()
    predictions["results"][4]["alarm_eligible"] = False
    report = evaluator.evaluate(manifest, predictions, tmp_path, coverage_plan=plan)
    assert report["per_class"]["POSSIBLE_CONCEALMENT"]["recall"] == 1
    assert report["alarm_eligibility"]["concealment_eligibility_recall"] == 0
    assert report["alarm_eligibility"]["eligible_precision_on_resolved_labels"] is None
    assert report["branch_coverage"]["branches"][0]["alarm_eligibility"]["concealment_eligibility_recall"] == 0


def test_unclear_truth_alarm_is_reported_separately_from_resolved_precision(tmp_path):
    manifest, predictions, _ = dataset()
    predictions["results"][7].update(action="POSSIBLE_CONCEALMENT", alarm_eligible=True)
    predictions["results"][0].update(action="POSSIBLE_CONCEALMENT", alarm_eligible=True)
    alarm = evaluator.evaluate(manifest, predictions, tmp_path)["alarm_eligibility"]
    assert alarm["eligible_on_unclear_labels"] == 1
    assert alarm["eligible_on_resolved_normal_labels"] == 1
    assert alarm["eligible_precision_on_resolved_labels"] == 0.5
    assert alarm["confidence_interval"] is None
    assert alarm["physical_alarm_delivery_tested"] is False


@pytest.mark.parametrize("site", [None, "outside-plan"])
def test_missing_or_unplanned_site_does_not_enter_planned_branch_coverage(tmp_path, site):
    manifest, predictions, plan = dataset()
    for session in manifest["sessions"]:
        if site is None:
            session.pop("site_id")
        else:
            session["site_id"] = site
    coverage = evaluator.evaluate(manifest, predictions, tmp_path, coverage_plan=plan)["branch_coverage"]
    assert coverage["branches"][0]["status"] == "NOT_RUN"
    assert coverage["branches"][0]["measured_covered_normal_camera_hours"] == 0
    assert coverage["unscoped_windows_excluded"] == (10 if site is None else 0)
    assert coverage["unplanned_sites_excluded"] == ([] if site is None else ["outside-plan"])


@pytest.mark.parametrize("fault", ["undeclared", "duplicate_site", "zero_class", "missing_class", "unknown_scenario", "zero_hours", "bool_count"])
def test_invalid_plan_fails_before_model_execution(fault):
    _, _, plan = dataset()
    branch = plan["branches"][0]
    if fault == "undeclared":
        plan["declared_before_test"] = False
    elif fault == "duplicate_site":
        plan["branches"].append(copy.deepcopy(branch))
    elif fault == "zero_class":
        branch["minimum_successful_windows_per_class"]["UNCLEAR"] = 0
    elif fault == "missing_class":
        del branch["minimum_successful_windows_per_class"]["UNCLEAR"]
    elif fault == "unknown_scenario":
        branch["minimum_successful_windows_per_scenario"]["IDENTIFY_CRIMINAL"] = 1
    elif fault == "zero_hours":
        branch["minimum_normal_camera_hours"] = 0
    else:
        branch["minimum_recordings"] = True
    with pytest.raises(evaluator.EvaluationError):
        evaluator.validate_coverage_plan(plan)


def test_cli_coverage_with_forged_local_provenance_remains_prediction_file_mode(tmp_path):
    manifest, predictions, plan = dataset()
    predictions["provenance"]["origin"] = "LOCAL_PROVIDER_EXECUTION"
    for name, data in (("manifest", manifest), ("predictions", predictions), ("plan", plan)):
        (tmp_path / (name + ".json")).write_text(json.dumps(data))
    args = ["--manifest", str(tmp_path / "manifest.json"), "--predictions", str(tmp_path / "predictions.json"), "--coverage-plan", str(tmp_path / "plan.json"), "--output", str(tmp_path / "report.json")]
    assert evaluator.main(args) == 0
    result = json.loads((tmp_path / "report.json").read_text())
    assert result["execution_mode"] == "PREDICTION_FILE_METRICS_ONLY"
    assert result["site_acceptance_established"] is False
    original = (tmp_path / "plan.json").read_bytes()
    args[-1] = str(tmp_path / "plan.json")
    assert evaluator.main(args) == 2
    assert (tmp_path / "plan.json").read_bytes() == original


def test_minimum_sample_counts_do_not_gate_or_claim_accuracy(tmp_path):
    manifest, predictions, plan = dataset()
    for prediction in predictions["results"]:
        prediction.update(action="UNCLEAR", alarm_eligible=False)
    report = evaluator.evaluate(manifest, predictions, tmp_path, coverage_plan=plan)
    assert report["branch_coverage"]["status"] == "SUFFICIENT_FOR_REVIEW"
    assert report["alarm_eligibility"]["concealment_eligibility_recall"] == 0
    assert report["site_acceptance_established"] is False


def test_branch_metrics_include_errors_and_normal_alarm_burden_without_pooling(tmp_path):
    manifest, predictions, plan = dataset()
    predictions["results"][-1].update(
        action="POSSIBLE_CONCEALMENT", alarm_eligible=True, wall_ms=10
    )
    predictions["results"][1].update(action="UNCLEAR", status="ERROR", alarm_eligible=False)
    branch = evaluator.evaluate(manifest, predictions, tmp_path, coverage_plan=plan)["branch_coverage"]["branches"][0]
    assert branch["per_class"]["TAKE_PRODUCT"]["recall"] == 0
    assert branch["simulated_false_alarm_episodes"] == 1
    assert branch["simulated_false_alarms_per_camera_hour"] == 360
    assert branch["abstention_rate"] == 0.3


def test_report_write_is_private_on_posix_and_does_not_follow_destination_symlink(tmp_path):
    original = tmp_path / "original.json"
    original.write_text("keep original")
    report = tmp_path / "report.json"
    try:
        report.symlink_to(original)
    except OSError:
        pytest.skip("This account cannot create symlinks")
    evaluator.write_private_report(report, {"result": "synthetic"})
    assert original.read_text() == "keep original"
    assert not report.is_symlink()
    assert json.loads(report.read_text()) == {"result": "synthetic"}
    if os.name == "posix":
        assert report.stat().st_mode & 0o777 == 0o600
