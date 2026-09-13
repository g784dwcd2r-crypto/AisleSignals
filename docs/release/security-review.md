# Independent security review of the local pilot

Reviewed 13 September 2026 by specialist 8, against the working release candidate derived from `704436ecf7e2c250022f2b6a1e9d39716bd4e03f`. The coordinating release lead integrates and identifies the final commit. This review covers the local API, account and branch authority, sampled media, recovery archives and supervised process launcher. It is not an external penetration test or customer-device acceptance.

## Reproduced finding and correction

**SEC-001 — direct account provisioning exposed the database to other local users under ordinary POSIX permissions. Fixed in source.**

The documented accounts setup path called `Store` before the launcher applied its private directory permissions and `umask`. With an ordinary `022` umask, an isolated reproduction created a new installation directory as `0755` and its named-account database as `0644`. The database contains staff account details and password hashes, and later holds case and interaction metadata. Another local account with access through the parent directories could read it. AES-GCM protection of sampled images did not protect those SQLite records.

Pilot database creation now happens with exclusive `0600` creation and any newly created ancestor directories use `0700`. Existing pilot database files and SQLite sidecars are hardened. Existing unrelated parent directories are not silently changed. Provenance and schema are checked before changing an existing database's permissions, so accidentally selecting a synthetic or newer database leaves its content and mode unchanged. Symbolic links through the database path or its parents, symbolic WAL/SHM/journal files, and non-file SQLite sidecars are refused. Synthetic database creation retains its existing behavior. Windows account-directory ACL validation remains separate from POSIX permission tests.

The ten regression cases in `tests/security/test_pilot_filesystem.py` check direct provisioning under `022`, database and active SQLite sidecar modes, new and existing parents, existing-pilot hardening, unchanged demo data on a mismatch, symbolic path variants, directory sidecars and a future-schema refusal. The vulnerable behavior was reproduced before the change; the complete targeted identity, schema and recovery suite passed after it.

## Verified boundaries

The review reproduced these existing automated checks using isolated synthetic databases and a clearly mocked model provider:

| Boundary | Evidence exercised |
|---|---|
| Pilot startup and accounts | No seeded demo credentials; persisted database-mode refusal; named password validation and rate limits; account disable/password reset/session revocation; last-manager protection |
| Branch authority | Server-resolved membership and role; branch and CSRF write guards; rotated cookies; absolute session expiry; stale tabs and old worker sessions cannot publish into the newly selected branch |
| Sampled evidence | Authenticated branch access; AES-GCM binds organisation, branch, job and frame; modified ciphertext or metadata cannot yield an image; missing keys fail closed; expiry and branch-scoped capacity rules |
| Recovery | Active-database lock; wrong passphrase/tamper/truncation refusal; authenticated archive traversal rejection; complete database/media verification; no overwrite of an existing target; restored sessions revoked and all accounts disabled |
| Model boundary | Bounded JPEG and response handling; strict local-provider configuration; disabled-provider interlock; schema-limited observations; stale/cancelled work suppressed |
| Launcher | No adoption or termination of an existing listener; owned-process cleanup; bounded recovery; casework-only fallback; sleep/rearm status; private token and report path handling |

Commands run on the development Mac:

```sh
.venv/bin/python -m pytest tests/security/test_pilot_filesystem.py tests/api/test_pilot_identity.py tests/api/test_schema_release.py tests/api/test_evidence_recovery.py -q
# 62 passed
.venv/bin/python -m pytest tests/api/test_interactions.py tests/deployment/test_pilot_launcher.py -q
# 81 passed
```

Both runs emitted existing Starlette/httpx deprecation warnings and no failures. These 143 passing cases are targeted security and integration evidence, not a claim that the final source, browser build, signed installers or all platforms have completed release acceptance. The release CI must include `tests/security`.

A filename-only secret scan of 157 tracked and non-ignored candidate files found no supported private-key header or common provider-token pattern, and no file exceeding 25 MB. It did not read ignored client-media or token directories. Pattern scanning cannot establish that every possible secret format or sensitive narrative is absent; final staging review remains required.

## Remaining deployment limits

No additional high-impact exploitable defect was reproduced in this bounded review after SEC-001 was corrected. This does not establish absence of defects.

SQLite metadata remains unencrypted by the application; an administrator with the database and evidence key can recover the sampled images. The accepted boundary is a private, protected local OS account. Full-disk protection, Windows ACLs, exact client OS/architecture and physical laptop recovery have not been verified on the six pharmacies' devices. The release is loopback-only named local authentication with no MFA, hosted identity, central branch synchronisation or public TLS deployment. Signing and client-device installer acceptance need their own evidence.

The classifier remains experimental. No authorised held-out pharmacy dataset, real branch camera, speaker audibility or response procedure was validated by these security tests. Keep those rollout gates unresolved until the test coordinator has actual per-branch evidence.
