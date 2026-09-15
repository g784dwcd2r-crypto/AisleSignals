"""Synthetic stored-shape fixtures; no database, footage or network is accessed."""

import copy
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid5

import pytest

from services.api.cloud_observation import (
    AdmissionSnapshot,
    BindingSnapshot,
    Exclusion,
    MappedObservation,
    ObservationScope,
    OBSERVATION_NAMESPACE,
    canonical_payload,
    map_observation,
    source_event_id,
    validate_payload,
)


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
INSTALLATION = "11111111-1111-4111-8111-111111111111"
BINDING_ID = "22222222-2222-4222-8222-222222222222"
ENTITY_ID = "33333333-3333-4333-8333-333333333333"
OTHER_ID = "44444444-4444-4444-8444-444444444444"
SCOPE = ObservationScope(INSTALLATION, "org-test", "branch-test")
BINDING = BindingSnapshot(INSTALLATION, BINDING_ID, 1, "org-test", "branch-test", NOW - timedelta(hours=1))
ADMISSION = AdmissionSnapshot(BINDING_ID, 1)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def interaction(**changes):
    # Mirrors the persisted interaction entity, not the flattened history DTO.
    return {
        "id": ENTITY_ID, "organisation_id": SCOPE.organisation_id,
        "site_id": SCOPE.site_id, "source_kind": "SCREEN_CAPTURE",
        "source_label": "synthetic-secret-window-title", "created_at": iso(NOW),
        "expires_at": iso(NOW + timedelta(hours=24)), "status": "completed",
        "result": {"action": "POSSIBLE_CONCEALMENT", "alarm_eligible": True},
        **changes,
    }


def live_event(**changes):
    # SQL row scope is separate; app.py live_event bodies have no org/site fields.
    return {
        "id": "live-" + "a" * 64, "source_kind": "CAMERA",
        "event_code": "REPEATED_HAND_TO_WAIST", "created_at": iso(NOW),
        "detected_at": "2026-09-14T08:00:00Z", "source_time_seconds": 900.0,
        "source_label": "synthetic-secret-camera-name", "track_id": 98,
        "sound_requested": True, **changes,
    }


def camera(layout="3x2", index=1):
    return {
        "source_id": OTHER_ID, "epoch": 1, "layout": layout,
        "camera_index": index, "source_width": 960, "source_height": 720,
        "crop": {"x": 0.0, "y": 0.0, "width": 0.3, "height": 0.3},
    }


def mapped(item=None, **changes):
    return map_observation(**{
        "database_mode": "pilot", "scope": SCOPE, "binding": BINDING,
        "admission": ADMISSION, "entity_kind": "interaction",
        "item": interaction() if item is None else item, "now": NOW, **changes,
    })


def test_exact_payload_and_no_mutation_or_forbidden_data_leakage():
    forbidden = "synthetic-forbidden-detail"
    item = interaction(
        source_label=forbidden, title=forbidden, name=forbidden, narrative=forbidden,
        actor_id=forbidden, session_hash=forbidden, track_id=678, person_id=forbidden,
        media_url=forbidden, camera_password=forbidden, detected_at=forbidden,
        review={"note": forbidden, "reviewer_name": forbidden},
        frames=[{"url": forbidden, "jpeg_base64": forbidden, "sha256": forbidden}],
    )
    item["result"].update(detail=forbidden, narrative=forbidden, model=forbidden)
    original = copy.deepcopy(item)
    result = mapped(item)
    assert isinstance(result, MappedObservation)
    assert item == original
    data = json.loads(canonical_payload(result.payload))
    assert data == {
        "source_event_id": source_event_id(
            installation_id=INSTALLATION, binding_id=BINDING_ID,
            organisation_id="org-test", site_id="branch-test",
            entity_kind="interaction", entity_id=ENTITY_ID,
        ),
        "event_code": "POSSIBLE_CONCEALMENT", "source_label": "Screen area · local observation",
        "occurred_at": "2026-09-14T12:00:00Z", "historical": True,
    }
    assert forbidden not in canonical_payload(result.payload).decode()
    assert len(canonical_payload(result.payload)) < 2048
    assert result.admitted_at == NOW and result.deadline == NOW + timedelta(hours=24)
    with pytest.raises(TypeError):
        result.payload["source_label"] = "changed"
    with pytest.raises(FrozenInstanceError):
        result.deadline = NOW
    with pytest.raises(FrozenInstanceError):
        BINDING.enabled = False


def test_uuid5_shared_contract_is_stable_and_scope_bound():
    arguments = dict(installation_id=INSTALLATION, binding_id=BINDING_ID,
                     organisation_id="org-test", site_id="branch-test",
                     entity_kind="interaction", entity_id=ENTITY_ID)
    identity = source_event_id(**arguments)
    wire = '["aislesignals-observation-v1","11111111-1111-4111-8111-111111111111","22222222-2222-4222-8222-222222222222","org-test","branch-test","interaction","33333333-3333-4333-8333-333333333333"]'
    assert identity == str(uuid5(UUID("b00af7d7-4931-5a90-a014-8c3deca79524"), wire))
    assert OBSERVATION_NAMESPACE == UUID("b00af7d7-4931-5a90-a014-8c3deca79524")
    assert UUID(identity).version == 5
    for field, value in (("installation_id", OTHER_ID), ("binding_id", OTHER_ID),
                         ("organisation_id", "org-other"), ("site_id", "branch-other"),
                         ("entity_id", OTHER_ID)):
        assert source_event_id(**{**arguments, field: value}) != identity
    assert mapped(now=NOW + timedelta(minutes=30)).payload == mapped().payload
    changed_generation = mapped(binding=replace(BINDING, generation=2), admission=AdmissionSnapshot(BINDING_ID, 2))
    assert changed_generation.payload == mapped().payload
    changed_camera = mapped(interaction(camera_context=camera()))
    assert changed_camera.payload["source_event_id"] == identity


@pytest.mark.parametrize("mode", ["synthetic", "demo", "PILOT", "", None])
def test_only_real_pilot_database_mode_is_eligible(mode):
    assert mapped(database_mode=mode) is Exclusion.NOT_PILOT


@pytest.mark.parametrize("changes,reason", [
    ({"binding": None}, Exclusion.BINDING_MISSING),
    ({"binding": replace(BINDING, enabled=False)}, Exclusion.BINDING_DISABLED),
    ({"admission": None}, Exclusion.ADMISSION_MISSING),
    ({"admission": AdmissionSnapshot(OTHER_ID, 1)}, Exclusion.BINDING_CHANGED),
    ({"admission": AdmissionSnapshot(BINDING_ID, 2)}, Exclusion.BINDING_CHANGED),
    ({"binding": replace(BINDING, generation=2)}, Exclusion.BINDING_CHANGED),
    ({"binding": replace(BINDING, activated_at=NOW + timedelta(microseconds=1))}, Exclusion.BEFORE_BINDING),
    ({"scope": replace(SCOPE, installation_id=OTHER_ID)}, Exclusion.SCOPE_MISMATCH),
    ({"scope": replace(SCOPE, organisation_id="wrong-org")}, Exclusion.SCOPE_MISMATCH),
    ({"scope": replace(SCOPE, site_id="wrong-site")}, Exclusion.SCOPE_MISMATCH),
])
def test_binding_revision_and_scope_admission_guards(changes, reason):
    assert mapped(**changes) is reason


def test_pre_pair_job_cannot_finish_into_new_binding_or_invent_admission():
    item = interaction(created_at=iso(BINDING.activated_at - timedelta(microseconds=1)))
    assert mapped(item) is Exclusion.BEFORE_BINDING
    assert mapped(item, admission=None) is Exclusion.ADMISSION_MISSING
    assert isinstance(mapped(interaction(created_at=iso(BINDING.activated_at))), MappedObservation)


@pytest.mark.parametrize("field", ["organisation_id", "site_id"])
def test_interaction_requires_matching_embedded_scope(field):
    item = interaction(**{field: "wrong"})
    assert mapped(item) is Exclusion.SCOPE_MISMATCH
    del item[field]
    assert mapped(item) is Exclusion.SCOPE_MISMATCH


@pytest.mark.parametrize("source,reason", [
    ("RECORDED_VIDEO", Exclusion.RECORDED_SOURCE),
    ("FILE", Exclusion.UNSUPPORTED_SOURCE), ("LIVE", Exclusion.UNSUPPORTED_SOURCE),
    ("screen_capture", Exclusion.UNSUPPORTED_SOURCE), (None, Exclusion.UNSUPPORTED_SOURCE),
])
def test_recorded_and_unknown_source_modes_excluded(source, reason):
    assert mapped(interaction(source_kind=source)) is reason


def test_legacy_source_labels_only_come_from_constants():
    assert mapped(interaction(source_kind="CAMERA")).payload["source_label"] == "Camera source · local observation"
    assert mapped(interaction(source_kind="SCREEN_CAPTURE")).payload["source_label"] == "Screen area · local observation"
    assert mapped(interaction(historical=True)) is Exclusion.RECORDED_SOURCE


@pytest.mark.parametrize("status", ["pending", "running", "failed", "cancelled", "COMPLETED", None])
def test_only_successful_completed_product_jobs(status):
    assert mapped(interaction(status=status)) is Exclusion.NOT_COMPLETED


@pytest.mark.parametrize("action", ["TAKE_PRODUCT", "RETURN_PRODUCT", "PLACE_IN_BASKET", "NORMAL_SHOPPING", "UNCLEAR", "THEFT", None])
def test_normal_or_unsupported_actions_never_export_even_if_flag_true(action):
    assert mapped(interaction(result={"action": action, "alarm_eligible": True})) is Exclusion.EVENT_EXCLUDED


@pytest.mark.parametrize("eligible", [False, None, 1, "true", {}, []])
def test_requires_exact_server_boolean_alarm_eligibility(eligible):
    item = interaction(result={"action": "POSSIBLE_CONCEALMENT", "alarm_eligible": eligible})
    assert mapped(item) is Exclusion.ALARM_INELIGIBLE


def test_review_only_candidate_does_not_enter_the_alarm_transport():
    item = interaction(
        result={
            "action": "POSSIBLE_CONCEALMENT",
            "alarm_eligible": False,
            "review_attention_eligible": True,
        }
    )
    assert mapped(item) is Exclusion.ALARM_INELIGIBLE


@pytest.mark.parametrize("code", ["REPEATED_HAND_TO_WAIST", "RESTRICTED_ZONE_ENTRY"])
def test_pose_opt_in_is_per_allowlisted_code_and_excludes_recordings(code):
    item = live_event(event_code=code)
    assert mapped(item, entity_kind="live_event") is Exclusion.POSE_DISABLED
    policy = replace(BINDING, pose_event_codes=frozenset({code}))
    result = mapped(item, entity_kind="live_event", binding=policy)
    assert result.payload["event_code"] == code
    assert result.payload["occurred_at"] == iso(NOW)  # not browser detected_at
    assert result.deadline == NOW + timedelta(hours=24)
    assert "track_id" not in result.payload and "sound_requested" not in result.payload
    assert mapped(live_event(event_code=code, source_kind="RECORDED_VIDEO"), entity_kind="live_event", binding=policy) is Exclusion.RECORDED_SOURCE
    other_code = "RESTRICTED_ZONE_ENTRY" if code == "REPEATED_HAND_TO_WAIST" else "REPEATED_HAND_TO_WAIST"
    assert mapped(live_event(event_code=other_code), entity_kind="live_event", binding=policy) is Exclusion.POSE_DISABLED
    assert mapped(live_event(site_id="wrong"), entity_kind="live_event", binding=policy) is Exclusion.SCOPE_MISMATCH


def test_live_event_sql_shape_and_identifier_not_browser_event_uuid():
    policy = replace(BINDING, pose_event_codes=frozenset({"REPEATED_HAND_TO_WAIST"}))
    result = mapped(live_event(), entity_kind="live_event", binding=policy)
    assert isinstance(result, MappedObservation)
    assert result.entity_id == "live-" + "a" * 64
    assert result.payload["source_event_id"] != result.entity_id
    assert mapped(live_event(id=ENTITY_ID), entity_kind="live_event", binding=policy) is Exclusion.INVALID_OBSERVATION
    assert mapped(live_event(event_code="POSSIBLE_CONCEALMENT"), entity_kind="live_event", binding=policy) is Exclusion.EVENT_EXCLUDED


@pytest.mark.parametrize("layout,index,count", [
    (layout, index, count)
    for layout, count in (("2x2", 4), ("3x2", 6), ("2x3", 6))
    for index in range(count)
])
def test_all_camera_positions_have_exact_safe_labels(layout, index, count):
    result = mapped(interaction(camera_context=camera(layout, index)))
    assert result.payload["source_label"] == f"Camera {index + 1} of {count} · {layout} screen grid · local observation"
    assert OTHER_ID not in canonical_payload(result.payload).decode()
    assert "crop" not in result.payload and "epoch" not in result.payload


@pytest.mark.parametrize("changes", [
    {"layout": "4x4"}, {"camera_index": True}, {"camera_index": -1},
    {"camera_index": 6}, {"layout": "2x2", "camera_index": 4},
    {"source_id": "secret-camera-name"}, {"source_width": 24}, {"epoch": 0},
    {"source_width": True}, {"source_height": 16385},
    {"crop": {"x": 0.9, "y": 0.0, "width": 0.3, "height": 0.3}},
    {"crop": {"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.3}},
    {"crop": {"x": 0.0, "y": 0.0, "width": float("nan"), "height": 0.3}},
    {"source_width": 96, "source_height": 96}, {"camera_name": "secret"},
])
def test_invalid_camera_context_cannot_downgrade_to_generic_label(changes):
    assert mapped(interaction(camera_context={**camera(), **changes})) is Exclusion.INVALID_CAMERA_CONTEXT


@pytest.mark.parametrize("value", [None, "", "secret-invalid-date", "2026-09-14", "2026-09-14T12:00:00", "2026-09-14 12:00:00Z", "2026-02-30T12:00:00Z", "2026-09-14T12:00:00+25:00", 1757851200, True])
def test_invalid_admission_clock_never_replaced_with_now(value):
    assert mapped(interaction(created_at=value)) is Exclusion.CLOCK_INVALID


@pytest.mark.parametrize("value", [None, "bad", "2026-09-15T12:00:00", iso(NOW), iso(NOW - timedelta(seconds=1))])
def test_invalid_or_impossible_expiry(value):
    assert mapped(interaction(expires_at=value)) is Exclusion.CLOCK_INVALID


def test_retention_exact_boundaries_and_no_extension_of_source_expiry():
    assert mapped(interaction(expires_at=iso(NOW + timedelta(days=99)))).deadline == NOW + timedelta(hours=24)
    earlier = NOW + timedelta(minutes=1)
    assert mapped(interaction(expires_at=iso(earlier))).deadline == earlier
    assert mapped(interaction(expires_at=iso(earlier)), now=earlier) is Exclusion.EXPIRED
    assert isinstance(mapped(interaction(expires_at=iso(earlier)), now=earlier - timedelta(microseconds=1)), MappedObservation)
    assert mapped(now=NOW + timedelta(hours=24)) is Exclusion.EXPIRED
    assert mapped(now=NOW + timedelta(days=31)) is Exclusion.CLOCK_INVALID
    assert mapped(interaction(created_at=iso(NOW + timedelta(minutes=5, microseconds=1)))) is Exclusion.CLOCK_INVALID
    assert isinstance(mapped(interaction(created_at=iso(NOW + timedelta(minutes=5)))), MappedObservation)
    assert mapped(now=NOW.replace(tzinfo=None)) is Exclusion.CLOCK_INVALID


def test_timezone_normalization_uses_same_admission_instant_and_frozen_input():
    item = interaction(created_at="2026-09-14T13:00:00+01:00")
    first = mapped(item)
    assert first.payload["occurred_at"] == iso(NOW)
    item["created_at"] = "bad-after-mapping"
    item["result"]["action"] = "UNCLEAR"
    assert first.payload["occurred_at"] == iso(NOW)
    assert first.payload["event_code"] == "POSSIBLE_CONCEALMENT"
    assert canonical_payload(first.payload) == canonical_payload(mapped().payload)


@pytest.mark.parametrize("changes", [
    {"extra_notes": "synthetic-secret"}, {"historical": False}, {"historical": 1},
    {"event_code": "POSSIBLE_PRODUCT_TAKE"}, {"event_code": "POSSIBLE_PRODUCT_RETURN"},
    {"event_code": ["POSSIBLE_CONCEALMENT"]}, {"source_label": ["Camera source"]},
    {"source_label": "Camera 5 of 4 · 2x2 screen grid · local observation"},
    {"source_label": "Camera source · local observation\nsynthetic-secret"},
    {"occurred_at": "2026-09-14T13:00:00+01:00"}, {"occurred_at": "now"},
    {"source_event_id": ENTITY_ID}, {"source_event_id": "synthetic-secret"},
])
def test_payload_validator_blocks_added_fields_or_noncanonical_fields_without_echo(changes):
    payload = {**mapped().payload, **changes}
    with pytest.raises(ValueError, match="^INVALID_PAYLOAD$"):
        canonical_payload(payload)


def test_payload_copy_is_immutable_and_missing_fields_reject():
    original = dict(mapped().payload)
    frozen = validate_payload(original)
    original["source_label"] = "synthetic-secret"
    assert frozen["source_label"] == "Screen area · local observation"
    with pytest.raises(TypeError):
        frozen["historical"] = False
    for key in frozen:
        short = dict(frozen)
        del short[key]
        with pytest.raises(ValueError, match="^INVALID_PAYLOAD$"):
            validate_payload(short)


@pytest.mark.parametrize("changes", [
    {"generation": True}, {"generation": 0}, {"generation": 2**63},
    {"enabled": 1}, {"pose_event_codes": {"REPEATED_HAND_TO_WAIST"}},
    {"pose_event_codes": frozenset({"TAKE_PRODUCT"})},
    {"activated_at": NOW.replace(tzinfo=None)}, {"binding_id": "bad"},
    {"site_id": "secret/name"}, {"organisation_id": "secret\nname"},
])
def test_trusted_snapshot_construction_rejects_invalid_or_mutable_values(changes):
    with pytest.raises(ValueError):
        replace(BINDING, **changes)


def test_malformed_stored_result_and_flattened_dto_reject():
    assert mapped(interaction(result=None)) is Exclusion.INVALID_OBSERVATION
    assert mapped(interaction(id="secret-local-id")) is Exclusion.INVALID_OBSERVATION
    dto = interaction()
    dto.update(dto.pop("result"))
    assert mapped(dto) is Exclusion.INVALID_OBSERVATION
    assert mapped(entity_kind="incident") is Exclusion.INVALID_OBSERVATION
    assert mapped(item=[]) is Exclusion.INVALID_OBSERVATION
