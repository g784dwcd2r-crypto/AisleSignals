# Local observation sender

This component implements recurring delivery of the already admitted encrypted
outbox. It sends only historical observation metadata and withdrawals. It never
uploads media or staff notes, infers identity, starts camera/model work, arms an
alarm, creates a reviewed case or reports active CCTV monitoring. There is no
heartbeat in this wave. Pairing, eligible source admission and atomic deletion
hooks are independently owned prerequisites.

## Lifecycle and provider

`CloudDelivery(store, provider, source_state=hooks.source_state)` performs no work
at construction. The coordinator stores it as `app.state.cloud_delivery`, calls
`start()` once in the pilot app lifespan, and calls `stop(timeout=6)` through
`asyncio.to_thread` during shutdown. `start()` returns false if a worker/request
or one-pass call is already running. `running` means its actual thread is alive;
it is never a camera-monitoring claim. `last_result` holds only the recurring
loop's latest bounded result. Explicit callers can use `run_once()` without
starting a thread and inspect its returned `DeliveryResult`.

The trusted provider's exact contract is:

- `delivery_context(conn)` returns `None` or a frozen context containing `scope`,
  `binding`, `target`, `outbox` and a credential excluded from repr. It validates
  private material against the installation, current local branch, immutable
  cloud target and key fingerprint within the owning Store transaction. It
  grants sending only for ACTIVE bindings in pilot mode.
- `access_revoked(conn, context, now=...)` pauses/fences only that exact current
  binding. No automatic retry follows an unauthorized/revoked bearer response.
- `pause_error(conn, context, code=..., now=...)` fences key/ciphertext/clock
  faults with a bounded error. Missing keys, corrupt ciphertext and backward
  clock failures stop this sender until an explicit restart/repair; it never
  replaces keys or skips a corrupt head.

The mandatory `source_state(conn, context, entity_kind, entity_id)` callback
returns AVAILABLE, DELETED, EXPIRED or INELIGIBLE. It must query the exact current
stored entity under context.scope and validate current source provenance and
eligibility, including recording exclusion. It must preserve valid previously
admitted backlog after pause/resume; do not rebuild its admission using a new
control generation or activation time. Other callback values fail closed.

Restored, synthetic, paused and disconnected bindings never send. Separate
local expiry/deletion hooks must still use the provider's ACTIVE/PAUSED source
context to erase expired queued payloads while sending is disabled. This sender
does not turn a pause into permission for withdrawal traffic.

## Transaction and uncertainty boundaries

Each pass takes a nonblocking OS lock at the protected database-derived path
`<database>.cloud-delivery.pilot-lock`, using the existing cross-platform pilot
lock implementation. This is separate from the API's lifetime lock. It prevents
another sender instance/process from starting a request while this owner still
has a live child. The outbox's durable capacity and lease provide an additional
restart fence, rather than being mistaken for proof that an HTTP request ended.

Inside the initial Store transaction the sender resolves current provider
authority, checks a fair bounded page of existing observation sources,
withdraws/cancels unavailable sources, prunes a bounded terminal metadata batch,
and claims exactly one due observation/withdrawal. No HTTP or child process runs
inside that transaction. Outbox IDs, encrypted bytes and deadlines are reused;
the sender does not remap or invent an observation.

After child startup, a fresh transaction rechecks the same scope, binding
generation, target, credential, exact lease token/phase, current source and
deadline before any credential/payload is released to the child. The same check
runs periodically during the request. Source deletion, expiry or invalidation
converts the observation into a withdrawal obligation. A binding/generation
change cancels the client and cannot publish a late receipt. Current binding
clock rollback prevents sending.

After the owned child has exited, another fresh transaction revalidates those
boundaries before acknowledging an actual receipt. Observation receipts use the
server's stable receipt ID; withdrawal acknowledgements use the validated echoed
source UUID. Source existence is required for observations, not for withdrawals.
Late/stale receipts are not applied to a new claim. A lost response may mean the
server committed: the same UUID/body/deadline is retried, or withdrawal is sent
if the source was deleted/expired. No timeout, local deletion, pause or restore
is represented as a remote deletion receipt.

Access revocation immediately fences the current binding. Permanent rejection
or exhausted observation retries moves the record into withdrawal, preserving
the uncertain-send obligation. Exhausted/rejected withdrawal becomes BLOCKED
without a fabricated receipt; its obligation remains in the durable outbox for
management resolution. Such failures are visible, not silently discarded.

## Overall request bound and packaging

Every request uses the explicit multiprocessing **spawn** context, including on
macOS/Linux. The standalone module-level `_request_child` target imports no app
and opens no database/default runtime. The child receives only a private pipe
at startup. It receives the exact allowlisted request after the parent validates
fresh authority. Credential/payload travel through bounded in-memory JSON IPC,
never argv, environment, URLs, files or logs. Parent and child limit messages to
8 KiB and validate result types/receipt shape; the child reuses strict
`CloudTransport` validation, exact HTTPS origin, fixed routes, normal certificate
verification, no ambient proxies/cookies/redirects and bounded HTTP responses.

The parent imposes a monotonic deadline covering child startup and the entire
request, including DNS, TLS, headers and slow response bodies. An independent
watchdog terminates and then kills/joins only that child if necessary, even when
the parent is waiting inside a source/SQLite revalidation callback. Cancellation
uses the same mechanism. The termination/reap allowance is up to one additional
second in normal OS operation. The parent retains its OS sender lock until the
child is confirmed gone. A child that cannot be killed/reaped causes
REQUEST_STOP_FAILED, a stopped sending gate and `stop()` returning false; no
replacement request is allowed over it. Blocking local database work can delay
the overall pass/shutdown response, but cannot extend its live HTTP client.

The frozen executable's **earliest entrypoint must call
`multiprocessing.freeze_support()` before command parsing, app construction or
service startup**. Package `services.api.cloud_delivery` and its statically
imported transport/mapper dependencies plus Python's multiprocessing support.
This prevents spawned frozen children from entering normal API/launcher startup.
Root owns that entrypoint/build integration. Source-mode tests use spawn already;
Windows CI and a frozen executable smoke test are required before claiming
packaged delivery support. No fork-only assumption or cloud credential setup
belongs in the child entrypoint.

## Bounds and operational meaning

Defaults: one request/pass, two-second loop cadence, eight-second request
deadline, 20-second claim lease, 250 ms local revalidation, 50 source records per
reconciliation page and 100 terminal records per pruning call. Observation
leases are clipped to source expiry, and sending requires a two-second margin
for child cleanup. Source reconciliation has an in-memory UUID cursor; restart
rescans safely, and a large backlog is handled over multiple passes. The frozen
outbox additionally performs its bounded-capacity expiry transition during
claiming; source callbacks must not open network requests.

Transient requests retain exact payloads and use persisted exponential retry
times starting at five seconds, capped at 300 seconds by default. A validated
server Retry-After can extend that wait up to 3,600 seconds. Default observation
attempt limit is eight; the record's total attempt limit is sixteen, including
the subsequent withdrawal lane. No per-pass request queue is created. Generic
local/database failures retry at ten-second loop intervals with a fixed opaque
DELIVERY_UNAVAILABLE result. Corruption/key/clock failures halt as above.

DELIVERED/WITHDRAWN means a validated receipt was committed locally, not that a
pharmacist reviewed an incident or that cameras currently work. FENCED means no
current claim may accept the result. A restored/offline/revoked branch still
needs the separate management/recovery workflow for unresolved obligations.

## Verification

Run the owned synthetic tests:

```sh
CLOUD_RUN_POSTGRES_TESTS=1 ../AisleSignals-camera-release/.venv/bin/python \
  -m pytest tests/api/test_cloud_delivery.py -q --tb=short
```

The fixtures generate a temporary CA/server certificate, serve actual HTTPS at
an owned random loopback port, and use that exact unchanged HTTPS Target. Only
the test process's TLS trust points to that temporary certificate. A real cloud
FastAPI/PostgreSQL bridge verifies committed observation receipts, loss after
commit, exact retry/deduplication, dashboard attention, explicit staff review and
case creation, retained staff notes after source withdrawal, deletion, expiry
and revoked device access.
Other tests exercise concurrent source/pause changes during requests, no SQLite
lock held during HTTP, repeated start/stop, actual process termination, a
deadline firing while authorization is blocked, callback/source exclusions,
restored/synthetic refusal, corrupt ciphertext, bounded dead-lettering and
truthful failed-shutdown ownership. No existing previews, customer data or
production cloud account are used.

Authoring run: **26 passed in 7.09 seconds**, with two existing dependency
deprecation warnings. The owned HTTPS servers, request children and disposable
PostgreSQL cluster were stopped and cleaned up. Packaged/native-platform checks
and app-level integration remain independent release checks.
