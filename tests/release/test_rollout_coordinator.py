"""Synthetic records verify coordination; no client acceptance is asserted."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath

import pytest

from scripts import coordinate_rollout as coordinator

NOW = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
STAMP = (NOW - timedelta(hours=1)).isoformat()
ROOT = Path(__file__).resolve().parents[2]


def manifest():
    return json.loads((ROOT / "deployment/six-branch-rollout.example.json").read_text())


def assign(slot):
    slot["context"] = {"site_id": "synthetic-site", "device_id": "synthetic-device", "platform": "Windows", "source_version": "synthetic-source-v1", "app_revision": "a" * 40, "model_sha256": "b" * 64, "prompt_version": "synthetic-prompt-v1"}


def file_reference(root, name, content):
    path = root / name
    path.write_bytes(content)
    return {"path": name, "sha256": hashlib.sha256(content).hexdigest()}


def attach(root, slot, kind, payload, **changes):
    envelope = {"schema_version": 1, "kind": kind, "context": deepcopy(slot["context"]), "binding": "OPERATOR_SUPPLIED_CONTEXT", "recorded_at": STAMP, "payload": payload}
    envelope.update(changes)
    slot["evidence"][kind] = file_reference(root, f"{slot['slot_id']}-{kind}.json", json.dumps(envelope).encode())


def complete_slot(root):
    data = manifest()
    slot = data["slots"][0]
    assign(slot)
    preflight = {"scope": "local_laptop_readiness", "generated_at": STAMP, "launch_status": "PASS", "checks": [{"id": name, "status": "PASS"} for name in coordinator.PREFLIGHT_CHECKS]}
    for check in preflight["checks"]:
        if check["id"] == "platform_inventory":
            check["os"] = "Windows"
        if check["id"] == "model_weight_1":
            check.update(actual_sha256="b" * 64, expected_sha256="b" * 64)
    log = file_reference(root, "synthetic-test-log.txt", b"Synthetic check fixture, not an actual rollout run.")
    artifact = file_reference(root, "synthetic-artifact.bin", b"Synthetic executable placeholder.")
    software = {"app_revision": "a" * 40, "checks": [{"id": name, "status": "PASS", "passed": 1, "failed": 0, "log": log} for name in coordinator.SOFTWARE_CHECKS], "artifacts": [{"platform": "Windows", "app_revision": "a" * 40, "file": artifact}]}
    plan = {"declared_before_test": True, "owner_reference": "synthetic-reviewer", "declared_at": (NOW - timedelta(hours=2)).isoformat(), "coverage_plan_sha256": "c" * 64, "bounds": {"max_false_alarms_per_camera_hour": 1.0, "max_abstention_rate": .2, "min_concealment_recall": .8, "min_concealment_precision": .9, "max_p95_wall_ms": 10000}}
    evaluation = {"report_version": "interaction-evaluator-v2", "created_at": STAMP, "split": "test", "execution_mode": "LOCAL_PROVIDER_EXECUTION", "split_leakage_check": "DISJOINT_CAMERA_DAY_PERSON_AND_FRAME_PATHS", "all_three_splits_present": True, "model_provenance": {"origin": "LOCAL_PROVIDER_EXECUTION", "frame_content_split_check": "PASSED", "model_digest": "b" * 64, "prompt_version": "synthetic-prompt-v1"}, "window_count": 100, "inference_errors": 0, "wall_timing": {"p95_ms": 2000}, "branch_coverage": {"plan_sha256": "c" * 64, "unscoped_windows_excluded": 0, "unplanned_sites_excluded": [], "branches": [{"site_id": "synthetic-site", "status": "SUFFICIENT_FOR_REVIEW", "window_count": 100, "inference_errors": 0, "missing": [], "simulated_false_alarms_per_camera_hour": .1, "abstention_rate": .05, "alarm_eligibility": {"concealment_eligibility_recall": .95, "eligible_precision_on_resolved_labels": .99}}]}}
    onsite = {"method": "HUMAN_ATTESTATION", "reviewer_reference": "synthetic-reviewer", "observed_at": STAMP, "checks": [{"id": name, "status": "PASS"} for name in coordinator.ONSITE_CHECKS]}
    payloads = dict(preflight=preflight, software=software, acceptance_plan=plan, evaluation=evaluation, onsite=onsite)
    for kind, payload in payloads.items():
        attach(root, slot, kind, payload)
    signoff = {"method": "HUMAN_ATTESTATION", "reviewer_reference": "synthetic-reviewer", "signed_at": NOW.isoformat(), "decision": "APPROVE_SUPERVISED_ROLLOUT", "held_out_performance_reviewed": True, "coverage_and_false_alert_workload_reviewed": True, "reviewed_sha256": {name: ref["sha256"] for name, ref in slot["evidence"].items() if name != "signoff"}}
    attach(root, slot, "signoff", signoff, recorded_at=NOW.isoformat())
    return data, payloads


def report_slot(data, root):
    return coordinator.build_report(data, root, now=NOW)["slots"][0]


def test_default_has_six_unassigned_unaccepted_slots(tmp_path):
    report = coordinator.build_report(manifest(), tmp_path, now=NOW)
    assert report["accepted_slots"] == 0
    assert len(report["slots"]) == 6
    assert {slot["status"] for slot in report["slots"]} == {"NOT_RUN"}
    assert all("onsite:physical_audio" in slot["missing_or_failed"] and "preflight:platform_inventory" in slot["missing_or_failed"] for slot in report["slots"])
    assert "Unassigned" in coordinator.markdown(report)


def test_exact_evidence_and_human_signoff_are_distinct(tmp_path):
    data, _ = complete_slot(tmp_path)
    report = coordinator.build_report(data, tmp_path, now=NOW)
    first = report["slots"][0]
    assert first["status"] == "PASS"
    assert report["accepted_slots"] == 1
    checks = {item["id"]: item for item in first["checks"]}
    assert checks["onsite:physical_audio"]["origin"] == "HUMAN_ATTESTATION_IMPORT"
    assert checks["software:python_regression"]["origin"] == "AUTOMATED_REPORT_IMPORT"
    assert "not authenticate report issuers" in " ".join(report["limits"])


@pytest.mark.parametrize("field,value", [("site_id", "other-site"), ("device_id", "other-laptop"), ("source_version", "other-view"), ("app_revision", "d" * 40), ("model_sha256", "d" * 64), ("platform", "Darwin"), ("prompt_version", "other-prompt")])
def test_context_mismatch_never_accepts_a_developer_or_other_source(tmp_path, field, value):
    data, _ = complete_slot(tmp_path)
    data["slots"][0]["context"][field] = value
    first = report_slot(data, tmp_path)
    assert first["status"] == "FAIL"
    assert "preflight_evidence" in first["missing_or_failed"]


def test_hash_mismatch_and_artifact_tampering_are_rejected(tmp_path):
    data, _ = complete_slot(tmp_path)
    (tmp_path / "synthetic-artifact.bin").write_bytes(b"changed bytes")
    first = report_slot(data, tmp_path)
    assert "software_content" in first["missing_or_failed"]
    data["slots"][0]["evidence"]["preflight"]["sha256"] = "0" * 64
    assert "preflight_evidence" in report_slot(data, tmp_path)["missing_or_failed"]


def test_missing_signoff_does_not_promote_sufficient_coverage(tmp_path):
    data, _ = complete_slot(tmp_path)
    data["slots"][0]["evidence"]["signoff"] = None
    first = report_slot(data, tmp_path)
    assert first["decision"] == "NOT_READY"
    assert first["status"] == "NOT_RUN"
    assert first["missing_or_failed"] == ["signoff_evidence", "branch_signoff"]


def test_coverage_sufficiency_does_not_override_failed_performance(tmp_path):
    data, payloads = complete_slot(tmp_path)
    payloads["evaluation"]["branch_coverage"]["branches"][0]["alarm_eligibility"]["concealment_eligibility_recall"] = .1
    attach(tmp_path, data["slots"][0], "evaluation", payloads["evaluation"])
    first = report_slot(data, tmp_path)
    assert "performance:min_concealment_recall" in first["missing_or_failed"]
    assert "signoff_content" in first["missing_or_failed"]


@pytest.mark.parametrize("kind", ["preflight", "onsite", "evaluation", "signoff"])
def test_stale_envelopes_fail(tmp_path, kind):
    data, _ = complete_slot(tmp_path)
    slot = data["slots"][0]
    path = tmp_path / slot["evidence"][kind]["path"]
    envelope = json.loads(path.read_text())
    envelope["recorded_at"] = (NOW - timedelta(days=8)).isoformat()
    slot["evidence"][kind] = file_reference(tmp_path, path.name, json.dumps(envelope).encode())
    assert kind + "_evidence" in report_slot(data, tmp_path)["missing_or_failed"]


def test_plan_declared_after_evaluation_is_rejected(tmp_path):
    data, payloads = complete_slot(tmp_path)
    payloads["acceptance_plan"]["declared_at"] = NOW.isoformat()
    attach(tmp_path, data["slots"][0], "acceptance_plan", payloads["acceptance_plan"])
    assert "evaluation_content" in report_slot(data, tmp_path)["missing_or_failed"]


def test_fixture_predictions_and_missing_metrics_cannot_qualify(tmp_path):
    data, payloads = complete_slot(tmp_path)
    payloads["evaluation"]["execution_mode"] = "PREDICTION_FILE_METRICS_ONLY"
    attach(tmp_path, data["slots"][0], "evaluation", payloads["evaluation"])
    assert "evaluation_content" in report_slot(data, tmp_path)["missing_or_failed"]
    payloads["evaluation"]["execution_mode"] = "LOCAL_PROVIDER_EXECUTION"
    payloads["evaluation"]["wall_timing"]["p95_ms"] = None
    attach(tmp_path, data["slots"][0], "evaluation", payloads["evaluation"])
    first = report_slot(data, tmp_path)
    check = next(item for item in first["checks"] if item["id"] == "performance:max_p95_wall_ms")
    assert check["status"] == "NOT_RUN"


def test_missing_onsite_check_remains_not_run(tmp_path):
    data, payloads = complete_slot(tmp_path)
    payloads["onsite"]["checks"] = [item for item in payloads["onsite"]["checks"] if item["id"] != "physical_audio"]
    attach(tmp_path, data["slots"][0], "onsite", payloads["onsite"])
    first = report_slot(data, tmp_path)
    assert next(item for item in first["checks"] if item["id"] == "onsite:physical_audio")["status"] == "NOT_RUN"


def test_multiple_branch_global_timing_cannot_hide_branch_latency(tmp_path):
    data, payloads = complete_slot(tmp_path)
    payloads["evaluation"]["branch_coverage"]["branches"].append({"site_id": "another-branch"})
    attach(tmp_path, data["slots"][0], "evaluation", payloads["evaluation"])
    assert "evaluation_content" in report_slot(data, tmp_path)["missing_or_failed"]


def test_duplicate_assignments_are_rejected(tmp_path):
    data = manifest()
    assign(data["slots"][0])
    assign(data["slots"][1])
    with pytest.raises(coordinator.CoordinationError, match="multiple rollout slots"):
        coordinator.build_report(data, tmp_path, now=NOW)


@pytest.mark.parametrize("path", ["../outside.json", "https://example.test/private", "/absolute.json", "folder\\private.json", "//server/share/file.json", "C:/outside.json", "C:outside.json", "nested/../../outside.json"])
def test_remote_and_escape_references_are_rejected(tmp_path, path):
    with pytest.raises(coordinator.CoordinationError):
        coordinator.reference_path(tmp_path, {"path": path, "sha256": "0" * 64})


def test_windows_rooted_reference_without_drive_cannot_escape_intake(monkeypatch):
    # Reproduce Windows path semantics on the development host too. A rooted
    # path is not is_absolute() until it also has a drive, but joining escapes.
    root = PureWindowsPath("C:/private/intake")
    assert not PureWindowsPath("/outside.json").is_absolute()
    assert root / PureWindowsPath("/outside.json") == PureWindowsPath("C:/outside.json")
    monkeypatch.setattr(coordinator, "Path", PureWindowsPath)
    monkeypatch.setattr(coordinator, "safe_path", lambda _path: True)
    with pytest.raises(coordinator.CoordinationError):
        coordinator.reference_path(root, {"path": "/outside.json", "sha256": "0" * 64})
    assert coordinator.reference_path(root, {"path": "records/branch.json", "sha256": "0" * 64}) == PureWindowsPath("C:/private/intake/records/branch.json")


def test_report_bytes_and_returned_digest_survive_windows_newline_rules(tmp_path, monkeypatch):
    real_fdopen = coordinator.os.fdopen

    def windows_fdopen(fd, mode, *args, **kwargs):
        if "b" not in mode:
            kwargs["newline"] = "\r\n"
        return real_fdopen(fd, mode, *args, **kwargs)

    monkeypatch.setattr(coordinator.os, "fdopen", windows_fdopen)
    path = tmp_path / "portable.json"
    content = '{\n  "label": "Pharmacy – test"\n}\n'
    returned_digest = coordinator.write_new(path, content)
    assert path.read_bytes() == content.encode("utf-8")
    assert returned_digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_symlink_reference_and_existing_output_are_preserved(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{}")
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip("Symlink creation unavailable on this platform")
    with pytest.raises(coordinator.CoordinationError):
        coordinator.verify_file(tmp_path, {"path": "linked.json", "sha256": hashlib.sha256(b"{}").hexdigest()})
    with pytest.raises(coordinator.CoordinationError):
        coordinator.write_new(source, "overwrite")
    assert source.read_text() == "{}"


def test_reports_do_not_echo_private_fields_from_imported_payload(tmp_path):
    data, payloads = complete_slot(tmp_path)
    payloads["preflight"]["private_hostname"] = "PRIVATE-HOST-SHOULD-NOT-LEAK"
    payloads["preflight"]["checks"][0]["detail"] = "PRIVATE-OPERATOR-SHOULD-NOT-LEAK"
    attach(tmp_path, data["slots"][0], "preflight", payloads["preflight"])
    output = json.dumps(coordinator.build_report(data, tmp_path, now=NOW))
    assert "PRIVATE-" not in output
    assert str(tmp_path) not in output


def test_cli_generates_json_and_markdown_without_inventing_acceptance(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()))
    json_path, markdown_path = tmp_path / "report.json", tmp_path / "report.md"
    assert coordinator.main(["report", "--manifest", str(path), "--json", str(json_path), "--markdown", str(markdown_path)]) == 2
    assert json.loads(json_path.read_text())["accepted_slots"] == 0
    assert "NOT_RUN" in markdown_path.read_text()
    assert "accepted_slots" in capsys.readouterr().out


def test_intake_keeps_original_report_timestamp_and_explicit_operator_binding(tmp_path, capsys):
    data = manifest()
    assign(data["slots"][0])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    now = datetime.now(timezone.utc).isoformat()
    raw = tmp_path / "preflight.json"
    raw.write_text(json.dumps({"generated_at": now, "scope": "local_laptop_readiness", "checks": []}))
    output = tmp_path / "bound.json"
    assert coordinator.main(["intake", "--manifest", str(path), "--slot", "slot-01", "--kind", "preflight", "--input", str(raw), "--output", str(output)]) == 0
    imported = json.loads(output.read_text())
    assert imported["recorded_at"] == now
    assert imported["binding"] == "OPERATOR_SUPPLIED_CONTEXT"
    assert json.loads(capsys.readouterr().out)["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_relabelled_developer_platform_report_fails_content_check(tmp_path):
    data, payloads = complete_slot(tmp_path)
    payloads['preflight']['checks'][0]['os'] = 'Darwin'
    attach(tmp_path, data['slots'][0], 'preflight', payloads['preflight'])
    assert 'preflight_content' in report_slot(data, tmp_path)['missing_or_failed']


def test_new_envelope_does_not_refresh_stale_underlying_observation(tmp_path):
    data, payloads = complete_slot(tmp_path)
    payloads['onsite']['observed_at'] = (NOW - timedelta(days=10)).isoformat()
    attach(tmp_path, data['slots'][0], 'onsite', payloads['onsite'])
    assert 'onsite_content' in report_slot(data, tmp_path)['missing_or_failed']


def test_signoff_cannot_predate_its_reviewed_evidence(tmp_path):
    data, _ = complete_slot(tmp_path)
    slot = data['slots'][0]
    path = tmp_path / slot['evidence']['signoff']['path']
    envelope = json.loads(path.read_text())
    envelope['payload']['signed_at'] = (NOW - timedelta(hours=2)).isoformat()
    slot['evidence']['signoff'] = file_reference(tmp_path, path.name, json.dumps(envelope).encode())
    assert 'signoff_content' in report_slot(data, tmp_path)['missing_or_failed']
