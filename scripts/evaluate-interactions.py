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
from pathlib import Path
import re
import statistics
import sys
import time
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
VERSION = "interaction-evaluator-v1"


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


def evaluate(manifest, predictions, root: Path, split="test", cooldown_seconds=30):
    by_session = validate_manifest(manifest, root)
    require(split in SPLITS, "Unknown evaluation split")
    number(cooldown_seconds, "alarm cooldown", maximum=3600)
    windows = [w for w in manifest["windows"] if by_session[w["session_id"]]["split"] == split]
    require(windows, "Selected split contains no windows")
    found = validate_predictions(predictions, windows)
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
    return {
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
            "Structured model output and alarm eligibility are not calibrated confidence.",
            "Window coverage does not mean every video frame was analysed; sampling may miss fast actions.",
            "Simulated alarm episodes use source-window end times, not live queueing or speaker delivery.",
            "Do not tune prompts or thresholds on the test split; relabelled feedback belongs to a new development version.",
        ],
    }


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--predictions", type=Path, help="Score supplied predictions; does not execute or validate a model")
    mode.add_argument("--run-local", action="store_true", help="Run the configured local vision provider serially")
    parser.add_argument("--split", choices=SPLITS, default="test")
    parser.add_argument("--alarm-cooldown-seconds", type=float, default=30)
    parser.add_argument("--output", type=Path, required=True, help="Private JSON report path; do not commit footage or personal metadata")
    args = parser.parse_args(argv)
    try:
        inputs = {args.manifest.resolve()}
        if args.predictions:
            inputs.add(args.predictions.resolve())
        require(args.output.resolve() not in inputs, "The output report must not overwrite an input file")
        manifest = json.loads(args.manifest.read_text())
        root = args.manifest.resolve().parent
        validate_manifest(manifest, root)
        if args.run_local:
            predictions = run_local(manifest, root, args.split)
        else:
            predictions = json.loads(args.predictions.read_text())
        report = evaluate(manifest, predictions, root, args.split, args.alarm_cooldown_seconds)
        report["execution_mode"] = "LOCAL_PROVIDER_EXECUTION" if args.run_local else "PREDICTION_FILE_METRICS_ONLY"
        report["results"] = predictions["results"]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    except (EvaluationError, OSError, json.JSONDecodeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Evaluated {report['window_count']} windows; {report['inference_errors']} inference errors. Site acceptance remains unestablished.")
    return 1 if report["inference_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
