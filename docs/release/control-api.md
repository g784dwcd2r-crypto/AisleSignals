# AisleSignals online control API

Implementation contract for the management console. All routes below use `/control-api`, same-origin JSON and opaque HttpOnly session cookies. Mutations require `X-CSRF-Token` from the authenticated session and an exact trusted Origin. Authentication/bootstrap mutations require Origin but obtain no session until MFA succeeds. Errors: `{ "error": { "code": "...", "message": "..." } }`. Collections return `{ "items": [...] }`. Dates are ISO UTC; nullable means unknown.

## Identity and administration

- `GET /setup/status` → `{configured:boolean, needs_setup:boolean}`. No account details.
- `POST /setup/begin` body `{token, organisation_name, name, email, password}` → `{challenge_token, totp_secret, totp_uri, expires_in_seconds}`. Requires private deployment bootstrap token and no existing owner.
- `POST /setup/complete` body `{challenge_token, code}` → Session; creates first organisation/owner after authenticator verification.
- `POST /login` body `{email,password}` → `{mfa_required:true,challenge_token,expires_in_seconds}`.
- `POST /login/mfa` body `{challenge_token,code}` → Session.
- `GET /session` → Session or401. Session is `{user:{id,name,email,role},organisation:{id,name},pharmacies:Pharmacy[],csrf_token}`.
- `POST /logout` body `{}` → `{ok:true}`; revoke session server-side.
- `GET /pharmacies` → scoped Pharmacy items. Pharmacy: `{id,organisation_id,name,address,timezone,active,version,created_at}`; timezone defaults Europe/Dublin.
- `POST /pharmacies` body `{name,address,timezone}` → Pharmacy (OWNER only).
- `PATCH /pharmacies/{id}` body `{expected_version,name?,address?,active?}` → Pharmacy (OWNER only).
- `GET /users` → User items (OWNER only). User: `{id,name,email,role,active,pharmacy_ids,version,created_at}`. Roles OWNER, MANAGER, REVIEWER.
- `POST /invitations` body `{email,name,role,pharmacy_ids}` → `{token,expires_at}`. UI creates same-origin `/#accept-invite?token=...` link and shows it once; owner shares privately. No automatic email.
- `POST /invitations/begin` body `{token,name,password}` → same TOTP enrolment challenge as setup.
- `POST /invitations/complete` body `{challenge_token,code}` → Session.
- `PATCH /users/{id}` body `{expected_version,active?,role?,pharmacy_ids?}` → User (OWNER only). Membership/role/disable changes revoke affected sessions. Preserve at least one active owner.

No bearer token or cached sensitive records in browser storage. A changed identity clears request continuations, cached rows, selection, dialogs and CSRF. Manual invite token is removed from the URL after consumption. Each invite is single use and expires; no default password or public first-visitor takeover.

## Operations

Read lists and dashboard accept optional `pharmacy_id` query; absent means all permitted active pharmacies. Every request resolves current permissions. Requested unauthorized pharmacy returns403/404. All detail/update paths recheck branch access, including nested records. Lists are bounded to newest200 and ordered newest first.

- `GET /dashboard` → `{generated_at,summary:{pharmacies,connected_laptops,total_laptops,open_alerts,reviewed_incidents},devices:Device[],alerts:Alert[],incidents:Incident[]}`; recent lists capped8. Counts reconcile to scoped records.
- `GET /devices` → Device items. Device: `{id,pharmacy_id,pharmacy_name,name,platform,app_version,last_seen_at,connection_status,monitoring_status,camera_count,version,revoked_at}`. Platform MACOS/WINDOWS/OTHER; connection ONLINE/OFFLINE/NEVER_CONNECTED/REVOKED; monitoring ACTIVE/STOPPED/DEGRADED/UNKNOWN. Heartbeat is fresh within120 seconds; stale connection never implies active monitoring.
- `POST /devices/enrolments` body `{pharmacy_id,name,platform}` → `{token,expires_at}` (OWNER/MANAGER). One-use10-minute laptop connection code, displayed once.
- `POST /devices/{id}/revoke` body `{expected_version}` → Device (OWNER/MANAGER); immediately invalidate device token.
- `GET /alerts` → Alert items; optional `status=OPEN|ACKNOWLEDGED|REVIEWED`. Alert: `{id,pharmacy_id,pharmacy_name,device_id,device_name,source_event_id,event_code,title,source_label,occurred_at,received_at,historical,status,version,review:null|{outcome,note,by,at},incident_id:null|string}`. All detections are observations requiring review. `historical` is always explicit.
- `GET /alerts/{id}` → Alert.
- `POST /alerts/{id}/acknowledge` body `{expected_version}` → Alert.
- `POST /alerts/{id}/review` body `{expected_version,outcome,note,create_incident,title?}` → `{alert:Alert,incident:Incident|null}`. Outcome NORMAL_SHOPPING/UNCLEAR/SUSPECTED_INCIDENT. Notes required; optional staff-created case remains unassessed evidence, never automatic theft finding. Repeated review uses version conflict; one case per alert.
- `GET /incidents` → Incident items. Incident: `{id,pharmacy_id,pharmacy_name,alert_id,title,classification,status,notes,reviewed_by,reviewed_at,created_at,version}`. Classification NORMAL_SHOPPING/UNCLEAR/SUSPECTED_INCIDENT. Status OPEN/CLOSED. Only reviewed observations create a case in this console.
- `GET /incidents/{id}` → Incident.
- `PATCH /incidents/{id}` body `{expected_version,status?,notes?}` → Incident. Notes/history changes audited.

No cloud video, screenshots, faces, camera credentials or remote alarm controls. The console handles operational metadata and human review. Empty accounts show truthful onboarding and empty states. Test records are confined to local synthetic test environments.

## Laptop protocol

- `POST /device-api/enrol` body `{token,name,platform,app_version}` → `{device_id,device_token,heartbeat_interval_seconds:30}`. Exchange a valid one-use code; credential scope is fixed by the server.
- `POST /device-api/heartbeat` bearer device credential; body `{sequence,monitoring_status,camera_count,app_version}` → `{ok:true,server_time}`. Strict increasing sequence; an identical retry is safe; changed payload at same sequence conflicts. Connection is online only after successful heartbeat. No remote output command returned.
- `POST /device-api/alerts` bearer credential; body `{source_event_id,event_code,source_label,occurred_at,historical}` → `{id,received:true}`. Device cannot supply branch, reviewer, case classification or clinical data. Same ID/payload returns same record; changed payload conflicts.

Device enrollment/revocation and local companion connection are explicitly initiated. Connecting a laptop does not activate its CCTV or arm its speakers. Staff keep using the attended local detection interface for camera selection and sound commissioning.
