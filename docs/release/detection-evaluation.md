# AisleSignals pharmacy detection evaluation

Owner: Jawahir Q. · Release workstream 9 · 13 September 2026

## Current evidence

The local interaction provider and its evaluation tools run. No authorised video from the six intended pharmacy branches has been supplied, so their recognition performance, camera suitability and action-to-alarm delay remain **NOT_RUN**. Synthetic negative controls establish only the measured control behaviour. Unit tests establish software rules and metric arithmetic. Neither establishes pharmacy theft-detection accuracy.

On this development Mac, one four-frame generated-geometry control was run against the already installed local model during this workstream. It returned `UNCLEAR`, person/product not visible and `alarm_eligible: false`, taking 3,523 ms for inference and 3,525 ms wall time. This single no-person control contains no pharmacy footage. The private report is `.local/interaction-evaluation/release-synthetic-negative.json`; it records frame hashes, model digest `66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a` and prompt hash `452bc9fcb99a240ccfbc64486bb26d7bcd9af8b978a22a397cff5542f33a60a5`. It is not a Windows benchmark or a live action-to-alert timing result.

The detailed [capture and independent labelling procedure](../prototype/interaction-evaluation.md) remains the dataset specification. Its six observable labels are pickup, return, basket placement, possible concealment, ordinary browsing and unclear. None establishes identity, intent, payment or theft. Concealment-labelled test actions should be staged by authorised participants. Actual incident material needs its own authorisation and adjudication.

## Obtain a useful test set

For each actual branch and intended camera view, collect authorised samples of normal browsing, taking a product, returning it, basket placement and staged concealment. Include phone retrieval, personal-bag adjustment, restocking, partial occlusion and multiple people. A reviewer who has not seen predictions assigns the latest clearly completed transition; unresolved scenes receive `UNCLEAR`. Use local pseudonymous branch, camera, day and participant identifiers. Do not put source videos, consent records or incident narratives in Git.

Review the exact 32–768 pixel JPEG derivatives submitted to inference. Small products, hidden hands or a tiny CCTV grid tile can make an action unobservable. Capture context before and after the action, and keep the sample spacing and crop consistent with the live configuration. An overlay that tracks a body does not prove the product transition can be recognised.

Include continuous, measured ordinary trading observations as well as selected staged actions. Only fully covered, adjudicated normal sessions without inference errors enter normal camera-hours. Do not use an edited highlight reel as the denominator for alerts per hour. Frame-window coverage is not continuous frame-by-frame inference and may miss actions between samples.

Preserve a genuinely untouched test split. Existing validation rejects camera/day/person/path overlap across splits, duplicate original recording hashes and, in real execution, exact cross-split image duplicates. A declaration in JSON cannot independently prove consent, correct labels, non-overlapping source exports or that a test set was never seen before.

## Branch metadata and coverage plan

Add `site_id` to every manifest session and `scenario` to the relevant windows. They are private evaluation references, not API membership or permission claims. Existing manifests remain supported; windows without `site_id` cannot count toward any branch's planned coverage.

Supported scenario names are:

```text
ORDINARY_BROWSING  PICKUP  RETURN  BASKET_PLACEMENT
STAGED_CONCEALMENT  PHONE_OR_BAG_HANDLING  STAFF_RESTOCKING
OCCLUSION  MULTIPLE_PEOPLE
```

Before viewing held-out predictions, record an agreed coverage plan. The following shows **one branch and illustrative inventory counts only**. These numbers are not validated acceptance thresholds. Set the actual minimums before testing, use the six actual branch references and archive the plan hash with the independent review record. Each branch needs its own object; success at one never counts for another.

```json
{
  "schema_version": "1.0",
  "plan_id": "private-pharmacy-evaluation-v1",
  "declared_before_test": true,
  "owner_reference": "private-evaluation-owner",
  "branches": [{
    "site_id": "REPLACE_WITH_ACTUAL_PRIVATE_BRANCH_REFERENCE",
    "minimum_successful_windows_per_class": {
      "TAKE_PRODUCT": 20,
      "RETURN_PRODUCT": 20,
      "PLACE_IN_BASKET": 20,
      "POSSIBLE_CONCEALMENT": 20,
      "NORMAL_SHOPPING": 20,
      "UNCLEAR": 20
    },
    "minimum_successful_windows_per_scenario": {
      "ORDINARY_BROWSING": 5,
      "PICKUP": 5,
      "RETURN": 5,
      "BASKET_PLACEMENT": 5,
      "STAGED_CONCEALMENT": 5,
      "PHONE_OR_BAG_HANDLING": 5,
      "STAFF_RESTOCKING": 5,
      "OCCLUSION": 5,
      "MULTIPLE_PEOPLE": 5
    },
    "minimum_recordings": 3,
    "minimum_days": 2,
    "minimum_cameras": 1,
    "minimum_normal_camera_hours": 8
  }]
}
```

All six class minimums must be positive integers. At least one supported scenario must have a positive minimum. Camera/day/recording counts and measured normal hours must also be positive. Include every relevant difficult scenario in the agreed plan; omitting one does not establish that the model handles it. The plan is a required sample inventory, separate from acceptable recall, alert burden, abstention and latency.

Run from the repository root with the same local provider configuration as the application:

```sh
.venv/bin/python scripts/evaluate-interactions.py \
  --manifest .local/interaction-evaluation/manifest.json \
  --coverage-plan .local/interaction-evaluation/coverage-plan.json \
  --run-local --split test \
  --output .local/interaction-evaluation/report.json
```

Use `.venv\Scripts\python.exe` for a Windows source installation. The selected local runtime must already be configured and available. The evaluator consumes local JPEGs, makes serial loopback inference calls and never triggers speakers, stores cases, downloads videos or uploads frames to a remote model.

Reports are written through a temporary file and replaced atomically, with mode `0600` on POSIX. Windows protection still depends on the user's directory ACLs and device protection. The output must be JSON and cannot overwrite the manifest, predictions or plan. Keep the entire dataset and reports in protected private storage.

## Read the result correctly

The report includes manifest, predictions, coverage-plan and evaluator-source hashes, model tag/digest when provided, prompt version/hash and frame hashes from local execution. The provider digest is available from the configured runtime/launcher; an arbitrary external report does not establish artifact verification. Preserve the verified model/runtime artifacts and application version alongside the run.

`branch_coverage` contains one record for every planned branch:

| Status | Meaning |
|---|---|
| `NOT_RUN` | No selected test windows carry this branch reference. |
| `INSUFFICIENT` | A declared sample minimum is missing or an inference error occurred. The `missing` list records observed and required counts. |
| `SUFFICIENT_FOR_REVIEW` | The declared sample inventory was exercised without recorded inference errors. Accuracy and live acceptance still require review. |

Counts use successfully processed ground-truth-labelled windows, including correctly or incorrectly answered and abstained windows. A model returning `UNCLEAR` for every image could satisfy inventory counts and still be unusable; the confusion matrix, abstention and recall expose that failure. Unscoped windows and unplanned branches are reported but cannot satisfy another branch's minimums. The `declared_before_test` assertion is explicitly marked as not independently verified.

Each branch also has its own confusion matrix, per-class precision/recall, abstention rate, simulated normal false-alarm episodes and normal camera-hour rate. These performance metrics include unsuccessful inferences as abstentions and missed labels, even though failed inferences cannot satisfy sample-coverage minimums. A missing denominator remains `null`.

The overall and per-branch `alarm_eligibility` metrics distinguish a predicted action from the application's routing rule. A correct possible-concealment label with insufficient visible evidence does not count as an eligible alert. Recall uses all concealment-labelled windows, including errors and abstentions. Eligible results on normal labels count against resolved precision; eligible results on `UNCLEAR` ground truth are listed separately because their truth is unresolved. These are replay eligibility counts, not proof that a speaker sounded.

`simulated_alarm_delivery` first applies a 15-second measured evaluation
processing-time proxy for the browser freshness gate, then replays the configured source-time cooldown and
separates eligible windows, new sound requests, cooldown suppression, missing
eligible signals and concealment windows without a new sound. It does not test
live queue and polling delay, the operating-system speaker, network delivery or staff
response. `routing_rule_strength` summarizes deterministic evidence gates when
the provider supplies them; it is not calibrated model confidence.

No population accuracy confidence interval is generated: overlapping samples, repeated people and repeated views are dependent observations. Reports retain raw support, null values for unmeasured denominators and explicit uncertainty. The testing coordinator must inspect the per-branch findings instead of pooling six branches into one apparent success.

`execution_mode: PREDICTION_FILE_METRICS_ONLY` means a supplied prediction file was scored. It remains that mode even if the file claims local-model provenance. It tests metric calculation, not model execution. `LOCAL_PROVIDER_EXECUTION` records the evaluator's actual local calls. Neither mode changes `site_acceptance_established: false`.

CLI exit status 0 means the requested calculation completed without inference errors; 1 means inference errors occurred; 2 means invalid input/configuration. Insufficient sample coverage remains visible in the JSON and does not masquerade as an execution failure. Automated release tooling must read the branch coverage and other readiness evidence, not just the process exit code.

Before enabling an automatic attention alarm for a view, review missed actions, normal-shopping false alerts and unclear results against pre-agreed operating bounds. Separately measure action-to-alert latency, live sampling gaps, camera disconnection, sleep/resume, stop/mute behaviour and physical speaker audibility on that actual laptop. A testing agent can execute scripts and consolidate evidence; pharmacy staff must grant access and observe physical checks.

## Casework-only runtime interlock

`AISLESIGNALS_VISION_DISABLED=1` disables the provider independently of whether any model is listening on the configured port. Disabled status is `ready: false, mode: disabled`; analysis rejects before image parsing or an HTTP client is created. A provider constructed disabled stays disabled for its lifetime even if the environment flag is removed. An instance constructed enabled also honours a later exact `1` flag before any new request. Other spellings such as `true` are not the contract.

This supports the launcher's explicit casework-only mode and unavailable-model fallback. A later unrelated loopback service cannot silently activate interaction inference. Restart through the launcher with an available verified configuration to enable analysis. This flag is an operational interlock, not a model qualification or pharmacy acceptance record.
