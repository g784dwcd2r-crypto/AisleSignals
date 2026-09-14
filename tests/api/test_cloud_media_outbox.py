"""Local encrypted-reference media delivery state; no customer media/network."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from types import SimpleNamespace

import pytest

from services.api.cloud_media_outbox import MediaOutbox, install_schema
from services.api.cloud_delivery import CloudDelivery
from services.api.cloud_observation import MappedObservation, source_event_id
from services.api.cloud_outbox import Outbox, OutboxError, Scope, Target, mark_restored
from services.api.store import Store

NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


class Local:
    def __init__(self, path):
        self.store = Store(path, mode="pilot")
        self.box, self.media = Outbox(b"m" * 32), MediaOutbox()
        with self.store.transaction() as conn:
            install_schema(conn)
            installation = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()[0]
            self.scope = Scope(installation, "synthetic-org", "synthetic-site")
            target = Target("https://synthetic.example.test", *(str(uuid4()) for _ in range(4)))
            binding = self.box.create_binding(conn, self.scope, binding_id=str(uuid4()), expected_generation=0,
                target=target, now=NOW-timedelta(hours=1))
            self.binding = self.box.set_paused(conn, self.scope, binding.ref, paused=False, now=NOW-timedelta(hours=1)).ref

    def enqueue(self, conn):
        entity_id = str(uuid4())
        source = source_event_id(installation_id=self.scope.installation_id, binding_id=self.binding.id,
            organisation_id=self.scope.organisation_id, site_id=self.scope.site_id,
            entity_kind="interaction", entity_id=entity_id)
        payload = {"source_event_id": source, "event_code": "POSSIBLE_CONCEALMENT",
            "source_label": "Camera source · local observation", "historical": True,
            "occurred_at": NOW.isoformat().replace("+00:00", "Z")}
        parent = self.box.enqueue(conn, self.scope, self.binding,
            MappedObservation("interaction", entity_id, payload, NOW, NOW+timedelta(hours=24)), now=NOW)
        item = {"frames": [
            {"sha256": "1" * 64, "bytes": 111}, {"sha256": "2" * 64, "bytes": 222}],
            "result": {"evidence_frame_indices": [0, 1]}}
        media = self.media.enqueue_overview(conn, self.scope, self.binding, parent, item, now=NOW)
        return parent, media, item


def row(conn, table, identifier):
    return dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone())


def test_atomic_single_overview_references_latest_model_cited_encrypted_frame(tmp_path):
    local = Local(tmp_path / "pilot.db")
    with pytest.raises(RuntimeError):
        with local.store.transaction() as conn:
            local.enqueue(conn)
            raise RuntimeError
    with local.store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_sync_items").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM cloud_media_items").fetchone()[0] == 0
        parent, media, item = local.enqueue(conn)
        saved = row(conn, "cloud_media_items", media)
        assert saved["observation_item_id"] == parent
        assert (saved["kind"], saved["content_type"], saved["frame_index"]) == ("OVERVIEW", "image/jpeg", 1)
        assert (saved["frame_sha256"], saved["frame_bytes"]) == ("2" * 64, 222)
        assert not any(name in saved for name in {"payload", "content", "bytes_blob"})
        assert local.media.enqueue_overview(conn, local.scope, local.binding, parent, item, now=NOW) == media
        item["frames"][1]["bytes"] = 223
        with pytest.raises(OutboxError, match="MEDIA_CONFLICT"):
            local.media.enqueue_overview(conn, local.scope, local.binding, parent, item, now=NOW)


def test_manifest_then_content_are_leased_retried_and_resumed_without_changing_identity(tmp_path):
    local = Local(tmp_path / "pilot.db")
    with local.store.transaction() as conn:
        parent, media, _ = local.enqueue(conn)
        assert local.media.claim(conn, local.scope, local.binding, now=NOW) is None
        observation = local.box.claim(conn, local.scope, local.binding, now=NOW)
        local.box.acknowledge(conn, local.scope, local.binding, observation, receipt_id=str(uuid4()), now=NOW)
        manifest = local.media.claim(conn, local.scope, local.binding, now=NOW)
        assert manifest.operation == "MEDIA_MANIFEST"
        assert manifest.manifest == {"schema_version": 1, "kind": "OVERVIEW", "content_type": "image/jpeg",
                                     "byte_count": 222, "sha256": "2" * 64}
        local.media.retry(conn, local.scope, local.binding, manifest, now=NOW, delay_seconds=5)
    with local.store.transaction() as conn:
        assert local.media.claim(conn, local.scope, local.binding, now=NOW+timedelta(seconds=4)) is None
        manifest = local.media.claim(conn, local.scope, local.binding, now=NOW+timedelta(seconds=5))
        evidence_id = str(uuid4())
        local.media.acknowledge_manifest(conn, local.scope, local.binding, manifest,
            evidence_id=evidence_id, upload_required=True, state="PENDING", now=NOW+timedelta(seconds=5))
        content = local.media.claim(conn, local.scope, local.binding, now=NOW+timedelta(seconds=5))
        assert content.operation == "MEDIA_CONTENT" and content.evidence_id == evidence_id
        assert (content.source_event_id, content.frame_index, content.sha256) == (
            manifest.source_event_id, manifest.frame_index, manifest.sha256)
        local.media.acknowledge_content(conn, local.scope, local.binding, content,
            evidence_id=evidence_id, state="READY", now=NOW+timedelta(seconds=5))
        assert row(conn, "cloud_media_items", media)["state"] == "READY"


def test_parent_withdrawal_stops_unsent_media_and_restore_blocks_uncertain_upload(tmp_path):
    local = Local(tmp_path / "pilot.db")
    with local.store.transaction() as conn:
        parent, first, _ = local.enqueue(conn)
        local.box.withdraw(conn, local.scope, local.binding, parent, reason="LOCAL_DELETED", now=NOW)
        assert local.media.claim(conn, local.scope, local.binding, now=NOW) is None
        assert row(conn, "cloud_media_items", first)["state"] == "CANCELLED"
    # A distinct database keeps the restore assertion independent of withdrawal.
    uncertain = Local(tmp_path / "uncertain.db")
    with uncertain.store.transaction() as conn:
        _, media, _ = uncertain.enqueue(conn)
        observation = uncertain.box.claim(conn, uncertain.scope, uncertain.binding, now=NOW)
        uncertain.box.acknowledge(conn, uncertain.scope, uncertain.binding, observation,
                                  receipt_id=str(uuid4()), now=NOW)
        assert uncertain.media.claim(conn, uncertain.scope, uncertain.binding, now=NOW).operation == "MEDIA_MANIFEST"
        mark_restored(conn, now=NOW+timedelta(seconds=1))
        saved = row(conn, "cloud_media_items", media)
        assert saved["state"] == "BLOCKED"
        assert saved["error_code"] == "RESTORED_REQUIRES_MANAGEMENT"
        assert saved["lease_token"] is None


def test_delivery_runs_observation_manifest_content_in_order(tmp_path):
    local = Local(tmp_path / "pilot.db")
    with local.store.transaction() as conn:
        _, media, _ = local.enqueue(conn)
    target = Target("https://synthetic.example.test", *(str(uuid4()) for _ in range(4)))
    context = SimpleNamespace(scope=local.scope, binding=local.binding, target=target,
                              outbox=local.box, credential="t" * 64)

    class Provider:
        def delivery_context(self, _conn):
            return context

    class Request:
        alive = False
        operations = []

        def perform(self, request, *, authorize, content=None, **_):
            assert authorize()
            self.operations.append(request["operation"])
            if request["operation"] == "OBSERVATION":
                result = {"id": str(uuid4()), "received": True, "source_state": "AVAILABLE"}
            elif request["operation"] == "MEDIA_MANIFEST":
                result = {"evidence_id": str(uuid4()), "upload_required": True, "state": "PENDING"}
            else:
                assert content == b"bounded-synthetic-jpeg"
                result = {"evidence_id": request["evidence_id"], "state": "READY"}
            return {"ok": True, "result": result}

        def close(self):
            return True

    request = Request()
    sender = CloudDelivery(local.store, Provider(), source_state=lambda *_: "AVAILABLE",
        media_source=lambda *_: True, media_reader=lambda _: b"bounded-synthetic-jpeg",
        clock=lambda: NOW, request_factory=lambda: request)
    assert [sender.run_once().operation for _ in range(3)] == ["OBSERVATION", "MEDIA_MANIFEST", "MEDIA_CONTENT"]
    assert request.operations == ["OBSERVATION", "MEDIA_MANIFEST", "MEDIA_CONTENT"]
    with local.store.transaction() as conn:
        assert row(conn, "cloud_media_items", media)["state"] == "READY"
    assert sender.stop()
