# Attended launcher health and recovery

Implemented in the production-readiness worktree from `789e5d6`. This is local service supervision, not acceptance of continuous CCTV monitoring or model accuracy. It adds no hardware, cloud service, image uploads or periodic model inference.

## Behaviour

- The launcher probes only processes it started, on their configured numeric loopback ports. An occupied port prevents starting or recovering a service; no existing process is adopted, signalled or killed by port lookup.
- API `/api/health` must return the protected `pilot` mode and `status: ok`. Model `/v1/models` must list the pinned `qwen3-vl:4b` alias. The model probe uses its existing private token. Tokens, command lines, user names, paths and footage are absent from the runtime report.
- Checks run sequentially, without a work queue or detached threads. Each check has a one-second absolute deadline covering connect, headers and body. Slow trickle responses cannot extend it. Stop requests cancel a pending socket operation, checked every 50 milliseconds. Responses are limited to 8 KiB of headers and 32 KiB of JSON; proxy use, redirects, compression and transfer encodings are refused.
- There is a two-second wait between rounds. One or two consecutive failed probes mark service health `SUSPECT` and overall state `DEGRADED`. A successful probe resets the streak. Three consecutive failed probes, or an exited owned process, start recovery. With two endpoints the nominal worst round is four seconds; these are bounded service-check timings, not an inference-latency or OS scheduling guarantee.
- Recovery uses the existing installation-wide lifetime restart allowance for each failed service: default two, configurable from zero to three, with two/four/eight-second backoff. A later failure does not reset this allowance. Readiness after each restart is still bounded by its configured startup timeout.
- An API fault aborts its owned process immediately instead of allowing a graceful HTTP response drain. A model fault first aborts its API consumer, then restarts the model. This prevents the old API process from subsequently publishing a model result during recovery. The consumer restarts once after the provider's bounded attempts, including when the provider is permanently disabled; those dependency restarts are counted separately and are bounded by the provider's failure episodes.
- Every supervised API child revokes all prior session capabilities before opening its HTTP listener. Accounts, cases and audit records remain. Existing API startup recovery handles interrupted jobs. A prior browser session must sign in again, reselect/check its source, explicitly enable analysis and commission/arm sound. A recovered service is reported `REARM_REQUIRED`, never as verified camera coverage.
- If model recovery fails, its consumer restarts with the immutable provider-disabled interlock, so a later unrelated listener on that model port cannot receive frames. Casework remains available if the API starts. An exhausted API recovery fails the launcher. Sleep, a clock discontinuity or a long scheduler pause stops the launcher and requires reopening it; this interlock remains separate from the recovery rearm latch.
- Ordinary operator shutdown retains graceful owned-process cleanup, with forced termination after its existing deadline. A cancellation cannot start a new recovery attempt.

## Runtime report and browser integration contract

The private `runtime-status.json` retains schema version 1 and adds:

| Field                                                            | Meaning                                                                                                                           |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `runtime_id`                                                     | Random identifier for this launcher lifetime; not an authentication capability                                                    |
| `recovery_generation`                                            | Increments before handling each confirmed runtime failure                                                                         |
| `generated_at`                                                   | Report timestamp; an old file is not a current health signal                                                                      |
| `rearm_required`                                                 | Latched after recovery or interruption for the rest of this launch; it is not acknowledgement that an operator completed rearming |
| `services[].health`                                              | `NOT_CHECKED`, `READY`, `SUSPECT`, `UNHEALTHY`, `EXITED` or `STOPPED`                                                             |
| `services[].consecutive_health_failures`                         | Consecutive failed HTTP probes; success clears it                                                                                 |
| `services[].last_checked_at`, `probe_elapsed_ms`, `probe_reason` | Privacy-minimised probe timing and categorical result                                                                             |
| `services[].restarts`, `dependency_restarts`                     | Failure-recovery attempts and consumer restarts caused by the model                                                               |
| `camera_monitoring`                                              | Always `NOT_VERIFIED`                                                                                                             |

**Browser integration is now included in the combined release.** The authenticated `/api/runtime/health` endpoint projects only safe report fields. The browser binds monitoring, job results and sound commissioning to the current opaque runtime context. A failed/stale heartbeat, a degraded or changed generation, offline/hidden state or clock gap stops capture and disarms sound when observed. Recovery restores readiness only; staff must explicitly restart analysis and recommission sound. Server and worker checks independently reject stale live jobs while keeping authorised historical review available.

See [Browser runtime health and recovery](runtime-browser-health.md) for probe deadlines, race handling, API/worker enforcement and acceptance evidence. JavaScript cannot execute cleanup during OS sleep; guards reject old context on resume. Unreachable-server cancellation remains best effort, and catalogue responsiveness does not establish inference progress or physical audibility.

## Verified and still unverified

Focused local verification: **64 tests passed** across `tests/deployment/test_service_health.py` and `tests/deployment/test_pilot_launcher.py`. Tests use ephemeral loopback ports, owned synthetic subprocesses and temporary databases. They reproduce live-process HTTP hangs, one-miss recovery without restart, permanent hangs exhausting the restart budget, API-before-model teardown, cancellation while a socket hangs, slow-header/body deadlines, wrong-model responses, port takeover without adoption, sleep interlock and session revocation preserving accounts/cases. Existing launcher/preflight/private-path checks remain included. No existing preview, client database, model service or private recording is used.

Commands from the isolated worktree:

```sh
../AisleSignals/.venv/bin/python -m pytest tests/deployment/test_service_health.py tests/deployment/test_pilot_launcher.py -q
git diff --check
```

Endpoint responsiveness cannot prove a healthy GPU/inference worker. A model whose catalogue responds while inference is wedged remains an explicit gap; the API's existing absolute inference deadline bounds each request, but this launcher does not observe inference progress or restart a model solely for an inference timeout. Similarly, `/api/health` does not exercise SQLite writes or every API worker. These need separate bounded workload/progress signals, not a false inference-readiness claim from HTTP health.

These tests ran on the development Mac. Windows process containment uses the existing job-object path but this revision still needs its Windows CI run and actual client laptop tests. Full-shift endurance, real camera freshness, sleep/permissions/audio and each pharmacy's operating acceptance remain unverified. A local health report is not a remote on-call service.
