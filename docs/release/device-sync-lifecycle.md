# Cloud observation source lifecycle

This is the FR-015/016/020 metadata prerequisite for explicit laptop/cloud
synchronisation. It does not start a local worker, enable automatic exports,
upload footage, or prove camera monitoring. The application coordinator owns
router registration and browser presentation. All test data is synthetic.

## Device contract

Factory: `services.cloud.device_sync.create_device_sync_router()`. Mount it inside
the existing cloud app/security boundary. Authentication reuses
`device_transaction`: current bearer credential, active pharmacy, organisation
then device lock. Cookie authentication and body tenant identifiers confer no
device authority. Responses contain only the authenticated device's receipts.

`POST /device-api/sync/v1/observations` accepts exactly these six JSON fields:

```json
{
  "source_event_id": "11111111-1111-4111-8111-111111111111",
  "event_code": "POSSIBLE_PRODUCT_TAKE",
  "source_label": "Synthetic camera label",
  "occurred_at": "2026-09-14T12:00:00Z",
  "historical": true,
  "expires_at": "2026-09-14T13:00:00Z"
}
```

The first five fields are the existing observation metadata contract;
`expires_at` adds a hard source-availability deadline. Event codes use the
existing seven allowed review/coverage labels. The identifier must be a
canonical lowercase UUID. Camera labels are printable, nonblank strings up to
120 characters. `historical` must be boolean `true`; integers and string coercion
are rejected. Times must be aware ISO strings with seconds and at most six
fractional digits; they are normalised to UTC before hashing. Expiry must be
after capture and no more than 24 hours after capture.

New intake accepts capture times up to five minutes ahead of server time and no
more than 30 days old. An already expired source creates only a payloadless
receipt, never an attention alert. Response status is `201`, including retries:

```json
{"id":"22222222-2222-4222-8222-222222222222","received":true,"source_state":"AVAILABLE"}
```

`source_state` is `AVAILABLE`, `WITHDRAWN` or `EXPIRED`. `id` is stable for a
retained receipt and is the alert ID only when an alert was actually created.
Never assume that an `EXPIRED`/`WITHDRAWN` receipt has a retrievable alert. A
canonical immutable hash covers all six fields, including expiry. Identical
retries return the same ID with current source state; changed content returns
`409 EVENT_CONFLICT`. A retry cannot extend expiry or convert historical data to
live monitoring. Equivalent UTC offsets hash identically.

`POST /device-api/sync/v1/withdrawals` accepts exactly:

```json
{"source_event_id":"11111111-1111-4111-8111-111111111111","reason":"LOCAL_DELETED"}
```

Allowed reasons are `LOCAL_DELETED`, `LOCAL_EXPIRED`, `LOCAL_EXPORT_REMOVED`.
Response is always the device-scoped idempotent receipt:

```json
{"source_event_id":"11111111-1111-4111-8111-111111111111","withdrawn":true}
```

Withdrawal can arrive before the observation. The tombstone prevents later v1
arrival from creating an alert. Its first valid arriving payload fixes the hash;
subsequent changed payloads conflict. First withdrawal time/reason are preserved
on repeats. Withdrawing another device's UUID affects only the calling device's
namespace and does not disclose whether another device has that UUID.

Legacy `/device-api/alerts` continues its normal behavior for ordinary alerts.
It rejects IDs already bound to lifecycle receipts or unavailable source shells
with `409 SOURCE_UNAVAILABLE`; it cannot restore withdrawn or expired sources.
An existing legacy source cannot be silently adopted by v1 with a different
expiry/hash. Legacy sources can be explicitly withdrawn through the v1 endpoint.

Schema errors return the existing secret-safe `422` response. Out-of-window
capture returns `422 INVALID_EVENT_TIME`; daily/capacity quotas return
`429 SYNC_LIMIT`. Revoked devices/inactive pharmacies return `401` through the
existing authentication dependency. There is no public cleanup route.

## Staff reads and retained reviews

Alert lists, dashboard attention counts, details, acknowledgements and reviews
exclude unavailable sources immediately using database wall clock, independently
of physical cleanup. Direct unavailable alert reads/writes return the existing
bounded `404 NOT_FOUND` response without source details.

Alert DTOs add `timestamp_basis` (`LAPTOP_REPORTED` for v1;
`SOURCE_REPORTED` for legacy) and `source_expires_at` (aware server-known deadline,
`null` for ordinary legacy alerts). The browser must use this deadline to remove
cached source details when expiry passes, including offline, and clear details
on fresh `404/403` responses. This module alone cannot clear an already delivered
browser response or an offline client's cache after withdrawal.

Incident DTOs always include boolean `source_unavailable`. Independently authored
case titles, classifications, notes and review authors/times remain available
under the same pharmacy/organisation permissions. Normal incident editing still
works. Withdrawal immediately nulls source event code, generated title, camera
label and capture time. Expiry cleanup does the same. Unreviewed source shells
without an incident are deleted; reviewed shells are retained as references for
staff records, with source metadata removed. Review notes are not silently
deleted. Their retention remains the existing staff-record policy, separate
from this short-lived source policy.

## Bounded retention and cleanup

Migration `004_device_sync.sql` adds lifecycle columns and the device-scoped
`device_sync_receipts` table. Migrations 001–003 remain unchanged; the existing
cumulative checksum mechanism binds migration 004 to its predecessors.

Receipt rows contain IDs, opaque immutable hash, timestamps, a first-withdrawal
reason and origin flag—no camera label, event code, captured narrative, frames or
credentials. Maximum new intake is 1,000 source IDs per device per day, counting
legacy alerts too. Unknown withdrawal-first IDs have a separate 1,000/day limit;
late arrival cannot reset that quota. A 64,000-receipt cap bounds new admissions.
Known source withdrawals and existing receipt retries bypass admission quotas
so saturation does not prevent source removal.

`cleanup_device_sources(conn, device, limit=100)` requires the organisation then
device lock held by the caller. It processes at most 100 unavailable alert
sources and 100 terminal receipt rows per call (configurable integer 1–500).
Intake/withdrawal call it opportunistically. A future maintenance coordinator can
call the same helper for a locked device; no background scheduler is installed
by this module. Thus read-time hiding is immediate, while physical expiry purge
requires the next cleanup pass. Deployment must arrange recurring cleanup if
physical deletion deadlines must hold while a laptop is disconnected.

Payloadless terminal receipts are pruned 31 days after expiry or first
withdrawal; repeats do not renew those timestamps. Original old payloads remain
inadmissible after pruning because new intake/legacy routes reject capture times
older than 30 days. The bounded replay horizon does not promise indefinite UUID
memory against a malicious device forging an entirely new capture time after
pruning. Keeping all retired IDs forever would violate bounded source retention.
Reviewed source shells remain explicitly unavailable even after receipt pruning.

## Validation

Run against a process-owned disposable database, never an inherited cloud URL:

```sh
CLOUD_RUN_POSTGRES_TESTS=1 PATH="$(pg_config --bindir):$PATH" \
  ../AisleSignals-camera-release/.venv/bin/python -m pytest \
  tests/cloud/test_device_sync.py tests/cloud/test_control_operations.py \
  tests/cloud/test_postgres.py tests/cloud/test_control_auth.py -q
```

Tests cover strict metadata/coercion limits, future and expired arrivals,
canonical immutable retries, withdrawal before arrival, legacy replay guards,
immediate expiry filtering before cleanup, reviewed case/notes preservation,
separate authenticated organisations/devices, revocation while a request waits,
quota saturation, and bounded 31-day tombstone cleanup. Runtime camera quality,
offline worker delivery and physical laptop readiness are outside this module.

Authoring verification: **98 passed** across lifecycle, existing operations and
cloud authentication suites with disposable PostgreSQL. The broader PostgreSQL
suite initially reported only its pre-existing exact table inventory missing
`device_sync_receipts`; the coordinator owns updating that assertion and rerunning
combined migration checks. Migration execution, repeat application and readiness
had already succeeded before that inventory assertion.
