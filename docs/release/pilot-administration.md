# Pharmacy users and branch administration

The protected local pilot now has an account administration API for its browser interface. A signed-in manager can add branches and named users, assign branch roles, change access, disable or enable an account and reset its local passphrase. No real pharmacy or staff account is created by installing the software. The synthetic demonstration workspace rejects these endpoints.

This is administration of the current installation's database. It does not synchronise users or records between six separate laptops, send invitations or email passwords, purchase a subscription, provide hosted identity or enable MFA.

## First owner setup

An empty pilot does not grant administration to its first visitor. The trusted local operator must first obtain a high-entropy one-time setup code. The launcher may issue and display this code privately in an interactive terminal. The equivalent local command is:

```sh
python -m services.api.pilot_identity --db .local/pilot/aislesignals.db setup-token
```

The bundled form is `AisleSignalsPilot accounts --db PATH setup-token`. The command refuses redirected or piped input/output. Each issue rotates the code; a code lasts fifteen minutes and is stored in a private `0600` file adjacent to the database, named `<database>.setup-token`. Only its SHA-256 hash and absolute expiry are stored in the database. The code is an OS-administrator capability: do not put it in a URL, message, shared log or source-control file.

The browser first checks `GET /api/setup/status`:

```json
{"available":true,"token_required":true,"token_ttl_seconds":900}
```

`available` is true only when there are no users and no branches. It is distinct from `/api/runtime`'s `setup_required`: an existing recovered workspace with disabled accounts needs access recovery, not a second initial owner. No public endpoint returns a setup code or its hash.

Submit the first group, branch and owner to `POST /api/setup`:

```json
{
  "organisation_name":"Your pharmacy group",
  "branch_name":"Your branch",
  "name":"Named manager",
  "email":"manager@example.com",
  "password":"Enter a unique private passphrase",
  "setup_token":"Enter the private code shown on this laptop"
}
```

The endpoint requires the existing local Host/Origin boundary, JSON body limits and a valid unexpired code. Five incorrect codes trigger a persistent fifteen-minute limit; issuing another code does not remove the failed-attempt limit. First-owner creation, branch creation and code consumption share one SQLite write transaction, so competing claims cannot create two initial owners. It returns `201 {"created":true,"sign_in_required":true}` and creates no session cookie. Clear the setup fields, then sign in with the new named account. Successful setup invalidates and removes the code file; a leftover file after a filesystem error is already invalid in the database.

Names must contain 2–120 printable characters, individual email addresses must be valid and must not use `.demo`, and passphrases must meet the existing 14–256-character validation. No default password is available. Windows private-directory ACLs remain part of client-device acceptance.

## Manager authority

The active branch must resolve to the `MANAGER` role. The manager can administer only branches where their account has an actual `MANAGER` membership in its immutable organisation. A manager role in one branch does not give access to an unassigned branch or elevate a reviewer membership elsewhere.

The user roster includes only accounts sharing a branch that the current manager manages. It shows only memberships in those managed branches. `can_manage_account` is true only if **every** branch assigned to the target account is managed by the caller. Disabling, enabling and resetting a passphrase are account-wide operations and require this stronger authority. A partial branch manager may change that account's access only within a branch they manage.

Every administration write requires the current session cookie, `X-CSRF-Token`, `X-AisleSignals-Site` and a `manager_password` field containing the acting manager's current passphrase. A wrong passphrase returns `401 REAUTH_REQUIRED` without invalidating the valid read session; the interface must retain the session and let the manager correct the confirmation. Five failed confirmations trigger `429 REAUTH_RATE_LIMITED` for fifteen minutes. All account changes are audited with the actual acting manager, without passwords, password hashes, salts or setup codes in audit details.

Changing account access, enabled state or password revokes every session of the target account, including model-job publication authority. If the manager edits their own account, they must sign in again after success. Password reset preserves the enabled/disabled state and existing failed-login limits. The last enabled manager of a branch cannot be disabled, demoted or removed until another enabled manager is appointed. Removing an account's final branch grant is refused; disable the account or assign its replacement branch first.

## Endpoint contracts

All requests use JSON. Responses with errors keep the existing `{ "error": { "code": "...", "message": "..." } }` shape. Inputs reject unknown fields and client-supplied organisation authority.

| Endpoint | Request | Success |
|---|---|---|
| `GET /api/admin/sites` | Session | `{sites:[site]}` |
| `POST /api/admin/sites` | `{name,manager_password}` | `201 {site}`; creator receives manager membership in the new branch |
| `GET /api/admin/users` | Session | `{users:[account]}` |
| `POST /api/admin/users` | `{name,email,password,branches:[{site_id,role}],manager_password}` | `201 {user:account}` |
| `POST /api/admin/users/{id}/access` | `{site_id,role,expected_version,manager_password}`; role is `MANAGER`, `REVIEWER` or `null` to remove | `{user:account}` |
| `POST /api/admin/users/{id}/enabled` | `{enabled,expected_version,manager_password}` | `{user:account}` |
| `POST /api/admin/users/{id}/password` | `{password,expected_version,manager_password}` | `{ok:true,sign_in_required:boolean}` |

A site contains `id,name,organisation_id,organisation_name,role`. Its returned administration role is `MANAGER`. Branch names must be unique within the group ignoring case. User email addresses must be unique. These checks prevent duplicate creation after a double-click or uncertain network response.

An account contains:

```json
{
  "id":"account UUID",
  "name":"Named staff member",
  "email":"staff@example.com",
  "enabled":true,
  "version":"64 hexadecimal characters",
  "branches":[{"site_id":"branch UUID","site_name":"Branch name","role":"REVIEWER"}],
  "can_manage_account":true
}
```

The opaque `version` changes when any membership, enabled state or password changes. It is a hash of the full state and an internal password-reset marker; the internal salt is never returned. Account edits must send the version the manager actually reviewed. A stale edit returns `409 ADMIN_ACCOUNT_CHANGED` before any account mutation. The interface must discard the stale edit, reload the roster and ask the manager to review the current state; it must not silently retry with a newer version. Concurrent edits using one snapshot have only one winner when both would change that state.

Other relevant codes are `PILOT_REQUIRED`, `MANAGER_REQUIRED`, `ACCOUNT_AUTHORITY_REQUIRED`, `ACCOUNT_NOT_AVAILABLE`, `SITE_NOT_AVAILABLE`, `ACCOUNT_CONFLICT`, `BRANCH_CONFLICT`, `BRANCH_REQUIRED`, `LAST_MANAGER_REQUIRED`, `SETUP_UNAVAILABLE`, `SETUP_TOKEN_INVALID`, `SETUP_RATE_LIMITED` and `INVALID_ACCOUNT_INPUT`. Unknown or unauthorised account IDs use the same unavailable response. Email conflicts do not reveal the other account's group or details.

## Verification and operational limits

`tests/api/test_pilot_admin.py` exercises the real FastAPI routes with isolated synthetic accounts. It covers private one-time setup, concurrent owner claims, expiry/rotation/reuse, rate limits, origin and validation rejection, disabled-workspace recovery exclusion, CLI terminal guards, demo/reviewer denial, creation, branch and organisation isolation, partial manager authority, session revocation, secret-free responses/audits, last-manager/final-grant protection, self password reset and optimistic concurrency, including a two-session edit race. The release lead records final integrated counts and browser evidence separately.

The account backend is registered by the application and uses the existing schema version 2 tables. No new database migration or seed data is introduced. An authorised local OS operator retains the accounts CLI for lockout and restore recovery. Browser administration does not replace that recovery capability or create remote access to a pharmacy laptop.
