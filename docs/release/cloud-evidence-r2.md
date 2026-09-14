# Private cloud evidence on Cloudflare R2

Status: implemented and tested with synthetic S3 clients. No R2 account, real
credential, customer footage or Render evidence mode has been activated.

The Render filesystem is not an evidence store. A Render deployment may enable
`ENCRYPTED` evidence only with the `R2` backend. Development may use an existing
private filesystem directory. `METADATA_ONLY` remains the default and rejects
stray evidence secrets so a partial configuration cannot silently activate
media handling.

## Required configuration

Use separate private EU-jurisdiction buckets and separate bucket-scoped Object
Read & Write credentials for staging and production. Leave `r2.dev`, custom
domains and CORS disabled. The application derives the only accepted production
endpoint, `https://<account-id>.eu.r2.cloudflarestorage.com`; it does not accept
an arbitrary URL or create a public/presigned link.

| Variable | Rule |
|---|---|
| `CLOUD_EVIDENCE_MODE` | `ENCRYPTED` only after the release gates below pass |
| `CLOUD_EVIDENCE_POLICY` | `SHORT_LIVED_V1` |
| `CLOUD_EVIDENCE_STORE_BACKEND` | `R2`; `FILESYSTEM` is development-only and rejected on Render |
| `CLOUD_EVIDENCE_KEK` | Canonical base64url encoding of a random 32-byte application key |
| `CLOUD_EVIDENCE_KEK_VERSION` | Stable version label; preserve the matching key until its evidence is deleted |
| `CLOUD_R2_ACCOUNT_ID` | Lower-case 32-character Cloudflare account ID |
| `CLOUD_R2_JURISDICTION` | `eu` |
| `CLOUD_R2_BUCKET` | Private environment-specific bucket name |
| `CLOUD_R2_ACCESS_KEY_ID` | Credential scoped to that one bucket |
| `CLOUD_R2_SECRET_ACCESS_KEY` | Corresponding secret, stored only as a Render secret |
| `CLOUD_R2_PREFIX` | Optional lower-case private prefix; separate buckets remain required |

The application encrypts each object with a random DEK under AES-256-GCM and
wraps the DEK with the configured versioned KEK. R2 receives only the encrypted
envelope under a random UUID key with `application/octet-stream` and `no-store`
metadata. R2's own encryption at rest is an additional layer. ETags are not
trusted for evidence integrity; authenticated decryption, byte count and the
manifest SHA-256 remain authoritative.

## Failure and transaction boundary

S3 requests never run while a PostgreSQL transaction or evidence row lock is
held. Upload and review first authenticate and copy immutable metadata in a
short transaction, perform bounded R2 I/O, then open a new transaction and
reauthorize the device or staff session, branch, alert, state and expiry before
publishing or returning bytes. A withdrawal or expiry during I/O therefore
prevents publication. Cleanup marks evidence revoked, commits, deletes the R2
object outside the database transaction, then conditionally records deletion.

Conditional `PutObject` with `If-None-Match: *` preserves create-only semantics.
A lost success response is reconciled by reading and authenticating the existing
envelope. Timeouts, throttling, provider errors, missing/corrupt READY objects
and failed deletes produce an opaque unavailable result. They never mark an
object READY or DELETED. Pending laptop delivery and revoked cleanup retry later.

## Staging and production gates

1. Record Cloudflare as a processor/subprocessor in the controller assessment
   and DPIA. Confirm the EU jurisdiction and processing terms for the pharmacies.
2. Create private `aislesignals-evidence-staging` and production buckets in the
   EU jurisdiction. Add a one-day lifecycle deletion rule as a backstop; the
   application cleanup remains authoritative because lifecycle execution may be delayed.
3. Create distinct bucket-scoped Object Read & Write credentials. Record a
   rotation owner without copying credentials into source, tickets or logs.
4. Deploy the code while `CLOUD_EVIDENCE_MODE=METADATA_ONLY`. Add all R2 and KEK
   secrets atomically, switch to `ENCRYPTED`, and redeploy. Startup must fail if
   the bucket cannot be authenticated.
5. In staging, run a synthetic create/read/conditional-conflict/delete probe,
   then the laptop manifest → upload → dashboard → authorised review journey.
   Restart/redeploy Render and prove the object persists.
6. Prove wrong-branch and wrong-alert reads fail, simulate R2 outage/recovery,
   force expiry, and verify physical deletion plus the database audit entry.
7. Repeat with independent production resources only after controller approval.

For rollback, first return evidence mode to `METADATA_ONLY` so laptops retain
and retry their local queues. Preserve PostgreSQL, both R2 buckets and every KEK
version referenced by retained rows. Do not roll back to a filesystem-backed
encrypted Render release and do not delete storage as part of application rollback.

Cloudflare references: [S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/),
[R2 credentials](https://developers.cloudflare.com/r2/api/tokens/),
[EU jurisdiction](https://developers.cloudflare.com/r2/reference/data-location/),
[consistency](https://developers.cloudflare.com/r2/reference/consistency/) and
[object lifecycle timing](https://developers.cloudflare.com/r2/buckets/object-lifecycles/).
