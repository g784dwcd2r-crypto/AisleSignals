# Cloud device transport foundation

Status: implemented locally as an unconnected library, 14 September 2026.
No application route, background sender, automatic observation upload or automatic
enrolment is enabled by this library. Its methods make individual explicit requests
when called; the online deployment is unchanged by these local files.

`services.api.cloud_transport.CloudTransport(origin)` accepts an exact HTTPS
origin. Explicit `allow_local_test=True` permits HTTP only on loopback for owned
synthetic tests. The application must never expose that option as a browser
request field. Credentials are supplied per call and are not stored on the
object, in cookies, URLs or files. One-use enrolment codes are 43 URL-safe
characters; device credentials issued by the current server are 64. These are
validated separately against the actual server contract.

- `identity(credential, expected_device_id=...)` performs a GET to the fixed
  identity route and validates the complete server-bound device/organisation/
  pharmacy identity. A different device ID is an explicit mismatch. Names are
  display values; they do not establish a local-to-cloud branch mapping.
- `enrol(code, name=..., platform=...)` makes exactly one POST. It requires the
  caller to preflight private credential storage and fresh local manager access.
  A lost or malformed response is uncertain enrolment, never an automatic retry.
- `heartbeat(credential, sequence=...)` sends only UNKNOWN monitoring, zero
  cameras and a fixed application version. The caller must durably reserve the
  sequence and enforce single-sender ownership. An exact retry has the same body.
- `observation(credential, payload, expires_at=..., expected_source_event_id=...,
  expected_receipt_id=None)` makes one POST to `/device-api/sync/v1/observations`.
  It reuses the pure mapper's exact five-field schema and adds only `expires_at`,
  serialized from the caller's stored aware datetime to UTC without losing
  microseconds. It rejects narrative labels, notes, media, authority fields,
  unapproved event codes, nonhistorical values and mismatched source UUIDs before
  opening a connection. The source UUID must be the known UUID5 from the scoped
  outbox claim. Its strict 201 receipt is `{id, received: true, source_state}`;
  source state is AVAILABLE, EXPIRED or WITHDRAWN. A known previous receipt ID
  must match on retry. The first receipt does not echo a source UUID, so the
  transport binds the outgoing request to the caller's expected source and does
  not claim to independently verify an unknown receipt's source association.
- `withdrawal(credential, payload, expected_source_event_id=...)` makes one POST
  to `/device-api/sync/v1/withdrawals`. Only `{source_event_id, reason}` is accepted;
  reason is LOCAL_DELETED, LOCAL_EXPIRED or LOCAL_EXPORT_REMOVED. The strict 200
  response must echo that exact UUID5 and `withdrawn: true`. Unknown delivery is
  still an obligation: lost, malformed or mismatched acknowledgements do not
  establish deletion and are never automatically retried by this transport.

Observation lifetimes must be positive and at most 24 hours from the immutable
`occurred_at`. No send-time clock renews the deadline. An already elapsed deadline
is allowed at this protocol boundary: the real server can acknowledge a replay
as EXPIRED without recreating an alert. The server also enforces its own future
clock tolerance and 30-day replay window. The local sender must separately
revalidate a fresh claim, its current binding, source existence and deadline
before any new observation send; accepting a protocol replay is not permission
to export expired local material.

Requests ignore ambient proxies and do not follow redirects. They verify TLS
using the standard HTTPS handler, use an eight-second socket-operation timeout,
limit requests to 4 KiB and responses to 64 KiB, and reject duplicate JSON keys,
nonfinite constants, unexpected response fields and malformed success receipts.
The socket timeout is not a claim of an absolute wall-clock deadline against a
server that slowly streams headers or bytes. Worker shutdown and overall request
cancellation remain integration requirements.

Errors expose fixed categories only, with optional bounded numeric Retry-After
for a rate limit. Server messages and bodies are never returned as user-facing
errors. SYNC_LIMIT, EVENT_CONFLICT and INVALID_EVENT_TIME remain fixed categories;
numeric Retry-After is bounded to 5–3,600 seconds. Identity, observation and
withdrawal do not refresh cloud connection health. No response can start
detection, arm speakers, create a case or grant local access.

The caller must perform HTTP outside SQLite/auth transactions, then revalidate
manager/branch authority, candidate expiry/revision and unchanged server identity
before committing a pairing. Private credential storage, pairing confirmation,
outbox sender ownership, withdrawal/retention scheduling and backup restoration are separate
integration work. This foundation must not be described as working end-to-end
alert sync.

For the outbox caller, pass `claim.payload`, `expires_at=claim.deadline` and
`expected_source_event_id=claim.source_event_id` for an OBSERVATION. Preserve a
previous observation receipt ID when known and pass it as `expected_receipt_id`.
After revalidating the live claim and authority, acknowledge with the returned
`id`; EXPIRED/WITHDRAWN source states must remain visible as unavailable and do
not imply current monitoring. For a WITHDRAWAL, pass the claim's exact stored
two-field payload and expected source ID. That route has no separate receipt
UUID: use the validated echoed `source_event_id` as the outbox withdrawal
acknowledgement identity. Do not substitute a current observation UUID, generate
a new one on retry, change the reason, or treat NETWORK_UNAVAILABLE as proof the
server did not receive the request.

The outbox's five-field canonical payload hash differs from the v1 six-field
wire payload hash. The latter includes `expires_at`; equivalent aware times are
normalized by the server. Exact retries retain the same stored payload, source
UUID and deadline. Transport JSON escaping is deterministic but its raw bytes
are not a replacement for the server's validated canonical digest.

Verification: `../AisleSignals-camera-release/.venv/bin/python -m pytest
tests/api/test_cloud_transport.py -q` passed 95 actual loopback/synthetic cases on
macOS. Tests exercise redirect non-forwarding, ignored proxy settings, exact
request bodies, identity/source/receipt mismatch, malformed/oversized responses,
bounded errors, one-attempt enrolment, strict mapping and exact deadline limits.
The owned real PostgreSQL/API integration additionally exchanges real synthetic
43-character enrolment codes for 64-character device credentials, validates
identity/UNKNOWN heartbeat/revocation, and exercises observation retry/conflict,
withdrawal-before-arrival, expiry and server clock refusal through an owned
loopback HTTP bridge. `CLOUD_RUN_POSTGRES_TESTS=1
../AisleSignals-camera-release/.venv/bin/python -m pytest
tests/cloud/test_cloud_transport_integration.py -q --tb=short` passed all four
journeys in 3.68 seconds (two existing dependency deprecation warnings).
No customer or production credentials are used. These
checks do not establish a running sender, real laptop acceptance or deployment.
