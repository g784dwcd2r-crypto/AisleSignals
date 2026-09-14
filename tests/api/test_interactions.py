"""Synthetic unit fixtures exercise real job/auth/evidence boundaries.

The provider is explicitly mocked in workflow tests. Separate adapter tests verify
wire schemas; none of these tests claims real pharmacy detection accuracy.
"""

import base64
import io
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from services.api.app import create_app
from services.api.interaction_vision import (
    ModelObservation,
    VisionError,
    VisionProvider,
    public_observation,
)
from services.api.interactions import ACTIVE_JOBS, ACTIVE_LOCK, InteractionInput
from services.api.store import encode

BASE = "http://127.0.0.1:8765"


def jpeg(size=(96, 64), color="blue"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "JPEG")
    return output.getvalue()


def sample(**changes):
    return {
        "run_id": str(uuid4()),
        "source_kind": "RECORDED_VIDEO",
        "source_label": "Synthetic unit-test shapes",
        "camera_calibration": {
            "schema_version": "1.0",
            "entrance_zone_confirmed": True,
            "exit_zone_confirmed": True,
            "cashier_zone_confirmed": True,
            "shelf_zones_confirmed": True,
        },
        "frames": [
            {
                "at_seconds": at,
                "jpeg_base64": base64.b64encode(jpeg(color=color)).decode(),
            }
            for at, color in [(0, "blue"), (2, "green"), (4, "red")]
        ],
        **changes,
    }


def observation(**changes):
    return {
        "action": "POSSIBLE_CONCEALMENT",
        "visibility": "clear",
        "person_visible": True,
        "product_visible": True,
        "sequence_observed": True,
        "evidence_frame_indices": [0, 2],
        **changes,
    }


class MockProvider:
    """Intentional synthetic provider fixture, never used by application code."""

    model = "mock-vision-for-unit-test"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.error = None
        self.calls = 0

    def status(self):
        return {
            "ready": True,
            "model": self.model,
            "mode": "experimental",
            "message": "Explicitly mocked in unit test.",
        }

    def analyze(self, frames):
        self.calls += 1
        self.started.set()
        assert self.release.wait(10)
        if self.error:
            raise self.error
        return {
            **public_observation(ModelObservation(**observation()), len(frames)),
            "model": self.model,
            "inference_ms": 10,
            "validated": False,
            "provenance": "experimental_local_vlm",
            "prompt_version": "synthetic-test",
        }


@pytest.fixture
def app(tmp_path, monkeypatch):
    for key in (
        "AISLESIGNALS_VISION_URL",
        "AISLESIGNALS_VISION_MODEL",
        "AISLESIGNALS_VISION_TOKEN",
        "AISLESIGNALS_VISION_TOKEN_FILE",
        "AISLESIGNALS_VISION_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    app = create_app(tmp_path / "interactions.db", tmp_path / "web")
    app.state.interactions.provider = MockProvider()
    yield app
    app.state.interactions.provider.release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with ACTIVE_LOCK:
            active = any(
                service is app.state.interactions for service, _ in ACTIVE_JOBS.values()
            )
        if not active:
            break
        time.sleep(0.01)
    app.state.interactions.close()


def sign_in(app, email="manager@harbour.demo"):
    client = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    response = client.post(
        "/api/login", json={"email": email, "password": "AisleDemo!2026"}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def submit(client, body=None, key=None):
    return client.post(
        "/api/interactions/jobs",
        json=body or sample(),
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def wait_job(client, item_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get("/api/interactions/jobs/" + item_id)
        assert response.status_code == 200, response.text
        if response.json()["status"] not in {"pending", "running"}:
            return response.json()
        time.sleep(0.01)
    raise AssertionError("Unit-test job did not finish")


def complete(client):
    response = submit(client)
    assert response.status_code == 202, response.text
    return wait_job(client, response.json()["id"])["result"]


def test_real_scoped_store_evidence_review_delete_and_audit(app):
    client = sign_in(app)
    before = client.get("/api/bootstrap").json()
    item = complete(client)
    assert item["alarm_eligible"] is True
    assert item["camera_calibration_status"] == "READY"
    assert item["camera_calibration"]["shelf_zones_confirmed"] is True
    assert item["validated"] is False and item["historical"] is True
    assert (
        item["review"] is None and item["evidence_kind"] == "sampled_jpeg_derivatives"
    )
    assert "theft" in item["reason"]
    assert client.get("/api/interactions").json()["items"] == [item]
    for frame in item["frames"]:
        response = client.get(frame["url"])
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (96, 64)
    reviewed = client.post(
        f"/api/interactions/{item['id']}/review",
        json={"outcome": "NORMAL_SHOPPING", "note": "Synthetic fixture review."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review"]["outcome"] == "NORMAL_SHOPPING"
    assert reviewed.json()["version"] == 2
    conflict = client.post(
        f"/api/interactions/{item['id']}/review",
        json={"outcome": "UNCLEAR", "note": "Changed."},
    )
    assert conflict.status_code == 409
    assert (
        client.post(
            f"/api/interactions/{item['id']}/review",
            json={"outcome": "UNCLEAR", "note": "Changed.", "expected_version": 2},
        ).status_code
        == 200
    )
    after = client.get("/api/bootstrap").json()
    for kind in ("incidents", "candidates", "assistance"):
        assert before[kind] == after[kind]
    assert client.delete(f"/api/interactions/{item['id']}").json() == {"deleted": True}
    assert not app.state.interactions.directory(item["id"]).exists()
    assert client.get(item["frames"][0]["url"]).status_code == 404
    assert client.get("/api/interactions").json() == {"items": []}
    assert any(
        event["action"] == "INTERACTION_DELETED"
        for event in client.get("/api/bootstrap").json()["audit"]
    )


def test_missing_calibration_keeps_analysis_but_blocks_automatic_alarm(app):
    client = sign_in(app)
    payload = sample()
    payload.pop("camera_calibration")
    response = submit(client, payload)
    assert response.status_code == 202
    item = wait_job(client, response.json()["id"])["result"]
    assert item["action"] == "POSSIBLE_CONCEALMENT"
    assert item["alarm_eligible"] is False
    assert item["camera_calibration_status"] == "MISSING"
    assert item["alarm_blocked_reason"] == "CAMERA_CALIBRATION_REQUIRED"
    assert "camera_calibration" not in item


@pytest.mark.parametrize(
    "field",
    [
        "entrance_zone_confirmed",
        "exit_zone_confirmed",
        "cashier_zone_confirmed",
        "shelf_zones_confirmed",
    ],
)
def test_partial_camera_calibration_is_rejected(field):
    payload = sample()["camera_calibration"]
    payload[field] = False
    with pytest.raises(ValidationError):
        InteractionInput.model_validate(sample(camera_calibration=payload))


def test_auth_csrf_origin_and_cross_branch_boundaries(app):
    anonymous = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    assert anonymous.get("/api/interactions/status").status_code == 401
    assert anonymous.get("/api/interactions").status_code == 401
    assert submit(anonymous).status_code == 401
    harbour = sign_in(app)
    item = complete(harbour)
    liffey = sign_in(app, "manager@liffey.demo")
    assert liffey.get("/api/interactions").json() == {"items": []}
    for path in (f"/api/interactions/jobs/{item['id']}", item["frames"][0]["url"]):
        assert liffey.get(path).status_code == 404
        assert anonymous.get(path).status_code == 401
    assert liffey.delete(f"/api/interactions/{item['id']}").status_code == 404
    assert (
        liffey.post(
            f"/api/interactions/{item['id']}/review", json={"outcome": "USEFUL"}
        ).status_code
        == 404
    )
    csrf = harbour.headers.pop("X-CSRF-Token")
    assert submit(harbour).status_code == 403
    assert harbour.delete(f"/api/interactions/{item['id']}").status_code == 403
    harbour.headers["X-CSRF-Token"] = csrf
    harbour.headers.pop("Origin")
    assert submit(harbour).status_code == 403


def test_retry_identity_payload_conflict_and_no_resurrection(app):
    client = sign_in(app)
    body, key = sample(), str(uuid4())
    first = submit(client, body, key)
    item_id = first.json()["id"]
    wait_job(client, item_id)
    assert submit(client, body, key).json()["id"] == item_id
    assert app.state.interactions.provider.calls == 1
    changed = {**body, "source_label": "Other source"}
    assert submit(client, changed, key).status_code == 409
    client.delete(f"/api/interactions/{item_id}")
    assert submit(client, body, key).status_code == 410
    assert client.post("/api/interactions/jobs", json=sample()).status_code == 400


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.update(frames=b["frames"][:2]),
        lambda b: b.update(frames=b["frames"] * 3),
        lambda b: b["frames"][0].update(jpeg_base64="not a JPEG"),
        lambda b: b["frames"][0].update(
            jpeg_base64="data:image/jpeg;base64," + b["frames"][0]["jpeg_base64"]
        ),
        lambda b: b["frames"][0].update(
            jpeg_base64=base64.b64encode(jpeg((769, 64))).decode()
        ),
        lambda b: b["frames"][0].update(
            jpeg_base64=base64.b64encode(jpeg()[:100]).decode()
        ),
        lambda b: b["frames"][1].update(at_seconds=0),
        lambda b: b["frames"][2].update(at_seconds=13),
        lambda b: b["frames"][1].update(at_seconds=True),
        lambda b: b.update(source_kind="CLOUD_LINK"),
        lambda b: b.update(organisation_id="attacker"),
        lambda b: b.update(run_id="not-uuid"),
        lambda b: b.update(source_label="invalid\x00label"),
    ],
)
def test_strict_frame_contract_rejects_invalid_input_without_model_call(app, mutation):
    client = sign_in(app)
    body = sample()
    mutation(body)
    response = submit(client, body)
    assert response.status_code == 422, response.text
    assert app.state.interactions.provider.calls == 0
    assert 'jpeg_base64":' not in response.text
    assert list(app.state.interactions.evidence_root.iterdir()) == []


def test_body_cap_is_route_specific_and_applies_to_actual_stream_size(app):
    client = sign_in(app)
    response = client.post(
        "/api/interactions/jobs",
        content=b" " * 3_000_001,
        headers={"Content-Type": "application/json", "Content-Length": "1"},
    )
    assert response.status_code == 413
    other = client.post(
        "/api/live-events",
        content=b" " * 65_537,
        headers={"Content-Type": "application/json"},
    )
    assert other.status_code == 413


def test_busy_no_backlog_cancel_no_publication_and_db_remains_available(app):
    client = sign_in(app)
    provider = app.state.interactions.provider
    provider.release.clear()
    first = submit(client).json()
    assert provider.started.wait(2)
    started = time.monotonic()
    assert client.get("/api/bootstrap").status_code == 200
    assert time.monotonic() - started < 1
    assert submit(client).status_code == 429
    assert (
        client.post(f"/api/interactions/jobs/{first['id']}/cancel", json={}).json()[
            "status"
        ]
        == "cancelled"
    )
    assert not app.state.interactions.directory(first["id"]).exists()
    assert (
        submit(client).status_code == 429
    )  # Cancel does not overlap active inference.
    provider.release.set()
    assert wait_job(client, first["id"])["status"] == "cancelled"
    assert client.get("/api/interactions").json() == {"items": []}


def test_provider_failure_is_safe_and_removes_evidence(app):
    client = sign_in(app)
    app.state.interactions.provider.error = RuntimeError(
        "secret frame data and backend credentials"
    )
    first = submit(client).json()
    job = wait_job(client, first["id"])
    assert job["status"] == "failed"
    assert "secret" not in job["error"]
    assert not app.state.interactions.directory(first["id"]).exists()
    assert client.get("/api/interactions").json() == {"items": []}


def test_revoked_session_during_inference_cannot_publish(app):
    client = sign_in(app)
    provider = app.state.interactions.provider
    provider.release.clear()
    first = submit(client).json()
    assert provider.started.wait(2)
    with app.state.store.transaction() as conn:
        conn.execute("DELETE FROM sessions")
    provider.release.set()
    new_session = sign_in(app)
    assert wait_job(new_session, first["id"])["status"] == "cancelled"
    assert new_session.get("/api/interactions").json() == {"items": []}


def test_same_organisation_other_site_and_changed_membership_cannot_read(app):
    client = sign_in(app)
    item = complete(client)
    with app.state.store.transaction() as conn:
        conn.execute(
            "UPDATE users SET site_id='different-site' WHERE email='manager@harbour.demo'"
        )
    assert client.get(item["frames"][0]["url"]).status_code == 404
    assert client.get("/api/interactions").json() == {"items": []}
    assert client.get(f"/api/interactions/jobs/{item['id']}").status_code == 404


def test_evidence_expiry_blocks_access_then_physically_removes_files(app):
    client = sign_in(app)
    item = complete(client)
    with app.state.store.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM entities WHERE id=?", (item["id"],)
        ).fetchone()
        stored = json.loads(row["body"])
        stored["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        conn.execute(
            "UPDATE entities SET body=? WHERE id=?", (encode(stored), item["id"])
        )
    assert client.get(item["frames"][0]["url"]).status_code == 410
    assert client.get("/api/interactions").json() == {"items": []}
    assert not app.state.interactions.directory(item["id"]).exists()
    assert client.get(item["frames"][0]["url"]).status_code == 404


def test_evidence_tampering_and_query_credentials_never_serve(app):
    client = sign_in(app)
    item = complete(client)
    url = item["frames"][0]["url"]
    assert client.get(url + "?token=anything").status_code == 400
    path = app.state.interactions.directory(item["id"]) / "0.jpg"
    path.write_bytes(b"corrupt")
    assert client.get(url).status_code == 404
    path.unlink()
    path.symlink_to(app.state.store.path)
    assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"person_visible": False},
        {"product_visible": False},
        {"sequence_observed": False},
        {"visibility": "partial"},
        {"visibility": "poor"},
        {"evidence_frame_indices": [1]},
    ],
)
def test_unsupported_concealment_becomes_unclear_and_cannot_route_alarm(change):
    result = public_observation(ModelObservation(**observation(**change)), 3)
    assert result["action"] == "UNCLEAR" and result["alarm_eligible"] is False


def test_evidence_strength_is_explainable_rule_output_not_probability():
    strong = public_observation(ModelObservation(**observation()), 3)
    assert strong["evidence_strength"] == "STRONG_RULE_MATCH"
    assert "not a probability" in strong["evidence_strength_note"]
    partial = public_observation(
        ModelObservation(**observation(visibility="partial", sequence_observed=False)),
        3,
    )
    assert partial["action"] == "UNCLEAR"
    assert partial["alarm_eligible"] is False
    assert partial["evidence_strength"] == "PARTIAL_RULE_MATCH"
    insufficient = public_observation(
        ModelObservation(
            action="UNCLEAR",
            visibility="poor",
            person_visible=False,
            product_visible=False,
            sequence_observed=False,
            evidence_frame_indices=[],
        ),
        3,
    )
    assert insufficient["evidence_strength"] == "INSUFFICIENT_RULE_MATCH"


@pytest.mark.parametrize("indices", [[-1, 2], [0, 3], [1, 1], [2, 0]])
def test_model_invalid_evidence_indices_rejected(indices):
    with pytest.raises(VisionError):
        public_observation(
            ModelObservation(**observation(evidence_frame_indices=indices)), 3
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1.evil.test:11435",
        "http://user:pass@127.0.0.1:11435",
        "http://127.0.0.1:11435/other",
        "http://192.168.1.1:11435",
        "http://127.0.0.1:11435?url=evil",
    ],
)
def test_provider_disallows_remote_urls_credentials_paths(url):
    with pytest.raises(ValueError):
        VisionProvider(url=url)


def test_provider_disallows_cloud_model():
    with pytest.raises(ValueError):
        VisionProvider(model="qwen:cloud")


@pytest.mark.parametrize("backend", ["ollama", "llamacpp"])
def test_real_adapter_wire_contract_constrained_output_and_no_external_tools(
    monkeypatch, backend
):
    monkeypatch.setenv("AISLESIGNALS_VISION_BACKEND", backend)
    provider = VisionProvider()
    requests = []

    def fake_request(method, path, *, payload=None, inference=False):
        requests.append((method, path, payload, inference))
        content = json.dumps(observation(action="RETURN_PRODUCT"))
        return (
            {"done": True, "message": {"content": content}}
            if backend == "ollama"
            else {
                "choices": [{"finish_reason": "stop", "message": {"content": content}}]
            }
        )

    monkeypatch.setattr(provider, "_request", fake_request)
    result = provider.analyze([(0, jpeg()), (2, jpeg()), (4, jpeg())])
    assert result["action"] == "RETURN_PRODUCT" and result["alarm_eligible"] is False
    assert result["validated"] is False and len(result["prompt_sha256"]) == 64
    method, path, payload, inference = requests[0]
    assert method == "POST" and inference is True and payload["stream"] is False
    assert "tools" not in payload
    assert path == ("/api/chat" if backend == "ollama" else "/v1/chat/completions")
    assert "untrusted" in payload["messages"][0]["content"]
    images = (
        payload["messages"][1].get("images") or payload["messages"][1]["content"][1:]
    )
    assert len(images) == 3


def test_model_schema_failure_cannot_leak_claim_or_trigger_action(monkeypatch):
    provider = VisionProvider()
    monkeypatch.setattr(
        provider,
        "_request",
        lambda *a, **k: {
            "done": True,
            "message": {
                "content": json.dumps(
                    {
                        **observation(),
                        "reason": "Named person is a thief",
                        "tool": "start alarm",
                    }
                )
            },
        },
    )
    with pytest.raises(VisionError) as error:
        provider.analyze([(0, jpeg()), (1, jpeg()), (2, jpeg())])
    assert "Named person" not in str(error.value)


def test_status_requires_exact_installed_model_and_safe_provider_failure(monkeypatch):
    provider = VisionProvider(model="qwen3-vl:4b")
    monkeypatch.setattr(
        provider, "_request", lambda *a, **k: {"models": [{"name": "different:4b"}]}
    )
    assert provider.status()["ready"] is False
    monkeypatch.setattr(
        provider,
        "_request",
        lambda *a, **k: {
            "models": [{"name": "qwen3-vl:4b", "digest": "unit-test-digest"}]
        },
    )
    assert provider.status()["ready"] is True
    assert provider.model_digest == "unit-test-digest"


def test_transaction_allows_sequential_dependency_exit_on_different_thread(app):
    from concurrent.futures import ThreadPoolExecutor

    transaction = app.state.store.transaction()
    conn = transaction.__enter__()
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(lambda: conn.execute("SELECT 1").fetchone()[0]).result() == 1
        pool.submit(transaction.__exit__, None, None, None).result()


def test_parallel_status_history_login_and_inference_do_not_hold_db_lock(app):
    from concurrent.futures import ThreadPoolExecutor

    clients = [sign_in(app) for _ in range(3)]
    provider = app.state.interactions.provider
    provider.release.clear()
    first = submit(clients[0]).json()
    assert provider.started.wait(2)
    started = time.monotonic()

    def read_pair(client):
        for _ in range(4):
            assert client.get("/api/interactions/status").status_code == 200
            assert client.get("/api/interactions").status_code == 200
            assert client.get("/api/session").status_code == 200
        return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(read_pair, client) for client in clients]
        futures.append(
            pool.submit(lambda: sign_in(app).get("/api/bootstrap").status_code == 200)
        )
        assert all(future.result(timeout=5) for future in futures)
    assert time.monotonic() - started < 3
    provider.release.set()
    assert wait_job(clients[0], first["id"])["status"] == "completed"


def test_changed_site_during_inference_cannot_publish(app):
    client = sign_in(app)
    provider = app.state.interactions.provider
    provider.release.clear()
    first = submit(client).json()
    assert provider.started.wait(2)
    with app.state.store.transaction() as conn:
        conn.execute(
            "UPDATE users SET site_id='different-site' WHERE email='manager@harbour.demo'"
        )
    provider.release.set()
    reviewer = sign_in(app, "reviewer@harbour.demo")
    assert wait_job(reviewer, first["id"])["status"] == "cancelled"
    assert reviewer.get("/api/interactions").json() == {"items": []}


def test_delete_during_inference_cannot_recreate_metadata_or_evidence(app):
    client = sign_in(app)
    provider = app.state.interactions.provider
    provider.release.clear()
    first = submit(client).json()
    assert provider.started.wait(2)
    assert client.delete(f"/api/interactions/{first['id']}").json() == {"deleted": True}
    provider.release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with ACTIVE_LOCK:
            active = first["id"] in ACTIVE_JOBS
        if not active:
            break
        time.sleep(0.01)
    assert client.get(f"/api/interactions/jobs/{first['id']}").status_code == 404
    assert client.get("/api/interactions").json() == {"items": []}
    assert not app.state.interactions.directory(first["id"]).exists()


def test_thread_dispatch_failure_marks_committed_job_failed_and_frees_slot(
    app, monkeypatch
):
    original_start = threading.Thread.start

    def fail_dispatch(thread):
        if thread.name == "local-interaction-analysis":
            raise RuntimeError("Synthetic thread creation failure")
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_dispatch)
    client = sign_in(app)
    body, key = sample(), str(uuid4())
    response = submit(client, body, key)
    assert response.status_code == 503
    retry = submit(client, body, key)
    assert retry.status_code == 202
    assert retry.json()["status"] == "failed"
    assert wait_job(client, retry.json()["id"])["status"] == "failed"
    assert not app.state.interactions.directory(retry.json()["id"]).exists()
    monkeypatch.setattr(threading.Thread, "start", original_start)
    assert complete(client)["validated"] is False


def test_provider_network_stays_loopback_authenticated_and_ignores_proxy_env(
    monkeypatch, tmp_path
):
    token_path = tmp_path / "unit-token"
    token_path.write_text("synthetic-local-token")
    monkeypatch.setenv("AISLESIGNALS_VISION_TOKEN_FILE", str(token_path))
    monkeypatch.setenv("HTTP_PROXY", "http://invalid.example:9999")
    original_client = httpx.AsyncClient
    seen = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"models": [{"name": "qwen3-vl:4b"}]}'

    async def handle(request):
        seen.append(request)
        return httpx.Response(200, stream=Stream())

    def client_factory(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    assert VisionProvider().status()["ready"] is True
    assert seen[0].url.host == "127.0.0.1"
    assert seen[0].headers["Authorization"] == "Bearer synthetic-local-token"


def test_provider_response_size_cap_closes_transport(monkeypatch):
    original_client = httpx.AsyncClient
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(5):
                yield b"x" * 8192

        async def aclose(self):
            closed.append(True)

    async def handle(request):
        return httpx.Response(200, stream=Stream())

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(handle)
        ),
    )
    with pytest.raises(VisionError, match="resource limit"):
        VisionProvider()._request("POST", "/api/chat", payload={}, inference=True)
    assert closed


def test_provider_absolute_deadline_closes_slow_stream(monkeypatch):
    import asyncio

    original_client = httpx.AsyncClient
    original_wait = asyncio.wait_for
    closed, deadlines = [], []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(60)
            yield b"{}"

        async def aclose(self):
            closed.append(True)

    async def handle(request):
        return httpx.Response(200, stream=Stream())

    async def shortened_wait(coroutine, timeout):
        deadlines.append(timeout)
        return await original_wait(coroutine, timeout=0.01)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(handle)
        ),
    )
    monkeypatch.setattr(asyncio, "wait_for", shortened_wait)
    with pytest.raises(VisionError, match="timeout"):
        VisionProvider()._request("POST", "/api/chat", payload={}, inference=True)
    assert deadlines == [150]
    assert closed


def test_provider_rejects_unexpected_compressed_response(monkeypatch):
    original_client = httpx.AsyncClient

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"not a gzip stream"

    async def handle(request):
        return httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=Stream()
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(handle)
        ),
    )
    with pytest.raises(VisionError, match="encoding"):
        VisionProvider()._request("POST", "/api/chat", payload={}, inference=True)
