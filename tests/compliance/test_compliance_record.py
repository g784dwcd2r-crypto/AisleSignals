from __future__ import annotations

import copy
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).parents[2]
TOOL = ROOT / "docs/compliance/tools/validate_compliance_record.py"
SPEC = importlib.util.spec_from_file_location("compliance_validator", TOOL)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def approved_record() -> dict:
    record = json.loads(
        (ROOT / "docs/compliance/branch-activation.example.json").read_text()
    )
    record.update(organisation_id="org-1", site_id="site-1")
    record["controller"].update(
        legal_name="Example Pharmacy Limited",
        privacy_contact="privacy@example.invalid",
        approval_status="APPROVED",
        approved_at="2026-09-14T10:00:00+00:00",
    )
    record["assessment"].update(
        dpia_status="APPROVED",
        lawful_basis_status="APPROVED",
        offence_data_assessment_status="APPROVED",
        residual_high_risk=False,
        privacy_review_status="APPROVED",
        reviewed_at="2026-09-14T10:00:00+00:00",
        next_review_at="2027-09-14T10:00:00+00:00",
    )
    record["transparency"].update(
        entrance_sign_verified=True,
        full_notice_published=True,
        notice_version="2026-09-14",
    )
    record["processing"].update(
        processor_terms_status="SIGNED",
        subprocessors_recorded=True,
        transfer_assessment_status="APPROVED",
    )
    record["retention"].update(
        schedule_status="APPROVED",
        automated_deletion_verified=True,
        backup_expiry_recorded=True,
        rights_procedure_status="APPROVED",
        disclosure_procedure_status="APPROVED",
    )
    record["commissioning"] = {key: True for key in record["commissioning"]}
    record["approvals"] = {
        "controller_signoff_reference": "controller-approval-1",
        "privacy_signoff_reference": "privacy-review-1",
    }
    return record


def test_approved_current_record_passes() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    assert MODULE.validate(approved_record(), now=now) == []


def test_repository_example_is_deliberately_blocked() -> None:
    record = json.loads(
        (ROOT / "docs/compliance/branch-activation.example.json").read_text()
    )
    errors = MODULE.validate(record, now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert errors
    assert any("residual_high_risk" in error for error in errors)
    assert any("controller.legal_name" in error for error in errors)


def test_each_safety_boundary_fails_closed() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    fields = (
        ("processing", "audio_disabled"),
        ("processing", "biometric_recognition_disabled"),
        ("processing", "autonomous_alarm_disabled"),
        ("retention", "automated_deletion_verified"),
        ("commissioning", "privacy_masks_verified"),
    )
    for section, key in fields:
        record = copy.deepcopy(approved_record())
        record[section][key] = False
        assert any(
            f"{section}.{key}" in error for error in MODULE.validate(record, now=now)
        )


def test_expired_review_and_high_residual_risk_block() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    record = approved_record()
    record["assessment"]["next_review_at"] = "2026-09-15T00:00:00+00:00"
    record["assessment"]["residual_high_risk"] = True
    errors = MODULE.validate(record, now=now)
    assert any("next_review_at" in error for error in errors)
    assert any("residual_high_risk" in error for error in errors)

