# Device identity for explicit pairing

Status: implemented locally and tested with disposable PostgreSQL. This is only
phase 1 of `cloud-sync-design.md`, supporting the FR-002/FR-008 scope boundary.
It does not implement local pairing, an outbox, observation forwarding, retention,
withdrawal, a sync worker or remote monitoring controls. Deployment and physical
laptop acceptance are separate.

## Contract

`GET /device-api/identity` requires the existing device bearer credential in the
`Authorization: Bearer …` header. It accepts no scope-selection fields and returns
exactly these JSON fields:

| Field | Server source |
| --- | --- |
| `device_id` | Authenticated, nonrevoked device ID |
| `organisation_id` | That device's assigned organisation ID |
| `organisation_name` | Current name from the assigned organisation |
| `pharmacy_id` | That device's assigned pharmacy ID |
| `pharmacy_name` | Current name from its active assigned pharmacy |
| `name` | Registered laptop name |
| `platform` | Registered `MACOS`, `WINDOWS` or `OTHER` value |
| `app_version` | Last app version reported by that device at enrolment/heartbeat |

Names are display information, not a rule for mapping local and cloud branches.
App version and platform are device-reported registration metadata, not verified
software inventory. A successful identity response establishes the credential's
current cloud target; it does not prove a physical laptop location, camera
coverage, monitoring activity, model accuracy or staff pairing approval.

The response is private (`Cache-Control: no-store`) and does not set a cookie.
There are no credentials, staff accounts, review notes, camera information,
other-device details or directory lists. Supplied query parameters or unrelated
scope headers cannot select another identity; only the bearer resolves scope.
There is no `/identity/{device_id}` route. The existing strict enrolment response
is unchanged.

Missing/invalid credentials, an enrolment code used as a credential, a revoked
device or an inactive assigned pharmacy receive the existing generic
`401 DEVICE_UNAUTHORIZED` response. A management-session cookie does not replace
a device bearer credential. Device credentials gain no management API access.

## Authority and state

The route reuses `device_transaction`: credential lookup, organisation share
lock, then the device row lock and current active-pharmacy/nonrevoked-device
check. Display names are joined inside the same transaction using only the
server-resolved IDs. Administration uses the same organisation-first order;
a pending request rechecks authority after a conflicting administration commit.

The route performs no record writes or audit insertions. It does not change
heartbeat sequence, last-seen time, monitoring state, camera count, session
activity, version, registration or credentials. Calling it repeatedly cannot
keep an absent laptop marked online. It may briefly hold the existing device row
lock, with the store's existing bounded lock/query timeouts.

## Verification

`tests/cloud/test_device_identity.py` exercises the real production router,
real device bearer validation and a new disposable PostgreSQL cluster containing
only synthetic records. Its 15 cases cover:

- Exact allowlisted identities across all three protocol platform values.
- Entire application-table snapshots remaining unchanged across repeated reads.
- Missing/wrong/short credentials, enrolment-code misuse and staff-cookie misuse.
- Device revocation and pharmacy deactivation, including committed changes while
  an identity request waits for the organisation authority lock.
- Forged scope headers/query parameters, another branch/device, and another
  organisation, without exposing directories or staff records.
- Current renamed pharmacy/app-version display with stable assigned IDs.
- Private response headers, no secret fields and unchanged enrolment shape.

Run with PostgreSQL's `initdb` and `pg_ctl` on PATH and the repository's cloud
Python dependencies installed:

```sh
PATH="$(pg_config --bindir):$PATH" CLOUD_RUN_POSTGRES_TESTS=1 python -m pytest tests/cloud/test_device_identity.py tests/cloud/test_control_operations.py -q
```

The fixture creates and stops its own cluster; it never connects to an inherited
customer database URL. These checks do not use external services, customer data,
footage, model processes or physical laptop credentials.
