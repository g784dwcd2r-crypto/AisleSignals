# AisleSignals interaction evaluation

Owner: Jawahir Q. · Requirements: FR-009, FR-017, FR-021, FR-023, FR-045, FR-067

**Current status: experimental interaction inference; no pharmacy-site accuracy validation or live-release acceptance has been established.** The evaluation CLI is implemented. Its committed synthetic prediction examples and unit tests verify calculation, input validation and failure handling. They contain no pharmacy footage and do not test a vision model's recognition ability.

## What is being evaluated

The local visual-language provider examines three to six ordered JPEG frames over at most twelve source seconds. It returns an observable interaction, visibility and evidence-frame references. This is a pretrained visual-language baseline, not a classifier trained on our pharmacies. Generated classifications and the `alarm_eligible` rule are not calibrated confidence probabilities. A valid JSON response can still describe the scene incorrectly.

The evaluator calls `services.api.interaction_vision.VisionProvider.analyze()` serially. It does not log into the web application, save incidents, start capture, trigger speakers or send frames outside the configured loopback provider. The selected local backend and model must already be running. Default settings target Ollama at `http://127.0.0.1:11435`; the provider also supports the configured local llama.cpp backend. Use the same model, prompt, frame size and sampling schedule as the application for a meaningful comparison.

Qwen's [4B Instruct model card](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) lists multi-image/video capabilities and Apache 2.0 licensing. Those general capabilities do not establish pharmacy concealment accuracy. Ollama documents [image input](https://docs.ollama.com/capabilities/vision) and [schema-constrained output](https://docs.ollama.com/capabilities/structured-outputs); schema enforcement verifies structure, not visual truth.

The existing skeleton overlay has a separate limitation: Google's [BlazePose GHUM model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf) describes fitness-oriented use and lists surveillance outside its intended scope. Do not use successful skeleton rendering as acceptance evidence for CCTV recognition. The interaction baseline must also be evaluated on windows where the skeleton tracker misses a person.

## Capture and label the first pharmacy dataset

1. Use an authorised existing camera's original export or supported software feed. Inspect the actual inference-size frames: products, hands and the transition must remain visible. A tiny multi-camera tile can be unsuitable even when the original recording is sharp. No required new camera, capture card or GPU is implied.
2. Record the pharmacy's authorisation reference, camera, view, recording date, source SHA-256 and participants' local pseudonymous IDs in the private dataset. Keep consent and identity records separately; never put names, patient information, audio or camera passwords in this repository. Exclude prescription screens and consultation areas from captured material.
3. Stage pickup, inspection, return, basket placement, staff restocking, phone retrieval, bag adjustment, handing an item to a colleague and possible concealment. Vary actors, clothing, product size, shelves, lighting and movement speed. Include occlusion, crowding and occasions when the product is too small to determine the action. A staged concealment is an acted movement, not an accusation.
4. Collect separately labelled ordinary trading observations with a measured duration. Do not use only a selection of interesting clips: that cannot measure the alarm burden over a working day. Record gaps, outages and changes in view rather than silently counting them as monitored time.
5. Have a pharmacist label each window without seeing the model prediction. A second reviewer resolves disagreements; unresolved examples receive `UNCLEAR`. Label the latest clearly completed product transition in a connected sequence: pickup then return is `RETURN_PRODUCT`; pickup followed by clear insertion into clothing is `POSSIBLE_CONCEALMENT`. Unrelated competing people or an unobservable transition make the window `UNCLEAR`.
6. Assign development and held-out splits before prompt tuning. The CLI rejects camera, day, person and frame-path groups that occur in different splits. It also rejects duplicate source-recording hashes. Real inference checks exact frame hashes across all splits, so renaming a copied image does not hide the duplication. These checks depend on honest metadata and cannot detect every near-duplicate or unrecorded participant.

| Label | Required visible evidence | Common confusing example |
|---|---|---|
| `TAKE_PRODUCT` | Product moves from shelf to hand | Hand reaches toward shelf but product is hidden |
| `RETURN_PRODUCT` | Product moves from hand back onto shelf | Hand merely passes near a shelf |
| `PLACE_IN_BASKET` | Product enters a shopping basket or trolley | Product moves behind a basket without visible placement |
| `POSSIBLE_CONCEALMENT` | Visible product moves into clothing or a personal bag in an observable sequence | Phone retrieval, bag adjustment, body occlusion |
| `NORMAL_SHOPPING` | Ordinary visible browsing without a supported product transition | Product interaction too small or hidden to classify |
| `UNCLEAR` | Evidence is insufficient or people/actions cannot be associated reliably | Cropped hands, fast motion, competing people, hidden transition |

These labels do not establish payment, intent, identity or theft. A personal-bag placement can have an innocent explanation. Staff still review the evidence and decide the response.

For two pilot cameras, fully disjoint camera/day/person train, validation and test sets may need more authorised recordings or camera views. Do not silently weaken the split check or call a same-camera test a cross-camera result. A test-only manifest is allowed for initial untouched footage; the report states that all three split inventories are absent, so it cannot establish independence from earlier development material.

## Local manifest and commands

Store authorised material under an ignored private directory such as `.local/interaction-evaluation/`. The manifest and its JPEG files stay together. Frame paths must be relative, remain inside the manifest directory after symlink resolution, and use `.jpg` or `.jpeg`. Remote URLs and absolute frame paths are rejected. The provider decodes and bounds each JPEG to 32–768 pixels per edge and at most 350,000 bytes. Resizing does not restore product detail lost in the source.

The manifest format is:

```json
{
  "schema_version": "1.0",
  "dataset_id": "private-pilot-v1",
  "split_policy": "DISJOINT_CAMERA_DAY_PERSON",
  "authorization": {"confirmed": true, "reference": "private-capture-approval-01"},
  "sessions": [{
    "id": "session-01", "split": "test",
    "camera_id": "camera-heldout-01", "day_id": "day-heldout-01",
    "recording_sha256": "REPLACE_WITH_64_LOWERCASE_HEX_CHARACTERS_FROM_THE_ORIGINAL_RECORDING",
    "source_kind": "STAGED", "duration_seconds": 6,
    "normal_duration_measured": false
  }],
  "windows": [{
    "id": "window-01", "session_id": "session-01",
    "start_seconds": 0, "end_seconds": 6,
    "label": "RETURN_PRODUCT", "person_ids": ["participant-heldout-01"],
    "frames": [
      {"path": "frames/window-01-0.jpg", "offset_seconds": 0},
      {"path": "frames/window-01-1.jpg", "offset_seconds": 3},
      {"path": "frames/window-01-2.jpg", "offset_seconds": 6}
    ]
  }]
}
```

Use one session per source recording. `source_kind` is `STAGED`, `AUTHORISED_INCIDENT` or `NORMAL_OBSERVATION`; split is `train`, `validation` or `test`. For a continuous reviewed normal recording, set `normal_duration_measured: true`, provide windows covering its entire duration and use only adjudicated normal labels. A window containing disputed behaviour belongs in a separate adjudicated dataset, not a normal-hours denominator. Frame offsets are seconds in the source recording, in increasing order; the first and last frames must delimit the labelled window. This CLI consumes already sampled frames; it does not download, decode or silently select moments from an original video.

Run actual inference from the repository root, using the same provider environment as the API:

```sh
.venv/bin/python scripts/evaluate-interactions.py \
  --manifest .local/interaction-evaluation/manifest.json \
  --run-local --split test \
  --output .local/interaction-evaluation/report.json
```

It validates all split frame files before inference and holds only one window's image bytes for a model call. An unavailable model fails the run before inference. Per-window inference failures are recorded as `UNCLEAR` with `status: ERROR`, count against coverage, never become alarm-eligible, and produce exit status 1. Manifest/configuration failures produce status 2; a completed run with no inference errors returns 0. A zero exit status is not site acceptance.

For a reproducible **metric-calculation demonstration with no footage and no model execution**:

```sh
.venv/bin/python scripts/evaluate-interactions.py \
  --manifest tests/fixtures/interaction-evaluation/metrics-only.manifest.json \
  --predictions tests/fixtures/interaction-evaluation/metrics-only.predictions.json \
  --output .local/interaction-evaluation/metrics-only.report.json
```

Prediction-file input requires a `provenance` object with `origin`, `model` and `prompt_version`, plus one result per selected window: `window_id`, `action`, `alarm_eligible`, `inference_ms`, and optional `status` (`OK` or `ERROR`). All missing, duplicate, unknown or invalid predictions fail validation. The report labels this mode `PREDICTION_FILE_METRICS_ONLY`; it cannot attest that those predictions came from any model. The committed example has intentionally wrong and uncertain synthetic predictions and deliberately nonexistent frame paths, so `--run-local` cannot use it as apparent accuracy evidence.

## Reading the report

- Confusion matrix: actual labels are rows; predictions are columns. Per-class precision and recall include integer support and prediction counts. A zero denominator is `null`, not 0% or 100%. No overall theft-accuracy number is generated.
- Abstention: `UNCLEAR` predictions divided by all selected windows. Errors count as abstentions and are also reported separately. A high abstention rate can conceal an unusable detector even if precision on a few answered windows looks good.
- Normal camera-hours: only measured normal sessions whose labelled windows cover the complete duration and whose inferences have no errors enter the denominator. Overlapping windows count the session duration once. Separate source hashes reject an exactly duplicated recording, but the operator must also avoid overlapping exports of the same time period.
- Simulated false-alarm episodes: alarm-eligible results on qualifying normal sessions are grouped using the configured source-time cooldown (default 30 seconds). This is a replay estimate of eligibility under that rule. It does not test browser freshness checks, live processing gaps, real alert queueing or physical sound. Without qualifying normal duration the rate is `null`.
- Timing: median, nearest-rank p95 and maximum inference times and wall times are reported. They are model-call timings, not detection latency. Separately measure action onset → captured frames → inference completion → delivered alert on each real laptop. Sparse samples and a slow model can miss a fast action altogether.
- Provenance: canonical manifest and prediction SHA-256, model tag/digest when provided, prompt version/hash, and frame hashes from real execution. Reports omit frame bytes and generated narrative. Pin and archive exact runtime/model artifacts separately; an absent model digest does not become a verified model version.

The evaluator always leaves `site_acceptance_established: false`: software cannot grant a pharmacy's deployment acceptance. Do not tune on the held-out test report and reuse it as a fresh independent result. Label corrections and feedback require a versioned dataset and a new untouched test allocation.

## Acceptance report template

**Status: NOT EVALUATED ON AUTHORISED PHARMACY FOOTAGE.** Replace this only after actual capture, independent labelling and measured runs.

| Record | To complete from the pilot |
|---|---|
| Pharmacy/view and authorised reviewer | Private references; no names in the public repository |
| Existing laptop and source | OS/architecture, RAM/processor, camera software, resolution, field of view |
| Reproducible version | Application commit, model artifact digest, prompt hash, manifest hash, frame sampling, cooldown |
| Held-out material | Cameras, days, participants and recording counts in each split; overlap audit |
| Labels and review | Count per action, disagreements and adjudication, staged versus ordinary footage |
| Detection results | Per-class precision/recall/support; confusion matrix; missed events; abstention; errors |
| Staff burden | Measured normal camera-hours, false-alarm episodes and examples, busy/quiet periods |
| Live latency and coverage | Action-to-alert timing, sampling gaps, model backlog, sleep/resume, source disconnection |
| Sound acceptance | Existing speaker test, stop/mute/acknowledge, OS mute, audible range; manager-observed result |
| Pre-agreed acceptance bounds | Maximum staff alert burden, minimum recall per enabled class, allowed latency and abstention; agree before viewing test results |
| Decision | Experimental test only / silent pilot / enabled classes for this view; reviewer and date |
| Remaining limitations | Occlusion, small products, viewpoint, crowded scenes, unavailable classes and rollback route |

Begin with silent classification and pharmacist review, then enable only the classes and views supported by that evidence. Maintain an untouched regression set and review errors rather than automatically retraining on unverified model-generated labels. Commission Mac and Windows separately. Neither model runtime installation nor a successful generated-data browser test establishes a pharmacy pilot.

## Additional research sources

[Shopformer](https://github.com/TeCSAR-UNCC/Shopformer) and [PoseLift](https://github.com/TeCSAR-UNCC/PoseLift) are relevant pose-sequence research baselines. Their repositories publish [Apache 2.0 licences](https://raw.githubusercontent.com/TeCSAR-UNCC/Shopformer/main/LICENSE) ([PoseLift licence](https://raw.githubusercontent.com/TeCSAR-UNCC/PoseLift/main/LICENSE)). PoseLift provides pose data and binary anomaly labels rather than raw RGB product footage or the six interaction labels above; it is not a drop-in labelled dataset for the current visual-language input. Verify the actual artifact's applicable terms and provenance before importing research data or weights. No research artifact or third-party media is bundled by this evaluator.

The user-supplied YouTube demonstrations remain visual references. Public playback does not establish download, redistribution or model-training rights, nor does a selected demonstration supply normal-trading false-alarm evidence. Authorised staged pharmacy recordings are the first directly relevant source for this project; none has yet been added to the repository.
