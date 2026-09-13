# Multi-camera scheduling and processing contract

Status: implemented pure scheduler and deterministic unit tests in the production-readiness worktree. **Not connected to the capture interface, inference workers, dashboard, evidence API or alarms yet.** These tests do not establish simultaneous four/six-camera operation or pharmacy detection accuracy. The release lead identifies the integrated source and final verification separately.

This component contributes to the split-screen monitoring workstream in `background-workplan.md`. It schedules work on one, four or six explicitly confirmed, non-overlapping camera areas from one captured source, within one branch. Camera geometry comes from the existing `cameraGridTiles` or `mapCameraGridToArea` helpers. Selecting another source, branch, camera layout, crop or source resolution requires explicit rearming and a new epoch. It does not discover cameras, prove the supplied branch is authorised or create an inference result.

## Integration API

`apps/web/src/multiCameraScheduler.ts` exports:

```ts
const scheduler = new MultiCameraScheduler({
  now: () => performance.now(),
  sampleIntervalMs: 250,
  maxInFlight: 1,
  maxFrameAgeMs: 1000,
  maxResultAgeMs: 1500,
  maxClockGapMs: 2000,
});

const context = scheduler.start({
  organisationId, branchId, sourceId,
  sourceWidth: video.videoWidth,
  sourceHeight: video.videoHeight,
  cameras: confirmedTiles.map(({ id, crop }) => ({ id, crop })),
});

const { tickets, reason } = scheduler.dispatch(context, {
  ...presentedFrame, // sequence, mediaTime (seconds), observedAt (monotonic ms)
  sourceWidth: video.videoWidth,
  sourceHeight: video.videoHeight,
});
```

`start` returns a deeply frozen `CameraRunContext`. It includes the source dimensions, organisation, branch, camera IDs/crops and an internally increasing epoch. Each call is an explicit restart, even when the input IDs are unchanged. A malformed replacement fails without replacing a valid run. Capture callbacks retain this exact context object: reconstructed objects and previous epochs are refused.

`dispatch` returns zero to `maxInFlight` `CameraWorkTicket`s, never queued images. Tickets include `id`, immutable `context`, `camera`, `frame`, `issuedAt`, `deadlineAt` and an `AbortSignal`. Frame snapshots contain only the declared numeric fields; extra caller properties are not retained. Different eligible cameras may use the same fresh presented frame. One camera cannot use the same sequence twice, schedule twice in one time slot, start more often than `sampleIntervalMs`, or have concurrent jobs. Rotation resumes after the last granted camera, so a busy camera cannot monopolise available processing slots.

`settle(ticket, "completed" | "failed" | "cancelled")` acknowledges terminal work. It returns `accepted` with the original ticket only for a completed result whose epoch is current, whose deadline has not elapsed, whose latest presented source frame is fresh, and which was not cancelled. Other returns are `failed` or `rejected` with a reason. Duplicate or forged ticket objects cannot release a real job's slot. The scheduler never receives model output or customer pixels, and `accepted` is **not alarm permission or a statement of detection correctness**.

`cancel(ticket)` requests cancellation of that job. `stop()` cancels the current run. `start` cancels the previous epoch before creating another. Cancellation **does not release capacity**: the adapter must wait for the job to settle, or terminate its owned worker and then call `settle(ticket, "cancelled")`. Deadlines abort jobs on the next scheduler call but retain their slots. Repeated restarts cannot create unlimited workers while cancelled work is still running. A hung worker therefore blocks available capacity safely until the owning adapter enforces its bounded termination timeout. Never acknowledge a timeout while the worker continues executing.

`snapshot()` returns current-epoch coverage plus installation-wide `inFlight`, `awaitingCancellation`, `saturated`, `active`, `stopReason` and `sourceFresh`. `isCurrent(context)` checks epoch continuity only; after any asynchronous publication step, check the ticket deadline and current source freshness too. Server-side session, branch and job authority remain mandatory.

## Clock and source continuity

All timestamps use the same injected monotonic clock; the class creates no timers. Its caller must invoke dispatch/snapshot on a bounded heartbeat and use the existing `watchVideoContinuity` watcher. Non-finite/backward time or a scheduler gap beyond `maxClockGapMs` stops the epoch and cancels work. The coverage window freezes at the last known continuous instant rather than inventing coverage during sleep.

Reported resolution changes, backwards frame/media order, inconsistent repeated sequence metadata, and media-time jumps exceeding the clock-gap tolerance stop the epoch. Future/malformed/stale input frames cannot dispatch work. A frame at or beyond the result deadline cannot start work. A new epoch is required after a discontinuity.

The existing watcher is still necessary for wall-clock sleep detection, track-ended events and browser frame freshness. Repeatedly presenting an upstream frozen CCTV image cannot be detected from presentation timestamps alone. Do not stamp an old captured bitmap with new frame metadata or reuse sequence identifiers after reconnecting. Snapshot a dispatched tile immediately from the exact confirmed source; if capture fails, settle the ticket as failed without submitting inference.

## Coverage is measured processing, not accuracy

The target opportunity for each camera occurs once per `sampleIntervalMs` slot, starting when the epoch is armed. Missed slots are not replayed or backfilled. Late processing starts on a fresh frame. The snapshot reports per camera:

- `expectedSamples`: elapsed target slots, including the current slot.
- `started`, `completed`, `failed`, `cancelled`, `rejected`: work lifecycle counters. Cancellation and rejection overlap; these are not mutually exclusive counts.
- `missedSamples`: closed slots with no work started. Missing input and absent scheduler calls contribute here as well as resource pressure.
- `capacityDeferrals`: distinct eligible slots observed waiting for global or per-camera capacity. Repeated polling does not inflate the count.
- `capacityMissedSamples`: an observed capacity-deferred slot closed before work could start. A slot recovered before closing is not counted as missed.
- `schedulingCoverage` and `completionCoverage`: started or accepted-completed work divided by target slots. Both are labelled `processing_opportunities`; they are not people recall, action recall, uptime or confidence.
- `lastStartedAt`, `lastCompletedAt`, `completionAgeMs`, `pending`: current epoch's timing and outstanding work. Old cancelled epochs occupy global capacity but never appear as current-camera coverage.

A default concurrency limit of one controls resource use. It does not promise each camera can reach the configured target rate. Select operating rates from actual device measurements and visibly report delayed/missed processing. There is no growing frame queue; retained scheduler state is at most six current cameras plus six in-flight tickets.

## Required next integration wave

1. Add an explicit all-camera mode after confirming all grid areas. Create a new context on branch/source/layout/crop/resolution changes and clear every old camera's frame buffers, tracks, reviews-in-flight and alarm commission state.
2. Drive the scheduler using presented-frame callbacks and the continuity watchdog. Own a bounded worker pool, close each bitmap in all success/failure/cancellation paths, and terminate unresponsive owned workers before acknowledging their slots. No silent fallback to another camera.
3. Keep pose tracker and product-frame history **separate per camera**. A MediaPipe VIDEO tracker must not alternate unrelated tiles through one persistent tracking state. Either maintain bounded per-camera state or reset explicitly; measure the resulting cost. For multi-frame product jobs, apply a separate bounded job stage so collecting samples does not falsely count as completed inference.
4. Propagate epoch, branch, source, camera and exact crop through results, review items and evidence. Existing server DTOs do not yet provide this complete multi-camera contract; extend them with server-resolved branch authority and negative tests. Preserve stale-session cancellation before model work and publication. Do not repurpose anonymous person IDs across cameras.
5. Gate alarms with the matching camera's fresh accepted observation, explicit arming, cooldown/duplicate protection and audible commissioning. A ticket settlement cannot directly sound an alarm. Recheck context after awaits. Display which camera requested attention and each camera's measured processing coverage.
6. Exercise four/six-tile browser flows with simultaneous activity, asymmetric workload, cancellation and crop changes, worker hangs, source loss/resize, branch switching, disk/model failures and stale responses. Follow with authorised per-camera footage and physical deployment-device tests. Until then, all-camera operation remains unintegrated and unvalidated.

## Verification

Deterministic Vitest cases exercise one/four/six-camera fairness; concurrency and per-camera exclusion; no catch-up queue or boundary burst; immutable/forged/stale contexts; explicit crop/branch/source changes; cancellation capacity retention; terminal acknowledgement; result expiry and source freshness; clock/media discontinuity; coverage/overload accounting; and bounded configuration rejection. The release lead records final integrated counts; local targeted commands are:

```sh
npm --prefix apps/web test -- --run src/multiCameraScheduler.test.ts src/cameraGrid.test.ts src/monitoringContinuity.test.ts
npm --prefix apps/web run typecheck
```
