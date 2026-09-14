"""FR-002/013/015/018/020: declared camera provenance never grants authority.

All JPEGs and account details are synthetic. The actual protected API, encrypted
evidence store and transactions run; only the vision provider is a fixture.
"""

import base64
from datetime import datetime, timedelta, timezone
import io
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
import pytest

from services.api.interactions import InteractionInput
from services.api.models import CameraContext, LiveEventInput, camera_provenance
from services.api.pilot_identity import add_site, add_user
from services.api.store import digest, encode
from test_interaction_case_link import pilot, link
from test_interactions import jpeg, sample, submit, wait_job
from test_live_events import sample as live_sample, write as write_live
from test_pilot_identity import BASE, PASSWORD, client_for


SOURCE = "12345678-1234-4234-8234-1234567890ab"


def camera(**changes):
    return {
        "source_id": SOURCE,
        "epoch": 3,
        "layout": "2x2",
        "camera_index": 2,
        "source_width": 192,
        "source_height": 128,
        "crop": {"x": 0, "y": 0.5, "width": 0.5, "height": 0.5},
        **changes,
    }


def frames(size):
    return [{"at_seconds": at, "jpeg_base64": base64.b64encode(jpeg(size=size, color=color)).decode()}
            for at, color in [(0, "blue"), (2, "green"), (4, "red")]]


def completed(client, context=None, **changes):
    context = camera() if context is None else context
    response = submit(client, sample(camera_context=context, **changes))
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "completed", job
    return job["result"]


@pytest.mark.parametrize("changes", [
    {"source_id": "not-a-uuid"}, {"source_id": SOURCE.upper()}, {"source_id": SOURCE.replace("-", "")},
    {"source_id": " " + SOURCE}, {"source_id": SOURCE + " "}, {"source_id": "\t" + SOURCE + "\n"},
    {"source_id": 123}, {"source_id": None}, {"epoch": True}, {"epoch": "3"},
    {"epoch": 3.0}, {"epoch": 0}, {"epoch": 2147483648},
    {"layout": "single"}, {"layout": "4x4"}, {"camera_index": True},
    {"camera_index": "1"}, {"camera_index": 1.0}, {"camera_index": -1},
    {"camera_index": 4}, {"layout": "3x2", "camera_index": 6},
    {"source_width": True}, {"source_width": "192"}, {"source_width": 192.0},
    {"source_width": 47}, {"source_height": 16385}, {"source_height": 47},
    {"source_width": 94},
    {"organisation_id": "other"}, {"site_id": "other"}, {"camera_id": "trusted-camera"},
])
def test_strict_context_fields(changes):
    with pytest.raises(ValidationError):
        CameraContext.model_validate(camera(**changes))


@pytest.mark.parametrize("field,value", [
    ("x", True), ("x", "0"), ("x", -0.01), ("y", 1.01),
    ("width", 0.049), ("height", 0.049), ("width", 1.01),
    ("height", "0.5"), ("width", False), ("x", float("nan")),
    ("height", float("inf")), ("y", float("-inf")),
    ("y", 0.50001), ("width", 0.24), ("height", 0.3),
    ("site_id", "other"),
])
def test_crop_bounds_strict_numbers_and_minimum_source_pixels(field, value):
    crop = {**camera()["crop"], field: value}
    with pytest.raises(ValidationError):
        CameraContext.model_validate(camera(crop=crop))


@pytest.mark.parametrize("layout,last", [("2x2", 3), ("3x2", 5), ("2x3", 5)])
def test_every_supported_layout_accepts_first_and_last_camera(layout, last):
    for index in (0, last):
        value = CameraContext.model_validate(camera(layout=layout, camera_index=index))
        assert value.camera_index == index
    with pytest.raises(ValidationError):
        CameraContext.model_validate(camera(layout=layout, camera_index=last + 1))


@pytest.mark.parametrize("width,height,crop,pixels,output", [
    (201, 129, {"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5}, (101, 65), (101, 65)),
    (1536, 129, {"x": 0, "y": 0, "width": 1, "height": 1}, (1536, 129), (768, 65)),
    (129, 1536, {"x": 0, "y": 0, "width": 1, "height": 1}, (129, 1536), (65, 768)),
    (1921, 1081, {"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5}, (961, 541), (768, 432)),
    (95, 95, {"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5}, (48, 48), (48, 48)),
    (200, 200, {"x": 0.5, "y": 0.5, "width": 0.5000005, "height": 0.5000005}, (100, 100), (100, 100)),
])
def test_browser_crop_and_resize_rounding_vectors(width, height, crop, pixels, output):
    # Golden values use interactionCropPixels' floor origin and Math.round
    # dimensions, particularly .5 ties where Python round() differs.
    value = CameraContext.model_validate(camera(source_width=width, source_height=height, crop=crop))
    assert value.pixel_size() == pixels
    assert value.jpeg_size() == output


@pytest.mark.parametrize("context,size", [
    (camera(), (96, 64)),
    (camera(source_width=201, source_height=129), (101, 65)),
    (camera(source_width=1536, source_height=129, crop={"x": 0, "y": 0, "width": 1, "height": 1}), (768, 65)),
])
def test_real_api_accepts_browser_sized_samples(pilot, context, size):
    app, _, client = pilot
    result = completed(client, context, frames=frames(size))
    assert result["camera_context"] == context
    assert app.state.interactions.provider.calls == 1
    for frame in result["frames"]:
        response = client.get(frame["url"])
        assert response.status_code == 200
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == size


def test_camera_provenance_survives_job_history_review_case_export_and_expiry(pilot, monkeypatch):
    app, initial, client = pilot
    context = camera()
    item = completed(client)
    expected = {"camera_context": context, "camera_id": f"{SOURCE}:3:2x2:2", "camera_label": "Camera 3 · 2x2"}
    assert item.items() >= expected.items()
    assert item["historical"] is True and item["validated"] is False
    with app.state.store.transaction() as conn:
        stored = app.state.store.get(conn, initial["user"], "interaction", item["id"])
        assert stored["camera_context"] == context
    assert client.get("/api/interactions").json()["items"][0].items() >= expected.items()
    reviewed = client.post(f"/api/interactions/{item['id']}/review", json={
        "expected_version": item["version"], "outcome": "UNCLEAR", "note": "Synthetic camera context review."})
    assert reviewed.status_code == 200
    assert reviewed.json().items() >= expected.items()
    linked = link(client, reviewed.json())
    assert linked.status_code == 201
    case = linked.json()["incident"]
    source = case["interaction_source"]
    assert source.items() >= expected.items()
    assert source["expires_at"] == item["expires_at"]
    exported = client.post(f"/api/incidents/{case['id']}/export", json={"expected_version": 1, "purpose": "Synthetic camera provenance verification"})
    assert exported.status_code == 200
    assert exported.json()["record"]["incident"]["interaction_source"] == source
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=2)
    monkeypatch.setattr("services.api.interactions.datetime", Later)
    expired = client.get(f"/api/incidents/{case['id']}/interaction-source").json()
    assert expired["evidence_status"] == "expired" and expired["frames"] == []
    assert expired["source"].items() >= expected.items()
    assert client.get(item["frames"][0]["url"]).status_code == 410


@pytest.mark.parametrize("kind,historical", [("RECORDED_VIDEO", True), ("SCREEN_CAPTURE", False), ("CAMERA", False)])
def test_camera_provenance_does_not_override_declared_source_history(pilot, kind, historical):
    _, _, client = pilot
    item = completed(client, source_kind=kind)
    assert item["source_kind"] == kind
    assert item["historical"] is historical
    assert item["camera_context"] == camera()


@pytest.mark.parametrize("bad_frame", [0, 1, 2])
def test_dimension_mismatch_is_rejected_before_inference_evidence_or_retry_receipt(pilot, bad_frame):
    app, _, client = pilot
    payload = sample(camera_context=camera())
    payload["frames"][bad_frame]["jpeg_base64"] = base64.b64encode(jpeg(size=(95, 64))).decode()
    key = str(uuid4())
    response = submit(client, payload, key)
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_FRAMES"
    assert app.state.interactions.provider.calls == 0
    assert list(app.state.interactions.evidence_root.iterdir()) == []
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE kind='interaction'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM idempotency WHERE key=?", (key,)).fetchone()[0] == 0
    payload["frames"] = frames((96, 64))
    accepted = submit(client, payload, key)
    assert accepted.status_code == 202
    assert wait_job(client, accepted.json()["id"])["status"] == "completed"


def test_nested_camera_fields_cannot_grant_scope_or_override_derived_identity(pilot):
    app, initial, manager = pilot
    other = add_site(app.state.store, initial["site"]["organisation_id"], "Synthetic Other Camera Branch")
    add_user(app.state.store, "camera.other@example.test", "Synthetic Camera Reviewer", PASSWORD, [other["id"]], "REVIEWER")
    outsider = client_for(app, "camera.other@example.test")
    context = camera(source_id=other["id"])
    item = completed(manager, context)
    # Even a valid source UUID equal to another branch's id is just provenance.
    assert outsider.get("/api/interactions").json()["items"] == []
    assert outsider.get(f"/api/interactions/jobs/{item['id']}").status_code == 404
    assert outsider.get(item["frames"][0]["url"]).status_code == 404
    for field in ["site_id", "organisation_id", "role", "camera_id", "camera_label"]:
        response = submit(manager, sample(camera_context={**context, field: "synthetic-untrusted-value"}))
        assert response.status_code == 422
        assert "synthetic-untrusted-value" not in response.text
    response = manager.post("/api/interactions/jobs", json=sample(camera_context=context), headers={"X-CSRF-Token": "wrong", "Idempotency-Key": str(uuid4())})
    assert response.status_code == 403
    response = manager.post("/api/interactions/jobs", json=sample(camera_context=context), headers={"X-AisleSignals-Site": other["id"], "Idempotency-Key": str(uuid4())})
    assert response.status_code == 409
    anonymous = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    assert submit(anonymous, sample(camera_context=context)).status_code == 401


def test_live_camera_metadata_is_scoped_and_duplicate_content_is_immutable(pilot):
    app, initial, client = pilot
    payload = live_sample(camera_context=camera())
    key = str(uuid4())
    first = write_live(client, payload, key)
    assert first.status_code == 200
    event = first.json()
    assert event["camera_context"] == payload["camera_context"]
    assert event["camera_id"] == f"{SOURCE}:3:2x2:2"
    assert event["camera_label"] == "Camera 3 · 2x2"
    assert write_live(client, payload, key).json() == event
    assert write_live(client, payload).json() == event
    changed = {**payload, "camera_context": camera(camera_index=1)}
    assert write_live(client, changed, key).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert write_live(client, changed).json()["error"]["code"] == "LIVE_EVENT_CONFLICT"
    assert client.get("/api/live-events").json() == [event]
    with app.state.store.transaction() as conn:
        assert app.state.store.get(conn, initial["user"], "live_event", event["id"])["camera_context"] == camera()
    assert client.get("/api/bootstrap").json()["incidents"] == []
    other = add_site(app.state.store, initial["site"]["organisation_id"], "Synthetic Other Live Branch")
    add_user(app.state.store, "live.other@example.test", "Synthetic Live Reviewer", PASSWORD, [other["id"]], "REVIEWER")
    outsider = client_for(app, "live.other@example.test")
    assert outsider.get("/api/live-events").json() == []
    # Copying the complete claimed camera identity cannot retrieve/acknowledge
    # another branch's event. A new actor/branch gets its own scoped event id.
    denied = outsider.post(f"/api/live-events/{event['id']}/acknowledge", json={}, headers={"Idempotency-Key": str(uuid4())})
    assert denied.status_code == 404
    other_event = write_live(outsider, payload)
    assert other_event.status_code == 200 and other_event.json()["id"] != event["id"]
    assert client.get("/api/live-events").json() == [event]
    assert client.post("/api/live-events", json=payload, headers={"X-CSRF-Token": "wrong", "Idempotency-Key": str(uuid4())}).status_code == 403
    assert write_live(client, {**payload, "camera_context": camera(site_id=other["id"])}).status_code == 422


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, "0.5"])
def test_bad_camera_crop_rejected_by_real_api_without_echo_or_writes(pilot, invalid):
    app, _, client = pilot
    context = camera(crop={**camera()["crop"], "width": invalid})
    response = client.post("/api/interactions/jobs", content=json.dumps(sample(camera_context=context)), headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid4())})
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_INPUT"
    assert app.state.interactions.provider.calls == 0
    assert list(app.state.interactions.evidence_root.iterdir()) == []


@pytest.mark.parametrize("omitted", [True, False])
def test_existing_context_cannot_be_removed_by_duplicate_event_with_new_key(pilot, omitted):
    _, _, client = pilot
    payload = live_sample(camera_context=camera())
    first = write_live(client, payload).json()
    changed = {**payload}
    if omitted:
        changed.pop("camera_context")
    else:
        changed["camera_context"] = None
    response = write_live(client, changed)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LIVE_EVENT_CONFLICT"
    assert client.get("/api/live-events").json() == [first]


@pytest.mark.parametrize("kind", ["interaction", "live"])
def test_legacy_absent_and_null_context_preserve_pre_upgrade_retry_hashes(pilot, kind):
    app, initial, client = pilot
    key = str(uuid4())
    scope = {key: initial["user"][key] for key in ("organisation_id", "site_id")}
    if kind == "interaction":
        payload = sample()
        first = submit(client, payload, key)
        assert first.status_code == 202
        item = wait_job(client, first.json()["id"])["result"]
        assert not any(key in item for key in ["camera_context", "camera_id", "camera_label"])
        old_payload = InteractionInput.model_validate(payload).model_dump(mode="json")
        old_payload.pop("camera_context")
        old_hash = digest(encode(old_payload))
        route = f"/api/interactions/jobs/{scope['organisation_id']}/{scope['site_id']}"
        write = submit
    else:
        payload = live_sample()
        first = write_live(client, payload, key)
        assert first.status_code == 200
        old_payload = LiveEventInput.model_validate(payload).model_dump(mode="json")
        old_payload.pop("camera_context")
        old_hash = digest(encode({"payload": {**old_payload, **scope}, **scope}))
        route = "/api/live-events"
        write = write_live
    with app.state.store.transaction() as conn:
        conn.execute("UPDATE idempotency SET payload_hash=? WHERE actor_id=? AND route=? AND key=?", (old_hash, initial["user"]["id"], route, key))
    for body in (payload, {**payload, "camera_context": None}):
        replay = write(client, body, key)
        assert replay.status_code == first.status_code, replay.text
        assert replay.json()["id"] == first.json()["id"]
    changed = write(client, {**payload, "camera_context": camera()}, key)
    assert changed.status_code == 409
    assert camera_provenance({}) == camera_provenance({"camera_context": None}) == {}
