# Explicit pilot laptop pairing

The local pharmacy application can now register its laptop with an explicitly selected HTTPS management console. This component supplies authentication and private connection material for later metadata delivery. It starts no sender, sends no observations or images, and never reports that CCTV monitoring is working. Synthetic/demo mode cannot pair.

## Staff flow

1. A currently authorised local pharmacy manager enters the exact HTTPS console origin, a new one-use laptop enrolment code, the laptop name expected by that code, and their current local manager passphrase.
2. The app validates private storage before consuming the code. It consumes the code once, stores the returned credential and a new 32-byte outbox key privately, then fetches the device's authenticated cloud identity. HTTP runs after the local authentication transaction has closed.
3. The manager reviews the cloud organisation, pharmacy and device alongside the current local branch. Confirmation requires those exact IDs and origin, the current branch ID, the same signed-in session and another passphrase check. The app rechecks the cloud identity and current local authority before atomically creating a **PAUSED** binding.
4. Resume is explicit. It reauthenticates the manager, verifies the same remote organisation/pharmacy/device, and rechecks the current local generation before enabling metadata admission. Pause always fences an in-flight resume, including when already paused or when a private key is missing.
5. Disconnect closes the local binding and preserves unresolved withdrawal obligations. It does not claim that remote sources were deleted. The manager must inspect the prior cloud device and any pending removals in the online console. A disconnected or restored binding cannot resume; re-enrolment creates a new binding and source identity namespace.

Only one ACTIVE/PAUSED binding is allowed per installation. Another branch can see that the laptop is occupied but cannot read or control that branch's remote identity. Preparing for one cloud organisation never gives that organisation authority over local pharmacy accounts or evidence.

A code can be consumed even when the network response, local file write or final authority check fails. Such a preparation is recorded as **UNCERTAIN** when possible. No automatic enrolment retry occurs. Inspect the selected console's Laptops page and revoke any unconfirmed registration before requesting a new code. If the response was lost, the manager must locate the registration by the entered laptop name; the local app cannot manufacture its device ID or credential.

Preparations expire after five minutes and are single-use, session-bound confirmations. The bounded local journal retains at most 32 preparation records/private handle reservations and prevents retrying previously attempted codes. It has no automatic destructive cleanup or reset route; reaching this limit requires local operator review of previous registrations. Private files are immutable create-new records. Unconfirmed/uncertain credentials remain private for review and are not used by the sender.

## Public local API

All routes require a signed-in MANAGER in protected pilot mode. Writes also use the existing Origin, CSRF and current-branch header checks. Every write requires `manager_password`; five incorrect reauthentication attempts are persistently limited for fifteen minutes. Values are strict JSON with unknown fields refused.

| Method and path | JSON body | Result |
| --- | --- | --- |
| GET `/api/cloud-connection` | None | Status envelope below |
| POST `/api/cloud-connection/prepare` | `origin`, `code`, `name`, `manager_password` | 201 status with PREPARED identity |
| POST `/api/cloud-connection/confirm` | `preparation_id`, `origin`, `organisation_id`, `pharmacy_id`, `device_id`, `local_site_id`, `manager_password` | 200 status with PAUSED binding |
| POST `/api/cloud-connection/pause` | `binding_id`, `expected_generation`, `manager_password` | 200 current status |
| POST `/api/cloud-connection/resume` | Same as pause | 200 current status |
| POST `/api/cloud-connection/disconnect` | Same as pause | 200 current status |

Origins allow HTTPS only, without userinfo, query, fragment or non-root path; one trailing slash is normalised away. There is no browser `allow_local` override and no default console URL. Codes are exactly 43 URL-safe characters. Laptop names are printable text of 1–100 characters. IDs are canonical nonzero UUIDs, generations are strict integers from 1 through 2147483647, and passphrases are bounded secret inputs. The response never includes a device bearer token, encryption key, credential path or enrolment code.

Every success returns:

```text
{
  enabled: true,
  local_site: {id, name},
  connection: null | {
    binding_id, generation, state, origin,
    organisation_id, pharmacy_id, device_id,
    credential_available, error_code, identity: null | RemoteIdentity
  },
  preparation: null | {
    preparation_id, status, expires_at, origin, local_site_id,
    identity: null | RemoteIdentity, remote_device_id: null | UUID
  },
  occupied_elsewhere: boolean,
  monitoring_status: "UNKNOWN",
  delivery: {pending, received, blocked, withdrawal_pending, worker_running}
}
```

`RemoteIdentity` contains exactly `device_id`, `organisation_id`, `organisation_name`, `pharmacy_id`, `pharmacy_name`, `name`, `platform`, `app_version`. Connection states are ACTIVE, PAUSED, DISCONNECTED and RESTORED. Visible preparation states are PREPARING, PREPARED and UNCERTAIN; `expires_at` is an aware UTC ISO timestamp. Preparations are visible only to their originating session and branch. The identity in connection status is the confirmed identity snapshot, not a fresh remote health probe.

Delivery counts are scoped to the selected local binding. `pending` includes unacknowledged PENDING/LEASED observations; `received` counts actual observation receipt markers; `blocked` counts BLOCKED items; `withdrawal_pending` counts unresolved withdrawals including blocked ones. These counters can overlap. No-binding values are zero. `worker_running` reads the actual `app.state.cloud_delivery.running` lifecycle property if present; an alive worker is not evidence of successful delivery or camera monitoring.

Errors use the existing `{error:{code,message}}` shape. Important outcomes are:

- 401 REAUTH_REQUIRED (retain the valid session, clear the passphrase); ordinary authentication/session expiry still requires sign-in.
- 409 CONNECTION_BUSY/CONNECTION_EXISTS/CONNECTION_CHANGED, PREPARATION_EXPIRED, IDENTITY_CONFIRMATION_MISMATCH, REMOTE_IDENTITY_CHANGED, CODE_ALREADY_ATTEMPTED, CONNECTION_LIMIT or CONNECTION_CLOSED. Reload status; never blindly retry enrolment or an expired confirmation.
- 502 CONNECTION_UNCERTAIN: registration may have happened remotely; perform the console review described above. CONNECTION_CHECK_FAILED means confirmation/resume was not committed.
- 503 CONNECTION_STORAGE_UNAVAILABLE: private key/credential storage was unavailable. Pause remains possible without that key. Resume and disconnect cannot invent a replacement encryption key or a withdrawal receipt.

## Internal provider contract

`install_cloud_connection(application, context, problem)` runs **before** interaction-service construction and installs `application.state.cloud_connection: CloudConnection`. Construction performs no disk, network or thread work. Each route uses a short `contextmanager(context)(request)` transaction, closes it before transport, then opens a fresh authenticated context to recheck session, manager role, current site, installation and binding generation/fingerprint.

The sender and atomic entity hooks use these APIs only inside their own Store transaction:

```text
delivery_context(conn) -> DeliveryContext | None
source_context(conn, scope: Scope) -> DeliveryContext | None
pause_error(conn, ctx, *, code, now) -> bool
access_revoked(conn, ctx, *, now) -> bool
```

The frozen `DeliveryContext` fields are `scope: Scope`, `binding: BindingRef`, `target: Target`, `outbox: Outbox`, `credential: str`. Outbox and credential are excluded from its representation. Do not log or serialise the context. `delivery_context` resolves only the current ACTIVE pilot installation binding. It intentionally needs no logged-in staff session: explicit manager resume authorises background sharing while the app owns its sender. `source_context` also allows PAUSED for exact-scope local removal hooks; access to that material does **not** authorise sending. Both return None for synthetic, disconnected and restored bindings. Missing/corrupt private material raises `ConnectionUnavailable` with a fixed code.

Every resolution verifies the installation UUID, local organisation/site, binding ID, opaque handle, exact origin, device ID and immutable key hash. Hooks must additionally compare the stored source entity's organisation/site to the returned scope before mapping or enqueueing. No remote response or nested client metadata establishes local scope.

`pause_error` supports fixed ACCESS_REVOKED, KEY_UNAVAILABLE, CIPHERTEXT_INVALID and CLOCK_ROLLBACK codes. It fences only the exact still-current ACTIVE context and records a bounded local error. It invalidates leases/generation without clearing an actual in-flight sender's separate capacity reservation. Source existence checks and sender ownership remain the responsibility of the separately integrated hooks/worker.

## Storage and recovery

The private directory is adjacent to the selected database, with suffix `.cloud-private`. Each immutable `<opaque-UUID>.json` contains a bound local/device identity, the 64-character device credential and a base64 encoding of a random 32-byte AES key. Keys are not passwords and are not stored in SQLite. Files reuse the companion's held-descriptor no-follow, owner/mode and hard-link checks on POSIX and its native ACL/owner/reparse/handle adapter on supported Windows storage. Existing files/permissions are never overwritten or silently repaired. Native Windows acceptance belongs to the actual native test run, not the Mac tests alone.

The journal stores only bounded IDs/identity snapshots, authentication/change fingerprints, code hashes and preparation state. Existing evidence backup allowlists exclude the private directory. After recovery, bindings are RESTORED and credentials/key material are absent; session-bound preparations cannot authorise a new restored session. Automatic cloud withdrawal cannot resume from a recovered archive. Preserve explicit management obligations and review the old remote device; no restore or disconnect response is a deletion receipt.

Pairing uses the existing transport's exact-origin, no-redirect, no-cookie, no-proxy and bounded-response behavior. Its eight-second socket timeout is not a hard overall deadline against a slow remote response. No camera frames or local database content are sent by pairing.

## Verification

```sh
python -m pytest tests/api/test_cloud_connection.py tests/api/test_cloud_outbox.py tests/api/test_pilot_admin.py -q
```

The tests use actual authenticated local API calls, process-owned loopback HTTP servers, disposable private paths and synthetic identities. They cover exact remote confirmation, separate local authority, CSRF/current-site checks, persistent reauthentication limits, no SQLite/auth lock during HTTP, revocation during prepare/confirm, single-use/expired confirmation, lost registration/write outcomes, pause versus in-flight resume, private-path/key refusal, scoped delivery counters, inactive/synthetic providers and secret-free responses. Pairing success does not establish detector accuracy, operating-system commissioning or a deployed sender.
