# Optional xAI incident verifier

## Status and purpose

This component is implemented as a disabled-by-default experimental second review of a completed local product-interaction observation. It is not a detector, identity system, finding of theft or alarm controller. No paid request has been made or accepted as validation evidence.

The local interaction model must first report a visible person, visible product, visible sequence and at least two ordered evidence frames for one of `TAKE_PRODUCT`, `RETURN_PRODUCT`, `PLACE_IN_BASKET` or `POSSIBLE_CONCEALMENT`. That is one local candidate, not independent corroboration. xAI receives no local action label: it first classifies the complete bounded frame sequence blindly, after which deterministic code compares the two results as support, contradiction or inconclusive. Every response records `advisory_only=true`, `alarm_decision_permitted=false` and `requires_local_candidate=true`. There is no code path from this adapter to an attention sound.

## Data minimisation

The request uses the complete existing interaction sample of three to six frames, preventing the local model from hiding contradictory context through its evidence-index selection. AisleSignals reads them from the authenticated branch-scoped evidence store, verifies their hashes, strips metadata, preserves up to the existing 768-pixel longest edge and re-encodes at bounded quality. It requests high image detail because tiny hand/product evidence is central to this task. It sends no local model action, account name, source label, branch name, camera address, incident narrative or historical identity. Metadata contains only frame offsets and the fact that camera calibration was present. Screen-captured mosaics must first be divided into a calibrated single-camera crop.

The current fixed policy identifier is `PUBLIC_STAGED_METADATA_STRIP_V1`. The adapter applies and verifies metadata stripping and pixel/size bounds, but it does not yet implement a commissioned pixel mask. For that reason, the route rejects camera and screen-capture sources and accepts only manager-approved public staged `RECORDED_VIDEO` trials. `app.state.xai_redactor` remains the fail-closed integration point for a future real mask. Real pharmacy footage remains prohibited until that mask is implemented and tested.

The strict response is bounded to enums, booleans and evidence indices. It contains no free-text narrative or identity field. Semantic contradictions such as a supporting verdict with inadequate visibility are rejected.

## Explicit enablement

A manager configures one branch through `PUT /api/xai-verifier`. Enabling requires affirmative privacy and United States processing review, approval that the input is public staged footage, verification that one call's current worst-case price is at most USD 1, and confirmation that the provider account has its own USD 5 hard cap. Feature enablement is independent per branch. The USD 5 local allowance is installation-global, so six branches cannot multiply it. `GET /api/xai-verifier` reports the branch state, provider readiness, processing region and global reserved allowance without exposing a credential or path. Only a manager may consume the allowance.

This implementation labels xAI processing as United States and requires explicit approval for that transfer. A live deployment still needs the controller's DPIA/processor review and the provider terms current at commissioning. Approval in the software records the manager action; it does not itself complete that legal review.

## Credential configuration

Only a private credential file is supported. Direct key values are deliberately not accepted from an application setting or repository file.

```sh
umask 077
mkdir -p "$HOME/Library/Application Support/AisleSignals/secrets"
# Write the key interactively into xai-api-key; do not put it in shell history.
chmod 600 "$HOME/Library/Application Support/AisleSignals/secrets/xai-api-key"
export AISLESIGNALS_XAI_API_KEY_FILE="$HOME/Library/Application Support/AisleSignals/secrets/xai-api-key"
export AISLESIGNALS_XAI_MODEL="<approved-current-image-model>"
```

The release launcher must set these environment variables from its protected configuration. The endpoint is fixed to `https://api.x.ai/v1/chat/completions`; proxy environment variables are ignored. The key file must be a regular, non-symbolic private file on macOS/Linux. Never paste a credential into Git, an incident, an audit record or a support bundle.

## Cost guard

Before a request, AisleSignals transactionally reserves a fixed USD 1 from one installation-global, pilot-lifetime ceiling of USD 5. This permits at most five test calls, regardless of the number of branches. The reservation is retained on success, provider failure or process interruption. Concurrent transactions cannot exceed the ceiling, and idempotency prevents a repeated browser request from reserving twice.

The reservation is an operational guard, not the provider invoice. Before live use, verify the current worst-case price for six high-detail images and 500 output tokens. Do not make a call if USD 1 is not a safe upper bound. The external provider account must retain its own USD 5 hard spend limit.

## Reliability and audit

Before inference, the adapter requests the configured model record and requires its exact identifier. The inference call has an eight-second timeout and is never retried automatically: a timeout could occur after a request was accepted and billed. A fresh attempt requires an explicit manager action, a new idempotency key and another USD 1 reservation. A circuit breaker opens after three failed attempts for sixty seconds. Startup recovery changes stale `REQUESTING` records to `UNKNOWN_BILLING`, retains their reservation and never retries them. Invalid or incoherent responses fail closed. The audit record stores requested and returned model, prompt version, complete frame hashes/indices, processing region, fixed policy, reserved USD cents, attempt count, token counts when returned and a hash of the provider request ID. It never stores the API key, request images or raw provider response.

A reserved request that fails remains a failed advisory verification. It does not change local evidence, local alarm eligibility or a staff decision. A new operator action and idempotency key are required for another paid attempt.

Deleting the local interaction while a request is in flight prevents the returned result from being accepted locally. It cannot retract frames already transmitted or override the provider's retention. Provider contractual terms, DPA and an appropriate zero-retention control therefore remain preconditions for any future private footage. The present pilot restriction avoids that transfer by allowing public staged footage only.

## Test evidence and remaining acceptance

Run the mocked tests with:

```sh
python -m pytest -q tests/api/test_xai_verifier.py
```

They cover strict JSON, blind classification, complete bounded frames, minimisation, high-detail requests, model-ID preflight, timeout without a second paid attempt, circuit breaker, branch approval, local-candidate gating, idempotency, auditing, cross-branch denial, deletion during a request, concurrent reservations and the installation-global USD 5 ceiling. The tests use synthetic images and `httpx.MockTransport`; they make no xAI request.

The 768-pixel/high-detail choice is a cautious starting point, not an accuracy result. Held-out evaluation must compare image-detail modes and crop sizes against small products before this reviewer can be enabled beyond the bounded test.

## Three-clip browser-playback evaluation

The first bounded trial uses three authorised browser-playback clips already selected by the owner. Do not download, commit or copy the YouTube footage. For each clip, use Live Detection to create one local interaction containing three to six ordered single-camera frames. Record the staff label and the local person-gate output in a private metadata manifest. Keep the interaction UUID; do not include a video URL, filename, person description or frame bytes.

Before approval for paid calls, set each manifest `xai_verification` to `null` and run:

```sh
python scripts/evaluate_xai_three_clips.py /private/path/three-clips.json
```

The report will show three local results and zero xAI calls. After safeguards and pricing are reviewed and the owner explicitly approves spending, make at most one branch-scoped verifier request for each interaction through the API, copy only the bounded verification record into the private manifest, and run the same evaluator again. The evaluator requires the complete ordered frame indices, compares the blind xAI action with the staff and local labels, and always reports `alarm_effect: NONE`. It never opens footage or contacts xAI.

The manifest has this metadata-only shape:

```json
{
  "schema_version": "1.0",
  "clips": [
    {
      "trial_id": "trial-1",
      "interaction_id": "00000000-0000-0000-0000-000000000001",
      "staff_label": "UNCLEAR",
      "local_person_gate": {
        "person_visible": true,
        "product_visible": true,
        "sequence_observed": true,
        "action": "POSSIBLE_CONCEALMENT",
        "evidence_frame_indices": [0, 2]
      },
      "xai_verification": null
    }
  ]
}
```

Supply exactly three clip entries. The example above is abbreviated to show one entry; the validator rejects anything other than three.

Before any live pharmacy use, complete the privacy/region review, rotate any credential previously shared in chat or another non-secret channel, select a currently supported image model, verify current pricing and reservation, test a newly issued limited credential, run held-out pharmacy evaluation, and confirm that external review improves false-alert workload without increasing missed events. A successful API response is not accuracy validation.
