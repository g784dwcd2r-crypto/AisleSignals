# Camera context in observations and cases

Requirements: FR-002, FR-013, FR-015, FR-018 and FR-020. Multi-camera samples can record their position in the browser's confirmed CCTV layout. This metadata is a browser declaration, not proof of the physical camera's identity or permission to access a pharmacy.

## Request contract

`POST /api/interactions/jobs` and `POST /api/live-events` accept optional `camera_context`:

```json
{
  "source_id": "12345678-1234-4234-8234-1234567890ab",
  "epoch": 3,
  "layout": "2x2",
  "camera_index": 2,
  "source_width": 1920,
  "source_height": 1080,
  "crop": { "x": 0, "y": 0.5, "width": 0.5, "height": 0.5 }
}
```

- `source_id` is a canonical lower-case UUID for the acquired browser source. No camera URL, password, local filename or blob URL belongs here.
- `epoch` is an integer from 1 through 2147483647 identifying a browser context generation. A source, crop, layout or resolution replacement requires a new context and explicit restart.
- Layouts are `2x2` (four positions), `3x2` or `2x3` (six positions), with a zero-based `camera_index` inside that layout.
- Source dimensions are integers from 48 through 16384 pixels. Crop coordinates are finite normalized numbers within the reported source; width and height are at least 5%, and the rounded crop must contain at least 48 source pixels per edge.
- Extra keys, coerced numeric strings, booleans and malformed identifiers are rejected. `organisation_id`, `site_id`, a derived label or a registered camera identifier cannot be supplied through this object.

The actual crop is retained, including measured gutters or margins. It is not reconstructed from the grid's nominal fractions. Interaction JPEGs must match the existing browser capture transform: floor the crop origin, round crop dimensions with JavaScript half-up ties, clamp to the source, then resize to at most 768 pixels on the longest edge without upscaling. Every submitted frame must match the resulting dimensions. Mismatches fail before an inference job or evidence directory is created.

## Persistence and authority

The API derives `camera_id` as `source_id:epoch:layout:camera_index` and a display label such as `Camera 3 · 2x2`. Completed observations, their historical list entries, reviewed case provenance, linked evidence responses and case JSON exports retain the submitted context. This identifier is local to a source generation; it does not link people across cameras or identify a registered physical device.

The authenticated session and selected authorized branch still determine every write, read, evidence download and export. Existing CSRF, runtime context, retention and review requirements remain in force. A client cannot grant itself access by supplying metadata for another branch.

Retries with a changed context conflict rather than relabel the first stored event. Removing an existing context also conflicts, even with a new idempotency key. Omitted and explicit null context preserve the legacy single-camera payload shape and retry hashes. Old records display without invented camera geometry.

Camera context does not extend evidence retention. A reviewed case continues to keep metadata and image hashes after its original sampled JPEGs expire or are deleted. The existing 24-hour derivative retention policy is unchanged; these samples are not continuous recordings or pre/post incident clips.

## Limits and verification

The server can check schema, pixel dimensions, ownership and persisted context. It cannot determine from a browser declaration whether the user selected the correct CCTV tile or replaced a view without reporting it. Browser run invalidation and actual source commissioning remain necessary. A matching context alone never authorizes an alarm.

`tests/api/test_camera_context.py` exercises the real protected API, transactions, encryption and evidence access with synthetic images and a fixture model provider. `apps/web/src/cameraContext.test.ts` verifies strict browser validation, measured pixel geometry, deep snapshots and invalidation keys. These checks do not measure model accuracy, prove all-camera scheduling coverage or qualify any real pharmacy installation. Native device/CCTV/speaker acceptance remains separately required.
