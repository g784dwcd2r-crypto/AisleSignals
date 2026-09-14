# AisleSignals Product Interaction Analysis

Prepared for Jawahir Q. · Implementation prototype · 13 September 2026

## What this adds

LIVE DETECTION can send four chronological frames from the selected CCTV video to a real local vision-language model. It classifies visible product pickup, return, placement in a basket, possible concealment, normal browsing or insufficient evidence. This layer samples independently of the body-pose rules. It is an experimental pretrained model baseline, not a pharmacy-trained or validated theft detector.

The model has no tools or output-control authority. A separate application rule can request an existing-laptop attention sound when the user explicitly enables the experimental interaction alarm, the observation meets the configured evidence conditions, and the current source is still fresh. No door, relay, public sounder or new hardware is involved. Neither an observation nor an alarm determines identity, criminality, intent or payment.

This iteration follows the owner's instruction to relax the earlier EUR 60 development constraint. The chosen implementation nevertheless incurs no per-frame cloud inference charge. The subscription offer has not been changed, and no paid service was purchased.

## Operator flow

1. Sign into the pharmacy workspace and open **LIVE DETECTION**.
2. Select an existing CCTV window, an available camera or an authorised recording. Use one original camera view with visible hands and products. For a CCTV grid, enable **Analyse this camera/aisle area** and set the rectangle around one tile. A thumbnail shows the sampled area; cropping happens before resizing. Changing the area cancels pending analysis and resets its frame sequence.
3. Start detection, then enable **Product interaction analysis**. Its status must show that the local model is ready.
4. Collect approximately four seconds of video and analyse the recent sequence, or enable automatic analysis. Only one analysis can run at a time; the app does not queue an accumulating backlog.
5. Inspect the result and its **sampled frames**. These are sequential JPEG derivatives, not a continuous video clip or an original evidence export.
6. Review it as useful, normal shopping or unclear. Delete it when no longer needed. The prototype expires sampled-frame evidence after 24 hours; that default is an engineering limit, not a pharmacy-specific retention assessment.

The experimental interaction alarm is a separate opt-in control. A recorded video remains labelled as a test. Stopping, seeking, changing the source, losing visibility or leaving the tab cancels the current analysis session and disarms its sound. Late results are for review only. Speaker output still depends on browser activation, system volume and the laptop remaining awake.

Before sound can be armed, staff must declare that the current camera selection covers the pharmacy entrance, exit, cashier and relevant shelf zones. The additive `camera_calibration` record is stored with new analysis jobs and surfaced in review history. Older clients and saved results without it remain readable and can still run analysis, but an otherwise alarm-eligible model observation is downgraded with `CAMERA_CALIBRATION_REQUIRED`. These confirmations are an operational gate, not an automated test of camera placement, image quality or model accuracy; each branch still needs a documented site acceptance exercise.

Every result includes an explainable `evidence_strength` of
`STRONG_RULE_MATCH`, `PARTIAL_RULE_MATCH` or `INSUFFICIENT_RULE_MATCH`. This is
computed from the visible-person, visible-product, sequence, visibility and
supporting-frame gates. It is not a confidence probability. Only a clear,
fully supported `POSSIBLE_CONCEALMENT` sequence is alarm-eligible, and staff
must still review it.

Sound requests use one 30-second cooldown across the active camera set and
deduplicate each saved observation ID. Acknowledging an all-camera attention
quietens repeat sound from that camera for two minutes while sampling and saved
review observations continue. Muting, stopping or changing the run disarms
sound and requires another physical speaker check. These controls reduce alarm
fatigue; they do not improve recognition accuracy or prove that anyone heard a
sound.

## Local model setup

The pinned model is the Q4_K_M version of [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF), accompanied by its F16 vision projector. Combined download is approximately 3.3 GB. The publisher marks this model Apache-2.0. It is a general vision-language model; its capabilities do not establish pharmacy detection accuracy.

From the repository root, using Python 3.12 or later:

```sh
python scripts/local-vision.py setup
python scripts/local-vision.py run
```

Setup downloads revision-pinned files and checks SHA-256 before use. The Mac ARM64 setup uses the llama-server and native libraries bundled in the official Ollama v0.34.0 release. It does not launch the Ollama desktop application. Everything is stored under the ignored `.local/vision-runtime/` directory. The model runs on loopback port 11435, with one inference slot, bounded image/context sizes, a local token file, disabled web UI/agent tools and disabled request logs. No cloud account is required. Ctrl+C stops the model process.

Start the AisleSignals API in a second terminal, using the existing database path for the intended workspace:

```sh
AISLESIGNALS_VISION_BACKEND=llamacpp \
AISLESIGNALS_VISION_URL=http://127.0.0.1:11435 \
AISLESIGNALS_VISION_MODEL=qwen3-vl:4b \
AISLESIGNALS_VISION_TOKEN_FILE=.local/vision-runtime/api-token \
AISLESIGNALS_DB_PATH=.local/preview/aislesignals.db \
.venv/bin/python -m uvicorn services.api.app:app \
  --host 127.0.0.1 --port 8765 --no-proxy-headers --no-access-log
```

These commands launch foreground development processes, not an all-shift service or signed installer. On Windows, `setup` prepares the same weights; `run --server PATH` requires a compatible installed llama-server executable. Alternatively the API supports an existing local Ollama server with `AISLESIGNALS_VISION_BACKEND=ollama`. Windows installation, camera permissions, throughput and speaker delivery require testing on the pilot Windows laptop. No Windows acceptance is implied by a Mac test.

## Data and failure behaviour

The browser sends a bounded set of JPEG frames only after analysis is enabled and submitted. The authenticated API validates frame count, bytes, decoded dimensions and increasing timestamps. The review store is capped at 100 jobs per site and 600 total; delete reviewed results to free capacity. The provider address is restricted to loopback. Incoming request text and image text never grant the model authority to use tools or change application settings.

Jobs, observations, reviews and evidence are scoped to the signed-in organisation and pharmacy site. Frame URLs require the same authenticated site access and do not expose arbitrary paths. The API retains metadata and sanitized JPEG derivatives locally; the browser and API do not send them to an external AI provider. All such files are excluded from git. Deletion and expiry revoke frame access. The existing local demonstration accounts/authentication remain a prototype limitation.

If the model is missing, busy, unreachable, timed out or returns invalid output, the UI reports that state. No substitute fixed-rule answer is presented as model inference. A cancelled or expired job cannot publish a fresh attention signal. The selected model version and prompt version accompany observations, so changing the model requires repeating evaluation.

## Detection limits and acceptance

- Four sampled frames may miss a brief action between samples. Whole-view downsampling can hide a small product. One inferred sequence may omit other simultaneous interactions.
- A general vision-language model can hallucinate or confuse normal bag handling and concealment. Structured JSON and evidence indices constrain the software response; they do not prove the model's interpretation.
- A claimed visible product is itself a model observation, not an independent object-detector verification. No product-SKU inventory matching, trained object tracker or person-to-item ownership proof has been added.
- Staff feedback is retained for review. It does not silently retrain the model. Authorised, labelled camera-specific examples and held-out evaluation are still required.
- Current classification does not establish whether a product was paid for. There is no cross-visit recognition or face database.
- Monitoring stops on laptop sleep, browser inactivity/visibility loss and the prototype's session expiry. This remains an attended development prototype.

Use the [interaction evaluation guide](interaction-evaluation.md) and its command-line evaluator to measure false alerts, missed interactions, abstentions and timing on held-out pharmacy examples. Passing workflow tests demonstrates software behaviour; it does not satisfy site detection acceptance.

## Reproduce the real-model runtime check

With the model running, use the same vision environment settings as the API command above:

```sh
AISLESIGNALS_VISION_BACKEND=llamacpp \
AISLESIGNALS_VISION_TOKEN_FILE=.local/vision-runtime/api-token \
.venv/bin/python scripts/smoke-interaction-model.py \
  --output .local/vision-runtime/api-smoke-result.json
```

This calls the real configured model through an isolated temporary API database, using four generated frames of a moving rectangle. It verifies no-person abstention, alarm suppression, authenticated JPEG retrieval, staff feedback and deletion. The control initially exposed a false normal-shopping label; the prompt and structured result gate were updated to require a reported visible person. The updated model returned UNCLEAR with no alarm eligibility. Its measured inference was about 2.7–3.2 seconds on this development Mac. A repeated static fitness photograph also returned UNCLEAR with no product reported. Neither test contains pharmacy pickup/return/concealment ground truth, so neither supplies a detection accuracy figure.
