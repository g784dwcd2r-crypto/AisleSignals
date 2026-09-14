#!/usr/bin/env python3
"""Evaluate labelled local frame windows without creating alerts or application records.

Prediction-file mode measures the evaluator, not a model. --run-local calls the
same experimental local vision provider as the application; it never uploads to
an external service. See docs/prototype/interaction-evaluation.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time
import tempfile
from datetime import datetime, timezone


CLASSES = (
    "TAKE_PRODUCT",
    "RETURN_PRODUCT",
    "PLACE_IN_BASKET",
    "POSSIBLE_CONCEALMENT",
    "NORMAL_SHOPPING",
    "UNCLEAR",
)
SPLITS = ("train", "validation", "test")
NORMAL_CLASSES = set(CLASSES) - {"POSSIBLE_CONCEALMENT", "UNCLEAR"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
VERSION = "interaction-evaluator-v3"
EVIDENCE_STRENGTHS = (
    "STRONG_RULE_MATCH",
    "PARTIAL_RULE_MATCH",
    "INSUFFICIENT_RULE_MATCH",
)
SCENARIOS = (
    "ORDINARY_BROWSING", "PICKUP", "RETURN", "BASKET_PLACEMENT",
    "STAGED_CONCEALMENT", "PHONE_OR_BAG_HANDLING", "STAFF_RESTOCKING",
    "OCCLUSION", "MULTIPLE_PEOPLE",
)


class EvaluationError(ValueError):
    """A safe manifest or evaluation failure; no image bytes in messages."""


def require(condition, message):
    if not condition:
        raise EvaluationError(message)


def identifier(value, field):
    require(isinstance(value, str) and IDENTIFIER.fullmatch(value), f"Invalid {field} identifier")
    return value


def number(value, field, minimum=0, maximum=86400):
    require(
        type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum,
        f"Invalid {field}",
    )
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def local_frame(root: Path, value: str):
    require(isinstance(value, str) and value and "\x00" not in value, "Invalid frame path")
    path = Path(value)
    require(not path.is_absolute() and ":" not in value and "\\" not in value, "Frame paths must be local relative paths")
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root.resolve()), "Frame path escapes the manifest directory")
    require(resolved.suffix.lower() in {".jpg", ".jpeg"}, "Frame files must be JPEG")
    return resolved


def validate_manifest(manifest, root: Path):
    require(isinstance(manifest, dict), "Manifest must be an object")
    require(manifest.get("schema_version") == "1.0", "Expected manifest schema_version 1.0")
    identifier(manifest.get("dataset_id"), "dataset")
    require(manifest.get("split_policy") == "DISJOINT_CAMERA_DAY_PERSON", "Disjoint camera/day/person split policy is required")
    authorization = manifest.get("authorization", {})
    require(isinstance(authorization, dict) and authorization.get("confirmed") is True, "Record authorisation before evaluating local media")
    identifier(authorization.get("reference"), "authorisation reference")
    sessions = manifest.get("sessions")
    windows = manifest.get("windows")
    require(isinstance(sessions, list) and sessions, "At least one session is required")
    require(isinstance(windows, list) and windows, "At least one labelled window is required")
    by_session = {}
    recordings = set()
    groups = {key: {} for key in ("camera", "day", "person", "frame")}

    def group(kind, value, split):
        previous = groups[kind].setdefault(value, split)
        require(previous == split, f"Split leakage: {kind} group appears in multiple splits")

    for session in sessions:
        require(isinstance(session, dict), "Sessions must be objects")
        sid = identifier(session.get("id"), "session")
        require(sid not in by_session, "Duplicate session id")
        if "site_id" in session:
            identifier(session["site_id"], "site")
        split = session.get("split")
        require(split in SPLITS, "Unknown session split")
        for kind in ("camera", "day"):
            group(kind, identifier(session.get(f"{kind}_id"), kind), split)
        require(session.get("source_kind") in {"STAGED", "AUTHORISED_INCIDENT", "NORMAL_OBSERVATION"}, "Unknown source_kind")
        recording_hash = session.get("recording_sha256")
        require(isinstance(recording_hash, str) and re.fullmatch(r"[a-f0-9]{64}", recording_hash), "Record the source recording SHA-256")
        require(recording_hash not in recordings, "Duplicate source recording: use one session per recording")
        recordings.add(recording_hash)
        require(number(session.get("duration_seconds"), "session duration") > 0, "Session duration must be positive")
        require(type(session.get("normal_duration_measured")) is bool, "normal_duration_measured must be explicit")
        require(not session["normal_duration_measured"] or session["source_kind"] == "NORMAL_OBSERVATION", "Only normal observations can supply normal camera-hours")
        by_session[sid] = session

    window_ids = set()
    time_ranges = set()
    for window in windows:
        require(isinstance(window, dict), "Windows must be objects")
        wid = identifier(window.get("id"), "window")
        require(wid not in window_ids, "Duplicate window id")
        window_ids.add(wid)
        require(isinstance(window.get("session_id"), str) and window["session_id"] in by_session, "Unknown window session")
        session = by_session[window["session_id"]]
        start = number(window.get("start_seconds"), "window start")
        end = number(window.get("end_seconds"), "window end")
        require(0 < end - start <= 12 and end <= session["duration_seconds"], "Windows must span at most 12 seconds within their session")
        time_range = (session["id"], start, end)
        require(time_range not in time_ranges, "Duplicate source window")
        time_ranges.add(time_range)
        require(window.get("label") in CLASSES, "Unknown ground-truth class")
        if "scenario" in window:
            require(window["scenario"] in SCENARIOS, "Unknown evaluation scenario")
        if session["source_kind"] == "NORMAL_OBSERVATION":
            require(window["label"] in NORMAL_CLASSES, "Normal observation sessions require adjudicated normal ground truth")
        people = window.get("person_ids")
        require(isinstance(people, list), "person_ids must be a list")
        for person in people:
            identifier(person, "person")
        require(len(people) == len(set(people)), "person_ids must not contain duplicates")
        for person in people:
            group("person", identifier(person, "person"), session["split"])
        frames = window.get("frames")
        require(isinstance(frames, list) and 3 <= len(frames) <= 6, "Each window needs 3 to 6 frames")
        previous_time = -1
        for frame in frames:
            require(isinstance(frame, dict), "Frames must be objects")
            offset = number(frame.get("offset_seconds"), "frame offset")
            require(start <= offset <= end and offset > previous_time, "Frame offsets must be chronological within the window")
            previous_time = offset
            group("frame", str(local_frame(root, frame.get("path"))), session["split"])
        require(abs(frames[0]["offset_seconds"] - start) < 1e-6 and abs(frames[-1]["offset_seconds"] - end) < 1e-6,
                "First and last frame timestamps must delimit the labelled window")
    return by_session


def validate_predictions(predictions, windows):
    require(isinstance(predictions, dict) and isinstance(predictions.get("results"), list), "Predictions require a results list")
    provenance = predictions.get("provenance")
    require(isinstance(provenance, dict), "Predictions require provenance")
    for field in ("model", "prompt_version", "origin"):
        require(isinstance(provenance.get(field), str) and provenance[field].strip(), f"Prediction provenance requires {field}")
    expected = {window["id"] for window in windows}
    found = {}
    for result in predictions["results"]:
        require(isinstance(result, dict), "Prediction results must be objects")
        wid = result.get("window_id")
        require(isinstance(wid, str) and wid in expected and wid not in found, "Unexpected or duplicate prediction window")
        require(result.get("action") in CLASSES, "Unknown predicted class")
        require(type(result.get("alarm_eligible")) is bool, "Prediction alarm_eligible must be explicit")
        require(not result["alarm_eligible"] or result["action"] == "POSSIBLE_CONCEALMENT", "Only possible concealment can be alarm-eligible")
        status = result.get("status", "OK")
        require(status in {"OK", "ERROR"}, "Unknown prediction status")
        require(status != "ERROR" or (result["action"] == "UNCLEAR" and not result["alarm_eligible"]), "Errors must abstain and suppress alarms")
        number(result.get("inference_ms"), "inference timing", maximum=3600000)
        if "wall_ms" in result:
            number(result["wall_ms"], "wall timing", maximum=3600000)
        if "evidence_strength" in result:
            require(result["evidence_strength"] in EVIDENCE_STRENGTHS, "Unknown evidence rule strength")
        found[wid] = {**result, "status": status}
    require(set(found) == expected, "Missing predictions: every selected window must be included, including errors")
    return found


def covered_session(windows, duration):
    cursor = 0.0
    for window in sorted(windows, key=lambda value: value["start_seconds"]):
        if window["start_seconds"] > cursor + 1e-6:
            return False
        cursor = max(cursor, window["end_seconds"])
    return cursor >= duration - 1e-6


def summarize_timings(values):
    if not values:
        return {"count": 0, "median_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "median_ms": statistics.median(values),
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "max_ms": max(values),
    }


def alarm_metrics(windows, found):
    """Measure the routing rule separately from the predicted action label."""
    support = sum(w["label"] == "POSSIBLE_CONCEALMENT" for w in windows)
    true_positive = sum(w["label"] == "POSSIBLE_CONCEALMENT" and found[w["id"]]["alarm_eligible"] for w in windows)
    false_positive = sum(w["label"] in NORMAL_CLASSES and found[w["id"]]["alarm_eligible"] for w in windows)
    unresolved = sum(w["label"] == "UNCLEAR" and found[w["id"]]["alarm_eligible"] for w in windows)
    return {
        "concealment_label_support": support,
        "eligible_on_concealment_label": true_positive,
        "concealment_eligibility_recall": true_positive / support if support else None,
        "eligible_on_resolved_normal_labels": false_positive,
        "eligible_precision_on_resolved_labels": true_positive / (true_positive + false_positive) if true_positive + false_positive else None,
        "eligible_on_unclear_labels": unresolved,
        "physical_alarm_delivery_tested": False,
        "confidence_interval": None,
        "uncertainty_note": "Counts are descriptive for these sampled windows. Overlapping windows and repeated actors are dependent; no population-accuracy or independence assumption is made.",
    }


def simulated_delivery_metrics(windows, found, cooldown_seconds):
    """Replay the browser cooldown over source time; never claim speaker delivery."""
    emitted = []
    suppressed = []
    concealment_without_signal = 0
    concealment_without_new_sound = 0
    for sid in sorted({window["session_id"] for window in windows}):
        last_sound = -math.inf
        candidates = sorted(
            (window for window in windows if window["session_id"] == sid),
            key=lambda value: (value["end_seconds"], value["id"]),
        )
        for window in candidates:
            prediction = found[window["id"]]
            eligible = prediction["alarm_eligible"] and prediction["status"] == "OK"
            sounded = eligible and window["end_seconds"] - last_sound >= cooldown_seconds
            if sounded:
                emitted.append(window["id"])
                last_sound = window["end_seconds"]
            elif eligible:
                suppressed.append(window["id"])
            if window["label"] == "POSSIBLE_CONCEALMENT":
                concealment_without_signal += not eligible
                concealment_without_new_sound += not sounded
    return {
        "eligible_windows": len(emitted) + len(suppressed),
        "simulated_new_sound_requests": len(emitted),
        "cooldown_suppressed_windows": len(suppressed),
        "concealment_windows_without_eligible_signal": concealment_without_signal,
        "concealment_windows_without_new_sound": concealment_without_new_sound,
        "cooldown_seconds": cooldown_seconds,
        "physical_alarm_delivery_tested": False,
        "offline_delivery_note": (
            "Application metadata uses the persistent retry outbox; this source-time "
            "simulation does not test a network, operating-system audio or staff response."
        ),
    }


def classification_metrics(windows, found):
    matrix = {actual: {predicted: 0 for predicted in CLASSES} for actual in CLASSES}
    for window in windows:
        matrix[window["label"]][found[window["id"]]["action"]] += 1
    metrics = {}
    for label in CLASSES:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        predicted = sum(matrix[other][label] for other in CLASSES)
        metrics[label] = {"support": support, "predicted": predicted, "true_positive": tp,
                          "precision": tp / predicted if predicted else None,
                          "recall": tp / support if support else None}
    return matrix, metrics


def validate_coverage_plan(plan):
    require(isinstance(plan, dict) and plan.get("schema_version") == "1.0", "Expected coverage plan schema_version 1.0")
    identifier(plan.get("plan_id"), "coverage plan")
    require(plan.get("declared_before_test") is True, "Declare a coverage plan before viewing test predictions")
    identifier(plan.get("owner_reference"), "coverage plan owner reference")
    branches = plan.get("branches")
    require(isinstance(branches, list) and 1 <= len(branches) <= 100, "Coverage plan requires 1 to 100 branches")
    sites = set()
    for branch in branches:
        require(isinstance(branch, dict), "Coverage plan branches must be objects")
        site_id = identifier(branch.get("site_id"), "coverage site")
        require(site_id not in sites, "Duplicate coverage site")
        sites.add(site_id)
        counts = branch.get("minimum_successful_windows_per_class")
        require(isinstance(counts, dict) and set(counts) == set(CLASSES), "Coverage plan must specify all six class minimums")
        for count in counts.values():
            require(type(count) is int and 1 <= count <= 100000, "Class minimums must be positive integers")
        scenarios = branch.get("minimum_successful_windows_per_scenario")
        require(isinstance(scenarios, dict) and scenarios and set(scenarios) <= set(SCENARIOS), "Coverage plan must specify supported scenarios")
        for count in scenarios.values():
            require(type(count) is int and 1 <= count <= 100000, "Scenario minimums must be positive integers")
        for field in ("minimum_recordings", "minimum_days", "minimum_cameras"):
            require(type(branch.get(field)) is int and 1 <= branch[field] <= 100000, "Recording, day and camera minimums must be positive integers")
        number(branch.get("minimum_normal_camera_hours"), "minimum normal camera-hours", minimum=0.001, maximum=100000)
    return sites


def coverage_report(plan, by_session, windows, found, included_normal_sessions, cooldown_seconds):
    planned_sites = validate_coverage_plan(plan)
    reports = []
    for branch in plan["branches"]:
        site_id = branch["site_id"]
        selected = [w for w in windows if by_session[w["session_id"]].get("site_id") == site_id]
        successful = [w for w in selected if found[w["id"]]["status"] == "OK"]
        sessions = {w["session_id"]: by_session[w["session_id"]] for w in successful}
        class_counts = {label: sum(w["label"] == label for w in successful) for label in CLASSES}
        scenario_counts = {scenario: sum(w.get("scenario") == scenario for w in successful) for scenario in SCENARIOS}
        normal_hours = sum(by_session[sid]["duration_seconds"] for sid in included_normal_sessions if by_session[sid].get("site_id") == site_id) / 3600
        normal_alarms = 0
        for sid in included_normal_sessions:
            if by_session[sid].get("site_id") != site_id:
                continue
            last_alarm = -math.inf
            for window in sorted((w for w in selected if w["session_id"] == sid), key=lambda value: value["end_seconds"]):
                if found[window["id"]]["alarm_eligible"] and window["end_seconds"] - last_alarm >= cooldown_seconds:
                    normal_alarms += 1
                    last_alarm = window["end_seconds"]
        matrix, metrics = classification_metrics(selected, found)
        actual = {
            "minimum_recordings": len(sessions),
            "minimum_days": len({s["day_id"] for s in sessions.values()}),
            "minimum_cameras": len({s["camera_id"] for s in sessions.values()}),
            "minimum_normal_camera_hours": normal_hours,
        }
        missing = []
        for field, count in actual.items():
            if count < branch[field]:
                missing.append({"check": field, "required": branch[field], "observed": count})
        for label, minimum in branch["minimum_successful_windows_per_class"].items():
            if class_counts[label] < minimum:
                missing.append({"check": "class:" + label, "required": minimum, "observed": class_counts[label]})
        for scenario, minimum in branch["minimum_successful_windows_per_scenario"].items():
            if scenario_counts[scenario] < minimum:
                missing.append({"check": "scenario:" + scenario, "required": minimum, "observed": scenario_counts[scenario]})
        errors = len(selected) - len(successful)
        if errors:
            missing.append({"check": "inference_errors", "required": 0, "observed": errors})
        reports.append({
            "site_id": site_id,
            "status": "NOT_RUN" if not selected else "INSUFFICIENT" if missing else "SUFFICIENT_FOR_REVIEW",
            "window_count": len(selected), "inference_errors": errors,
            "successful_class_support": class_counts,
            "successful_scenario_support": scenario_counts,
            "successful_recording_count": len(sessions),
            "measured_covered_normal_camera_hours": normal_hours,
            "simulated_false_alarm_episodes": normal_alarms,
            "simulated_false_alarms_per_camera_hour": normal_alarms / normal_hours if normal_hours else None,
            "confusion_matrix_actual_rows_predicted_columns": matrix,
            "per_class": metrics,
            "abstention_rate": sum(found[w["id"]]["action"] == "UNCLEAR" for w in selected) / len(selected) if selected else None,
            "alarm_eligibility": alarm_metrics(selected, found),
            "missing": missing,
            "site_acceptance_established": False,
        })
    unscoped = sum("site_id" not in by_session[w["session_id"]] for w in windows)
    unplanned = sorted({by_session[w["session_id"]]["site_id"] for w in windows if "site_id" in by_session[w["session_id"]] and by_session[w["session_id"]]["site_id"] not in planned_sites})
    return {
        "plan_id": plan["plan_id"], "plan_sha256": digest(plan),
        "plan_declared_before_test": True, "declaration_independently_verified": False,
        "purpose": "DATA_SUFFICIENCY_ONLY_NOT_ACCEPTANCE",
        "status": "SUFFICIENT_FOR_REVIEW" if all(r["status"] == "SUFFICIENT_FOR_REVIEW" for r in reports) else "INCOMPLETE",
        "site_acceptance_established": False, "branches": reports,
        "unscoped_windows_excluded": unscoped, "unplanned_sites_excluded": unplanned,
        "warning": "Minimum counts are the supplied plan, not an accuracy guarantee. Review errors, per-class metrics, alert burden and live delivery separately. The declaration timestamp has not been independently attested.",
    }


def evaluate(manifest, predictions, root: Path, split="test", cooldown_seconds=30, coverage_plan=None):
    by_session = validate_manifest(manifest, root)
    require(split in SPLITS, "Unknown evaluation split")
    number(cooldown_seconds, "alarm cooldown", maximum=3600)
    windows = [w for w in manifest["windows"] if by_session[w["session_id"]]["split"] == split]
    require(windows, "Selected split contains no windows")
    found = validate_predictions(predictions, windows)
    matrix, metrics = classification_metrics(windows, found)

    normal_seconds = 0.0
    normal_alarms = 0
    included_sessions = []
    excluded_sessions = []
    for sid, session in by_session.items():
        if session["split"] != split or session["source_kind"] != "NORMAL_OBSERVATION":
            continue
        candidates = [w for w in windows if w["session_id"] == sid]
        if not session["normal_duration_measured"]:
            excluded_sessions.append({"session_id": sid, "reason": "Normal duration not measured"})
        elif not covered_session(candidates, session["duration_seconds"]):
            excluded_sessions.append({"session_id": sid, "reason": "Windows do not cover the entire normal observation"})
        elif any(found[w["id"]]["status"] == "ERROR" for w in candidates):
            excluded_sessions.append({"session_id": sid, "reason": "Inference errors prevent complete model coverage"})
        else:
            included_sessions.append(sid)
            normal_seconds += session["duration_seconds"]
            last_alarm_time = -math.inf
            for window in sorted(candidates, key=lambda value: value["end_seconds"]):
                if found[window["id"]]["alarm_eligible"] and window["end_seconds"] - last_alarm_time >= cooldown_seconds:
                    normal_alarms += 1
                    last_alarm_time = window["end_seconds"]

    errors = sum(item["status"] == "ERROR" for item in found.values())
    abstentions = sum(item["action"] == "UNCLEAR" for item in found.values())
    unknown_truth_alarms = sum(w["label"] == "UNCLEAR" and found[w["id"]]["alarm_eligible"] for w in windows)
    report = {
        "report_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": "EXPERIMENTAL_REQUIRES_SITE_ACCEPTANCE",
        "site_acceptance_established": False,
        "dataset_id": manifest["dataset_id"],
        "manifest_sha256": digest(manifest),
        "predictions_sha256": digest(predictions),
        "split": split,
        "split_leakage_check": "DISJOINT_CAMERA_DAY_PERSON_AND_FRAME_PATHS",
        "all_three_splits_present": set(s["split"] for s in manifest["sessions"]) == set(SPLITS),
        "model_provenance": predictions["provenance"],
        "window_count": len(windows),
        "source_kinds": {kind: sum(by_session[w["session_id"]]["source_kind"] == kind for w in windows)
                         for kind in ("STAGED", "AUTHORISED_INCIDENT", "NORMAL_OBSERVATION")},
        "confusion_matrix_actual_rows_predicted_columns": matrix,
        "per_class": metrics,
        "abstention": {"count": abstentions, "rate": abstentions / len(windows), "includes_errors": True},
        "inference_errors": errors,
        "alarms_on_unclear_ground_truth": unknown_truth_alarms,
        "alarm_eligibility": alarm_metrics(windows, found),
        "simulated_alarm_delivery": simulated_delivery_metrics(
            windows, found, cooldown_seconds
        ),
        "routing_rule_strength": {
            strength: sum(
                item.get("evidence_strength") == strength for item in found.values()
            )
            for strength in EVIDENCE_STRENGTHS
        },
        "normal_observation": {
            "measured_covered_camera_hours": normal_seconds / 3600,
            "simulated_false_alarm_episodes": normal_alarms,
            "simulated_false_alarms_per_camera_hour": normal_alarms * 3600 / normal_seconds if normal_seconds else None,
            "cooldown_seconds": cooldown_seconds,
            "included_sessions": included_sessions,
            "excluded_sessions": excluded_sessions,
            "physical_alarm_delivery_tested": False,
        },
        "inference_timing": summarize_timings([p["inference_ms"] for p in found.values() if p["status"] == "OK"]),
        "wall_timing": summarize_timings([p["wall_ms"] for p in found.values() if "wall_ms" in p]),
        "warnings": [
            "Counts describe these labelled sampled windows, not general theft accuracy or proof of theft.",
            "Evidence rule strength, structured model output and alarm eligibility are not calibrated confidence.",
            "Window coverage does not mean every video frame was analysed; sampling may miss fast actions.",
            "Simulated alarm episodes use source-window end times, not live queueing or speaker delivery.",
            "Do not tune prompts or thresholds on the test split; relabelled feedback belongs to a new development version.",
        ],
    }
    if coverage_plan is not None:
        report["branch_coverage"] = coverage_report(coverage_plan, by_session, windows, found, included_sessions, cooldown_seconds)
    return report


def read_jpeg(path):
    require(path.is_file(), "Frame must be a regular local file")
    try:
        with path.open("rb") as source:
            content = source.read(350001)
    except OSError as exc:
        raise EvaluationError("Cannot read a local frame file") from exc
    require(0 < len(content) <= 350000 and content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"), "Frame must contain bounded JPEG data")
    return content


def load_frames(manifest, root: Path, split):
    """Read bounded JPEGs and reject exact cross-split duplicate contents."""
    by_session = validate_manifest(manifest, root)
    hashes = {}
    selected = {}
    selected_hashes = {}
    for window in manifest["windows"]:
        window_split = by_session[window["session_id"]]["split"]
        frames = []
        frame_hashes = []
        for frame in window["frames"]:
            path = local_frame(root, frame["path"])
            content = read_jpeg(path)
            sha = hashlib.sha256(content).hexdigest()
            require(hashes.setdefault(sha, window_split) == window_split, "Split leakage: identical frame content appears in multiple splits")
            if window_split == split:
                frames.append((frame["offset_seconds"], path))
                frame_hashes.append(sha)
        if window_split == split:
            selected[window["id"]] = frames
            selected_hashes[window["id"]] = frame_hashes
    return selected, selected_hashes


def run_local(manifest, root: Path, split, provider=None):
    frames, frame_hashes = load_frames(manifest, root, split)
    require(frames, "Selected split contains no frame windows")
    prompt_version = "unknown"
    prompt_sha256 = None
    if provider is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from services.api.interaction_vision import PROMPT_VERSION, SYSTEM_PROMPT, VisionProvider
        prompt_version = PROMPT_VERSION
        prompt_sha256 = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
        try:
            provider = VisionProvider()
        except ValueError as exc:
            raise EvaluationError("The local vision provider configuration is invalid") from exc
    if hasattr(provider, "status"):
        require(provider.status().get("ready") is True, "The configured local vision model is unavailable")
    results = []
    model = getattr(provider, "model", "unknown")
    model_digest = getattr(provider, "model_digest", None)
    for wid, frame_list in frames.items():
        start = time.perf_counter()
        try:
            # Keep only one window of bytes in memory, even for long recordings.
            loaded = [(stamp, read_jpeg(path)) for stamp, path in frame_list]
            require([hashlib.sha256(content).hexdigest() for _, content in loaded] == frame_hashes[wid], "Frame changed after split validation")
            result = provider.analyze(loaded)
            model = result.get("model", model)
            prompt_version = result.get("prompt_version", prompt_version)
            model_digest = result.get("model_digest", model_digest)
            prompt_sha256 = result.get("prompt_sha256", prompt_sha256)
            results.append({"window_id": wid, "action": result["action"], "alarm_eligible": result["alarm_eligible"],
                            **({"evidence_strength": result["evidence_strength"]} if "evidence_strength" in result else {}),
                            "inference_ms": result["inference_ms"], "status": "OK", "wall_ms": (time.perf_counter() - start) * 1000})
        except Exception:
            # Provider errors can contain raw generated text. Retain a count,
            # never that text, frames, file paths, or a possibly personal reason.
            results.append({"window_id": wid, "action": "UNCLEAR", "alarm_eligible": False,
                            "inference_ms": 0, "status": "ERROR", "wall_ms": (time.perf_counter() - start) * 1000})
    return {
        "provenance": {"origin": "LOCAL_PROVIDER_EXECUTION", "model": model, "prompt_version": prompt_version,
                       "model_digest": model_digest, "prompt_sha256": prompt_sha256,
                       "backend": getattr(provider, "backend", "unknown"),
                       "frame_sha256_by_window": frame_hashes, "frame_content_split_check": "PASSED"},
        "results": results,
    }


def write_private_report(path: Path, report):
    """Replace only the selected report, with private permissions on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".evaluation-report-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--predictions", type=Path, help="Score supplied predictions; does not execute or validate a model")
    mode.add_argument("--run-local", action="store_true", help="Run the configured local vision provider serially")
    parser.add_argument("--split", choices=SPLITS, default="test")
    parser.add_argument("--alarm-cooldown-seconds", type=float, default=30)
    parser.add_argument("--coverage-plan", type=Path, help="Predeclared branch-specific sample minimums; reports sufficiency, never site acceptance")
    parser.add_argument("--output", type=Path, required=True, help="Private JSON report path; do not commit footage or personal metadata")
    args = parser.parse_args(argv)
    try:
        inputs = {args.manifest.resolve()}
        if args.predictions:
            inputs.add(args.predictions.resolve())
        if args.coverage_plan:
            inputs.add(args.coverage_plan.resolve())
        require(args.output.resolve() not in inputs, "The output report must not overwrite an input file")
        require(args.output.resolve().suffix.lower() == ".json", "The output report must be a JSON file, never a source JPEG")
        manifest = json.loads(args.manifest.read_text())
        root = args.manifest.resolve().parent
        validate_manifest(manifest, root)
        coverage_plan = json.loads(args.coverage_plan.read_text()) if args.coverage_plan else None
        if coverage_plan is not None:
            validate_coverage_plan(coverage_plan)
        if args.run_local:
            predictions = run_local(manifest, root, args.split)
        else:
            predictions = json.loads(args.predictions.read_text())
        report = evaluate(manifest, predictions, root, args.split, args.alarm_cooldown_seconds, coverage_plan)
        report["execution_mode"] = "LOCAL_PROVIDER_EXECUTION" if args.run_local else "PREDICTION_FILE_METRICS_ONLY"
        report["results"] = predictions["results"]
        report["evaluator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if "branch_coverage" in report:
            report["branch_coverage"]["execution_mode"] = report["execution_mode"]
        write_private_report(args.output, report)
    except (EvaluationError, OSError, json.JSONDecodeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Evaluated {report['window_count']} windows; {report['inference_errors']} inference errors. Site acceptance remains unestablished.")
    return 1 if report["inference_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
