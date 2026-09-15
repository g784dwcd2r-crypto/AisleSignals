"""Real scoped pilot case workflow; only model observations use a synthetic fixture."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.pilot_identity import add_site, add_user, disable_user, initialise
from test_interactions import MockProvider, complete, submit, wait_job
from test_pilot_identity import BASE, EMAIL, PASSWORD, client_for


@pytest.fixture
def pilot(tmp_path):
    app = create_app(tmp_path / "case-link.db", tmp_path / "web", mode="pilot")
    initial = initialise(app.state.store, "Synthetic Case Group", "Synthetic Case Branch", EMAIL, "Synthetic Manager", PASSWORD)
    app.state.interactions.provider = MockProvider()
    try:
        yield app, initial, client_for(app)
    finally:
        app.state.interactions.provider.release.set()
        app.state.interactions.close()


def reviewed(client, outcome="USEFUL"):
    item = complete(client)
    result = client.post(f"/api/interactions/{item['id']}/review", json={"expected_version": item["version"], "outcome": outcome, "note": "Synthetic staff review, not an allegation."})
    assert result.status_code == 200, result.text
    return result.json()


def link(client, item, key=None, **changes):
    return client.post(f"/api/interactions/{item['id']}/case", headers={"Idempotency-Key": key or str(uuid4())},
                       json={"expected_version": item["version"], "title": "Staff follow-up", "notes": "Staff will check the source recording. No loss has been established.", **changes})


def amend(pilot, item, **changes):
    app, initial, _ = pilot
    with app.state.store.transaction() as conn:
        stored = app.state.store.get(conn, initial["user"], "interaction", item["id"])
        stored.update(changes)
        app.state.store.put(conn, initial["user"], "interaction", stored)


@pytest.mark.parametrize("outcome", ["USEFUL", "NORMAL_SHOPPING", "UNCLEAR"])
def test_reviewed_observation_creates_unassessed_case_and_preserves_provenance(pilot, outcome):
    app, initial, client = pilot
    item = reviewed(client, outcome)
    assert client.get("/api/bootstrap").json()["incidents"] == []
    response = link(client, item)
    assert response.status_code == 201, response.text
    case = response.json()["incident"]
    assert case["classification"] == "UNASSESSED" and case["outcome"] == "UNRESOLVED"
    assert case["loss_cents"] is None and case["candidate_id"] is None
    source = case["interaction_source"]
    assert source["id"] == item["id"] and source["version"] == item["version"]
    assert source["source_label"] == item["source_label"] and source["run_id"] == item["run_id"]
    assert source["observation"]["validated"] is False
    assert source["review"]["outcome"] == outcome
    assert source["expires_at"] == item["expires_at"]
    assert all(len(frame["sha256"]) == 64 for frame in source["frames"])
    assert response.json()["interaction"]["incident_id"] == case["id"]
    result = client.get(f"/api/incidents/{case['id']}/interaction-source").json()
    assert result["evidence_status"] == "available"
    assert all(
        frame["url"].startswith(f"/api/incidents/{case['id']}/interaction-source/frames/")
        for frame in result["frames"]
    )
    responses = [client.get(frame["url"]) for frame in result["frames"]]
    assert [response.status_code for response in responses] == [200] * 3
    assert all(response.headers["content-type"] == "image/jpeg" for response in responses)
    assert all(response.headers["cache-control"] == "no-store" for response in responses)
    assert client.get(f"{result['frames'][0]['url']}?token=forbidden").status_code == 400
    assert client.get(f"/api/incidents/{case['id']}/interaction-source/frames/3").status_code == 404
    edited = client.patch(f"/api/incidents/{case['id']}", json={"expected_version": 1, "classification": "BENIGN", "outcome": "NO_LOSS_ESTABLISHED"})
    assert edited.status_code == 200 and edited.json()["interaction_source"] == source
    exported = client.post(f"/api/incidents/{case['id']}/export", json={"expected_version": 2, "purpose": "Synthetic review of provenance"})
    assert exported.status_code == 200
    assert exported.json()["record"]["incident"]["interaction_source"] == source


def test_requires_completed_staff_review_and_latest_version(pilot):
    _, _, client = pilot
    item = complete(client)
    assert link(client, item).json()["error"]["code"] == "INTERACTION_REVIEW_REQUIRED"
    reviewed_item = client.post(f"/api/interactions/{item['id']}/review", json={"expected_version": 1, "outcome": "USEFUL"}).json()
    assert link(client, item).json()["error"]["code"] == "VERSION_CONFLICT"
    assert link(client, reviewed_item, expected_version=True).status_code == 422
    assert link(client, reviewed_item, organisation_id=str(uuid4())).status_code == 422
    assert client.get("/api/bootstrap").json()["incidents"] == []


def test_pending_job_cannot_create_case(pilot):
    app, _, client = pilot
    app.state.interactions.provider.release.clear()
    response = submit(client)
    item = {"id": response.json()["id"], "version": 1}
    assert link(client, item).json()["error"]["code"] == "INTERACTION_NOT_READY"
    app.state.interactions.provider.release.set()
    wait_job(client, item["id"])


@pytest.mark.parametrize("damage", ["expired", "missing", "tampered"])
def test_unavailable_evidence_cannot_create_or_replay_case(pilot, damage):
    app, _, client = pilot
    item = reviewed(client)
    if damage == "expired":
        amend(pilot, item, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    else:
        path = app.state.interactions.directory(item["id"]) / "0.jpg"
        if damage == "missing": path.unlink()
        else: path.write_bytes(b"synthetic corruption")
    response = link(client, item)
    assert response.status_code == (410 if damage == "expired" else 404)
    assert client.get("/api/bootstrap").json()["incidents"] == []


def test_retry_conflicts_and_concurrent_requests_create_exactly_one_case(pilot):
    app, _, client = pilot
    item = reviewed(client)
    key = str(uuid4())
    first = link(client, item, key)
    assert first.status_code == 201
    assert link(client, item, key).json() == first.json()
    assert link(client, item, key, title="Different title").json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert link(client, item).json()["error"]["code"] == "INTERACTION_ALREADY_LINKED"
    another = reviewed(client)
    peers = [client_for(app), client_for(app)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda peer: link(peer, another), peers))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(client.get("/api/bootstrap").json()["incidents"]) == 2
    amend(pilot, item, expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    assert link(client, item, key).status_code == 410


def test_reviewer_permission_branch_scope_csrf_and_revocation(pilot):
    app, initial, manager = pilot
    reviewer_email = "synthetic.reviewer@example.test"
    add_user(app.state.store, reviewer_email, "Synthetic Reviewer", PASSWORD, [initial["site"]["id"]], "REVIEWER")
    reviewer = client_for(app, reviewer_email)
    item = reviewed(reviewer)
    invalid_csrf = reviewer.post(f"/api/interactions/{item['id']}/case", headers={"X-CSRF-Token": "invalid"}, json={"expected_version": 2, "title": "Test", "notes": "Synthetic notes"})
    assert invalid_csrf.status_code == 403
    wrong_site = reviewer.post(f"/api/interactions/{item['id']}/case", headers={"X-AisleSignals-Site": str(uuid4())}, json={"expected_version": 2, "title": "Test", "notes": "Synthetic notes"})
    assert wrong_site.status_code == 409
    case = link(reviewer, item).json()["incident"]
    other = add_site(app.state.store, initial["site"]["organisation_id"], "Synthetic Other Branch")
    add_user(app.state.store, "other.reviewer@example.test", "Other Reviewer", PASSWORD, [other["id"]], "REVIEWER")
    outsider = client_for(app, "other.reviewer@example.test")
    assert link(outsider, item).status_code == 404
    assert outsider.get(f"/api/incidents/{case['id']}/interaction-source").status_code == 404
    assert outsider.get(f"/api/incidents/{case['id']}/interaction-source/frames/0").status_code == 404
    assert outsider.get(item["frames"][0]["url"]).status_code == 404
    disable_user(app.state.store, reviewer_email)
    assert link(reviewer, item).status_code == 401
    anonymous = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    assert link(anonymous, item).status_code == 401


def test_link_does_not_extend_retention_or_recreate_deleted_frames(pilot):
    _, _, client = pilot
    item = reviewed(client)
    case = link(client, item).json()["incident"]
    assert client.delete(f"/api/interactions/{item['id']}").status_code == 200
    source = client.get(f"/api/incidents/{case['id']}/interaction-source").json()
    assert source["evidence_status"] == "deleted" and source["frames"] == []
    assert source["source"] == case["interaction_source"]
    assert client.get(item["frames"][0]["url"]).status_code == 404
    assert client.get(f"/api/incidents/{case['id']}/interaction-source/frames/0").status_code == 404
    assert link(client, item).status_code == 404


def test_case_source_and_image_access_expire_at_the_original_deadline(pilot, monkeypatch):
    _, _, client = pilot
    item = reviewed(client)
    case = link(client, item).json()["incident"]
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=2)
    monkeypatch.setattr("services.api.interactions.datetime", Later)
    source = client.get(f"/api/incidents/{case['id']}/interaction-source").json()
    assert source["evidence_status"] == "expired" and source["frames"] == []
    assert source["source"]["expires_at"] == item["expires_at"]
    assert client.get(item["frames"][0]["url"]).status_code == 410
    assert client.get(f"/api/incidents/{case['id']}/interaction-source/frames/0").status_code == 410
    assert link(client, item).status_code == 410


def test_linked_evidence_damage_is_reported_without_discarding_provenance(pilot):
    app, _, client = pilot
    item = reviewed(client)
    case = link(client, item).json()["incident"]
    (app.state.interactions.directory(item["id"]) / "0.jpg").write_bytes(b"Synthetic corrupted evidence")
    source = client.get(f"/api/incidents/{case['id']}/interaction-source").json()
    assert source["evidence_status"] == "unavailable" and source["frames"] == []
    assert source["source"] == case["interaction_source"]


def test_link_failure_rolls_back_case_and_retry_receipt(pilot, monkeypatch):
    app, _, client = pilot
    item = reviewed(client)
    key = str(uuid4())
    original = app.state.store.put
    def fail(conn, user, kind, value):
        if kind == "interaction" and value.get("incident_id"):
            raise RuntimeError("Synthetic write failure")
        return original(conn, user, kind, value)
    with monkeypatch.context() as change:
        change.setattr(app.state.store, "put", fail)
        with pytest.raises(RuntimeError, match="Synthetic write failure"):
            link(client, item, key)
    assert client.get("/api/bootstrap").json()["incidents"] == []
    assert link(client, item, key).status_code == 201
