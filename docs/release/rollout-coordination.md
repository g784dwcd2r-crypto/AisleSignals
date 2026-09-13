# Six-pharmacy rollout coordination

Owner: Jawahir Q. Target: Tuesday, 15 September 2026.

The coordinator collates evidence for six independently accepted pharmacy/laptop configurations. The supplied manifest contains **six unassigned slots and zero accepted pharmacies**. It contains no client names, credentials, camera addresses or acceptance claims. A developer MacBook report must never be assigned to a client's Windows laptop or copied into six slots.

The tool validates local file hashes, timestamps, matching identifiers, required checks, predeclared performance limits and recorded human signoff. It does not authenticate the person or machine that produced an imported record. A hash detects changes to the selected file; it is not a digital signature or proof that a test happened. Keep the intake directory under the coordinator's control and verify the originating branch/laptop with the person supplying it.

## Run a readiness report

Copy `deployment/six-branch-rollout.example.json` into a private intake directory such as `.local/rollout/manifest.json`. Keep all referenced records, test logs and artifacts inside that directory. Existing evidence files are preserved; choose new report filenames for each run.

```sh
python scripts/coordinate_rollout.py report \
  --manifest .local/rollout/manifest.json \
  --json .local/rollout/readiness-01.json \
  --markdown .local/rollout/readiness-01.md
```

The protected desktop bundle exposes the same arguments after `rollout` when included by its launcher. The source command requires only Python's standard library. It does not start an API, open cameras, sound speakers, upload footage, contact a network service or alter the manifest.

Exit codes: `0` means all six slots have complete passing records; `2` means one or more slots remain unaccepted; `1` means a configuration or file error prevented reporting. `RECORDED_ACCEPTANCE_COMPLETE` means the supplied supervised configuration has complete passing records. It does not mean perfect detection, unattended monitoring or independently witnessed commissioning.

Each report lists the exact missing/failed check IDs and separates imported automated results from imported human attestations. Missing evidence is `NOT_RUN`; inconsistent, stale, tampered or failed evidence is `FAIL`. The coordinator's checks do not convert a hardware inventory or successful build into physical laptop acceptance.

## Assign a deployment slot

Every slot has an opaque `slot_id`, a `context` object and six `evidence` references. Populate all context fields before importing evidence:

| Field | Required value |
|---|---|
| `site_id` | The actual provisioned site's opaque ID; unique across these six slots |
| `device_id` | An opaque locally assigned laptop ID; unique across slots |
| `platform` | `Darwin` for macOS or `Windows` |
| `source_version` | Versioned identifier for the selected camera/view/crop configuration |
| `app_revision` | Exact 40-character lowercase application Git revision |
| `model_sha256` | Exact 64-character lowercase model-weight SHA-256 |
| `prompt_version` | Exact prompt version used during evaluation |

Keep addresses, passwords, staff names, footage and customer descriptions out of identifiers. A context change invalidates previous evidence until the matching configuration is tested and signed off again. Source selection is an operator assertion, not an automatically authenticated camera identity.

Each evidence reference is `{"path":"relative-file.json","sha256":"64-lowercase-hex-characters"}`. Relative paths are resolved against the manifest directory. External URLs, absolute paths, `..`, symlinks and checksum mismatches are rejected. Referenced JSON is limited to 1 MiB, test logs to 16 MiB and executable artifacts to 2 GiB each.

## Import existing preflight and evaluation reports

Run the preflight on the actual assigned laptop, before starting services on its configured ports. Its `api_port_free` and `vision_port_free` checks describe whether the launcher can start its own processes; `api_availability` is not an acceptance gate during this pre-start check.

```sh
python scripts/pilot_preflight.py --config .local/pilot/config.json \
  --report .local/rollout/raw-preflight-01.json

python scripts/coordinate_rollout.py intake \
  --manifest .local/rollout/manifest.json --slot slot-01 --kind preflight \
  --input .local/rollout/raw-preflight-01.json \
  --output .local/rollout/slot-01-preflight-01.json
```

The intake command returns the output SHA-256. Add its relative path and digest to that slot's `evidence.preflight`. It preserves the original report timestamp and labels its context as `OPERATOR_SUPPLIED_CONTEXT`; it does not certify that a supplied report came from that laptop. Invalid payloads can be bound, but the `report` command will reject them. Repeat with `--kind evaluation` for a report from `scripts/evaluate-interactions.py --run-local --split test --coverage-plan ...`.

Generate **one evaluation report per branch**. The evaluator currently reports timing globally; requiring exactly one branch, zero unscoped/unplanned windows and matching window counts prevents a fast branch from hiding another branch's latency. Record actual local-provider execution, all three disjoint splits and successful frame-content split checks. A prediction-file fixture cannot qualify a model. No authorised client video or held-out measurements are supplied with this repository.

The default evidence age is 72 hours and can be tightened in the manifest (1–168 hours). Envelope and underlying observation/evaluation timestamps must be current. A new envelope does not refresh an old report. Clock errors must be corrected at the source. Acceptance plans may have been declared earlier, but their imported review envelope must still be current.

## Common evidence envelope

All six record types use this structure. Copy the assigned slot's context exactly and use an actual timezone-aware timestamp:

```json
{
  "schema_version": 1,
  "kind": "onsite",
  "context": {},
  "binding": "OPERATOR_SUPPLIED_CONTEXT",
  "recorded_at": "2026-09-13T12:00:00+00:00",
  "payload": {}
}
```

The empty objects above are placeholders, not passing records. Evidence payload fields follow the contracts below. Hash the complete envelope bytes after saving it. Changing any evidence requires a new hash and new signoff of the updated evidence set.

### Preflight

Use the unedited payload returned by `pilot_preflight.py`. Required automated checks are `platform_inventory`, `python_runtime`, `web_build`, `data_path`, `free_disk`, `memory_inventory`, `api_port_free`, `vision_port_free`, `vision_runtime`, `model_weight_1`, `model_weight_2` and `vision_token_file`. The launch status must be `PASS`; OS and model digest must match the assigned context. Physical checks remain in the separate on-site record.

### Software verification

The `software` payload requires the exact `app_revision`, a `checks` array and an `artifacts` array. Required check IDs are `python_regression`, `web_unit`, `chromium_workflows` and `platform_bundle`. Each check contains `status` (`PASS`, `FAIL` or `NOT_RUN`), actual integer `passed`/`failed` counts, and a `log` file reference containing `path` and `sha256`. A passing result requires a positive pass count and zero failures. Preserve actual test output; do not fill a report from an expected count or agent assertion.

Each artifact contains `platform`, `app_revision` and `file` (a path/SHA-256 reference). At least one artifact must match the assigned OS and revision. Hash checks establish artifact identity, not code signing or physical execution. The on-site `platform_workflow` check covers the actual laptop. The coordinator imports reported test results and verifies their supporting files; it does not re-run or semantically parse arbitrary test logs.

### Predeclared acceptance plan

The `acceptance_plan` payload requires `declared_before_test: true`, an opaque `owner_reference`, `declared_at`, the evaluator coverage plan's `coverage_plan_sha256`, and a `bounds` object containing **all five** values:

- `max_false_alarms_per_camera_hour`
- `max_abstention_rate` (0–1)
- `min_concealment_recall` (0–1)
- `min_concealment_precision` (0–1)
- `max_p95_wall_ms` (positive)

The owner must choose bounds appropriate to staff workload, missed-event risk and the agreed operating scope **before viewing held-out results**. There are no shipped acceptance percentages. The declaration is an imported record, not an independently verified timestamp; the final reviewer must check that it was genuinely predeclared. Metrics are measured on labelled sampled windows, and simulated alarm episodes do not measure physical alarm delivery.

### Held-out evaluation

Use `interaction-evaluator-v2` output. The coordinator requires real local-provider execution, matching model/prompt versions, the test split, disjoint camera/day/person/frame-path checks, successful frame-content split checks, and zero inference errors. `branch_coverage.status` may be `SUFFICIENT_FOR_REVIEW`, but this establishes only the supplied sample-count plan. The coordinator independently compares branch metrics to all five declared bounds. Missing denominators/timing remain `NOT_RUN`; unmet bounds fail. All results still require explicit branch signoff.

### On-site human attestation

The `onsite` payload requires `method: "HUMAN_ATTESTATION"`, an opaque `reviewer_reference`, actual `observed_at`, and a `checks` array of `{ "id": "...", "status": "PASS|FAIL|NOT_RUN" }`. Required checks:

| Check ID | What the person must verify on the assigned laptop/source |
|---|---|
| `authorised_camera` | Authorised source and correct pharmacy view; excluded patient screens |
| `readable_product_view` | Staff can see the relevant product/hand interaction at its actual scale |
| `source_disconnect` | Lost capture stops analysis and requires reconnect |
| `physical_audio` | Staff actually hear the test through the intended existing speakers |
| `silence_and_acknowledge` | Stop, mute and acknowledgement controls work without stale replay |
| `sleep_resume_rearm` | Actual laptop sleep/wake stops capture and requires explicit restart/reselection |
| `platform_workflow` | Sign-in, source, analysis, review and shutdown work on that physical OS/laptop |
| `filesystem_acl` | The actual laptop's evidence/data access permissions are restricted |
| `backup_restore` | A restore drill recovers the intended evidence and access controls |
| `named_reviewer_coverage` | An identified local staff reviewer covers the supervised operating hours |

The test agent cannot hear speakers, grant camera permissions, impersonate staff or attest to tests it did not observe. Leaving any entry `NOT_RUN` preserves the gap.

### Branch signoff

The final `signoff` payload requires `method: "HUMAN_ATTESTATION"`, an opaque `reviewer_reference`, `signed_at`, `decision: "APPROVE_SUPERVISED_ROLLOUT"`, `held_out_performance_reviewed: true` and `coverage_and_false_alert_workload_reviewed: true`.

It also requires `reviewed_sha256`, mapping each of `preflight`, `software`, `acceptance_plan`, `evaluation` and `onsite` to the exact digest the person reviewed. Its signing time must follow those evidence records. A new application build, view, model, prompt, evidence file or expired observation invalidates the earlier combination. Branch signoff cannot override failed performance or missing physical checks.

## Current external dependencies

Actual branch IDs, laptop inventory, camera access, held-out footage, owner-selected acceptance bounds and physical commissioning/signoff have not been supplied. The six-slot report therefore begins at **0 of 6 accepted**. The coordinating agent can run software checks and maintain this matrix when the task is active; it is not a continuously operating support service.
