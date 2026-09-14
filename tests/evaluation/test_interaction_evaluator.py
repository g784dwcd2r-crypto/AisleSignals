"""Synthetic predictions test metric arithmetic, never pharmacy model accuracy."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate-interactions.py"
SPEC = importlib.util.spec_from_file_location("interaction_evaluator", SCRIPT)
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def session(sid="s1", split="test", kind="STAGED", duration=6):
    return {
        "id": sid, "split": split, "camera_id": f"camera-{sid}", "day_id": f"day-{sid}",
        "recording_sha256": evaluator.digest(sid), "source_kind": kind,
        "duration_seconds": duration, "normal_duration_measured": kind == "NORMAL_OBSERVATION",
    }


def window(wid="w1", sid="s1", label="TAKE_PRODUCT", start=0, end=6):
    return {
        "id": wid, "session_id": sid, "start_seconds": start, "end_seconds": end,
        "label": label, "person_ids": [f"person-{sid}"],
        "frames": [{"path": f"frames/{wid}-{index}.jpg", "offset_seconds": stamp}
                   for index, stamp in enumerate((start, (start + end) / 2, end))],
    }


def manifest(sessions=None, windows=None):
    return {
        "schema_version": "1.0", "dataset_id": "synthetic-metrics-only",
        "split_policy": "DISJOINT_CAMERA_DAY_PERSON",
        "authorization": {"confirmed": True, "reference": "synthetic-unit-test-no-footage"},
        "sessions": sessions or [session()], "windows": windows or [window()],
    }


def predictions(windows, changes=None):
    return {
        "provenance": {"origin": "SYNTHETIC_METRICS_TEST", "model": "synthetic-not-a-model", "prompt_version": "none"},
        "results": [{"window_id": w["id"], "action": w["label"], "alarm_eligible": w["label"] == "POSSIBLE_CONCEALMENT",
                     "inference_ms": 10, "wall_ms": 10, **(changes or {}).get(w["id"], {})} for w in windows],
    }


def test_confusion_precision_recall_abstention_errors_and_zero_support(tmp_path):
    windows = [window("take"), window("return", label="RETURN_PRODUCT"), window("conceal", label="POSSIBLE_CONCEALMENT")]
    # Different windows in the same recording must have different intervals.
    for i, w in enumerate(windows):
        w["start_seconds"] = i * 6
        w["end_seconds"] = (i + 1) * 6
        for frame in w["frames"]:
            frame["offset_seconds"] += i * 6
    data = manifest([session(duration=18)], windows)
    guesses = predictions(windows, {
        "return": {"action": "TAKE_PRODUCT"},
        "conceal": {"action": "UNCLEAR", "alarm_eligible": False, "status": "ERROR", "inference_ms": 0},
    })
    report = evaluator.evaluate(data, guesses, tmp_path)
    assert report["per_class"]["TAKE_PRODUCT"]["precision"] == 0.5
    assert report["per_class"]["TAKE_PRODUCT"]["recall"] == 1
    assert report["per_class"]["RETURN_PRODUCT"]["recall"] == 0
    assert report["per_class"]["PLACE_IN_BASKET"]["recall"] is None
    assert report["per_class"]["POSSIBLE_CONCEALMENT"]["precision"] is None
    assert report["confusion_matrix_actual_rows_predicted_columns"]["RETURN_PRODUCT"]["TAKE_PRODUCT"] == 1
    assert report["abstention"]["rate"] == 1 / 3
    assert report["inference_errors"] == 1
    assert report["inference_timing"]["count"] == 2
    assert report["site_acceptance_established"] is False
    assert "accuracy" not in report


def normal_case():
    windows = [window(f"w{i}", label="NORMAL_SHOPPING", start=i * 10, end=(i + 1) * 10) for i in range(6)]
    data = manifest([session(kind="NORMAL_OBSERVATION", duration=60)], windows)
    guesses = predictions(windows, {f"w{i}": {"action": "POSSIBLE_CONCEALMENT", "alarm_eligible": True} for i in (0, 1, 3)})
    return data, guesses


def test_false_alarm_episodes_use_measured_continuous_duration_and_cooldown(tmp_path):
    data, guesses = normal_case()
    report = evaluator.evaluate(data, guesses, tmp_path)
    normal = report["normal_observation"]
    assert normal["measured_covered_camera_hours"] == 1 / 60
    assert normal["simulated_false_alarm_episodes"] == 2
    assert normal["simulated_false_alarms_per_camera_hour"] == 120
    assert normal["physical_alarm_delivery_tested"] is False
    delivery = report["simulated_alarm_delivery"]
    assert delivery["eligible_windows"] == 3
    assert delivery["simulated_new_sound_requests"] == 2
    assert delivery["cooldown_suppressed_windows"] == 1
    assert delivery["physical_alarm_delivery_tested"] is False


def test_delivery_report_separates_missed_signal_from_cooldown_suppression(tmp_path):
    windows = [
        window("c1", label="POSSIBLE_CONCEALMENT", start=0, end=6),
        window("c2", label="POSSIBLE_CONCEALMENT", start=6, end=12),
        window("c3", label="POSSIBLE_CONCEALMENT", start=12, end=18),
    ]
    data = manifest([session(duration=18)], windows)
    guesses = predictions(windows, {"c2": {"alarm_eligible": False}})
    delivery = evaluator.evaluate(data, guesses, tmp_path)["simulated_alarm_delivery"]
    assert delivery["eligible_windows"] == 2
    assert delivery["simulated_new_sound_requests"] == 1
    assert delivery["cooldown_suppressed_windows"] == 1
    assert delivery["concealment_windows_without_eligible_signal"] == 1
    assert delivery["concealment_windows_without_new_sound"] == 2


def test_delivery_applies_processing_freshness_gate_and_does_not_guess_missing_timing(tmp_path):
    windows = [
        window("fresh", label="POSSIBLE_CONCEALMENT", start=0, end=6),
        window("stale", label="POSSIBLE_CONCEALMENT", start=36, end=42),
        window("unknown", label="POSSIBLE_CONCEALMENT", start=72, end=78),
    ]
    data = manifest([session(duration=78)], windows)
    guesses = predictions(windows, {"stale": {"wall_ms": 15001}})
    del guesses["results"][2]["wall_ms"]
    delivery = evaluator.evaluate(data, guesses, tmp_path)["simulated_alarm_delivery"]
    assert delivery["eligible_windows"] == 3
    assert delivery["fresh_eligible_windows"] == 1
    assert delivery["stale_eligible_windows"] == 1
    assert delivery["freshness_unassessed_eligible_windows"] == 1
    assert delivery["simulated_new_sound_requests"] == 1
    assert delivery["simulation_complete"] is False


def test_normal_observation_excludes_unassessed_alarm_freshness(tmp_path):
    data, guesses = normal_case()
    del guesses["results"][0]["wall_ms"]
    normal = evaluator.evaluate(data, guesses, tmp_path)["normal_observation"]
    assert normal["simulated_false_alarms_per_camera_hour"] is None
    assert normal["measured_covered_camera_hours"] == 0
    assert "Wall timing missing" in normal["excluded_sessions"][0]["reason"]


def test_optional_rule_strength_is_counted_and_strictly_validated(tmp_path):
    data = manifest()
    guesses = predictions(data["windows"])
    guesses["results"][0]["evidence_strength"] = "STRONG_RULE_MATCH"
    report = evaluator.evaluate(data, guesses, tmp_path)
    assert report["routing_rule_strength"]["STRONG_RULE_MATCH"] == 1
    guesses["results"][0]["evidence_strength"] = "92_PERCENT_CONFIDENT"
    with pytest.raises(evaluator.EvaluationError, match="Unknown evidence rule strength"):
        evaluator.evaluate(data, guesses, tmp_path)


@pytest.mark.parametrize("issue", ["unmeasured", "coverage_gap", "model_error", "staged"])
def test_does_not_invent_false_alarms_per_hour(tmp_path, issue):
    data, guesses = normal_case()
    if issue == "unmeasured":
        data["sessions"][0]["normal_duration_measured"] = False
    elif issue == "coverage_gap":
        data["windows"].pop(1)
        guesses["results"].pop(1)
    elif issue == "model_error":
        guesses["results"][0].update(action="UNCLEAR", alarm_eligible=False, status="ERROR")
    else:
        data["sessions"][0].update(source_kind="STAGED", normal_duration_measured=False)
    normal = evaluator.evaluate(data, guesses, tmp_path)["normal_observation"]
    assert normal["simulated_false_alarms_per_camera_hour"] is None
    assert normal["measured_covered_camera_hours"] == 0


@pytest.mark.parametrize("group", ["camera_id", "day_id", "person_ids", "frame_path"])
def test_rejects_training_test_group_leakage(tmp_path, group):
    data = manifest([session("a", "train"), session("b", "test")], [window("a", "a"), window("b", "b")])
    if group in {"camera_id", "day_id"}:
        data["sessions"][1][group] = data["sessions"][0][group]
    elif group == "person_ids":
        data["windows"][1][group] = data["windows"][0][group]
    else:
        data["windows"][1]["frames"][0]["path"] = data["windows"][0]["frames"][0]["path"]
    with pytest.raises(evaluator.EvaluationError, match="Split leakage"):
        evaluator.validate_manifest(data, tmp_path)


def test_rejects_duplicate_recording_and_duplicate_window(tmp_path):
    data = manifest([session("a"), session("b")], [window("a", "a"), window("b", "b")])
    data["sessions"][1]["recording_sha256"] = data["sessions"][0]["recording_sha256"]
    with pytest.raises(evaluator.EvaluationError, match="Duplicate source recording"):
        evaluator.validate_manifest(data, tmp_path)
    data = manifest(windows=[window("a"), window("b")])
    with pytest.raises(evaluator.EvaluationError, match="Duplicate source window"):
        evaluator.validate_manifest(data, tmp_path)


@pytest.mark.parametrize("path", ["https://example.com/frame.jpg", "../outside.jpg", "/tmp/outside.jpg", "frames\\outside.jpg"])
def test_frames_cannot_use_remote_or_escaping_paths(tmp_path, path):
    data = manifest()
    data["windows"][0]["frames"][0]["path"] = path
    with pytest.raises(evaluator.EvaluationError):
        evaluator.validate_manifest(data, tmp_path)


def test_symlink_cannot_escape_manifest_directory(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    (root / "frames").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(evaluator.EvaluationError, match="escapes"):
        evaluator.validate_manifest(manifest(), root)


@pytest.mark.parametrize("change", ["unauthorised", "unordered", "nan", "bad_label", "bad_person", "unsampled_tail"])
def test_invalid_manifest_fails_closed(tmp_path, change):
    data = manifest()
    if change == "unauthorised":
        data["authorization"]["confirmed"] = False
    elif change == "unordered":
        data["windows"][0]["frames"][1]["offset_seconds"] = 0
    elif change == "nan":
        data["windows"][0]["end_seconds"] = float("nan")
    elif change == "bad_person":
        data["windows"][0]["person_ids"] = [{}]
    elif change == "unsampled_tail":
        data["windows"][0]["frames"][-1]["offset_seconds"] = 5
    else:
        data["windows"][0]["label"] = "THIEF"
    with pytest.raises(evaluator.EvaluationError):
        evaluator.validate_manifest(data, tmp_path)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "invalid_alarm", "error_alarm"])
def test_invalid_predictions_cannot_improve_metrics(tmp_path, change):
    data = manifest()
    guesses = predictions(data["windows"])
    if change == "missing":
        guesses["results"] = []
    elif change == "duplicate":
        guesses["results"].append(copy.deepcopy(guesses["results"][0]))
    elif change == "unknown":
        guesses["results"][0]["action"] = "THEFT"
    elif change == "invalid_alarm":
        guesses["results"][0]["alarm_eligible"] = True
    else:
        guesses["results"][0].update(status="ERROR", action="POSSIBLE_CONCEALMENT", alarm_eligible=True)
    with pytest.raises(evaluator.EvaluationError):
        evaluator.evaluate(data, guesses, tmp_path)


def write_metric_only_frame_stubs(root, data, duplicate=False):
    """JPEG envelope stubs for the injected fake provider, not real video/model tests."""
    for w in data["windows"]:
        for frame in w["frames"]:
            target = root / frame["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            content = b"stub" if duplicate else frame["path"].encode()
            target.write_bytes(b"\xff\xd8\xff" + content + b"\xff\xd9")


def test_local_runner_sends_ordered_bytes_serially_and_keeps_hash_provenance(tmp_path):
    data = manifest()
    write_metric_only_frame_stubs(tmp_path, data)

    class FakeProvider:
        model = "synthetic-not-real-inference"
        model_digest = "test-digest"

        def status(self):
            return {"ready": True}

        def analyze(self, frames):
            assert [stamp for stamp, _ in frames] == [0, 3, 6]
            assert all(isinstance(content, bytes) for _, content in frames)
            return {"action": "TAKE_PRODUCT", "alarm_eligible": False, "inference_ms": 20,
                    "model": self.model, "prompt_version": "test-v1", "prompt_sha256": "test-prompt-digest"}

    result = evaluator.run_local(data, tmp_path, "test", FakeProvider())
    assert result["results"][0]["action"] == "TAKE_PRODUCT"
    assert result["provenance"]["model_digest"] == "test-digest"
    assert result["provenance"]["prompt_sha256"] == "test-prompt-digest"
    assert len(result["provenance"]["frame_sha256_by_window"]["w1"]) == 3
    assert "frames/w1" not in json.dumps(result)


def test_cross_split_duplicate_frame_content_is_rejected_even_with_new_names(tmp_path):
    data = manifest([session("a", "train"), session("b", "test")], [window("a", "a"), window("b", "b")])
    write_metric_only_frame_stubs(tmp_path, data, duplicate=True)
    with pytest.raises(evaluator.EvaluationError, match="identical frame content"):
        evaluator.load_frames(data, tmp_path, "test")


def test_runtime_error_abstains_without_persisting_provider_text(tmp_path):
    data = manifest()
    write_metric_only_frame_stubs(tmp_path, data)

    class FakeProvider:
        def analyze(self, frames):
            raise RuntimeError("private image or patient text must never leak")

    result = evaluator.run_local(data, tmp_path, "test", FakeProvider())
    assert result["results"][0]["status"] == "ERROR"
    assert result["results"][0]["action"] == "UNCLEAR"
    assert result["results"][0]["alarm_eligible"] is False
    assert "patient" not in json.dumps(result)


def test_cli_prediction_mode_records_that_no_model_was_executed(tmp_path):
    data = manifest()
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data))
    guesses = tmp_path / "predictions.json"
    guesses.write_text(json.dumps(predictions(data["windows"])))
    output = tmp_path / "report.json"
    assert evaluator.main(["--manifest", str(source), "--predictions", str(guesses), "--output", str(output)]) == 0
    result = json.loads(output.read_text())
    assert result["execution_mode"] == "PREDICTION_FILE_METRICS_ONLY"
    assert result["site_acceptance_established"] is False
    assert result["manifest_sha256"] == evaluator.digest(data)
    assert not (tmp_path / "frames").exists()


def test_cli_never_overwrites_the_labelled_source_manifest(tmp_path):
    data = manifest()
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data))
    original = source.read_bytes()
    guesses = tmp_path / "predictions.json"
    guesses.write_text(json.dumps(predictions(data["windows"])))
    assert evaluator.main(["--manifest", str(source), "--predictions", str(guesses), "--output", str(source)]) == 2
    assert source.read_bytes() == original
