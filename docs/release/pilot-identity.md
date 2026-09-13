# Local pilot identity and branch contracts

Implemented for the Tuesday release work; not a declaration of customer commissioning or managed identity readiness. Requirement coverage: local named passwords and revocation contribute to FR-001/003/005; server-resolved organisation and branch membership enforce FR-002. Managed OIDC, MFA, email invitations and online group synchronisation are not implemented by this module.

## Separate runtime and data

`AISLESIGNALS_MODE=synthetic` preserves the existing demo accounts and synthetic records. `AISLESIGNALS_MODE=pilot` starts without accounts, branches, cameras or sample observations. The simulator endpoint is denied in pilot mode.

When no path is supplied, the API defaults to `.local/aislesignals.db` in synthetic mode and `.local/aislesignals-pilot.db` in pilot mode. Release launchers use the dedicated `.local/pilot/` directory and **must set `AISLESIGNALS_DB_PATH` explicitly** to their chosen database. Provisioning and startup must point to the same file. Never point a pilot launcher at the existing preview database.

Database mode is persisted. A legacy database containing records is classified as synthetic. Changing the environment flag cannot convert it: startup refuses a mismatch before schema changes. Starting synthetic mode against a pilot database is likewise refused. No user preview database is reset or converted.

This service remains bound to loopback. It is one laptop's database, not a public server or centrally synchronised multi-pharmacy SaaS. A group can contain multiple branches on that installation, with named users assigned only to permitted branches of one organisation. The SQLite file is local sensitive data; OS access to it is administrator access. File encryption, backup, laptop access and retention require their separate release controls.

## Provisioning commands

Run these in an interactive terminal from the repository with the configured Python runtime. The example values are placeholders; operators enter actual authorised group, branch and individual account details.

```sh
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db init --organisation "Your group" --branch "Your branch" --email manager@example.com --name "Named Manager"
```

The CLI prompts privately for a unique passphrase twice. It rejects pipes, password arguments, the demo password, demo addresses and invalid values. No password is printed, placed in command arguments or stored in configuration. Passwords use a unique 32-byte salt and PBKDF2-HMAC-SHA256 with 600,000 iterations. Accounts require at least 14 characters; use a unique password-manager-generated passphrase. Failed login limits remain persistent across application restarts and password changes.

The first command atomically creates one group, branch and manager; repeating `init` cannot overwrite an existing installation. It prints the non-secret organisation, branch and account IDs for subsequent administration:

```sh
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db add-site --organisation-id ORGANISATION_ID --branch "Another branch"
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db add-user --email reviewer@example.com --name "Named Reviewer" --site-id BRANCH_ID --role REVIEWER
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db grant-user --email manager@example.com --site-id BRANCH_ID --role MANAGER
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db revoke-site-access --email reviewer@example.com --site-id BRANCH_ID
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db disable-user --email reviewer@example.com
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db set-password --email reviewer@example.com
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db list-access
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db enable-user --email reviewer@example.com
```

Repeat `--site-id` on `add-user` to assign several branches in one group. `add-site` does not automatically grant anybody access. Grant a named manager before using the branch. A branch's last enabled manager cannot be demoted, removed or disabled until a replacement manager is appointed. Password reset does not re-enable a disabled account. In an emergency, stop the local service while the trusted local operator replaces access.

Role changes, membership removal, disabling and password reset revoke every session of the account. Account and branch provisioning is audited without credential material. These are local operator functions; there is no public setup HTTP endpoint.

The bundled pilot includes this CLI as `AisleSignalsPilot accounts` (`AisleSignalsPilot.exe accounts` on Windows). An encrypted restore disables all accounts and revokes sessions so an old snapshot cannot silently reactivate access removed later. Use `list-access` to review the recovered grants, remove obsolete permissions, reset any uncertain passwords and explicitly `enable-user` only for current authorised staff. Appoint a current manager before reopening the restored workspace. Review output contains local account details and belongs with the installation's operator.

## HTTP contract for frontend integration

Public `GET /api/runtime`:

```json
{"mode":"pilot","setup_required":true,"local_only":true,"authentication":"local_named_password","mfa_enabled":false}
```

Only runtime capabilities and whether an enabled manager exists are exposed before login. No branch names, account addresses or membership IDs are public. The frontend must fetch this before displaying a login form: only synthetic mode may show demo credentials or sample-account shortcuts. If runtime loading fails, do not default to demo credentials.

`POST /api/login` keeps `{email,password}`. It sets an opaque HttpOnly, SameSite=Strict cookie and returns the same envelope as authenticated `GET /api/session`:

```json
{
  "user":{"id":"USER_ID","name":"Named Manager","email":"manager@example.com","role":"MANAGER"},
  "csrf_token":"OPAQUE_TOKEN",
  "mode":"pilot",
  "current_site_id":"BRANCH_ID",
  "allowed_sites":[{"id":"BRANCH_ID","name":"Your branch","organisation_id":"GROUP_ID","organisation_name":"Your group","role":"MANAGER"}]
}
```

Bootstrap retains its existing `user,site,cameras,candidates,incidents,assistance,audit,budget` shape and adds `mode,current_site_id,allowed_sites`. Newly provisioned branches have empty cameras/candidates/incidents/assistance; the UI must handle those empty states. Audit has provisioning entries. Registering a branch or beginning a review shift does not establish qualified detection or a camera connection.

Every authenticated pilot mutation must supply **both** `X-CSRF-Token` and `X-AisleSignals-Site: BRANCH_ID`. Only `/api/logout` and `/api/session/site` are exempt from the branch header; they still require CSRF. Missing/stale branch context returns `409 SITE_CONTEXT_CHANGED` before any write. Never derive authority from request-body organisation/site fields.

`POST /api/session/site` accepts `{site_id:"BRANCH_UUID"}`. An unavailable branch returns the same `404 SITE_NOT_AVAILABLE` whether it exists elsewhere or not. Successful switching rotates the cookie and CSRF, preserves the original eight-hour absolute session expiry, revokes old job authority, and returns the session envelope above. Switching to the already active branch is a no-op. The frontend must stop capture, cancel unsaved source-specific work, clear previous-branch data and reload bootstrap before allowing new captures or writes. Old tabs' CSRF and branch headers fail closed. Never silently resend a failed mutation under a different branch.

The 15-minute idle lock remains. API session polling counts as activity at `/api/session`; passive media and bootstrap loads do not. Monitoring acceptance must account for this behaviour separately. Password reauthentication and MFA are not implied by branch switching.

## Background job integration

Call `Store.resolve_session_user(conn, session)` after loading the saved session. It returns the active user plus resolved role, immutable organisation and branch authority, or `None` when disabled/revoked/unassigned. Retain existing session-expiry checks. Compare the resolved scope to the job's captured organisation and branch before model execution **and before publication**. Do not compare a user's legacy home branch directly: authorised multi-branch users can select another branch. Branch rotation deletes the old session, so queued jobs from that session lose publication authority.

Core API idempotency hashes include server-resolved organisation and branch for every operation. Reusing a retry key in another branch produces a conflict rather than exposing the prior branch's response.

## Verification

`python -m pytest tests/api/test_pilot_identity.py -q` exercises empty pilot startup, both directions of database mismatch, transactional initialisation, credential handling, invalid passwords, runtime shape, cookie controls, branch and CSRF guards, branch rotation, role and membership revocation, account disabling, password resets, last-manager protection, immutable group scope, persistent login rate limits, simulator denial and CLI secret handling. The coordinating release agent must also run the full API and frontend suites after integrating mode and branch behaviour.
