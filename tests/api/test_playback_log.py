"""Recorded playback logs remain scoped, immutable and separate from incidents."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app


BASE = "http://127.0.0.1:8765"
PATH = "/api/playback-events"


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "playback.db", tmp_path / "web")


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
        "run_id": "synthetic-run-001",
        "event_index": 0,
        "category": "SUSTAINED_VISUAL_ACTIVITY",
        "video_start_seconds": 1.5,
        "video_end_seconds": 4,
        "peak_changed_ratio": 0.25,
        "alarm_status": "MUTED",
        **changes,
    }


def write(client, body=None, key=None):
    return client.post(
        PATH,
        json=sample() if body is None else body,
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def test_log_survives_restart_with_server_provenance_and_no_live_side_effects(app):
    client = sign_in(app)
    before = client.get("/api/bootstrap").json()
    start = datetime.now(timezone.utc)
    response = write(client, sample(alarm_status="SOUND_REQUESTED"))
    assert response.status_code == 200, response.text
    event = response.json()
    assert event["source"] == "RECORDED_PLAYBACK_TEST"
    assert event["provenance"] == "RULE_BASED_VISUAL_CHANGE_V1"
    assert event["recorded_by"] == before["user"]["name"]
    assert event["created_at"] == event["recorded_at"]
    assert datetime.fromisoformat(event["recorded_at"].replace("Z", "+00:00")) >= start
    assert set(event) == set(sample()) | {
        "id", "source", "provenance", "created_at", "recorded_at", "recorded_by"
    }
    after = client.get("/api/bootstrap").json()
    for kind in ("candidates", "incidents", "assistance"):
        assert before[kind] == after[kind]
    audit = [a for a in after["audit"] if a["action"] == "PLAYBACK_TEST_EVENT_LOGGED"]
    assert len(audit) == 1
    assert audit[0]["resource_id"] == event["id"]
    restarted = create_app(app.state.store.path)
    assert sign_in(restarted).get(PATH).json() == [event]


def test_auth_csrf_origin_and_http_idempotency_are_required(app):
    anonymous = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    assert anonymous.get(PATH).status_code == 401
    assert write(anonymous).status_code == 401
    client = sign_in(app)
    csrf = client.headers.pop("X-CSRF-Token")
    assert write(client).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post(PATH, json=sample()).status_code == 400
    assert write(client, key="invalid key").status_code == 400
    origin = client.headers.pop("Origin")
    assert write(client).status_code == 403
    client.headers["Origin"] = origin
    assert client.get(PATH).json() == []


def test_duplicate_event_and_changed_payload_conflicts_preserve_original(app):
    client = sign_in(app)
    first = write(client, key="first-key").json()
    assert write(client, key="first-key").json() == first
    assert write(client, key="different-key").json() == first
    changed = sample(peak_changed_ratio=0.5)
    http_conflict = write(client, changed, key="first-key")
    assert http_conflict.status_code == 409
    assert http_conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    event_conflict = write(client, changed, key="third-key")
    assert event_conflict.status_code == 409
    assert event_conflict.json()["error"]["code"] == "PLAYBACK_EVENT_CONFLICT"
    assert client.get(PATH).json() == [first]
    audit = client.get("/api/bootstrap").json()["audit"]
    assert sum(a["action"] == "PLAYBACK_TEST_EVENT_LOGGED" for a in audit) == 1


def test_concurrent_same_actor_event_retries_log_once(app):
    clients = [sign_in(app), sign_in(app)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(write, clients))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert clients[0].get(PATH).json() == [responses[0].json()]


def test_branch_listing_and_actor_event_identity_are_isolated(app):
    harbour = sign_in(app)
    reviewer = sign_in(app, "reviewer@harbour.demo")
    liffey = sign_in(app, "manager@liffey.demo")
    original = write(harbour).json()
    assert reviewer.get(PATH).json() == [original]
    assert liffey.get(PATH).json() == []
    other_actor = write(reviewer).json()
    assert other_actor["id"] != original["id"]
    other_branch = write(liffey).json()
    assert other_branch["id"] not in {original["id"], other_actor["id"]}
    assert liffey.get(PATH).json() == [other_branch]
    assert {r["id"] for r in harbour.get(PATH).json()} == {
        original["id"], other_actor["id"]
    }
    # Scope cannot be overridden with a guessed entity ID or tenant query.
    assert liffey.get(PATH, params={"id": original["id"]}).json() == [other_branch]
    assert liffey.get(PATH + "/" + original["id"]).status_code == 404


def test_same_organisation_site_change_cannot_read_old_records_or_retry_response(app):
    client = sign_in(app)
    before = client.get("/api/bootstrap").json()
    first = write(client, key="stable-key").json()
    # Model a staff membership moving to another branch of the same company.
    # Reads and retry caches must honour the newly resolved session authority.
    with app.state.store.transaction() as conn:
        conn.execute(
            "UPDATE users SET site_id=? WHERE id=?",
            ("different-branch-same-organisation", before["user"]["id"]),
        )
    assert client.get(PATH).json() == []
    assert write(client, key="stable-key").status_code == 409
    second = write(client, key="new-branch-key")
    assert second.status_code == 200
    assert second.json()["id"] != first["id"]
    assert client.get(PATH).json() == [second.json()]


def test_strict_bounded_metadata_rejects_identity_media_and_invalid_numbers(app):
    client = sign_in(app)
    invalid = [
        {"run_id": ""}, {"run_id": "../video"}, {"run_id": "r" * 101},
        {"run_id": 123}, {"event_index": -1}, {"event_index": 100},
        {"event_index": True}, {"event_index": "1"}, {"event_index": 1.5},
        {"category": "THEFT"}, {"alarm_status": "DELIVERED"},
        {"video_start_seconds": -1}, {"video_end_seconds": 601},
        {"video_start_seconds": 5, "video_end_seconds": 4},
        {"video_start_seconds": "1.5"}, {"video_end_seconds": True},
        {"peak_changed_ratio": -0.1}, {"peak_changed_ratio": 1.01},
        {"peak_changed_ratio": "0.5"}, {"peak_changed_ratio": True},
        {"filename": "private-recording.mp4"}, {"media": "data:video/mp4;base64,xxx"},
        {"notes": "Do not save person details"}, {"organisation_id": "other"},
        {"site_id": "other"}, {"recorded_at": "2000-01-01T00:00:00Z"},
        {"provenance": "THEFT_AI"},
    ]
    for change in invalid:
        response = write(client, sample(**change))
        assert response.status_code == 422, (change, response.text)
        assert response.json()["error"]["code"] == "INVALID_INPUT"
    for field in ("video_start_seconds", "video_end_seconds", "peak_changed_ratio"):
        for value in (float("nan"), float("inf"), float("-inf")):
            response = client.post(
                PATH,
                content=json.dumps(sample(**{field: value})),
                headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid4())},
            )
            assert response.status_code == 422, response.text
    assert client.get(PATH).json() == []


def test_boundary_values_and_category_status_enums_round_trip(app):
    client = sign_in(app)
    for index, (category, status) in enumerate([
        ("SUSTAINED_VISUAL_ACTIVITY", "MUTED"),
        ("EXTENDED_VISUAL_ACTIVITY", "BLOCKED"),
        ("LARGE_SCENE_CHANGE", "SOUND_REQUESTED"),
    ]):
        body = sample(
            event_index=index,
            category=category,
            alarm_status=status,
            video_start_seconds=0 if index == 0 else 600,
            video_end_seconds=600,
            peak_changed_ratio=0 if index == 0 else 1,
        )
        response = write(client, body)
        assert response.status_code == 200, response.text
        assert all(response.json()[key] == value for key, value in body.items())


def test_latest_hundred_limit_and_hundred_unique_events_per_run(app):
    client = sign_in(app)
    events = []
    for index in range(100):
        response = write(client, sample(event_index=index))
        assert response.status_code == 200, response.text
        events.append(response.json())
    assert write(client, sample(event_index=100)).status_code == 422
    assert write(client, sample(event_index=0)).json() == events[0]
    for index in range(2):
        response = write(client, sample(run_id="another-run", event_index=index))
        assert response.status_code == 200
        events.append(response.json())
    recent = client.get(PATH).json()
    assert len(recent) == 100
    assert [r["id"] for r in recent] == [r["id"] for r in reversed(events[2:])]
    with app.state.store.transaction() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM entities WHERE kind='playback_event'"
        ).fetchone()[0] == 102


def test_existing_request_size_boundary_applies_to_test_logs(app):
    client = sign_in(app)
    response = client.post(
        PATH,
        content=json.dumps(sample(run_id="x" * 65536)),
        headers={"Content-Type": "application/json", "Idempotency-Key": "large"},
    )
    assert response.status_code == 413
    assert client.get(PATH).json() == []


def test_csp_only_allows_the_explicit_youtube_reference_frame(app):
    response = TestClient(app, base_url=BASE).get("/api/health")
    policy = response.headers["Content-Security-Policy"]
    assert "frame-src https://www.youtube-nocookie.com;" in policy
    assert "connect-src 'self';" in policy
    assert "media-src 'self' blob:;" in policy
    assert "frame-ancestors 'none';" in policy
    assert response.headers["Referrer-Policy"] == "no-referrer"
