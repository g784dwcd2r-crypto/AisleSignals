# Person-first detector and anonymous tracker

## Shipped boundary

The browser worker now runs a dedicated object detector before pose analysis.
Only a result classified as `person` at score 0.40 or above can open a pose
crop. The green/yellow/red rectangle is the object detector's complete box,
not a rectangle reconstructed from pose points. A detected person remains
visible when pose fails, while all pose behavior rules abstain.

The worker rejects non-finite scores, limits a frame to six people, bounds the
selected camera image to 640 pixels on its longest edge, and bounds each pose
crop to 384 pixels. A person touching a frame edge or measuring less than
32 x 72 pixels / 4,000 pixels area is labelled partial or too small. Pose does
not run for either state. Accepted pose landmarks must form a coherent torso
and remain spatially consistent with the detector box.

Tracking is anonymous and camera-local. It uses a short constant-velocity
prediction, box overlap, centre displacement and scale. Mutual/global
competition invalidates every rival history and starts a fresh anonymous ID.
A short occlusion can recover a display ID, but the recovery frame clears
behavior evidence. There is no face embedding, biometric template,
cross-camera re-identification or cross-camera ID handoff.

Four/six-camera mode uses two workers at most. The scheduler records the last
32 completion revisit gaps per camera, exposes p95 revisit latency and marks a
ticket when its camera exceeded 1.2 seconds. The consumer resets that camera's
engine before using the resumed result. These are processing-health measures,
not detection coverage or an accuracy result.

## Model provenance and offline bundle

| Component                              | Pinned source                                                                                                       | Version/hash                                                                                | Licence evidence                                                                                 |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| MediaPipe Tasks Vision Web runtime     | `@mediapipe/tasks-vision`                                                                                           | 0.10.35                                                                                     | MediaPipe repository: Apache-2.0                                                                 |
| EfficientDet-Lite0 uint8 with metadata | Google MediaPipe Tasks storage; TensorFlow/Kaggle model `tensorflow/efficientdet/tfLite/lite0-detection-metadata/1` | 4,563,519 bytes; SHA-256 `2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b` | TensorFlow model page lists Apache 2.0                                                           |
| Pose Landmarker Lite float16           | Google MediaPipe Models storage                                                                                     | 5,777,746 bytes; SHA-256 `59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a` | Existing project provenance; redistribution terms still require final release/legal confirmation |

Primary references:

- https://developers.google.com/edge/mediapipe/solutions/vision/object_detector/web_js
- https://www.kaggle.com/models/tensorflow/efficientdet/tfLite/lite0-detection-metadata/1
- https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE

`scripts/prepare-vision.mjs` downloads only during development/release build,
enforces exact length and SHA-256, and reuses a verified local copy. Vite copies
the runtime and both models under `dist/vision`; runtime inference uses
same-origin files and performs no model download. A clean release build still
needs network access once unless the verified build cache is pre-seeded.

## Verification on this change

- Pure tracker, behavior and scheduler regression tests include smooth motion,
  tied assignments, competing-track invalidation, short occlusion, ID overflow,
  malformed scores, detector-box rendering, partial/too-small people, and
  bounded six-camera pairs.
- The existing `synthetic-video.webm` browser fixture exercised the actual
  model worker. It produced no people and no alarms, and requested the runtime
  and model files only from the application origin.
- On an Apple Silicon Mac using headless Chrome 153, 20 detector frames of that
  empty synthetic 640-scale fixture measured mean 40.82 ms, p50 38.30 ms and
  p95 41.00 ms after model initialization (one 82.40 ms outlier,
  `hardwareConcurrency=14`). This is a local engineering measurement, not a
  supported-device benchmark; it does not measure frames containing people,
  where gated pose adds work.

## Deliberate release limits

This change does not emit product-custody facts and does not enable a new alarm
path. It does not prove theft detection, person recall, false-alarm rate, six
camera throughput or pharmacy-footage performance. The custody layer remains
disabled until real source adapters derive bounded-gap coverage in one explicit
clock/session domain, POS finalization is trustworthy, and held-out branch
acceptance tests pass. Release packaging must also carry required third-party
notices and confirm the Pose model's redistribution terms.
