"""Pose metadata stays scoped, bounded, idempotent and separate from incidents."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app


BASE = "http://127.0.0.1:8765"
PATH = "/api/live-events"
DETECTED_AT = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "live.db", tmp_path / "web")


def sign_in(app, email="manager@harbour.demo"):
    client = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    response = client.post(
        "/api/login", json={"email": email, "password": "AisleDemo!2026"}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def sample(**changes):
    return {
        "run_id": "a94c30aa-bd3c-400c-a204-66bdcb8b9e15",
        "event_id": "a6d76357-36ed-4426-9114-2c42e3889a1c",
        "source_kind": "SCREEN_CAPTURE",
        "source_label": "Synthetic front-shop view",
        "event_code": "REPEATED_HAND_TO_WAIST",
        "track_id": 1,
        "source_time_seconds": 4.5,
        "detected_at": DETECTED_AT,
        "model_version": "mediapipe-pose-lite-f16-v1",
        "rule_version": "pose-rules-v1",
        "sound_requested": False,
        **changes,
    }


def write(client, body=None, key=None):
    return client.post(
        PATH,
        json=sample() if body is None else body,
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def acknowledge(client, event_id, key=None, **kwargs):
    return client.post(
        f"{PATH}/{event_id}/acknowledge",
        headers={"Idempotency-Key": key or str(uuid4()), "Content-Type": "application/json"},
        **kwargs,
    )


def test_persistent_pose_metadata_server_descriptions_and_no_incident_side_effects(app):
    client = sign_in(app)
    before = client.get("/api/bootstrap").json()
    started = datetime.now(timezone.utc)
    response = write(client, sample(sound_requested=True))
    assert response.status_code == 200, response.text
    event = response.json()
    assert event["label"] == "Repeated hand-to-waist movement"
    assert "does not establish concealment or theft" in event["detail"]
    assert event["acknowledged_at"] is None
    assert event["acknowledged_by"] is None
    assert datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")) >= started
    assert set(event) == set(sample()) | {
        "id", "label", "detail", "created_at", "acknowledged_at", "acknowledged_by"
    }
    after = client.get("/api/bootstrap").json()
    for kind in ("candidates", "incidents", "assistance"):
        assert before[kind] == after[kind]
    audit = [a for a in after["audit"] if a["action"] == "LIVE_POSE_EVENT_LOGGED"]
    assert len(audit) == 1
    assert audit[0]["resource_id"] == event["id"]
    assert "client declaration" in audit[0]["detail"]
    restarted = create_app(app.state.store.path)
    assert sign_in(restarted).get(PATH).json() == [event]


def test_updated_rules_preserve_versioned_history_and_reject_unknown_rule_versions(app):
    client = sign_in(app)
    old = write(client, sample()).json()
    current = write(client, sample(event_id=str(uuid4()), rule_version="pose-rules-v2",
                                  source_label="Synthetic view · Camera 3 · 3x2 grid"))
    assert current.status_code == 200
    assert current.json()["rule_version"] == "pose-rules-v2"
    assert "Camera 3" in current.json()["source_label"]
    assert old["rule_version"] == "pose-rules-v1"
    assert write(client, sample(event_id=str(uuid4()), rule_version="invented-rules")).status_code == 422
    versions = {item["rule_version"] for item in client.get(PATH).json()}
    assert versions == {"pose-rules-v1", "pose-rules-v2"}


def test_auth_csrf_origin_and_idempotency_required_for_create_and_ack(app):
    anonymous = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    assert anonymous.get(PATH).status_code == 401
    assert write(anonymous).status_code == 401
    client = sign_in(app)
    event = write(client).json()
    assert acknowledge(anonymous, event["id"]).status_code == 401
    csrf = client.headers.pop("X-CSRF-Token")
    assert write(client).status_code == 403
    assert acknowledge(client, event["id"]).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post(PATH, json=sample()).status_code == 400
    assert write(client, key="invalid key").status_code == 400
    assert client.post(f"{PATH}/{event['id']}/acknowledge", json={}).status_code == 400
    origin = client.headers.pop("Origin")
    assert write(client).status_code == 403
    assert acknowledge(client, event["id"]).status_code == 403
    client.headers["Origin"] = origin
    assert client.get(PATH).json() == [event]


def test_duplicate_identity_and_changed_payload_conflicts_preserve_original(app):
    client = sign_in(app)
    first = write(client, key="first-key").json()
    assert write(client, key="first-key").json() == first
    assert write(client, key="different-key").json() == first
    changed = sample(track_id=2)
    same_key = write(client, changed, key="first-key")
    assert same_key.status_code == 409
    assert same_key.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    other_key = write(client, changed, key="third-key")
    assert other_key.status_code == 409
    assert other_key.json()["error"]["code"] == "LIVE_EVENT_CONFLICT"
    assert client.get(PATH).json() == [first]
    audit = client.get("/api/bootstrap").json()["audit"]
    assert sum(a["action"] == "LIVE_POSE_EVENT_LOGGED" for a in audit) == 1


def test_concurrent_same_actor_retry_creates_one_event(app):
    clients = [sign_in(app), sign_in(app)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(write, clients))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert clients[0].get(PATH).json() == [responses[0].json()]


def test_branch_reads_acknowledgements_and_actor_identity_are_isolated(app):
    harbour = sign_in(app)
    reviewer = sign_in(app, "reviewer@harbour.demo")
    liffey = sign_in(app, "manager@liffey.demo")
    original = write(harbour).json()
    assert reviewer.get(PATH).json() == [original]
    assert liffey.get(PATH).json() == []
    assert acknowledge(liffey, original["id"]).status_code == 404
    other_actor = write(reviewer).json()
    other_branch = write(liffey).json()
    assert len({original["id"], other_actor["id"], other_branch["id"]}) == 3
    assert liffey.get(PATH, params={"site_id": "guessed", "id": original["id"]}).json() == [other_branch]
    assert {r["id"] for r in harbour.get(PATH).json()} == {original["id"], other_actor["id"]}
    assert liffey.get(PATH + "/" + original["id"]).status_code == 404


def test_changed_site_membership_cannot_replay_old_branch_response_or_ack(app):
    client = sign_in(app)
    user = client.get("/api/bootstrap").json()["user"]
    original = write(client, key="stable-create").json()
    assert acknowledge(client, original["id"], key="stable-ack").status_code == 200
    with app.state.store.transaction() as conn:
        conn.execute(
            "UPDATE users SET site_id=? WHERE id=?",
            ("another-site-same-company", user["id"]),
        )
    assert client.get(PATH).json() == []
    assert write(client, key="stable-create").status_code == 409
    assert acknowledge(client, original["id"], key="stable-ack").status_code == 404
    second = write(client, key="new-branch")
    assert second.status_code == 200, second.text
    assert second.json()["id"] != original["id"]
    assert client.get(PATH).json() == [second.json()]


def test_acknowledgement_persists_is_idempotent_and_keeps_first_staff_member(app):
    manager = sign_in(app)
    reviewer = sign_in(app, "reviewer@harbour.demo")
    original = write(manager).json()
    response = acknowledge(reviewer, original["id"], key="ack", json={})
    assert response.status_code == 200, response.text
    event = response.json()
    assert event["acknowledged_at"] is not None
    assert event["acknowledged_by"] == reviewer.get("/api/session").json()["user"]["name"]
    assert acknowledge(reviewer, original["id"], key="ack").json() == event
    assert acknowledge(manager, original["id"]).json() == event
    assert write(manager).json() == event
    assert manager.get(PATH).json() == [event]
    for field in sample():
        assert event[field] == original[field]
    assert event["created_at"] == original["created_at"]
    audit = manager.get("/api/bootstrap").json()["audit"]
    assert sum(a["action"] == "LIVE_POSE_EVENT_ACKNOWLEDGED" for a in audit) == 1
    restarted = create_app(app.state.store.path)
    assert sign_in(restarted).get(PATH).json() == [event]
    assert acknowledge(manager, original["id"], json={"acknowledged_by": "someone"}).status_code == 422


def test_strict_input_rejects_identity_media_versions_controls_and_invalid_numbers(app):
    client = sign_in(app)
    invalid = [
        {"run_id": "not-a-uuid"}, {"event_id": ""}, {"run_id": 123},
        {"event_id": "a" * 36}, {"source_kind": "YOUTUBE"},
        {"source_label": ""}, {"source_label": "x" * 121}, {"source_label": 7},
        {"source_label": "camera\nview"}, {"source_label": "camera\x00view"},
        {"source_label": "<img src=x>"}, {"source_label": "camera\u202eview"},
        {"event_code": "THEFT"}, {"track_id": 0}, {"track_id": -1},
        {"track_id": 1_000_001}, {"track_id": True}, {"track_id": "1"},
        {"track_id": 1.5}, {"source_time_seconds": -0.1},
        {"source_time_seconds": 43_201}, {"source_time_seconds": "1.5"},
        {"source_time_seconds": True}, {"sound_requested": "true"},
        {"sound_requested": 1}, {"model_version": "latest"},
        {"rule_version": "pose-rules-v3"}, {"detected_at": "2026-01-01T00:00:00"},
        {"detected_at": 12345}, {"detected_at": "12345"},
        {"organisation_id": "other"}, {"site_id": "other"},
        {"label": "Theft"}, {"detail": "private narrative"},
        {"person_name": "someone"}, {"media": "data:video/mp4;base64,xx"},
        {"created_at": DETECTED_AT}, {"acknowledged_by": "someone"},
    ]
    for change in invalid:
        response = write(client, sample(**change))
        assert response.status_code == 422, (change, response.text)
        assert response.json()["error"]["code"] == "INVALID_INPUT"
    for value in (float("nan"), float("inf"), float("-inf")):
        response = client.post(
            PATH,
            content=json.dumps(sample(source_time_seconds=value)),
            headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 422, response.text
    assert client.get(PATH).json() == []


def test_observation_time_window_rejects_stale_or_future_clock_and_accepts_offsets(app):
    client = sign_in(app)
    stamp = datetime.now(timezone.utc)
    for when in (stamp - timedelta(hours=24, minutes=1), stamp + timedelta(minutes=2)):
        assert write(client, sample(detected_at=when.isoformat())).status_code == 422
    for when in (stamp - timedelta(hours=23), stamp + timedelta(seconds=30)):
        body = sample(event_id=str(uuid4()), detected_at=when.astimezone(timezone(timedelta(hours=1))).isoformat())
        response = write(client, body)
        assert response.status_code == 200, response.text


def test_recorded_and_live_sources_remain_explicit_and_time_bounded(app):
    client = sign_in(app)
    for kind, upper_bound in (("RECORDED_VIDEO", 600), ("CAMERA", 43_200), ("SCREEN_CAPTURE", 43_200)):
        body = sample(event_id=str(uuid4()), source_kind=kind, source_time_seconds=upper_bound,
                      event_code="RESTRICTED_ZONE_ENTRY", track_id=1_000_000)
        response = write(client, body)
        assert response.status_code == 200, response.text
        assert response.json()["source_kind"] == kind
        assert response.json()["label"] == "Restricted zone entry"
        assert "does not establish wrongdoing" in response.json()["detail"]
        assert write(client, {**body, "source_time_seconds": upper_bound + 1}).status_code == 422
    assert client.get("/api/playback-events").json() == []
    assert len(client.get(PATH).json()) == 3


def test_latest_hundred_limit_preserves_older_rows_and_order_on_ack(app):
    client = sign_in(app)
    events = []
    for _ in range(102):
        response = write(client, sample(event_id=str(uuid4())))
        assert response.status_code == 200, response.text
        events.append(response.json())
    assert acknowledge(client, events[0]["id"]).status_code == 200
    recent = client.get(PATH).json()
    assert len(recent) == 100
    assert [r["id"] for r in recent] == [r["id"] for r in reversed(events[2:])]
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE kind='live_event'").fetchone()[0] == 102


def test_request_size_limit_applies_to_live_metadata(app):
    client = sign_in(app)
    response = client.post(
        PATH,
        content=json.dumps(sample(source_label="x" * 65536)),
        headers={"Content-Type": "application/json", "Idempotency-Key": "large"},
    )
    assert response.status_code == 413
    assert client.get(PATH).json() == []
