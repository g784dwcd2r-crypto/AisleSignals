# Local custody observations

## Purpose and boundary

This module supplies deterministic contracts for the observable layer beneath a future product-custody engine. It does not detect theft, identify a person, infer intent, calculate suspicion, determine payment or trigger an alarm. Its output types deliberately contain no alarm field.

The implementation lives in `apps/web/src/custodyObservations.ts`. It can consume future person, product, bag and basket detector outputs after those models are locally commissioned. Current pose tracking alone cannot supply all of these inputs.

## Observable contracts

### Source quality

`evaluateObservationQuality` rejects invalid geometry, source frames below 640 × 360, shelf regions below 96 pixels on either edge, people below 120 pixels high or 40 pixels wide, products below 6 × 6 and 64 total pixels, and hand landmarks below 0.65 visibility. These are conservative engineering defaults rather than measured accuracy thresholds. A rejected sample produces an explicit quality code.

For a four/six-camera recorder mosaic, these checks run after the selected tile has been cropped. They prevent full-mosaic resolution from disguising a low-resolution individual camera.

### Body, pocket and bag geometry

`deriveBodyInteractionRegions` requires four visible shoulder/hip landmarks and a plausible torso. It produces torso, waist and image-left/image-right pocket-proximity regions. These regions are geometric areas only. They do not show that clothing contains a pocket or that an item entered clothing.

`observeBagRegion` requires an explicit `BAG` object detection of sufficient confidence near the person. Pose geometry cannot invent a bag. The result reports only overlap or adjacency.

### Hand and product proximity

`associateHandWithProduct` accepts only a confident object explicitly classified as `PRODUCT`, a visible hand and a plausible distance relative to person size. Phone, wallet, bag and unknown objects cannot produce a product association. A returned relation means contact or proximity, never possession or ownership.

### Shelf change

`observeShelfChange` compares bounded luminance frames inside a configured shelf ROI. It subtracts uniform luminance drift, rejects a uniform lighting step, searches small whole-frame translations to reject camera movement, and requires a localized shelf change with low outside-ROI activity. It reports unresolved scene activity rather than forcing an object-removal result.

This algorithm does not yet compensate for perspective, large camera motion, rolling exposure, reflections, customer occlusion or shelf restocking across long intervals. It must be evaluated per camera.

### Hard negative observations

`classifyLocalObservation` applies normal explanations before product proximity observations:

- Phone and wallet handling remain `PERSONAL_ITEM_HANDLING`.
- Hand-to-pocket movement without an associated product remains `CLOTHING_ADJUSTMENT_WITHOUT_PRODUCT`.
- Bag handling without an associated product remains `BAG_HANDLING_WITHOUT_PRODUCT`.
- An authenticated/commissioned staff context plus repeated associated product placements is recorded as `CONFIRMED_STAFF_BULK_SHELF_PLACEMENT`.
- Return and basket placement are explicit normal product transitions.
- Product proximity to a pocket or bag stays an observation; it is never called concealment.

The `confirmedStaff` input must come from an authorised local operating context, not facial recognition, clothing appearance or the vision model guessing someone's job.

Every accepted observation includes bounded evidence references plus feature kind, detector source and version. Required feature sources are fixed by contract: a person tracker cannot claim an object detection, shelf change or authenticated staff state. Mutually exclusive product locations (pocket, bag, shelf return and basket) are rejected instead of being resolved by priority. These contracts expose false integration claims; they do not make the synthetic inputs independent until the named detectors are implemented.

### Temporal episodes

`CustodyObservationLedger` groups at most 32 chronological facts for the same camera, capture session, ephemeral person track and stable non-null product token when gaps are no more than three seconds. Different products or capture sessions never share an episode. Each observation carries a source timestamp, normalized coordinator timestamp, estimated clock skew and bounded skew uncertainty; inconsistent, fractional or unsafe timestamps are rejected. Ambiguous, recovered or switched person tracks break continuity. A larger gap starts a new episode. Exact camera/source discontinuity deletes continuity, preventing events on similarly named cameras, different visits or after a reconnect from being joined. Accepted strings are bounded and stored arrays are copied and frozen. The in-memory ledger expires old episodes and enforces per-camera and global episode caps. Episodes remain observational and contain no risk score or sound instruction.

## Integration sequence

1. Run a dedicated person detector and tracker before pose, then apply the existing person-geometry gate.
2. Run locally trained or qualified product, phone, wallet, bag and basket detectors on the single-camera crop.
3. Apply the source-quality contract before producing any product event.
4. Calculate body regions, bag regions, hand/product proximity and shelf change independently.
5. Normalize all detector timestamps to the capture coordinator clock and reject observations outside the commissioned skew uncertainty.
6. Attach the detector/model version and an immutable local evidence reference for every claimed feature. Supply staff state only from authenticated local operating context.
7. Convert those facts through `classifyLocalObservation` and feed only the resulting observational event into `CustodyObservationLedger`.
8. Persist the episode with model versions, thresholds, camera calibration version and evidence references.
9. A later reviewed policy may consume episodes. It must remain separately tested and cannot treat a single proximity observation as concealment or theft.

## What still needs footage and training

The contracts and deterministic failure handling are implemented and tested with synthetic arrays. They do not provide the missing object detections. Before operational use, AisleSignals still needs:

- Labelled pharmacy footage for person boxes, visible hands, shelf cells, products, phones, wallets, bags and baskets.
- Frame-by-frame transition labels for take, return, basket placement, ordinary bag use, clothing adjustment and restocking.
- Occlusion and visibility labels, including crowded aisles, small products, reflections, low light and camera compression.
- Camera/day/person-separated training, validation and held-out test sets.
- Calibration of source-size, confidence, association-distance, shelf-change and episode-gap thresholds per supported camera class.
- Measured recall, precision, abstention, false events per camera-hour and event latency, with confidence intervals.
- Explicit hard-negative acceptance for phone use, wallet use, clothing adjustment, ordinary bag handling and staff restocking.
- On-device CPU/memory/thermal measurements for four and six simultaneous camera tiles on the real Mac and Windows laptops.
- A worker-isolated/downsampled shelf differencer. The current baseline is deliberately not connected to the live pipeline: it can still be costly on 4K frames and does not reject local shadows, auto-exposure gradients, reflections, zoom, rotation or people occluding the shelf.

Until those inputs exist, the module is an implemented observation contract and deterministic baseline, not a trained product-custody detector.

## Verification

Run:

```sh
npm --prefix apps/web test -- --run src/custodyObservations.test.ts
npm --prefix apps/web run typecheck
npm --prefix apps/web run format:check
```

The focused suite covers source visibility, body geometry, explicit bag detection, product-only hand association, localized shelf changes, lighting/camera-motion rejection, phone, wallet, clothing, bag and staff-restocking negatives, temporal continuity and absence of alarm authority.
