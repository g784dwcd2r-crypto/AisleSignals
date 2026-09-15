"""The xAI verifier is tested without paid calls or real pharmacy footage."""

import hashlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from services.api.app import create_app
from services.api.store import Store, ident, now
from services.api.xai_verifier import (
    CircuitBreaker,
    VerifierResult,
    XaiProvider,
    XaiVerifierError,
    minimise_jpeg,
    reserve_global_usage,
)

BASE = "http://127.0.0.1:8765"


def jpeg(size=(900, 600), color="blue"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "JPEG", quality=95, exif=b"test-metadata")
    return output.getvalue()


def provider_response():
    return {
        "model": "synthetic-model",
        "choices": [{
            "message": {
                "content": (
                    '{"observed_action":"UNCLEAR","visibility":"PARTIAL",'
                    '"person_present":true,"product_transition_visible":true,'
                    '"sequence_observed":false,'
                    '"alternative_explanation":"OCCLUSION_OR_MISSING_CONTEXT",'
                    '"evidence_frame_indices":[0,1],'
                    '"reason_code":"OCCLUDED_TRANSITION"}'
                )
            }
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 25},
    }


def test_provider_uses_strict_schema_and_bounded_frames():
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "synthetic-model"})
        return httpx.Response(200, json=provider_response(), headers={"x-request-id": "synthetic-request"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.x.ai")
    provider = XaiProvider(
        model="synthetic-model",
        api_key="xai-unit-test-credential-never-real",
        client=client,
    )
    result, audit = provider.verify([minimise_jpeg(jpeg()), minimise_jpeg(jpeg(color="red"))], {
        "frame_offsets_seconds": [0, 2],
        "camera_calibrated": True,
    })

    assert result.observed_action == "UNCLEAR"
    assert audit["attempts"] == 1
    assert audit["provider_request_id_sha256"] == hashlib.sha256(b"synthetic-request").hexdigest()
    assert len(calls) == 2
    payload = __import__("json").loads(calls[-1].content)
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["max_completion_tokens"] == 500
    assert all(
        item["image_url"]["detail"] == "high"
        for item in payload["messages"][1]["content"]
        if item["type"] == "image_url"
    )
    assert "identity" in payload["messages"][0]["content"]
    assert "local_action" not in calls[-1].content.decode()


def test_timeout_is_not_retried_because_billing_is_ambiguous():
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "synthetic-model"})
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    provider = XaiProvider(
        model="synthetic-model",
        api_key="xai-unit-test-credential-never-real",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.x.ai"),
    )
    try:
        provider.verify([minimise_jpeg(jpeg())] * 3, {
            "frame_offsets_seconds": [0, 1, 2], "camera_calibrated": True,
        })
    except XaiVerifierError as exc:
        assert exc.code == "PROVIDER_INVALID_RESPONSE"
        assert "automatic paid retry was not attempted" in exc.message
    else:
        raise AssertionError("Synthetic timeout was accepted")
    assert len([request for request in calls if request.method == "POST"]) == 1


def test_provider_invalid_responses_open_circuit_without_secret_in_error():
    secret = "xai-unit-test-secret-never-real"
    clock = [10.0]
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=60, clock=lambda: clock[0])
    def malformed(request):
        if request.method == "GET":
            return httpx.Response(200, json={"id": "synthetic-model"})
        return httpx.Response(200, json={"bad": secret})

    client = httpx.Client(
        transport=httpx.MockTransport(malformed),
        base_url="https://api.x.ai",
    )
    provider = XaiProvider(
        model="synthetic-model", api_key=secret, client=client,
        breaker=breaker,
    )
    frame = minimise_jpeg(jpeg())
    for _ in range(2):
        try:
            provider.verify([frame, frame], {"local_action": "TAKE_PRODUCT"})
        except XaiVerifierError as exc:
            assert secret not in str(exc)
            assert exc.code == "PROVIDER_INVALID_RESPONSE"
        else:
            raise AssertionError("Malformed provider response was accepted")
    try:
        provider.verify([frame, frame], {"local_action": "TAKE_PRODUCT"})
    except XaiVerifierError as exc:
        assert exc.code == "CIRCUIT_OPEN"
    else:
        raise AssertionError("Open circuit allowed a request")


def test_minimisation_bounds_pixels_and_removes_metadata():
    clean = minimise_jpeg(jpeg())
    with Image.open(io.BytesIO(clean)) as image:
        assert max(image.size) == 768
        assert not image.getexif()


class MockXai:
    model = "mock-xai-for-unit-test"

    def __init__(self):
        self.calls = []

    def ready(self):
        return True

    def verify(self, frames, metadata):
        self.calls.append((frames, metadata))
        return VerifierResult(
            observed_action="RETURN_PRODUCT",
            visibility="ADEQUATE",
            person_present=True,
            product_transition_visible=True,
            sequence_observed=True,
            alternative_explanation="PRODUCT_RETURNED",
            evidence_frame_indices=[0, 1],
            reason_code="VISIBLE_RETURN",
        ), {
            "attempts": 1,
            "provider_request_id_sha256": "0" * 64,
            "input_tokens": 100,
            "output_tokens": 20,
        }


def sign_in(app, email="manager@harbour.demo"):
    client = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    response = client.post("/api/login", json={"email": email, "password": "AisleDemo!2026"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def insert_interaction(app, client, *, enough_local_evidence=True):
    session = client.get("/api/session").json()
    user_id = session["user"]["id"]
    with app.state.store.transaction() as conn:
        user = dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        item_id = ident()
        directory = app.state.interactions.directory(item_id)
        directory.mkdir(mode=0o700)
        frames = []
        for index, color in enumerate(("blue", "green", "red")):
            data = minimise_jpeg(jpeg(size=(300, 200), color=color))
            (directory / f"{index}.jpg").write_bytes(data)
            frames.append({
                "at_seconds": index * 2,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            })
        item = {
            "id": item_id,
            "run_id": str(uuid4()),
            "source_kind": "RECORDED_VIDEO",
            "source_label": "Synthetic test shapes",
            "created_at": now(),
            "expires_at": "2999-01-01T00:00:00Z",
            "status": "completed",
            "frames": frames,
            "review": None,
            "version": 1,
            "camera_calibration": {
                "schema_version": "1.0",
                "entrance_zone_confirmed": True,
                "exit_zone_confirmed": True,
                "cashier_zone_confirmed": True,
                "shelf_zones_confirmed": True,
            },
            "actor_id": user_id,
            "organisation_id": user["organisation_id"],
            "site_id": user["site_id"],
            "result": {
                "action": "POSSIBLE_CONCEALMENT" if enough_local_evidence else "UNCLEAR",
                "visibility": "clear",
                "person_visible": enough_local_evidence,
                "product_visible": enough_local_evidence,
                "sequence_observed": enough_local_evidence,
                "evidence_frame_indices": [0, 2] if enough_local_evidence else [],
                "alarm_eligible": enough_local_evidence,
            },
        }
        app.state.store.put(conn, user, "interaction", item)
    return item_id


def test_branch_enablement_budget_audit_idempotency_and_advisory_result(tmp_path):
    app = create_app(tmp_path / "verifier.db", tmp_path / "web")
    app.state.xai_verifier = MockXai()
    client = sign_in(app)
    status = client.get("/api/xai-verifier").json()
    assert status["config"]["enabled"] is False
    assert status["alarm_decision_permitted"] is False
    item_id = insert_interaction(app, client)
    disabled = client.post(
        f"/api/interactions/{item_id}/xai-verification",
        json={"expected_interaction_version": 1},
        headers={"Idempotency-Key": "disabled"},
    )
    assert disabled.status_code == 403

    enabled = client.put("/api/xai-verifier", json={
        "enabled": True,
        "privacy_review_approved": True,
        "us_processing_approved": True,
        "public_staged_footage_approved": True,
        "price_ceiling_verified": True,
        "provider_cap_confirmed": True,
        "expected_version": 0,
    })
    assert enabled.status_code == 200, enabled.text
    headers = {"Idempotency-Key": "stable-test-request"}
    first = client.post(
        f"/api/interactions/{item_id}/xai-verification",
        json={"expected_interaction_version": 1}, headers=headers,
    )
    assert first.status_code == 201, first.text
    result = first.json()
    assert result["result"]["blind_observation"]["observed_action"] == "RETURN_PRODUCT"
    assert result["result"]["comparison"] == "CONTRADICTS_LOCAL_CANDIDATE"
    assert result["advisory_only"] is True
    assert result["alarm_decision_permitted"] is False
    assert result["requires_local_candidate"] is True
    assert result["reserved_usd_cents"] == 100
    assert len(app.state.xai_verifier.calls[0][0]) == 3
    assert "local_action" not in app.state.xai_verifier.calls[0][1]

    replay = client.post(
        f"/api/interactions/{item_id}/xai-verification",
        json={"expected_interaction_version": 1}, headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == result["id"]
    assert len(app.state.xai_verifier.calls) == 1
    status = client.get("/api/xai-verifier").json()
    assert status["budget"]["reserved_usd_cents"] == 100
    assert status["budget"]["request_count"] == 1
    assert status["budget"]["period"] == "pilot-lifetime"
    audit = client.get("/api/bootstrap").json()["audit"]
    assert {entry["action"] for entry in audit} >= {
        "XAI_VERIFIER_CONFIGURED", "XAI_VERIFICATION_RESERVED", "XAI_VERIFICATION_COMPLETED"
    }
    other_branch = sign_in(app, "manager@liffey.demo")
    cross_site = other_branch.get(f"/api/xai-verifications/{result['id']}")
    assert cross_site.status_code == 404
    app.state.interactions.close()


def test_hard_five_usd_ceiling_is_installation_global_and_flags_are_branch_scoped(tmp_path):
    app = create_app(tmp_path / "budget.db", tmp_path / "web")
    app.state.xai_verifier = MockXai()
    harbour = sign_in(app)
    enabled = {
        "enabled": True,
        "privacy_review_approved": True,
        "us_processing_approved": True,
        "public_staged_footage_approved": True,
        "price_ceiling_verified": True,
        "provider_cap_confirmed": True,
        "expected_version": 0,
    }
    assert harbour.put("/api/xai-verifier", json=enabled).status_code == 200
    item_id = insert_interaction(app, harbour)
    for index in range(5):
        response = harbour.post(
            f"/api/interactions/{item_id}/xai-verification",
            json={"expected_interaction_version": 1},
            headers={"Idempotency-Key": f"budget-{index}"},
        )
        assert response.status_code == 201, response.text
    blocked = harbour.post(
        f"/api/interactions/{item_id}/xai-verification",
        json={"expected_interaction_version": 1},
        headers={"Idempotency-Key": "budget-over-cap"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "XAI_BUDGET_EXHAUSTED"
    assert harbour.get("/api/xai-verifier").json()["budget"]["reserved_usd_cents"] == 500

    liffey = sign_in(app, "manager@liffey.demo")
    liffey_status = liffey.get("/api/xai-verifier").json()
    assert liffey_status["config"]["enabled"] is False
    assert liffey_status["budget"]["reserved_usd_cents"] == 500
    app.state.interactions.close()


def test_concurrent_global_reservations_cannot_exceed_five_usd(tmp_path):
    store = Store(tmp_path / "concurrent-budget.db")

    def reserve(_index):
        try:
            with store.transaction() as conn:
                reserve_global_usage(conn)
            return True
        except XaiVerifierError as exc:
            assert exc.code == "XAI_BUDGET_EXHAUSTED"
            return False

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(reserve, range(10)))
    assert results.count(True) == 5
    with store.transaction() as conn:
        row = conn.execute(
            "SELECT value FROM runtime_settings WHERE key='xai_verifier_global_usage'"
        ).fetchone()
    ledger = __import__("json").loads(row[0])
    assert ledger["reserved_usd_cents"] == 500
    assert ledger["request_count"] == 5


class BlockingXai(MockXai):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def verify(self, frames, metadata):
        self.started.set()
        assert self.release.wait(5)
        return super().verify(frames, metadata)


def test_deleted_source_during_request_discards_advisory_result(tmp_path):
    app = create_app(tmp_path / "deleted-source.db", tmp_path / "web")
    blocker = app.state.xai_verifier = BlockingXai()
    requesting = sign_in(app)
    deleting = sign_in(app)
    assert requesting.put("/api/xai-verifier", json={
        "enabled": True,
        "privacy_review_approved": True,
        "us_processing_approved": True,
        "public_staged_footage_approved": True,
        "price_ceiling_verified": True,
        "provider_cap_confirmed": True,
        "expected_version": 0,
    }).status_code == 200
    item_id = insert_interaction(app, requesting)

    def external_review():
        return requesting.post(
            f"/api/interactions/{item_id}/xai-verification",
            json={"expected_interaction_version": 1},
            headers={"Idempotency-Key": "delete-during-request"},
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(external_review)
        assert blocker.started.wait(5)
        deleted = deleting.delete(f"/api/interactions/{item_id}")
        assert deleted.status_code == 200, deleted.text
        blocker.release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"
    audits = requesting.get("/api/bootstrap").json()["audit"]
    assert "XAI_VERIFICATION_FAILED" in {item["action"] for item in audits}
    app.state.interactions.close()


def test_stale_request_recovers_without_retry_or_budget_refund(tmp_path):
    app = create_app(tmp_path / "stale.db", tmp_path / "web")
    client = sign_in(app)
    session = client.get("/api/session").json()
    verification_id = ident()
    with app.state.store.transaction() as conn:
        user = dict(conn.execute("SELECT * FROM users WHERE id=?", (session["user"]["id"],)).fetchone())
        app.state.store.put(conn, user, "xai_verification", {
            "id": verification_id,
            "status": "REQUESTING",
            "requested_at": "2000-01-01T00:00:00Z",
            "expires_at": "2999-01-01T00:00:00Z",
            "reserved_usd_cents": 100,
        })
        conn.execute(
            "INSERT INTO runtime_settings(key,value) VALUES(?,?)",
            ("xai_verifier_global_usage", '{"currency":"USD","hard_cap_usd_cents":500,"period":"pilot-lifetime","request_count":1,"request_reservation_usd_cents":100,"reserved_usd_cents":100}'),
        )
    assert client.get("/api/xai-verifier").status_code == 200
    recovered = client.get(f"/api/xai-verifications/{verification_id}").json()
    assert recovered["status"] == "UNKNOWN_BILLING"
    assert recovered["failure_code"] == "INTERRUPTED_AFTER_RESERVATION"
    assert client.get("/api/xai-verifier").json()["budget"]["reserved_usd_cents"] == 100
    app.state.interactions.close()


def test_approval_and_independent_local_evidence_are_required(tmp_path):
    app = create_app(tmp_path / "guards.db", tmp_path / "web")
    app.state.xai_verifier = MockXai()
    manager = sign_in(app)
    rejected = manager.put("/api/xai-verifier", json={
        "enabled": True,
        "privacy_review_approved": True,
        "us_processing_approved": False,
        "public_staged_footage_approved": True,
        "price_ceiling_verified": True,
        "provider_cap_confirmed": True,
        "expected_version": 0,
    })
    assert rejected.status_code == 422
    missing_public_approval = manager.put("/api/xai-verifier", json={
        "enabled": True,
        "privacy_review_approved": True,
        "us_processing_approved": True,
        "public_staged_footage_approved": False,
        "price_ceiling_verified": True,
        "provider_cap_confirmed": True,
        "expected_version": 0,
    })
    assert missing_public_approval.status_code == 422
    manager.put("/api/xai-verifier", json={
        "enabled": True,
        "privacy_review_approved": True,
        "us_processing_approved": True,
        "public_staged_footage_approved": True,
        "price_ceiling_verified": True,
        "provider_cap_confirmed": True,
        "expected_version": 0,
    })
    weak = insert_interaction(app, manager, enough_local_evidence=False)
    response = manager.post(
        f"/api/interactions/{weak}/xai-verification",
        json={"expected_interaction_version": 1},
        headers={"Idempotency-Key": "weak-local-result"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOCAL_EVIDENCE_REQUIRED"
    assert not app.state.xai_verifier.calls
    app.state.interactions.close()
