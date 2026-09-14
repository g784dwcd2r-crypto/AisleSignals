# Local observation metadata contract

Implemented component: `services/api/cloud_observation.py`. This is a pure projection and eligibility module for the proposed sync path in [cloud-sync-design.md](cloud-sync-design.md). It does not add pairing, entity hooks, an outbox, HTTP delivery, cloud retention or withdrawal. Its tests use only synthetic in-memory stored-shape fixtures. Passing them does not establish physical-camera compatibility, detector accuracy or end-to-end sync readiness.

## Inputs and authority boundary

`map_observation(database_mode=..., scope=..., binding=..., admission=..., entity_kind=..., item=..., now=...)` returns either a frozen `MappedObservation` or one bounded `Exclusion` enum member. No error includes entity content. `now` must be an explicitly supplied aware datetime; the module reads no clock, file, database, media or network.

The caller supplies these trusted server-owned objects:

- `ObservationScope(installation_id, organisation_id, site_id)` from installation state and the SQL entity row scope. A request's tenant fields or the selected browser branch are not authority.
- `BindingSnapshot(installation_id, binding_id, generation, organisation_id, site_id, activated_at, enabled=True, pose_event_codes=frozenset())`. It is immutable. `activated_at` is the admission boundary for this generation, including a revision after pause/resume or policy change. A new target requires a new binding UUID; it must not edit the target behind an old ID.
- `AdmissionSnapshot(binding_id, generation)` captured at the original local API admission. For a product job it is captured before inference starts and persisted locally with the job. An absent snapshot cannot be reconstructed from the current binding when inference finishes. For pose events it is captured in the original scoped admission transaction.
- `database_mode` from the validated Store provenance, never from the browser. Only exactly `pilot` is eligible; `synthetic` and unknown modes exclude.
- `item` is the persisted local entity, not a browser body or flattened history DTO. `entity_kind` is exactly `interaction` or `live_event`.

Current `source_kind` values are **browser declarations**. The module excludes declared `RECORDED_VIDEO`, but cannot verify that `SCREEN_CAPTURE` or `CAMERA` is physically live. A screen capture may contain playback. Generated camera labels describe screen positions, not authenticated physical camera identities. Existing session, current-branch, cancellation, source-continuity and runtime-generation gates must still pass at the actual publication hook. This mapper does not replace them or establish that a speaker sounded.

Binding/scope/policy must be rechecked in the enqueue transaction. A sender must recheck source existence, source expiry and current binding authority before transmission. This pure result is not an authorisation token and does not resolve a deletion or disconnect racing an already transmitted request.

## Actual stored-shape mapping

| Persisted input | Eligibility |
| --- | --- |
| `interaction` UUID; matching embedded `organisation_id` and `site_id`; `status == "completed"`; nested `result.action == "POSSIBLE_CONCEALMENT"`; nested `result.alarm_eligible is True` | Eligible by default under an enabled binding. The flag is the existing server-derived experimental routing result. Truthy strings or integers do not qualify. |
| `live_event` ID `live-` followed by 64 lower-case hex characters; top-level `event_code` | Only `REPEATED_HAND_TO_WAIST` and `RESTRICTED_ZONE_ENTRY`, each requiring explicit inclusion in the binding's frozen `pose_event_codes`. Both default off. Live bodies currently omit organisation/site, so the trusted SQL row scope is mandatory. Any embedded scope, if present, must also match. |
| Product pickup, return, basket placement, normal shopping, unclear or unknown class | Excluded, even with a contradictory true alarm flag. This first version offers no pickup/return opt-in. |
| Pending/running/failed/cancelled interaction, malformed result or unsupported kind/source | Excluded. A successful-looking stale result cannot bypass job status. |
| Declared recording, explicit historical marker on an input entity, non-pilot database | Excluded from this feed. Outbound `historical: true` has a different meaning: every accepted management observation is intentionally historical. |
| Missing admission, changed binding/generation, or `created_at < activated_at` | Excluded. A pre-pair or earlier-generation job cannot become a new export on late completion. |

Do not call the mapper again against a newer binding to rewrite an already queued observation. A valid previously committed queue row retains its payload and UUID through pause/resume; outbox generation checks fence workers and new publication independently.

## Immutable output and retry identity

`MappedObservation` has `entity_kind`, `entity_id`, `payload`, `admitted_at`, and `deadline`. Only its `payload` is the observation HTTP body; the other values are local lifecycle metadata. The dataclass is frozen and the payload is a copied `MappingProxyType` containing only scalar immutable values.

The exact five outbound fields are:

```json
{
  "source_event_id": "a generated canonical UUIDv5",
  "event_code": "POSSIBLE_CONCEALMENT",
  "source_label": "Camera 2 of 6 · 3x2 screen grid · local observation",
  "occurred_at": "2026-09-14T12:00:00Z",
  "historical": true
}
```

`source_event_id(...)` is shared with the outbox. Its keyword arguments are `installation_id`, `binding_id`, `organisation_id`, `site_id`, `entity_kind`, `entity_id`. Installation and binding IDs must be nonzero canonical lower-case UUIDs; local scope IDs are bounded ASCII identifiers. Local entity IDs must match the actual stored shapes above.

The UUIDv5 namespace is **`b00af7d7-4931-5a90-a014-8c3deca79524`**. The name is UTF-8 encoding of the following ordered JSON array, encoded with `ensure_ascii=True` and `separators=(",", ":")`:

```text
["aislesignals-observation-v1", installation_id, binding_id, organisation_id, site_id, entity_kind, entity_id]
```

Do not include mutable generation, delivery time, retry count, camera layout, current selection or names in this identity. The outbox independently recomputes it using trusted scope/binding and the stored entity reference. A changed payload under this identity must produce a conflict rather than a new UUID.

`validate_payload(payload)` copies and freezes only the exact five-field shape, known event codes, generated labels, canonical UTC timestamp and exact boolean `True`. `canonical_payload(payload)` returns UTF-8 JSON bytes with sorted keys, compact separators, `ensure_ascii=False` and no non-finite numbers. The maximum is 2 KiB. Persist these bytes/hash once; an actual retry reuses the unchanged payload. Neither helper checks current source availability or network admission time bounds.

## Source labels and privacy

Labels are chosen only from these constants and validated screen positions:

- `Camera source · local observation` for a declared camera without mosaic context.
- `Screen area · local observation` for a declared screen capture without mosaic context.
- `Camera N of M · L screen grid · local observation`, where `L` is `2x2`, `3x2` or `2x3`; `M` is 4 or 6 and `N` is within that layout.

An existing non-null `camera_context` must pass the shared `CameraContext` validator, including canonical source UUID, epoch, numeric source dimensions, index, normalized crop bounds and at least 48 source pixels per cropped edge. Invalid context excludes; it never falls back to a misleading generic label. Full geometry, source UUID and epoch remain local and are not outgoing fields.

The projection never copies the stored `source_label`, filename/window/tab title, source URL, camera credential, staff/customer name, actor/session identifier, track/person ID, model narrative, review/case notes, product or patient details, JPEG/audio/media, evidence hash/path/link, or source-relative sample time. Operational IDs and timestamps are minimised metadata; this is not a promise of anonymity.

## Timestamp and deadline

For both entity kinds, `occurred_at` is the persisted **local API admission timestamp `created_at`** normalized to the same instant in canonical UTC. It is not model completion, upload time, browser `detected_at`, or reconstructed frame-capture time. The management UI must explain it as “Reported observation time — laptop clock.” The module never substitutes `now` for an invalid or inconvenient timestamp.

Dates require ISO seconds with an explicit timezone. Admission outside the cloud protocol window (more than 30 days old or over five minutes ahead of the supplied clock), malformed dates, impossible expiry, an invalid caller clock or a future binding boundary beyond the same tolerance yield `CLOCK_INVALID`.

The delivery deadline is the earlier of `created_at + 24 hours` and product `expires_at`. Pose records have no existing source expiry field, so receive the 24-hour admission deadline. Equality with the deadline is expired. Restart, receipt, retry, pairing or source changes cannot extend it. Cloud retention and withdrawal remain independent completion requirements; a local deadline alone cannot remove a delivered record.

## Verification

Run the focused pure tests from the repository root:

```sh
python -m pytest tests/api/test_cloud_observation.py -q
```

Coverage includes exact forbidden-field non-leakage, immutable payloads and canonical retries; scope/binding/generation and pre-pair jobs; successful product eligibility versus ordinary actions; default-off per-code pose policy; declared recording and synthetic-mode exclusion; all 16 camera positions across the three layouts and invalid geometry; UUID identity across installation/binding/scope; local versus browser timestamp provenance; invalid clocks, timezone normalization and exact expiry boundaries. No fixture is customer data, and no test contacts a service or runs an alarm.
