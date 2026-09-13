# Prototype 0.1 integration contract

Implementation slice of baseline 2.2, not its completed production contract. Root owns this shared contract; coordinate changes. Base URL `/api`. All dates UTC ISO strings; UI displays Europe/Dublin. JSON request/response; errors `{error:{code,message,current_version?}}`. Successful writes return the resource or `{ok:true}` and UI reloads bootstrap. UUID identifiers. Synthetic data only in this release.

## Transport and auth

Same-origin local browser/API deployment on 127.0.0.1:8765. Vite developer proxy forwards /api. FastAPI serves built apps/web/dist with SPA fallback. Local prototype SQLite persists to ignored `.local/aislesignals.db`; not production PostgreSQL/RLS. Cookies are HttpOnly and SameSite=Strict, scoped to local host; CSRF token returned at login and GET /api/session and required in X-CSRF-Token on authenticated writes. Reject foreign Origin, unknown Host, expired sessions and cross-organisation resource IDs (404). Eight-hour absolute / fifteen-minute idle session. Demo password auth is explicitly not production OIDC/MFA. No credentials in browser storage. Login rate limited.

POST /api/login `{email,password}` -> `{user,csrf_token}`. GET /api/session -> same. POST /api/logout -> `{ok:true}`.
Demo users: manager@harbour.demo, reviewer@harbour.demo, manager@liffey.demo. Shared deliberately-public demo password: `AisleDemo!2026`. Only synthetic local use. Seeded sites: Harbour Pharmacy / Liffey Pharmacy, separate organisations. UI displays demo credentials on login. No registration/payment flows.

## Read model

GET /api/bootstrap -> `{user,site,cameras,candidates,incidents,assistance,audit,budget}`. All collections organisation AND site scoped. Lists bounded to latest 200; prototype uses full refresh after writes and 10-second refresh while authenticated; stop polling on logout. Data is server state, never fabricated by UI. An API outage preserves unsaved form input, shows connection loss, disables writes and explicitly makes coverage unknown.

user: `{id,name,email,role}` role MANAGER or REVIEWER.
site: `{id,name,organisation_name,timezone,monthly_price_cents:6000,shift_active:boolean}`. Shift activation does not imply real monitoring.
camera: `{id,name,zone,status,connection_kind,last_seen_at,detail,version}`. status DEMO_ONLINE/OFFLINE/FROZEN/UNCONFIGURED. connection_kind SIMULATOR. UI clearly calls it simulated; no live detector claim.
candidate: `{id,title,camera_id,camera_name,zone,occurred_at,received_at,status,source,scenario,event_label,summary,media_status,historical,version,incident_id,evidence_url}`. status NEW/ACKNOWLEDGED/DISMISSED/CONVERTED, source SIMULATOR, media_status AVAILABLE/MISSING; evidence_url authenticated synthetic SVG route or null. No real footage upload.
incident: `{id,reference,title,notes,candidate_id,classification,status,outcome,loss_cents,recovered_cents,version,created_at,updated_at,tasks,history,draft}`. status OPEN/CLOSED. classification UNASSESSED/BENIGN/INSUFFICIENT_EVIDENCE/SUSPECTED_INCIDENT/STORE_CONFIRMED_LOSS. outcome UNRESOLVED/NO_LOSS_ESTABLISHED/GOODS_RETURNED/GOODS_PAID_FOR/LOSS_RECORDED. Monetary unknown=null, otherwise nonnegative integer cents. Only manager confirms loss/edits monetary values/reopens/exports. tasks `{id,title,assignee,due_at,done}`. history `{id,at,actor,action,detail}`. draft null or `{text,engine,approved,created_at}`.
assistance: `{id,reason,status,created_at,requested_by}` status REQUESTED/ACKNOWLEDGED/RESOLVED. Acknowledged never means colleague arrived.
audit: `{id,at,actor,action,resource_type,resource_id,detail}` no passwords or session secrets.
budget: `{monthly_cap_cents:500,spent_cents:0,engine:'LOCAL_TEMPLATE',cloud_enabled:false}`. Deterministic factual report template is NOT AI inference and costs zero. Provider-backed AI is deferred; no paid calls made.

## Writes

All resource changes take expected_version, except new creates and assistance. Use Idempotency-Key on creates and candidate review; changed payload under same actor+route+key -> 409. Browser preserves key on network retry, generates new key only for a changed logical action. No success UI before acknowledgement.

POST /api/shift `{active:boolean}` -> site.
POST /api/simulator `{scenario,source_event_id}` -> candidate (or `{ok:true}` for camera health). Managers only. scenario SHELF_EVENT/RETURNED_ITEM/MISSING_MEDIA/HISTORICAL_EVENT/CAMERA_OFFLINE/CAMERA_FROZEN/CAMERA_RECOVERED. Duplicate source_event_id and same payload yields existing resource; mismatched payload ->409. Camera health may still be changed during no shift. No notifications for historical events.
New simulated observations require an active demo shift; current observations also require a DEMO_ONLINE source. Historical replay may be created during a source outage. These guards do not apply to the independent manual case/help workflows.
POST /api/candidates/{id}/acknowledge `{expected_version}` -> candidate.
POST /api/candidates/{id}/review `{expected_version,decision,reason}` -> candidate. decision DISMISS or OPEN_INCIDENT. Reason required, 5..2000 chars. Cannot re-review terminal candidate. OPEN_INCIDENT creates exactly one OPEN incident with SUSPECTED_INCIDENT classification, linked candidate and human reason; no automatic criminal finding. DISMISS creates no incident.
POST /api/incidents `{title,notes}` -> incident. Title 3..120; notes 5..4000. Manual path independent of detector/shift.
PATCH /api/incidents/{id} `{expected_version,title?,notes?,classification?,outcome?,loss_cents?,recovered_cents?}` -> incident. Closed incident requires explicit manager reopen first. Benign/no-loss cannot retain financial loss; loss must have manager-confirmed classification and LOSS_RECORDED outcome; recovered cannot exceed recorded loss (UI presents both separately, not as savings).
POST /api/incidents/{id}/close `{expected_version,reason}` -> incident. Reject UNRESOLVED outcome or incomplete tasks. Store reason in history.
POST /api/incidents/{id}/reopen `{expected_version,reason}` -> incident. Manager only.
POST /api/incidents/{id}/tasks `{expected_version,title,assignee,due_at}` -> incident. Validate due_at ISO; assignee named string, not arbitrary user ID; title 3..200.
POST /api/incidents/{id}/tasks/{task_id}/complete `{expected_version}` -> incident. Parent incident version controls tasks.
POST /api/incidents/{id}/draft `{expected_version}` -> incident. Local deterministic draft from current reviewed structured facts; no external calls; excludes unreviewed identifying allegations. Replacing facts invalidates previous approval/draft. Draft clearly labelled template, staff review required.
POST /api/incidents/{id}/draft/approve `{expected_version}` -> incident. Requires existing current draft. Increment version and audit.
Draft generation and approval work on closed cases as well: a final report can be prepared after closure without reopening factual fields. Closure invalidates the prior open-status draft.
POST /api/incidents/{id}/export `{expected_version,purpose}` -> downloadable JSON evidence manifest/report, manager only. Response content-disposition with safe name; rechecks scope/session; includes structured case, history, SHA256 digest and explicit synthetic provenance. No external recipient/send. Purpose required 5..500. Prototype export is case record, not a real video evidence package.
GET /api/evidence/{candidate_id} -> authenticated no-store synthetic SVG. Missing/expired/wrong-scope ->404. Reject credentials in URL; no external URLs rendered.
Synthetic illustration access expires 72 hours after received_at; this prototype access check does not implement production deletion/holds/retention workers.
POST /api/assistance `{reason}` -> assistance. 3..500 chars. POST /api/assistance/{id}/transition `{status}` -> assistance (ACKNOWLEDGED or RESOLVED); valid state transitions only.
GET /api/health -> `{status:'ok',mode:'synthetic-prototype',version:'0.1.0'}` no customer details.

## First prototype boundaries

Implements useful end-to-end synthetic review and incident management. No real video surveillance, automatic person recognition, physical output adapter, cloud AI or automatic billing. Laptop companion work in this release is an explicit read-only readiness/probe utility, not an always-on detector or a signed installed service. Both OS build/check workflows provided; actual Windows and pharmacy Mac acceptance remains pending. Declared production gaps must be listed in docs/prototype/README.md and STATUS.md, without falsely marking the full handover requirements complete.
