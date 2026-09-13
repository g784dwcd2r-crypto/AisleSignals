# Local video-file test

Implemented in the prototype after the automatic CCTV monitoring specification. This is a browser-local playback and visual-activity test, separate from the synthetic pharmacy records. It is not a live camera adapter, trained AI detector or evidence-import service.

## Use it

1. Sign in to the pharmacy workspace and select **Video test** in the navigation.
2. Choose or drop one MP4 or WebM file you are authorised to use. Use a short synthetic or appropriately authorised test recording. The file must be at most 250 MiB, no longer than 10 minutes, at most 3840 × 2160 total pixels and no more than 4096 pixels on either side. MP4 with H.264 or WebM is a useful starting point; the browser must support the actual codec. An extension alone does not prove decodability.
3. Preview the recording. Playback starts muted; audio is not analysed. The screen is explicitly labelled **Recorded playback test**.
4. Select **Analyse video**. The local scan samples frames at half-second intervals and groups sustained visual changes into timestamped segments.
5. Select a timeline timestamp to seek the preview. Inspect what actually happened. Ordinary movement, camera movement and lighting changes can all produce a result.
6. Optionally select **Download test summary** for a JSON record of the analysis method, sampling coverage and activity intervals. This is a test summary, not an evidence export or theft report.
7. Remove the file or leave the page to clear the in-memory video and results. Signing out or session expiry also removes the component and releases its media resources. Downloaded summaries remain on the device.

An original synthetic six-second WebM is provided at `tests/fixtures/synthetic-video.webm`; its provenance and regeneration instructions are in `tests/fixtures/README.md`. It contains geometric shapes only, with no people or pharmacy recordings.

## What the scan measures

The scanner downsamples decoded video to a small canvas and compares frame luminance. Pixels changing by at least 24 levels out of 255 contribute to a changed-pixel ratio. Two consecutive samples with at least 3% changed pixels start an activity segment. Two seconds without activity close a segment. The interface and JSON call this frame change, never confidence in theft.

There are at most 1200 sampled frames and 100 displayed segments. The UI shows the sampled-through time; sampling does not inspect every video frame or necessarily the final fractional second. A result-limit notice appears at 100 segments. Brief, subtle or obstructed events can be missed. No detected activity does not establish safety or absence of an incident. This method cannot identify products, concealment, aggression, people or returning visitors.

## Local data and lifecycle

The browser creates an object URL for the selected file and decodes it locally. There is no video upload endpoint, third-party model call, filename logging, browser-storage persistence or automatic case creation. The existing authenticated bootstrap polling can continue independently; its requests do not include the file, pixels, filename or test results. Files do not become accessible to another pharmacy through the API because they never enter server storage.

Object URLs are temporary references that the browser must release; [MDN documents creating and revoking them](https://developer.mozilla.org/en-US/docs/Web/API/URL/createObjectURL_static). The component pauses video and removes media sources before revocation, aborts the decoder on removal/navigation, and rejects stale async work using a generation guard. Preview decoding is separate from the scan decoder, so scrubbing the preview does not change the scan sequence.

Leaving the tab in the background cancels a running scan. Cancellation does not publish a partial result as complete; a fresh run starts from the beginning. File metadata has a 12-second wait limit, each scan load/seek has an 8-second limit, and the complete scan has a two-minute processing deadline. Unsupported or corrupt media produces a recoverable error. These guards bound ordinary test work; browser/OS codec support and actual laptop resource use still vary.

The CSP permits only same-origin and local blob media; it does not permit arbitrary remote video sources. The API contract and simulated source status are unchanged. No live staff sound or physical alarm is triggered by a recording, and no simulation is promoted to a live detector.

## Verification scope

The pure helper tests cover file/metadata limits, noise, frame differences, temporal continuity, segment grouping and result caps. Browser journeys use real decoding/seeking of the original synthetic WebM to check timeline output, preview seeking, corrupted-file recovery, cancellation, cleanup, no uploads or live-record writes, local summary download, narrow layout and accessibility. Async cancellation boundaries are deliberately held in isolated test contexts; that instrumentation is not application code.

These tests establish the implemented visual-activity workflow. They do not establish AI theft-detection accuracy, support for every CCTV export codec, or acceptance of a real pharmacy monitoring installation.
