# Recorded playback test event log

This local prototype API stores recording-test metadata in the signed-in pharmacy branch. It does not receive footage, recognise people, establish theft, create a live incident or actuate an alarm. The browser reports its local attention-sound request state; the server cannot verify that a speaker was audible.

## Create or retry an event

`POST /api/playback-events` requires the existing authenticated session, local Origin, CSRF header and a valid `Idempotency-Key`. Manager and reviewer demo accounts may record tests for their own branch.

```json
{
  "run_id": "example-recording-test-001",
  "event_index": 0,
  "category": "SUSTAINED_VISUAL_ACTIVITY",
  "video_start_seconds": 1.5,
  "video_end_seconds": 4,
  "peak_changed_ratio": 0.25,
  "alarm_status": "MUTED"
}
```

| Field | Accepted values |
| --- | --- |
| `run_id` | 1–100 ASCII letters, numbers, underscore or hyphen; use an opaque generated identifier |
| `event_index` | Strict integer 0–99; at most 100 distinct events per actor, branch and run |
| `category` | `SUSTAINED_VISUAL_ACTIVITY`, `EXTENDED_VISUAL_ACTIVITY`, `LARGE_SCENE_CHANGE` |
| `video_start_seconds`, `video_end_seconds` | Finite JSON numbers, 0–600, end at or after start |
| `peak_changed_ratio` | Finite JSON number, 0–1; a changed-frame proportion, not a probability of wrongdoing |
| `alarm_status` | `SOUND_REQUESTED`, `MUTED`, `BLOCKED`; none confirms speaker delivery |

Unknown fields are rejected. Numeric strings, booleans in numeric fields, NaN and infinity are rejected. No filename, URL, media, notes, person identity, tenant authority, server timestamp or model provenance may be supplied. The existing 65,536-byte body cap applies.

Category, interval, changed-frame proportion and alarm status are reported by the browser test. The server validates their format and limits, but does not inspect the video or independently establish that the submitted category is correct. The fixed provenance identifies the supported test contract, not a verified theft detector.

Success returns HTTP 200 and the submitted metadata plus server fields:

```json
{
  "id": "playback-<opaque deterministic hash>",
  "source": "RECORDED_PLAYBACK_TEST",
  "provenance": "RULE_BASED_VISUAL_CHANGE_V1",
  "created_at": "<server UTC timestamp>",
  "recorded_at": "<same server UTC timestamp>",
  "recorded_by": "<signed-in staff display name>"
}
```

Both exact retries with the same HTTP key and exact retries of the same actor/branch/run/index with another key return the original record. Changing the payload conflicts with HTTP 409 (`IDEMPOTENCY_CONFLICT` or `PLAYBACK_EVENT_CONFLICT`). Insertion, audit and retry results share the existing immediate SQLite transaction, including concurrent requests. Branch authority is part of the retry hash, so changing a staff membership cannot reveal an earlier branch's cached response.

`SOUND_REQUESTED` means only that the browser requested local audio. Browser policy, a muted operating system, unplugged speakers or device failure may prevent audible output. `MUTED` and `BLOCKED` are stored as reported. The record is immutable; replaying a test uses a new `run_id`.

## Read recent tests

`GET /api/playback-events` returns a plain array of the latest 100 records in the authenticated organisation and branch, newest first. No query parameter changes authority. There is no public single-event or cross-branch lookup. Staff within the same branch can see its recording tests; the deduplication identity remains per actor.

Metadata survives application restart in the local SQLite database. Each new event also produces `PLAYBACK_TEST_EVENT_LOGGED` in the branch audit. Retries add no duplicate event or audit. Candidates, incidents, assistance requests and their reviewed status are unchanged. The listing cap is not a retention policy: automatic playback-log expiry and deletion are not implemented in this prototype.

## Reference player boundary

The application Content Security Policy permits frame navigation only to `https://www.youtube-nocookie.com`. The interface must request that player only after the user chooses to load the external reference. The iframe may set `referrerPolicy="strict-origin-when-cross-origin"` for YouTube player compatibility; the rest of the application retains the existing `no-referrer` response policy. An embedded external player is a reference view, not a source available to the local frame scanner.

## Verification

Run `.venv/bin/python -m pytest tests/api/test_playback_log.py` from the repository. Tests exercise persistence, server provenance, branch and actor isolation, membership changes, concurrent retry deduplication, changed-payload conflicts, strict malformed inputs, bounded listing, per-run event limits, existing request limits and the narrow reference-player CSP change.
