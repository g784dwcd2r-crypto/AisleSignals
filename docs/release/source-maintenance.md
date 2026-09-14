# Recurring cloud source maintenance

Implemented as an explicit lifecycle component for FR-015/016. Importing or
constructing it performs no database work and starts no thread. This change does
not modify the application lifespan, configuration, routes, local outbox, camera
processing or paid services. The application coordinator must wire the lifecycle
before claiming recurring physical cleanup is running.

## Integration contract

Create one `SourceMaintenance(app.state.control_store)` for the app, using its
existing `ControlStore` and validated `CloudSettings`. Call `start()` from app
startup after store construction; it returns `True` only when it creates a new
thread. Duplicate starts return `False`. A missing database/key or wrong schema
produces a bounded retry status; it does not change readiness or fabricate
successful deletion. Migrations still run through the existing startup process.

In an asynchronous lifespan, run synchronous `stop(timeout=6)` through
`asyncio.to_thread` during shutdown. Stop signals the owner, cancels only its
published database connection using a bounded PostgreSQL cancellation request,
wakes backoff immediately and joins the thread. It returns `True` only once the
thread and any one-pass call have finished. A `False` result is a real shutdown
failure: keep that ownership visible and do not start a replacement worker over
it. A completely stopped instance can be started again deliberately.

For an explicit, nonrecurring operation, call `run_once()` without starting the
thread. It returns an immutable `PassResult` with a fixed status, aggregate
device/row counts and an optional fixed error code. Do not run it inside another
database transaction. The class owns its transactions, and publishes counts only
after commit. No public endpoint or account credential is part of this contract.

Read-only properties are `running`, `last_result` and `retry_delay_seconds`.
Statuses are COMPLETED, PARTIAL, BUSY, CANCELLED and UNAVAILABLE. Error codes are
DEVICE_RETRY, DATABASE_UNAVAILABLE or MAINTENANCE_FAILED. No exception text,
query, DSN, credentials, device names, source narratives or branch directory is
returned or logged. These statuses describe maintenance, never CCTV monitoring.

## Scope, ownership and fairness

Every pass takes PostgreSQL advisory transaction lock `6802449210735` with the
nonblocking try operation. Another maintenance instance/process returns BUSY
without processing devices. A local lock also excludes overlapping calls on one
instance. The advisory lock releases on commit, rollback or connection loss;
there is no persistent lease to abandon or unrelated process to adopt.

The pass pages actual stored device IDs in UUID order. Its in-memory cursor
advances past both processed and contended devices, and wraps when the end is
reached. An instance restart resumes at the beginning, safely rechecking
idempotent cleanup. A busy early device therefore does not starve later devices
in a running instance. The cursor contains no client-supplied authority and is
not a cross-instance durable job queue.

For each candidate, acquire its current organisation's SHARE lock first, then
the matching current device's UPDATE lock, both with SKIP LOCKED. Only that
locked row is passed to frozen `device_sync.cleanup_device_sources`. This
matches the existing organisation→device ordering and avoids blocking staff
administration unnecessarily. Revoked devices, inactive pharmacies and laptops
with no heartbeat remain eligible: loss of camera connection or bearer access
does not cancel their storage-cleanup obligation.

Each device runs inside a savepoint. A statement/lock failure rolls that device
back, advances the fair cursor and allows later devices to progress. A whole
connection/commit failure rolls back the pass, leaves the cursor unchanged and
reports zero committed counts. Cancellation also rolls back uncommitted work.
Source deletion is idempotent, so a later pass can safely repeat any uncertain
commit. No source can be reintroduced by this coordinator.

## Bounded work and retention

`MaintenancePolicy` defaults to 20 devices per pass, 100 alert rows and 100
terminal receipts per device, a 30-second interval, a five-second cooperative
pass budget, one-second statement limits and 200 ms lock limits. Configured
values are bounded: at most 100 devices, 500 rows/device, 30 seconds/pass, five
seconds/statement and one second/lock. Existing ControlStore connection capacity,
three-second connection timeout, schema validation and transaction limits also
apply. The component shares that store's capacity instead of introducing a new
pool. It stops beginning another device when its pass budget is exhausted and
tightens statement limits as that budget ends.

The cooperative budget is not an absolute network or real-time deletion
deadline: an in-flight connection, statement, commit or cancellation must still
finish within its own mechanism, and interrupted service cannot perform work.
At steady state a full scan takes approximately
`ceil(device_count / devices_per_pass) × interval`, plus processing and any
retries. Larger per-device backlogs require further fair passes. Contention and
errors remain visible in results. PARTIAL/UNAVAILABLE retry with exponential
backoff capped at 300 seconds by default; a successful or BUSY pass uses the
normal interval. Stop interrupts the wait rather than sleeping until its end.

The frozen cleanup helper nulls expired/withdrawn source event codes, generated
titles, camera labels and capture timestamps. It deletes unreviewed source shells
without cases, preserves independently authored notes/review details/cases, and
keeps reviewed shells as unavailable references. Terminal receipt metadata is
pruned only after the existing 31-day replay horizon. Recent receipts are kept.
Read-time source hiding remains immediate and independent of this physical
maintenance pass. No heartbeat, monitoring flag, alarm or staff record is
invented or refreshed.

## Verification

Run only with a process-owned disposable PostgreSQL cluster:

```sh
CLOUD_RUN_POSTGRES_TESTS=1 ../AisleSignals-camera-release/.venv/bin/python \
  -m pytest tests/cloud/test_source_maintenance.py -q --tb=short
```

The synthetic tests cover physical expiry without any further API traffic,
preserved staff case notes, recent versus old receipt retention, bounded/fair
device and row batches, revoked/inactive/offline devices, organisation/device
contention, another owner's advisory lock, per-device rollback, SQL timeout,
transient failure/backoff/recovery, no post-stop work, active-query cancellation
and clean restart. No existing preview, cloud account or customer data is used.
App lifespan integration and its independent validation remain the coordinator's
next step; this module alone is not deployment evidence.

Authoring run: **22 passed in 2.75 seconds**, with two existing dependency
deprecation warnings. The owned disposable database was stopped and removed.
