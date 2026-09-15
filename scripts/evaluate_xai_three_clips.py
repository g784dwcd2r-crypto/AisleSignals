#!/usr/bin/env python3
"""Score exactly three private AisleSignals trials from metadata-only records.

This tool never opens media, calls a provider, logs source paths, or changes alarm
state. It compares human labels with the local candidate and an optional blind
xAI observation exported from the branch-scoped API.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

ACTIONS = {
    "TAKE_PRODUCT", "RETURN_PRODUCT", "PLACE_IN_BASKET",
    "POSSIBLE_CONCEALMENT", "NORMAL_SHOPPING", "UNCLEAR",
}
COMPARISONS = {
    "SUPPORTS_LOCAL_CANDIDATE", "CONTRADICTS_LOCAL_CANDIDATE", "INCONCLUSIVE",
}


class ManifestError(ValueError):
    pass


def require_keys(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ManifestError(f"{where} must contain exactly: {', '.join(keys)}")


def validate_manifest(data):
    require_keys(data, ("schema_version", "clips"), "manifest")
    if data["schema_version"] != "1.0" or not isinstance(data["clips"], list) or len(data["clips"]) != 3:
        raise ManifestError("Use schema 1.0 with exactly three clips.")
    seen = set()
    for index, clip in enumerate(data["clips"]):
        where = f"clips[{index}]"
        require_keys(
            clip,
            ("trial_id", "interaction_id", "staff_label", "local_person_gate", "xai_verification"),
            where,
        )
        if not isinstance(clip["trial_id"], str) or not 1 <= len(clip["trial_id"]) <= 40:
            raise ManifestError(f"{where}.trial_id is invalid.")
        try:
            if str(UUID(clip["interaction_id"])) != clip["interaction_id"]:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ManifestError(f"{where}.interaction_id must be a canonical UUID.") from None
        if clip["interaction_id"] in seen:
            raise ManifestError("Each trial must use a different interaction.")
        seen.add(clip["interaction_id"])
        if clip["staff_label"] not in ACTIONS:
            raise ManifestError(f"{where}.staff_label is invalid.")
        local = clip["local_person_gate"]
        require_keys(
            local,
            ("person_visible", "product_visible", "sequence_observed", "action", "evidence_frame_indices"),
            f"{where}.local_person_gate",
        )
        if any(type(local[name]) is not bool for name in ("person_visible", "product_visible", "sequence_observed")):
            raise ManifestError(f"{where}.local_person_gate flags must be booleans.")
        if local["action"] not in ACTIONS:
            raise ManifestError(f"{where}.local_person_gate.action is invalid.")
        local_indices = local["evidence_frame_indices"]
        if (
            not isinstance(local_indices, list)
            or local_indices != sorted(set(local_indices))
            or any(type(value) is not int or not 0 <= value <= 5 for value in local_indices)
        ):
            raise ManifestError(f"{where}.local_person_gate evidence indices are invalid.")
        external = clip["xai_verification"]
        if external is None:
            continue
        require_keys(
            external,
            (
                "status", "advisory_only", "alarm_decision_permitted",
                "requires_local_candidate", "selected_frame_indices", "result",
            ),
            f"{where}.xai_verification",
        )
        if (
            external["status"] != "COMPLETED"
            or external["advisory_only"] is not True
            or external["alarm_decision_permitted"] is not False
            or external["requires_local_candidate"] is not True
        ):
            raise ManifestError(f"{where} contains an unsafe or incomplete xAI record.")
        frames = external["selected_frame_indices"]
        if not isinstance(frames, list) or not 3 <= len(frames) <= 6 or frames != list(range(len(frames))):
            raise ManifestError(f"{where} must show that the complete ordered frame sample was reviewed.")
        result = external["result"]
        require_keys(result, ("blind_observation", "comparison", "local_action"), f"{where}.result")
        if result["comparison"] not in COMPARISONS or result["local_action"] != local["action"]:
            raise ManifestError(f"{where} has an inconsistent local comparison.")
        observed = result["blind_observation"].get("observed_action")
        if observed not in ACTIONS:
            raise ManifestError(f"{where} has an invalid blind xAI action.")
    return data


def evaluate(data):
    validate_manifest(data)
    completed = [clip for clip in data["clips"] if clip["xai_verification"] is not None]
    rows = []
    for clip in data["clips"]:
        external = clip["xai_verification"]
        rows.append({
            "trial_id": clip["trial_id"],
            "staff_label": clip["staff_label"],
            "local_action": clip["local_person_gate"]["action"],
            "local_matches_staff": clip["local_person_gate"]["action"] == clip["staff_label"],
            "xai_action": external["result"]["blind_observation"]["observed_action"] if external else None,
            "xai_matches_staff": (
                external["result"]["blind_observation"]["observed_action"] == clip["staff_label"]
                if external else None
            ),
            "comparison": external["result"]["comparison"] if external else "NOT_RUN",
            "alarm_effect": "NONE",
        })
    return {
        "schema_version": "1.0",
        "trial_count": 3,
        "xai_completed": len(completed),
        "paid_calls_requested_by_this_tool": 0,
        "local_matches_staff": sum(row["local_matches_staff"] for row in rows),
        "xai_matches_staff": sum(row["xai_matches_staff"] is True for row in rows),
        "xai_inconclusive": sum(row["comparison"] == "INCONCLUSIVE" for row in rows),
        "alarm_effect": "NONE",
        "trials": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, help="Private metadata-only three-clip manifest")
    parser.add_argument("--output", type=Path, help="Optional metadata-only JSON result")
    args = parser.parse_args(argv)
    try:
        report = evaluate(json.loads(args.manifest.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ManifestError) as exc:
        print(f"Evaluation manifest rejected: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
