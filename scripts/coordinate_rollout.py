#!/usr/bin/env python3
"""Collate six pharmacy readiness records; never manufacture site acceptance."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys

KINDS = ("preflight", "software", "acceptance_plan", "evaluation", "onsite", "signoff")
CONTEXT = ("site_id", "device_id", "platform", "source_version", "app_revision", "model_sha256", "prompt_version")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")
PREFLIGHT_CHECKS = ("platform_inventory", "python_runtime", "web_build", "data_path", "free_disk", "memory_inventory", "api_port_free", "vision_port_free", "vision_runtime", "model_weight_1", "model_weight_2", "vision_token_file")
SOFTWARE_CHECKS = ("python_regression", "web_unit", "chromium_workflows", "platform_bundle")
ONSITE_CHECKS = ("authorised_camera", "readable_product_view", "source_disconnect", "physical_audio", "silence_and_acknowledge", "sleep_resume_rearm", "platform_workflow", "filesystem_acl", "backup_restore", "named_reviewer_coverage")
BOUNDS = ("max_false_alarms_per_camera_hour", "max_abstention_rate", "min_concealment_recall", "min_concealment_precision", "max_p95_wall_ms")


class CoordinationError(ValueError):
    """Safe errors contain no imported text, private paths or footage."""


def require(value, reason):
    if not value:
        raise CoordinationError(reason)


def identifier(value):
    return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))


def digest_bytes(content):
    return hashlib.sha256(content).hexdigest()


def parse_time(value):
    require(isinstance(value, str), "A timezone-aware evidence timestamp is required.")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CoordinationError("Invalid evidence timestamp.") from None
    require(stamp.tzinfo is not None, "Evidence timestamps require a timezone.")
    return stamp.astimezone(timezone.utc)


def fresh(value, now, hours):
    stamp = parse_time(value)
    require(now - timedelta(hours=hours) <= stamp <= now + timedelta(minutes=5), "Evidence is stale or dated in the future.")
    return stamp


def safe_path(path):
    return all(not p.is_symlink() for p in (path, *path.parents))


def read_json(path, limit=1024 * 1024):
    require(path.is_file() and safe_path(path), "The evidence file is unavailable or traverses a symbolic link.")
    require(path.stat().st_size <= limit, "The evidence JSON exceeds its size limit.")
    try:
        content = path.read_bytes()
        require(len(content) <= limit, "The evidence JSON exceeds its size limit.")
        data = json.loads(content)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CoordinationError("Cannot read the selected evidence JSON.") from None
    require(isinstance(data, dict), "Evidence JSON must be an object.")
    return data, digest_bytes(content)


def reference_path(root, reference):
    require(isinstance(reference, dict) and set(reference) == {"path", "sha256"}, "Evidence references require only path and sha256.")
    name = reference["path"]
    require(isinstance(name, str) and name and ":" not in name and "\\" not in name and all(ord(c) >= 32 for c in name), "Use a local relative evidence path.")
    # References have portable forward-slash syntax. On Windows, Path('/x')
    # has a root but no drive and is_absolute() is false, despite escaping a
    # joined intake directory. Interpret the reference independently of OS.
    part = PurePosixPath(name)
    require(not part.is_absolute() and ".." not in part.parts, "Evidence paths must remain inside the intake directory.")
    require(isinstance(reference["sha256"], str) and SHA256.fullmatch(reference["sha256"]), "Evidence references require a SHA-256 digest.")
    path = root.joinpath(*part.parts)
    require(safe_path(path), "Evidence cannot traverse symbolic links.")
    return path


def verify_file(root, reference, limit=2 * 1024**3):
    path = reference_path(root, reference)
    require(path.is_file() and path.stat().st_size <= limit, "The referenced artifact is unavailable or exceeds the size limit.")
    hasher = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            count += len(chunk)
            require(count <= limit, "The referenced artifact exceeds the size limit.")
            hasher.update(chunk)
    require(hasher.hexdigest() == reference["sha256"], "Evidence checksum does not match.")
    return path


def validate_manifest(manifest):
    require(manifest.get("schema_version") == 1, "Expected rollout schema_version 1.")
    require(set(manifest) == {"schema_version", "rollout_id", "max_evidence_age_hours", "slots"}, "Unexpected rollout manifest fields.")
    require(identifier(manifest["rollout_id"]), "Use an opaque rollout identifier.")
    age = manifest["max_evidence_age_hours"]
    require(type(age) is int and 1 <= age <= 168, "Evidence age must be between 1 and 168 hours.")
    slots = manifest["slots"]
    require(isinstance(slots, list) and len(slots) == 6, "The rollout requires exactly six deployment slots.")
    names, sites, devices = set(), set(), set()
    for slot in slots:
        require(isinstance(slot, dict) and set(slot) == {"slot_id", "context", "evidence"}, "Unexpected deployment slot fields.")
        require(identifier(slot["slot_id"]) and slot["slot_id"] not in names, "Deployment slot identifiers must be unique.")
        names.add(slot["slot_id"])
        context = slot["context"]
        require(isinstance(context, dict) and set(context) == set(CONTEXT), "Every slot requires the documented context fields.")
        require(isinstance(slot["evidence"], dict) and set(slot["evidence"]) == set(KINDS), "Every slot requires the six documented evidence types.")
        for key, value in context.items():
            if value is None:
                continue
            if key == "platform":
                valid = isinstance(value, str) and value in {"Darwin", "Windows"}
            elif key == "app_revision":
                valid = isinstance(value, str) and bool(REVISION.fullmatch(value))
            elif key == "model_sha256":
                valid = isinstance(value, str) and bool(SHA256.fullmatch(value))
            else:
                valid = identifier(value)
            require(valid, "Invalid deployment context; use opaque identifiers and exact revisions.")
        for key, seen in (("site_id", sites), ("device_id", devices)):
            if context[key] is not None:
                require(context[key] not in seen, "A branch or laptop cannot be assigned to multiple rollout slots.")
                seen.add(context[key])
    return slots


def load_envelope(root, reference, kind, context, now, hours):
    path = verify_file(root, reference, 1024 * 1024)
    envelope, read_digest = read_json(path)
    require(read_digest == reference["sha256"], "Evidence changed while being read.")
    require(envelope.get("schema_version") == 1 and envelope.get("kind") == kind, "Evidence type or schema does not match.")
    require(envelope.get("context") == context, "Evidence branch, laptop, source or software/model version does not match.")
    require(envelope.get("binding") == "OPERATOR_SUPPLIED_CONTEXT", "Record the source of the branch/device binding.")
    fresh(envelope.get("recorded_at"), now, hours)
    payload = envelope.get("payload")
    require(isinstance(payload, dict), "Evidence payload must be an object.")
    return payload, envelope["recorded_at"]


def checks_by_id(checks):
    require(isinstance(checks, list), "The evidence requires a check list.")
    result = {}
    for check in checks:
        require(isinstance(check, dict) and identifier(check.get("id")) and check["id"] not in result, "Check identifiers must be valid and unique.")
        require(check.get("status") in {"PASS", "FAIL", "NOT_RUN"}, "Unknown check result.")
        result[check["id"]] = check
    return result


def add_check(checks, check_id, status, detail, origin="COORDINATOR_VALIDATION", sha256=None):
    item = {"id": check_id, "status": status, "detail": detail, "origin": origin}
    if sha256:
        item["evidence_sha256"] = sha256
    checks.append(item)


def imported_checks(checks, payload, required, prefix, origin, sha256):
    source = checks_by_id(payload.get("checks"))
    for name in required:
        status = source.get(name, {}).get("status", "NOT_RUN")
        add_check(checks, prefix + name, status, "Imported recorded result." if name in source else "Required check has not been recorded.", origin, sha256)
    return source


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def evaluate_slot(slot, root, now, hours):
    checks, loaded, recorded = [], {}, {}
    context = slot["context"]
    missing_context = [field for field, value in context.items() if value is None]
    if missing_context:
        add_check(checks, "deployment_assignment", "NOT_RUN", "Assign: " + ", ".join(missing_context) + ".")
    else:
        add_check(checks, "deployment_assignment", "PASS", "Identifiers are assigned; physical device identity is operator-supplied.")
    for kind in KINDS:
        reference = slot["evidence"][kind]
        if reference is None or missing_context:
            add_check(checks, kind + "_evidence", "NOT_RUN", "Matching, current " + kind.replace("_", " ") + " evidence is required.")
            continue
        try:
            loaded[kind], recorded[kind] = load_envelope(root, reference, kind, context, now, hours)
            add_check(checks, kind + "_evidence", "PASS", "Local file digest, context and timestamp verified; issuer identity is not authenticated.", sha256=reference["sha256"])
        except (CoordinationError, OSError) as error:
            add_check(checks, kind + "_evidence", "FAIL", str(error) if isinstance(error, CoordinationError) else "Evidence file could not be read.")
    for kind, names in (("preflight", PREFLIGHT_CHECKS), ("software", SOFTWARE_CHECKS), ("onsite", ONSITE_CHECKS)):
        if kind not in loaded:
            for name in names:
                add_check(checks, kind + ":" + name, "NOT_RUN", "No current matching record for this required check.")
    if "evaluation" not in loaded:
        add_check(checks, "evaluation:coverage", "NOT_RUN", "Held-out branch sample coverage has not been supplied.")
        for bound in BOUNDS:
            add_check(checks, "performance:" + bound, "NOT_RUN", "No matched held-out result and predeclared bound to compare.")
    if "signoff" not in loaded:
        add_check(checks, "branch_signoff", "NOT_RUN", "A human reviewer has not signed the exact current evidence set.")
    for kind, payload in list(loaded.items()):
        sha = slot["evidence"][kind]["sha256"]
        try:
            if kind == "preflight":
                require(payload.get("scope") == "local_laptop_readiness", "A local laptop preflight report is required.")
                fresh(payload.get("generated_at"), now, hours)
                entries = imported_checks(checks, payload, PREFLIGHT_CHECKS, "preflight:", "AUTOMATED_REPORT_IMPORT", sha)
                require(entries.get("platform_inventory", {}).get("os") == context["platform"], "Preflight platform differs from the assigned laptop platform.")
                model = entries.get("model_weight_1", {})
                require(model.get("actual_sha256") == context["model_sha256"] == model.get("expected_sha256"), "Preflight model digest differs from the qualified model.")
                require(payload.get("launch_status") == "PASS", "Preflight reports a failed or degraded launch configuration.")
            elif kind == "software":
                require(payload.get("app_revision") == context["app_revision"], "Software verification targets a different application revision.")
                entries = imported_checks(checks, payload, SOFTWARE_CHECKS, "software:", "AUTOMATED_REPORT_IMPORT", sha)
                for check in entries.values():
                    require(type(check.get("passed")) is int and type(check.get("failed")) is int and check["passed"] >= 0 and check["failed"] >= 0, "Software checks require actual pass/fail counts.")
                    require(check["status"] != "PASS" or check["passed"] > 0 and check["failed"] == 0, "Software PASS conflicts with the recorded test counts.")
                    verify_file(root, check.get("log"), 16 * 1024**2)
                artifacts = payload.get("artifacts")
                require(isinstance(artifacts, list) and artifacts, "A platform executable artifact is required.")
                require(any(item.get("platform") == context["platform"] and item.get("app_revision") == context["app_revision"] for item in artifacts if isinstance(item, dict)), "No artifact matches the assigned platform and revision.")
                for artifact in artifacts:
                    require(isinstance(artifact, dict), "Invalid artifact reference.")
                    verify_file(root, artifact.get("file"))
                add_check(checks, "software:artifacts", "PASS", "Local artifact bytes match the recorded hashes; code signing and physical execution are separate checks.", "AUTOMATED_REPORT_IMPORT", sha)
            elif kind == "acceptance_plan":
                require(payload.get("declared_before_test") is True and identifier(payload.get("owner_reference")), "A named pre-test acceptance plan is required.")
                parse_time(payload.get("declared_at"))
                require(SHA256.fullmatch(str(payload.get("coverage_plan_sha256", ""))), "Acceptance plan must identify the evaluation coverage plan digest.")
                bounds = payload.get("bounds")
                require(isinstance(bounds, dict) and set(bounds) == set(BOUNDS), "Declare all five performance bounds; no automatic accuracy threshold is supplied.")
                for key, value in bounds.items():
                    require(numeric(value) and value >= 0 and (value <= 1 if "rate" in key or "recall" in key or "precision" in key else True), "Invalid predeclared performance bound.")
                require(bounds["max_p95_wall_ms"] > 0, "A positive wall-time limit is required.")
            elif kind == "onsite":
                require(payload.get("method") == "HUMAN_ATTESTATION" and identifier(payload.get("reviewer_reference")), "On-site checks need an identified human attestation.")
                fresh(payload.get("observed_at"), now, hours)
                imported_checks(checks, payload, ONSITE_CHECKS, "onsite:", "HUMAN_ATTESTATION_IMPORT", sha)
            elif kind == "evaluation":
                require(payload.get("report_version") == "interaction-evaluator-v2" and payload.get("split") == "test", "A held-out test evaluation report is required.")
                fresh(payload.get("created_at"), now, hours)
                require(payload.get("execution_mode") == "LOCAL_PROVIDER_EXECUTION", "Prediction fixtures cannot qualify a deployed model.")
                provenance = payload.get("model_provenance", {})
                require(provenance.get("origin") == "LOCAL_PROVIDER_EXECUTION" and provenance.get("frame_content_split_check") == "PASSED", "Local model execution and held-out frame content checks are required.")
                require(payload.get("split_leakage_check") == "DISJOINT_CAMERA_DAY_PERSON_AND_FRAME_PATHS" and payload.get("all_three_splits_present") is True, "Independent training, validation and held-out test groups are required.")
                require(provenance.get("model_digest") == context["model_sha256"] and provenance.get("prompt_version") == context["prompt_version"], "Evaluation model or prompt does not match the assigned version.")
                coverage = payload.get("branch_coverage", {})
                branches = coverage.get("branches", [])
                require(isinstance(branches, list), "Invalid branch coverage report.")
                matching = [branch for branch in branches if isinstance(branch, dict) and branch.get("site_id") == context["site_id"]]
                require(len(matching) == 1, "Evaluation must contain exactly one coverage entry for this branch.")
                branch = matching[0]
                require(len(branches) == 1 and coverage.get("unscoped_windows_excluded") == 0 and coverage.get("unplanned_sites_excluded") == [] and payload.get("window_count") == branch.get("window_count"), "Use a separate evaluation report per branch so global timing metrics describe this branch only.")
                require(payload.get("inference_errors") == 0 and branch.get("inference_errors") == 0, "Inference errors prevent branch acceptance.")
                coverage_status = branch.get("status")
                add_check(checks, "evaluation:coverage", "PASS" if coverage_status == "SUFFICIENT_FOR_REVIEW" else "NOT_RUN" if coverage_status == "NOT_RUN" else "FAIL", "Data sufficiency only; performance and explicit branch signoff are separate requirements.", "AUTOMATED_REPORT_IMPORT", sha)
                for item in branch.get("missing", []):
                    name = item.get("check") if isinstance(item, dict) else None
                    if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,100}", name):
                        add_check(checks, "coverage:" + name, "FAIL", "The supplied branch coverage minimum was not met.", "AUTOMATED_REPORT_IMPORT", sha)
                plan = loaded.get("acceptance_plan")
                if not plan:
                    add_check(checks, "evaluation:performance", "NOT_RUN", "A valid predeclared acceptance plan is required.")
                    continue
                require(parse_time(plan["declared_at"]) < parse_time(payload["created_at"]), "Acceptance bounds were not declared before the evaluation.")
                require(coverage.get("plan_sha256") == plan["coverage_plan_sha256"], "Evaluation used a different coverage plan.")
                metrics = {
                    "max_false_alarms_per_camera_hour": branch.get("simulated_false_alarms_per_camera_hour"),
                    "max_abstention_rate": branch.get("abstention_rate"),
                    "min_concealment_recall": branch.get("alarm_eligibility", {}).get("concealment_eligibility_recall"),
                    "min_concealment_precision": branch.get("alarm_eligibility", {}).get("eligible_precision_on_resolved_labels"),
                    "max_p95_wall_ms": payload.get("wall_timing", {}).get("p95_ms"),
                }
                for key, observed in metrics.items():
                    bound = plan["bounds"][key]
                    passed = numeric(observed) and (observed >= bound if key.startswith("min_") else observed <= bound)
                    add_check(checks, "performance:" + key, "PASS" if passed else "NOT_RUN" if observed is None else "FAIL", "Observed " + str(observed if numeric(observed) else "unavailable") + "; declared bound " + str(bound) + ".", "AUTOMATED_REPORT_IMPORT", sha)
            elif kind == "signoff":
                require(payload.get("method") == "HUMAN_ATTESTATION" and identifier(payload.get("reviewer_reference")), "Branch signoff requires an identified human reviewer.")
                fresh(payload.get("signed_at"), now, hours)
                require(payload.get("decision") == "APPROVE_SUPERVISED_ROLLOUT" and payload.get("held_out_performance_reviewed") is True and payload.get("coverage_and_false_alert_workload_reviewed") is True, "Explicit review of held-out performance and alert workload is required.")
                refs = payload.get("reviewed_sha256")
                require(isinstance(refs, dict) and set(refs) == set(KINDS) - {"signoff"}, "Signoff must reference all five reviewed evidence digests.")
                for name, expected in refs.items():
                    require(slot["evidence"][name] and expected == slot["evidence"][name]["sha256"], "Signoff references changed or missing evidence.")
                    if name in loaded:
                        # Each record has already passed its envelope freshness check.
                        require(parse_time(payload["signed_at"]) >= parse_time(recorded[name]), "Signoff predates the evidence it claims to approve.")
                add_check(checks, "branch_signoff", "PASS", "Human approval imported for this exact evidence set; the coordinator did not witness the tests.", "HUMAN_ATTESTATION_IMPORT", sha)
            add_check(checks, kind + "_content", "PASS", "Required report structure and scope verified.", sha256=sha)
        except (CoordinationError, OSError, KeyError, TypeError, AttributeError) as error:
            loaded.pop(kind, None)
            add_check(checks, kind + "_content", "FAIL", str(error) if isinstance(error, CoordinationError) else "Required evidence content is missing or invalid.", sha256=sha)
    complete = bool(checks) and all(item["status"] == "PASS" for item in checks)
    return {"slot_id": slot["slot_id"], "context": context,
            "decision": "RECORDED_ACCEPTANCE_COMPLETE" if complete else "NOT_READY",
            "status": "PASS" if complete else "FAIL" if any(item["status"] == "FAIL" for item in checks) else "NOT_RUN",
            "checks": checks, "missing_or_failed": [item["id"] for item in checks if item["status"] != "PASS"]}


def build_report(manifest, root, *, now=None):
    slots = validate_manifest(manifest)
    now = now or datetime.now(timezone.utc)
    reports = [evaluate_slot(slot, root, now, manifest["max_evidence_age_hours"]) for slot in slots]
    return {"schema_version": 1, "report_kind": "six_pharmacy_rollout_coordination", "rollout_id": manifest["rollout_id"], "generated_at": now.isoformat(),
            "accepted_slots": sum(item["decision"] == "RECORDED_ACCEPTANCE_COMPLETE" for item in reports), "total_slots": 6,
            "slots": reports, "limits": ["This coordinator validates local evidence hashes and recorded decisions; it does not authenticate report issuers or independently witness human tests.",
                "Branch/device bindings are supplied by the operator. A developer laptop report is not evidence from a client laptop.",
                "Data sufficiency is not detection accuracy acceptance. Recorded acceptance applies only to this supervised configuration, never a 100% theft-detection guarantee.",
                "The agent is not continuously on call and cannot grant camera permissions or hear physical speakers."]}


def markdown(report):
    lines = ["# AisleSignals — six-pharmacy rollout readiness", "", "Owner: Jawahir Q.", "", f"Recorded acceptance complete: **{report['accepted_slots']} of 6**. Generated: {report['generated_at']}", "", "| Slot | Branch | Laptop | Platform | Status |", "|---|---|---|---|---|"]
    for slot in report["slots"]:
        context = slot["context"]
        lines.append(f"| {slot['slot_id']} | {context['site_id'] or 'Unassigned'} | {context['device_id'] or 'Unassigned'} | {context['platform'] or 'Unassigned'} | {slot['status']} |")
    for slot in report["slots"]:
        lines.extend(["", f"## {slot['slot_id']}", ""])
        for check in slot["checks"]:
            if check["status"] != "PASS":
                lines.append(f"- **{check['status']} · {check['id']}** — {check['detail']}")
        automated = [check for check in slot["checks"] if check["origin"] == "AUTOMATED_REPORT_IMPORT" and check["status"] == "PASS"]
        human = [check for check in slot["checks"] if check["origin"] == "HUMAN_ATTESTATION_IMPORT" and check["status"] == "PASS"]
        if automated:
            lines.append("- Passing imported automated checks: " + ", ".join(check["id"] for check in automated) + ".")
        if human:
            lines.append("- Passing imported human attestations: " + ", ".join(check["id"] for check in human) + ".")
    lines.extend(["", "## Scope of this report", ""] + ["- " + limit for limit in report["limits"]])
    return "\n".join(lines) + "\n"


def write_new(path, content):
    require(safe_path(path) and not path.exists(), "Choose a new output filename without symbolic links; existing files are preserved.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    # Binary UTF-8 output prevents Windows text-mode newline translation from
    # making the saved report differ from its advertised evidence digest.
    with os.fdopen(fd, "wb") as output:
        output.write(content.encode("utf-8"))
    return digest_bytes(path.read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    report_parser = commands.add_parser("report", help="Validate evidence and report missing rollout checks.")
    report_parser.add_argument("--manifest", type=Path, required=True)
    report_parser.add_argument("--json", type=Path)
    report_parser.add_argument("--markdown", type=Path)
    intake = commands.add_parser("intake", help="Bind an existing local report to operator-supplied slot context.")
    intake.add_argument("--manifest", type=Path, required=True)
    intake.add_argument("--slot", required=True)
    intake.add_argument("--kind", choices=("preflight", "evaluation"), required=True)
    intake.add_argument("--input", type=Path, required=True)
    intake.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest, _ = read_json(args.manifest, 65536)
        slots = validate_manifest(manifest)
        if args.command == "intake":
            selected = [slot for slot in slots if slot["slot_id"] == args.slot]
            require(len(selected) == 1 and all(value is not None for value in selected[0]["context"].values()), "Assign every context field before importing a report.")
            payload, _ = read_json(args.input)
            stamp = payload.get("generated_at") if args.kind == "preflight" else payload.get("created_at")
            fresh(stamp, datetime.now(timezone.utc), manifest["max_evidence_age_hours"])
            envelope = {"schema_version": 1, "kind": args.kind, "context": selected[0]["context"], "binding": "OPERATOR_SUPPLIED_CONTEXT", "recorded_at": stamp, "payload": payload}
            content = json.dumps(envelope, indent=2) + "\n"
            require(args.output.suffix == ".json", "Intake output must use a .json filename.")
            stored_digest = write_new(args.output, content)
            print(json.dumps({"kind": args.kind, "slot_id": args.slot, "sha256": stored_digest, "binding": "OPERATOR_SUPPLIED_CONTEXT"}))
            return 0
        report = build_report(manifest, args.manifest.parent)
        if args.json:
            require(args.json.suffix == ".json", "JSON report must use a .json filename.")
            write_new(args.json, json.dumps(report, indent=2) + "\n")
        if args.markdown:
            require(args.markdown.suffix == ".md", "Markdown report must use a .md filename.")
            write_new(args.markdown, markdown(report))
        print(json.dumps(report, indent=2))
        return 0 if report["accepted_slots"] == 6 else 2
    except (CoordinationError, OSError) as error:
        print(str(error) if isinstance(error, CoordinationError) else "Could not read or write the local coordination files.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
