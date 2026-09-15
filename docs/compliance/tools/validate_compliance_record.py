#!/usr/bin/env python3
"""Fail closed when a branch compliance record cannot enable evidence mode."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLACEHOLDERS = {"", "REPLACE_ME", "UNKNOWN", "TBD"}


def _value(record: dict[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def validate(record: dict[str, Any], *, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []

    required_values = {
        "record_version": 1,
        "mode_requested": "EVIDENCE",
        "controller.approval_status": "APPROVED",
        "assessment.dpia_status": "APPROVED",
        "assessment.lawful_basis_status": "APPROVED",
        "assessment.offence_data_assessment_status": "APPROVED",
        "assessment.residual_high_risk": False,
        "assessment.privacy_review_status": "APPROVED",
        "transparency.entrance_sign_verified": True,
        "transparency.full_notice_published": True,
        "processing.processor_terms_status": "SIGNED",
        "processing.subprocessors_recorded": True,
        "processing.transfer_assessment_status": "APPROVED",
        "processing.audio_disabled": True,
        "processing.biometric_recognition_disabled": True,
        "processing.autonomous_alarm_disabled": True,
        "retention.schedule_status": "APPROVED",
        "retention.automated_deletion_verified": True,
        "retention.backup_expiry_recorded": True,
        "retention.rights_procedure_status": "APPROVED",
        "retention.disclosure_procedure_status": "APPROVED",
        "commissioning.camera_scope_approved": True,
        "commissioning.privacy_masks_verified": True,
        "commissioning.authorised_reviewers_assigned": True,
        "commissioning.staff_training_completed": True,
    }

    for path, expected in required_values.items():
        try:
            actual = _value(record, path)
        except KeyError:
            errors.append(f"missing required field: {path}")
            continue
        if actual != expected:
            errors.append(f"{path} must be {expected!r}; got {actual!r}")

    for path in (
        "organisation_id",
        "site_id",
        "controller.legal_name",
        "controller.privacy_contact",
        "transparency.notice_version",
        "approvals.controller_signoff_reference",
        "approvals.privacy_signoff_reference",
    ):
        try:
            actual = _value(record, path)
        except KeyError:
            errors.append(f"missing required field: {path}")
            continue
        if not isinstance(actual, str) or actual.strip().upper() in PLACEHOLDERS:
            errors.append(f"{path} must be a completed non-placeholder value")

    for path in (
        "controller.approved_at",
        "assessment.reviewed_at",
        "assessment.next_review_at",
    ):
        try:
            raw = _value(record, path)
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{path} must be an ISO-8601 timestamp with timezone")
            continue
        if path.endswith("next_review_at") and parsed <= now:
            errors.append("assessment.next_review_at must be in the future")
        if not path.endswith("next_review_at") and parsed > now:
            errors.append(f"{path} cannot be in the future")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"eligible": False, "errors": [str(exc)]}))
        return 2
    if not isinstance(record, dict):
        errors = ["record root must be a JSON object"]
    else:
        errors = validate(record)
    result = {"eligible": not errors, "errors": errors}
    if args.json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif errors:
        print("Evidence mode BLOCKED")
        for error in errors:
            print(f"- {error}")
    else:
        print("Evidence mode compliance record PASSED")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

