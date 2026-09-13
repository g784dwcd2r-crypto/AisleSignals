# Reviewed product observation to case

This connects the existing LIVE DETECTION interaction review to the existing Casebook. The action is explicit and performed by a signed-in manager or reviewer. Analysis completion, a movement event and an attention alarm never create a case automatically.

## Staff workflow

1. Expand the sampled frames and inspect the source, times and model limitations.
2. Record Useful, Normal shopping or Unclear. These are review outcomes, not findings of theft. Any of them may support a documented follow-up case.
3. Choose **Create case from reviewed observation**, enter a case title and staff-written reviewed notes, and submit **Create reviewed case**. Model reasoning is not inserted into the staff notes.
4. Open the linked case in Casebook. It starts **UNASSESSED**, **OPEN**, **UNRESOLVED**, with no monetary loss or recovered value. Existing review, manager financial controls, tasks, closure and export rules apply.

The case shows the original source label and kind, recorded-video test label where applicable, run and interaction IDs, observation version, model/prompt provenance, the review outcome and reviewer at linking, link actor/time, and each frame's timestamp, byte count and SHA-256. Model context remains visibly unverified and separate from the staff facts. A later review of the observation does not silently rewrite that snapshot.

## API and transaction boundaries

`POST /api/interactions/{interaction_id}/case` requires the existing authenticated branch/CSRF context, an Idempotency-Key, and `{expected_version, title, notes}`. Version is a strict positive integer. Title is 3–120 characters and notes 5–4000. Extra client-supplied scope, classification or financial fields are rejected.

The interaction must be in the active branch, complete, unexpired and staff-reviewed. Before creating a case, the API reads each referenced JPEG through the existing authenticated encryption/hash checks; absent, damaged or empty evidence fails closed. Case creation, provenance snapshot, interaction link/version increment, audit records and retry receipt use one SQLite transaction. The existing immediate write lock serialises concurrent attempts.

The same actor/route/key/payload can retrieve its original successful receipt. Scope and expiry are checked before replay. A different payload with that key conflicts. An observation already linked by another request produces `INTERACTION_ALREADY_LINKED`; it never creates a second case. Stale review versions produce `VERSION_CONFLICT`. The UI reloads latest history and requires a fresh staff decision after either conflict. A lost response can be retried explicitly with the same form contents; no automatic retry runs.

`GET /api/incidents/{incident_id}/interaction-source` requires case access in the active branch. It returns the immutable source metadata, current evidence status, and current scoped image references only while available. Missing cases or another branch's cases return the ordinary scoped not-found response.

## Evidence lifetime

Linking copies metadata, not media. It does **not** extend the original 24-hour sampled-image retention or preserve the original CCTV recording. Staff may still delete the interaction and its JPEGs earlier. Casebook then shows deleted/expired evidence and retains only the reviewed case and provenance metadata/hashes. Image reads independently enforce authentication, branch scope, expiry and integrity. A refresh checks current availability; a failed image read is labelled unavailable.

The case itself follows the existing Casebook record lifecycle; this change does not add an archive, legal hold, automatic case deletion or a new backup retention policy. Metadata remains in the protected local SQLite store, subject to the already documented OS-account/full-disk requirements. Exports contain metadata and references, not an embedded image package; availability must not be inferred from an old exported reference.

Case drafts and retry references stay in browser memory and are cleared on cancellation or page/branch teardown. Late responses cannot navigate a replaced account or branch context. This flow neither identifies a person nor concludes intent, payment status or criminality, and contacts nobody outside the local application.

## Verification

`tests/api/test_interaction_case_link.py` uses the real pilot API, SQLite transactions and encrypted JPEG files, with an explicitly synthetic provider boundary. It covers reviews and provenance, stale/unfinished inputs, missing/tampered/expired evidence, same-key retries, conflicting payloads, concurrent requests, branch/CSRF/revoked-session authority, deletion without retention extension, and rollback after an injected write failure.

`tests/e2e/interaction-case-link.spec.ts` starts a separate protected API on an OS-assigned unused port for each journey. It uses the original generated video fixture and real API review/case/evidence operations. Only model/pose providers are synthetic fixtures. It covers the disabled-before-review action, staff notes, double-click deduplication, Casebook navigation, source images/hashes, deletion, stale review and branch teardown, with narrow-screen/accessibility checks. This verifies application behavior, not pharmacy detection accuracy or physical alarm acceptance.
