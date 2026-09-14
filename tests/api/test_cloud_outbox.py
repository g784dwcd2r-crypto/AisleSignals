"""Synthetic SQLite/AES-GCM foundation tests; no API, network, media or OS key store."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from uuid import uuid4

import pytest

from services.api.cloud_observation import MappedObservation, source_event_id
from services.api.cloud_outbox import BindingRef, Limits, Outbox, OutboxError, Scope, Target, install_schema


NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
KEY = bytes(range(32))


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


class Local:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.box = Outbox(KEY)
        self.scope = Scope(str(uuid4()), "synthetic-org", "synthetic-branch")
        self.target = Target("https://synthetic.example.test", *(str(uuid4()) for _ in range(4)))
        with transaction(self.conn):
            install_schema(self.conn)
            self.conn.execute("CREATE TABLE local_observations(id TEXT PRIMARY KEY)")
            self.binding = self.box.create_binding(self.conn, self.scope, binding_id=str(uuid4()),
                                                  expected_generation=0, target=self.target, now=NOW)
            self.binding = self.box.set_paused(self.conn, self.scope, self.binding.ref, paused=False, now=NOW)

    def call(self, method, *args, now=NOW, scope=None, ref=None, **kwargs):
        with transaction(self.conn):
            return getattr(self.box, method)(self.conn, scope or self.scope, ref or self.binding.ref, *args, now=now, **kwargs)

    def observation(self, *, entity_id=None, entity_kind="interaction", at=NOW, **overrides):
        entity_id = entity_id or (str(uuid4()) if entity_kind == "interaction" else "live-" + "a" * 64)
        identifier = source_event_id(installation_id=self.scope.installation_id, binding_id=self.binding.ref.id,
                                     organisation_id=self.scope.organisation_id, site_id=self.scope.site_id,
                                     entity_kind=entity_kind, entity_id=entity_id)
        payload = {"source_event_id": identifier, "event_code": "POSSIBLE_CONCEALMENT",
                   "source_label": "Camera 2 of 6 · 3x2 screen grid · local observation",
                   "occurred_at": at.isoformat().replace("+00:00", "Z"), "historical": True}
        payload.update(overrides)
        return MappedObservation(entity_kind, entity_id, payload, at, at + timedelta(hours=24))

    def row(self, identifier):
        cursor = self.conn.execute("SELECT * FROM cloud_sync_items WHERE id=?", (identifier,))
        return dict(zip((column[0] for column in cursor.description), cursor.fetchone()))


@pytest.fixture
def local(tmp_path):
    value = Local(tmp_path / "synthetic.sqlite")
    yield value
    value.conn.close()


def test_schema_requires_transaction_and_rolls_back_without_committing_caller_data(tmp_path):
    conn = sqlite3.connect(tmp_path / "schema.sqlite", isolation_level=None)
    try:
        with pytest.raises(OutboxError, match="TRANSACTION_REQUIRED"):
            install_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE sentinel(id INTEGER)")
        conn.execute("INSERT INTO sentinel VALUES(1)")
        install_schema(conn)
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
        with transaction(conn):
            conn.execute("PRAGMA user_version=2")
            install_schema(conn)
            install_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_unknown_module_schema_is_preserved(local):
    local.conn.execute("UPDATE cloud_sync_schema SET version=99")
    with transaction(local.conn):
        with pytest.raises(OutboxError, match="SCHEMA_INCOMPATIBLE"):
            install_schema(local.conn)
    assert local.conn.execute("SELECT version FROM cloud_sync_schema").fetchone()[0] == 99


def test_observation_and_outbox_commit_or_rollback_together(local):
    observation = local.observation()
    with pytest.raises(RuntimeError):
        with transaction(local.conn):
            local.conn.execute("INSERT INTO local_observations VALUES(?)", (observation.entity_id,))
            local.box.enqueue(local.conn, local.scope, local.binding.ref, observation, now=NOW)
            raise RuntimeError("synthetic interrupted publication")
    assert local.conn.execute("SELECT count(*) FROM cloud_sync_items").fetchone()[0] == 0
    assert local.conn.execute("SELECT count(*) FROM local_observations").fetchone()[0] == 0
    with transaction(local.conn):
        local.conn.execute("INSERT INTO local_observations VALUES(?)", (observation.entity_id,))
        identifier = local.box.enqueue(local.conn, local.scope, local.binding.ref, observation, now=NOW)
    assert local.row(identifier)["state"] == "PENDING"


@pytest.mark.parametrize("kind", ["interaction", "live_event"])
def test_duplicate_identity_and_payload_survive_reopen_without_reencrypting(local, kind):
    observation = local.observation(entity_kind=kind)
    identifier = local.call("enqueue", observation)
    original = local.row(identifier)
    local.conn.close()
    local.conn = sqlite3.connect(local.path, isolation_level=None)
    local.box = Outbox(KEY)
    assert local.call("enqueue", observation) == identifier
    assert local.row(identifier) == original
    claim = local.call("claim")
    assert dict(claim.payload) == dict(observation.payload)
    assert claim.source_event_id == observation.payload["source_event_id"]
    with pytest.raises(TypeError):
        claim.payload["historical"] = False


@pytest.mark.parametrize("change", ["class", "deadline", "label"])
def test_duplicate_changed_content_conflicts_and_keeps_original(local, change):
    observation = local.observation()
    identifier = local.call("enqueue", observation)
    before = local.row(identifier)
    if change == "deadline":
        changed = replace(observation, deadline=NOW + timedelta(hours=12))
    else:
        payload = dict(observation.payload)
        payload["event_code" if change == "class" else "source_label"] = (
            "RESTRICTED_ZONE_ENTRY" if change == "class" else "Camera source · local observation")
        changed = replace(observation, payload=payload)
    with pytest.raises(OutboxError, match="OBSERVATION_CONFLICT"):
        local.call("enqueue", changed)
    assert local.row(identifier) == before


@pytest.mark.parametrize("field", ["installation_id", "organisation_id", "site_id"])
def test_foreign_scope_cannot_read_claim_mutate_or_redirect(local, field):
    identifier = local.call("enqueue", local.observation())
    foreign = replace(local.scope, **{field: str(uuid4())})
    before = local.row(identifier)
    for method, args, kwargs in (
        ("inspect", (), {}), ("claim", (), {}), ("withdraw", (identifier,), {"reason": "LOCAL_DELETED"}),
        ("set_paused", (), {"paused": True}), ("disconnect", (), {}),
    ):
        with pytest.raises(OutboxError, match="NOT_FOUND"):
            local.call(method, *args, scope=foreign, **kwargs)
    assert local.row(identifier) == before


def test_payload_identity_from_another_binding_is_not_authority(local):
    observation = local.observation()
    payload = dict(observation.payload)
    payload["source_event_id"] = source_event_id(installation_id=local.scope.installation_id, binding_id=str(uuid4()),
        organisation_id=local.scope.organisation_id, site_id=local.scope.site_id,
        entity_kind=observation.entity_kind, entity_id=observation.entity_id)
    with pytest.raises(OutboxError, match="SOURCE_ID_MISMATCH"):
        local.call("enqueue", replace(observation, payload=payload))


def test_aes_ciphertext_has_no_payload_and_swapping_records_fails_aad(local):
    first = local.call("enqueue", local.observation())
    second = local.call("enqueue", local.observation())
    ciphertext = local.row(first)["payload"]
    assert isinstance(ciphertext, bytes)
    for prohibited in (b"POSSIBLE_CONCEALMENT", b"source_label", b"historical", b"Camera 2"):
        assert prohibited not in ciphertext
    local.conn.execute("UPDATE cloud_sync_items SET payload=? WHERE id=?", (local.row(second)["payload"], first))
    # Ensure the tampered row is first without changing immutable admission.
    local.conn.execute("UPDATE cloud_sync_items SET next_ms=? WHERE id=?", (int((NOW + timedelta(minutes=1)).timestamp() * 1000), second))
    with pytest.raises(OutboxError, match="CIPHERTEXT_INVALID"):
        local.call("claim")
    assert local.row(first)["ever_attempted"] == 0
    assert local.conn.execute("SELECT count(*) FROM cloud_sync_capacity").fetchone()[0] == 0


def test_wrong_key_cannot_read_or_replace_saved_key(local):
    identifier = local.call("enqueue", local.observation())
    before = local.row(identifier)
    local.box = Outbox(b"x" * 32)
    with pytest.raises(OutboxError, match="KEY_UNAVAILABLE"):
        local.call("claim")
    assert local.row(identifier) == before


@pytest.mark.parametrize("field", ["admitted_ms", "deadline_ms", "admitted_iso", "deadline_iso"])
def test_aad_rejects_time_tampering_even_if_sql_immutability_is_bypassed(local, field):
    identifier = local.call("enqueue", local.observation())
    original = local.row(identifier)
    # Simulate raw DB corruption, bypassing the ordinary immutable-field guard.
    local.conn.execute("DROP TRIGGER cloud_sync_item_immutable")
    value = original[field] + 60000 if field.endswith("_ms") else "2026-09-16T12:00:00Z"
    local.conn.execute(f"UPDATE cloud_sync_items SET {field}=? WHERE id=?", (value, identifier))
    with pytest.raises(OutboxError, match="CIPHERTEXT_INVALID"):
        local.call("claim")
    row = local.row(identifier)
    assert row["state"] == "PENDING" and row["payload"] == original["payload"]
    assert row["ever_attempted"] == 0


def test_claim_preserves_exact_admitted_and_expiry_times_for_future_wire_payload(local):
    admitted = NOW + timedelta(microseconds=123456)
    observation = local.observation(at=admitted)
    local.call("enqueue", observation, now=admitted)
    claim = local.call("claim", now=admitted)
    assert claim.admitted_at == admitted and claim.deadline == observation.deadline
    assert claim.payload["occurred_at"] == admitted.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("field,value", [("device_id", str(uuid4())), ("site_id", "another-branch")])
def test_immutable_binding_columns_reject_accidental_sql_retarget(local, field, value):
    with pytest.raises(sqlite3.IntegrityError, match="immutable cloud binding"):
        local.conn.execute(f"UPDATE cloud_sync_bindings SET {field}=? WHERE id=?", (value, local.binding.ref.id))


@pytest.mark.parametrize("limit", ["pending_per_binding", "pending_per_installation", "records_per_binding", "records_per_installation", "daily_per_binding"])
def test_quota_failure_does_not_rollback_caller_entity_or_discard_queued_work(local, limit):
    local.box = Outbox(KEY, replace(Limits(), **{limit: 1}))
    first = local.call("enqueue", local.observation())
    second = local.observation()
    with transaction(local.conn):
        local.conn.execute("INSERT INTO local_observations VALUES(?)", (second.entity_id,))
        with pytest.raises(OutboxError, match="QUEUE_LIMIT|DAILY_LIMIT"):
            local.box.enqueue(local.conn, local.scope, local.binding.ref, second, now=NOW)
    assert local.conn.execute("SELECT count(*) FROM local_observations").fetchone()[0] == 1
    assert local.row(first)["state"] == "PENDING"
    assert local.conn.execute("SELECT count(*) FROM cloud_sync_items").fetchone()[0] == 1


@pytest.mark.parametrize("limit", ["bytes_per_binding", "bytes_per_installation"])
def test_byte_quota_is_enforced_before_write(local, limit):
    local.box = Outbox(KEY, replace(Limits(), **{limit: 1}))
    with pytest.raises(OutboxError, match="QUEUE_LIMIT"):
        local.call("enqueue", local.observation())
    assert local.conn.execute("SELECT count(*) FROM cloud_sync_items").fetchone()[0] == 0


def test_claim_lease_is_exclusive_and_reclaimed_with_identical_payload_after_restart(local):
    identifier = local.call("enqueue", local.observation())
    claim = local.call("claim", lease_seconds=5)
    assert claim.id == identifier and local.call("claim", now=NOW + timedelta(seconds=4)) is None
    local.conn.close()
    local.conn = sqlite3.connect(local.path, isolation_level=None)
    replacement = local.call("claim", now=NOW + timedelta(seconds=5))
    assert replacement.token != claim.token and dict(replacement.payload) == dict(claim.payload)
    assert local.row(identifier)["attempts"] == 2
    with pytest.raises(OutboxError, match="STALE_LEASE"):
        local.call("acknowledge", claim, receipt_id=str(uuid4()), now=NOW + timedelta(seconds=6))


def test_retry_preserves_ciphertext_and_enforces_due_time(local):
    identifier = local.call("enqueue", local.observation())
    ciphertext = local.row(identifier)["payload"]
    claim = local.call("claim")
    local.call("retry", claim, delay_seconds=10, now=NOW + timedelta(seconds=1))
    assert local.call("claim", now=NOW + timedelta(seconds=10)) is None
    next_claim = local.call("claim", now=NOW + timedelta(seconds=11))
    assert dict(next_claim.payload) == dict(claim.payload)
    assert local.row(identifier)["payload"] == ciphertext


def test_pause_resume_keeps_backlog_and_capacity_but_fences_old_publication_and_claim(local):
    observation = local.observation()
    identifier = local.call("enqueue", observation)
    old_ref = local.binding.ref
    claim = local.call("claim", lease_seconds=5)
    before = local.row(identifier)["payload"]
    local.binding = local.call("set_paused", paused=True, now=NOW + timedelta(seconds=1))
    assert local.call("claim", now=NOW + timedelta(seconds=2)) is None
    local.binding = local.call("set_paused", paused=False, now=NOW + timedelta(seconds=2))
    assert local.binding.ref.id == old_ref.id and local.binding.ref.generation > old_ref.generation
    assert local.call("claim", now=NOW + timedelta(seconds=3)) is None
    with pytest.raises(OutboxError, match="STALE_GENERATION"):
        local.call("acknowledge", claim, receipt_id=str(uuid4()), ref=old_ref, now=NOW + timedelta(seconds=3))
    with pytest.raises(OutboxError, match="STALE_ADMISSION"):
        local.call("enqueue", local.observation(), now=NOW + timedelta(seconds=3))
    resumed = local.call("claim", now=NOW + timedelta(seconds=5))
    assert resumed.id == identifier and dict(resumed.payload) == dict(observation.payload)
    assert local.row(identifier)["payload"] == before


def test_receipt_erases_payload_and_duplicate_receipt_cannot_change_identity(local):
    observation = local.observation()
    identifier = local.call("enqueue", observation)
    claim = local.call("claim")
    receipt = str(uuid4())
    local.call("acknowledge", claim, receipt_id=receipt)
    local.call("acknowledge", claim, receipt_id=receipt)
    assert local.row(identifier)["state"] == "RECEIVED" and local.row(identifier)["payload"] is None
    with pytest.raises(OutboxError, match="RECEIPT_CONFLICT"):
        local.call("acknowledge", claim, receipt_id=str(uuid4()))
    assert local.call("enqueue", observation) == identifier
    assert local.call("claim") is None


def test_unsent_delete_erases_payload_without_creating_remote_obligation(local):
    identifier = local.call("enqueue", local.observation())
    local.call("withdraw", identifier, reason="LOCAL_DELETED")
    assert local.row(identifier)["state"] == "CANCELLED" and local.row(identifier)["payload"] is None
    assert local.call("inspect")["withdrawal_obligations"] == 0
    assert local.call("claim") is None


def test_uncertain_send_withdrawal_uses_same_reserved_record_at_full_capacity(local):
    local.box = Outbox(KEY, replace(Limits(), records_per_binding=1, pending_per_binding=1))
    identifier = local.call("enqueue", local.observation())
    observation_hash = local.row(identifier)["observation_hash"]
    claim = local.call("claim", lease_seconds=5)
    local.call("withdraw", identifier, reason="LOCAL_DELETED", now=NOW + timedelta(seconds=1))
    assert local.call("inspect", now=NOW + timedelta(seconds=1))["withdrawal_obligations"] == 1
    assert local.call("claim", now=NOW + timedelta(seconds=1)) is None
    with pytest.raises(OutboxError, match="STALE_LEASE"):
        local.call("acknowledge", claim, receipt_id=str(uuid4()), now=NOW + timedelta(seconds=1))
    withdrawal = local.call("claim", now=NOW + timedelta(seconds=5))
    assert withdrawal.id == identifier and withdrawal.operation == "WITHDRAWAL"
    assert dict(withdrawal.payload) == {"source_event_id": claim.source_event_id, "reason": "LOCAL_DELETED"}
    assert local.row(identifier)["observation_hash"] == observation_hash
    local.call("acknowledge", withdrawal, receipt_id=str(uuid4()), now=NOW + timedelta(seconds=6))
    assert local.row(identifier)["payload"] is None and local.row(identifier)["state"] == "WITHDRAWN"
    assert local.call("inspect", now=NOW + timedelta(seconds=6))["withdrawal_obligations"] == 0


@pytest.mark.parametrize("attempted", [False, True])
def test_expiry_never_sends_old_observation_and_preserves_uncertain_obligation(local, attempted):
    observation = replace(local.observation(), deadline=NOW + timedelta(seconds=3))
    identifier = local.call("enqueue", observation)
    if attempted:
        local.call("claim")
    local.call("expire", now=NOW + timedelta(seconds=3))
    row = local.row(identifier)
    assert row["operation"] == ("WITHDRAWAL" if attempted else "OBSERVATION")
    assert row["state"] == ("PENDING" if attempted else "EXPIRED")
    if not attempted:
        assert row["payload"] is None


@pytest.mark.parametrize("restored", [False, True])
def test_disconnect_and_restore_never_repoint_or_resume_and_keep_obligations(local, restored):
    identifier = local.call("enqueue", local.observation())
    local.call("claim")
    local.binding = local.call("disconnect", restored=restored)
    assert local.binding.state == ("RESTORED" if restored else "DISCONNECTED")
    assert local.row(identifier)["operation"] == "WITHDRAWAL"
    assert local.call("inspect")["withdrawal_obligations"] == 1
    assert local.call("claim") is None
    with pytest.raises(OutboxError, match="BINDING_DISCONNECTED"):
        local.call("set_paused", paused=False)
    assert local.binding.target == local.target


def test_rejected_observation_becomes_withdrawal_and_failed_withdrawal_stays_visible(local):
    identifier = local.call("enqueue", local.observation())
    claim = local.call("claim")
    local.call("reject", claim, code="REMOTE_CONFLICT")
    withdrawal = local.call("claim")
    assert withdrawal.operation == "WITHDRAWAL"
    local.call("reject", withdrawal, code="REMOTE_REJECTED")
    row = local.row(identifier)
    assert row["state"] == "BLOCKED" and row["payload"] is None
    assert local.call("inspect")["withdrawal_obligations"] == 1


def test_clock_rollback_never_reclaims_a_lease_early(local):
    local.call("enqueue", local.observation())
    local.call("claim", now=NOW + timedelta(seconds=5))
    with pytest.raises(OutboxError, match="CLOCK_ROLLBACK"):
        local.call("claim", now=NOW + timedelta(seconds=4))


def test_new_target_needs_new_binding_and_old_capacity_is_not_adopted(local):
    identifier = local.call("enqueue", local.observation())
    local.call("claim", lease_seconds=5)
    with transaction(local.conn):
        with pytest.raises(OutboxError, match="BINDING_CONFLICT"):
            local.box.create_binding(local.conn, local.scope, binding_id=str(uuid4()), expected_generation=0,
                target=replace(local.target, device_id=str(uuid4())), now=NOW)
    local.binding = local.call("disconnect")
    old = local.binding
    with transaction(local.conn):
        binding = local.box.create_binding(local.conn, local.scope, binding_id=str(uuid4()), expected_generation=0,
            target=replace(local.target, device_id=str(uuid4())), now=NOW)
        local.binding = local.box.set_paused(local.conn, local.scope, binding.ref, paused=False, now=NOW)
    local.call("enqueue", local.observation())
    assert local.call("claim", now=NOW + timedelta(seconds=4)) is None
    assert local.call("claim", now=NOW + timedelta(seconds=5)).id != identifier
    assert local.call("inspect", ref=old.ref, now=NOW + timedelta(seconds=5))["withdrawal_obligations"] == 1


def test_pruning_is_bounded_and_retains_pending_or_uncertain_rows(local):
    cancelled = [local.call("enqueue", local.observation()) for _ in range(3)]
    for identifier in cancelled:
        local.call("withdraw", identifier, reason="LOCAL_DELETED")
    pending = local.call("enqueue", local.observation())
    claim = local.call("claim")
    assert claim.id == pending
    assert local.call("prune_terminal", now=NOW + timedelta(days=30)) == 0
    assert local.call("prune_terminal", now=NOW + timedelta(days=31), limit=1) == 1
    assert local.call("prune_terminal", now=NOW + timedelta(days=31)) == 2
    assert local.row(pending)["state"] == "LEASED"
    assert local.call("inspect", now=NOW + timedelta(days=31))["total_records"] == 1


def test_prune_requires_confirmed_withdrawal_and_keeps_original_receipt(local):
    identifier = local.call("enqueue", local.observation())
    claim = local.call("claim")
    original_receipt = str(uuid4())
    local.call("acknowledge", claim, receipt_id=original_receipt)
    assert local.call("prune_terminal", now=NOW + timedelta(days=31)) == 0
    local.call("withdraw", identifier, reason="LOCAL_EXPIRED", now=NOW + timedelta(days=31))
    withdrawal = local.call("claim", now=NOW + timedelta(days=31))
    local.call("acknowledge", withdrawal, receipt_id=str(uuid4()), now=NOW + timedelta(days=31))
    assert local.row(identifier)["observation_receipt_id"] == original_receipt
    assert local.call("prune_terminal", now=NOW + timedelta(days=61)) == 0
    assert local.call("prune_terminal", now=NOW + timedelta(days=62)) == 1


def test_default_terminal_capacity_supports_31_days_without_reserving_payload_forever(local):
    count = Limits().daily_per_binding * 31
    assert Limits().records_per_binding >= count + Limits().pending_per_binding
    identifier = local.call("enqueue", local.observation())
    local.call("withdraw", identifier, reason="LOCAL_DELETED")
    old = local.row(identifier)
    # A bounded synthetic fixture of retained terminal receipts tests the real
    # SQL quota query without 15,500 independent commits/encryptions.
    local.conn.execute("DELETE FROM cloud_sync_items WHERE id=?", (identifier,))
    columns = list(old)
    stamp = int((NOW - timedelta(days=2)).timestamp() * 1000)
    rows = []
    for _ in range(count):
        row = {**old, "id": str(uuid4()), "entity_id": str(uuid4()), "source_event_id": str(uuid4()),
               "created_ms": stamp, "terminal_ms": stamp}
        rows.append(tuple(row[column] for column in columns))
    with transaction(local.conn):
        local.conn.executemany("INSERT INTO cloud_sync_items(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")", rows)
    added = local.call("enqueue", local.observation())
    assert local.row(added)["state"] == "PENDING"
    summary = local.call("inspect")
    assert summary["total_records"] == count + 1 and len(summary["items"]) == 200


@pytest.mark.parametrize("key", [None, b"short", "x" * 32, bytearray(32)])
def test_missing_or_invalid_key_has_no_plaintext_fallback(key):
    with pytest.raises(OutboxError, match="KEY_UNAVAILABLE"):
        Outbox(key)


@pytest.mark.parametrize("origin", ["http://example.test", "https://user:secret@example.test", "https://example.test/path", "https://example.test?token=x", "https://example.test/#x"])
def test_target_cannot_embed_credentials_or_path(origin):
    with pytest.raises(OutboxError, match="INVALID_TARGET"):
        Target(origin, *(str(uuid4()) for _ in range(4)))
