# AisleSignals — Live Detection

Built for Jawahir Q. · 13 September 2026 · Experimental local detection prototype

## Use the dedicated tab

1. Start AisleSignals, sign in and open **LIVE DETECTION** in the sidebar.
2. Select **Share CCTV screen** for an existing CCTV browser/app window, **Connect camera** for an explicitly authorised browser-accessible video device, or **Load CCTV video** for an MP4/WebM recording.
3. Keep one clear camera view visible in the selected source. A separate recorder monitor needs its existing laptop viewer first. Direct RTSP decoding and automatic CCTV-grid tile extraction are not implemented.
4. Use **Test speaker sound**, choose a comfortable audible volume, then **Start detection**. Camera frames and model inference remain local. No source audio is captured.
5. Person boxes, body keypoints and activity labels appear on the video. A supported rule event raises a prominent banner, requests the attention tone and saves a classified branch event automatically.
6. **Silence alarm** stops the sound. **Acknowledge** records staff receipt of a saved event. **Stop detection** terminates inference and live capture. For a recording, **View moment** seeks back to the event while detection is stopped.

Recorded footage is clearly marked **Recorded CCTV**, even though it is analysed as it plays. It never becomes a live-camera record. The existing Video test tab remains a separate image-change tool.

## Implemented detection

The model is MediaPipe Pose Landmarker Lite, float16 bundle version 1, using the pinned Tasks Vision runtime 0.10.35. It detects body poses and estimates 33 keypoints, with up to four poses requested per frame. Original AisleSignals rules follow anonymous people within the current view; there is no face identification or cross-visit recognition.

| Rule | Trigger | What the event establishes |
|---|---|---|
| Repeated reach toward waist | Two sustained outward-reach/waist-return sequences on the same visible hand within a bounded window. Both sensitivity modes require two sequences; sensitive uses shorter confirmation dwell. | A pose pattern for staff review. It does not establish a product, concealment, payment status or theft. |
| Restricted-zone presence | A clearly observed torso/hip remains inside a staff-enabled rectangular zone for at least two seconds. Disabled by default. | Presence in the configured image area. The software cannot distinguish staff authorisation or physical depth from this rule. |

Green, amber and red overlays describe normal-pattern, watching and review-alert states. Lack of a detected pose is shown explicitly. Low-visibility limbs cannot satisfy the hand rule; uncertain crossings discard partial histories. Geometry compensates for video aspect ratio. Rules have per-track/event cooldown, while audio has its own source-level minimum gap.

These rules are experimental. A pose model plus temporal rules is not a trained pharmacy shoplifting classifier. Ordinary activities can match them, while obstructed or small figures can be missed. The model card describes fitness-oriented use and excludes surveillance validation; neither this implementation nor its software tests establishes suitability or accuracy for a pharmacy deployment. Qualify camera views and measure event precision, missed events and false alerts before operational reliance. [Model documentation](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker), [model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf).

## Alarm and records

Starting detection attempts to enable Web Audio from the user's click. A new eligible alert can request three alternating-tone bursts of up to eight seconds, spread across a bounded 28-second episode. New events do not extend that episode. Mute, silence, acknowledgement, Stop, source loss and page exit stop the relevant audio. Browser/OS volume and actual speakers determine physical loudness; successful audio API calls are not proof of audibility.

Alarm delivery and saving are independent. Metadata includes branch authority resolved by the server, run/event IDs, source type and label, track number, video offset, detection time, rule/model version and whether sound was requested for that event. The server assigns the event description, saves it in local SQLite, and records acknowledgement/audit. It does not create a theft case automatically.

No video or snapshot is uploaded or saved by this slice. The existing CCTV system or the chosen local file remains the recording source. Each event states **No video clip saved**. Pre/post-event evidence capture is future work. Unsaved events remain visible for retry while the tab is open; reaching 100 unsaved events stops detection. Closing the tab loses unsaved event metadata. Duplicate retries preserve the original record, and conflicting reuse is rejected.

## Runtime and recovery

- A dedicated Web Worker owns the model and processes one frame at a time, capped at roughly four samples per second. Input is resized to a maximum dimension of 640 pixels. There is no growing queue and no paid inference API.
- Starting, stopping or changing source uses cancellation and generation checks. Late results cannot publish alerts after stop. Model initialization can be cancelled immediately.
- Pausing, seeking, changing playback speed, hiding the tab, losing capture or source stalls stop or reset detection. Results older than one second are discarded; repeated slow results stop the run with an actionable message.
- This browser prototype must remain visible and the laptop awake. The existing session policy also expires after 15 minutes without qualifying session activity and stops the detection view; it is not an all-shift background companion.
- Screen capture detects readable pixels supplied by the browser. A CCTV viewer can show an old/frozen picture while its playback clock advances; the prototype does not independently verify the recorder's timestamps or every camera's health.
- File inputs are bounded to 250 MiB, ten minutes and 4K, using browser-supported MP4/WebM codecs. Capture permissions are requested explicitly. Camera/recorder passwords and RTSP URLs are not requested or stored.
- API authentication, CSRF, origin restrictions, branch isolation and idempotency remain in force. CSP allows only same-origin workers/model assets and WebAssembly compilation; external connections remain blocked by the application policy.

## Build, assets and licences

`npm ci --prefix apps/web` installs the pinned runtime. `npm run build` runs `scripts/prepare-vision.mjs`, copies its local WASM assets and downloads the model only if a matching local file is absent. The build verifies version, byte count and SHA-256 before accepting the model. It then places the assets in the web build so runtime inference needs no CDN or internet connection.

Model SHA-256: `59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a` (5,777,746 bytes). Generated assets are ignored by git and included in the compiled web application. Attribution and the Apache 2.0 licence are shipped in `THIRD-PARTY-NOTICES.txt`. No code or models from the four user-supplied repositories were copied into this detector.

Browser-support, sustained performance, camera angles and physical audio still need acceptance on both pharmacy laptops. Source installation is updated by this change; historical downloadable desktop bundles predate it until rebuilt.

## API slice

| Route | Behaviour |
|---|---|
| `GET /api/live-events` | Latest 100 authorised branch metadata events |
| `POST /api/live-events` | Strict, idempotent observation intake with pinned source/rule/model fields |
| `POST /api/live-events/{id}/acknowledge` | Branch-authorised, idempotent staff acknowledgement |

These are client-reported detector observations. Valid schema/provenance fields do not cryptographically attest that a browser actually executed the model. Production device attestation and operational monitoring remain separate work.

## Validation boundary

The local test suite includes real model execution on decoded video with no people, deterministic synthetic pose sequences for event/audio/log integration, source cleanup, cancelled startup, branch isolation, duplicates, invalid payloads and mobile/accessibility checks. A separate browser smoke test ran actual inference on Google's public pose-test image and produced one pose with 33 landmarks. The image was temporary, was not pharmacy footage, and is not part of the shipped application. These checks establish functioning software paths, not theft accuracy or verified physical loudness.
