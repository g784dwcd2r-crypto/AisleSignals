# AisleSignals attention alarm and playback log

Prepared for Jawahir Q. This implements a recording-test version of the requested activity → attention alarm → automatic category → saved event flow. It does not implement a trained suspicious-interaction detector or a live CCTV connection.

## Try the flow

1. Open the local pharmacy workspace and select **Video test**.
2. Choose an authorised MP4/WebM recording and select **Analyse video**. Analysis alone is silent and does not save an alarm event.
3. Set the alarm volume and use **Test speaker · 2 seconds** to check the laptop output. The application cannot detect whether the operating system or speakers are muted.
4. Select **Start alarm playback**. The recording replays at normal speed. A qualifying visual-change segment triggers a prominent static attention notice, an optional repeating tone and a saved test-log entry.
5. Use **Silence current tone**, **Mute alarm sound** or **Stop alarm test** as needed. An individual tone lasts at most eight seconds. Pause, seek, tab hiding and leaving the page stop sound; hidden tabs disarm playback.
6. Review **Saved playback test events**. Events include recording offsets, a rules category and sound-request status. They remain in the branch's local database after navigation, sign-out or restart. A new explicit playback run can log another test of the same interval.

The file stays in the browser. Only numeric recording offsets, an opaque run identifier, category and sound-request status reach the local server. No filename, footage, identity or inference about wrongdoing is sent. The server adds branch scope, time, provenance and staff account attribution. Latest 100 records are shown; automated log expiry is not implemented. [API details](playback-alerts-api.md).

If saving fails, playback stops and the interface shows the event as unconfirmed. Retrying preserves the request key and event identity. File replacement keeps pending metadata available; leaving the page loses unsaved metadata, so retry before navigating. No success is claimed until the server acknowledges the write. Local JSON analysis summaries remain separate from this saved event log.

## What is automatically classified

| Category | Rule | Meaning |
| --- | --- | --- |
| Sustained visual activity | Confirmed consecutive frame changes | Movement or another visible image change needs review |
| Extended visual activity | Confirmed interval spans at least five seconds | A longer interval of visual change |
| Large scene change | Peak changed-pixel ratio at least 65% | Possible lighting change, camera movement or scene cut |

Large scene change takes precedence over duration. These thresholds are explicit prototype rules, not trained AI classes, accuracy estimates or probabilities of theft. Concealment, shelf removal/return, aggression and person recognition are not automatically classified. The completed file scan is replayed for this test; results must not be presented as a real-time detector's latency or performance.

Events can trigger once per segment per armed run during continuous foreground playback at 1×. Backward movement, seeks, unexpected playback speed, long callback gaps and paused playback do not create catch-up alarms. Skipping an interval means it may have no playback event even though it remains in the analysis timeline. No activity result establishes safety or absence of an incident.

## Attention sound

The Web Audio tone alternates 740 and 980 Hz with short gaps, using the laptop's current output. The UI starts at 65% of the application's bounded volume scale; this is not a calibrated sound-pressure level. It never adjusts system volume, contacts emergency services or activates external equipment. The visual attention notice does not flash.

Audio activation begins with an explicit button click because browsers restrict automatic sound. A successful Web Audio request does not prove a person heard it. The log distinguishes sound requested, muted and blocked. [MDN Web Audio guidance](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Best_practices).

## Real pharmacy reference from YouTube

The reference card links to and can load the FBI's **Seeking Information in Pharmaceutical Robberies in Maryland and Pennsylvania** video, ID `TkS5CyFuumI`. The [FBI publisher page](https://www.fbi.gov/video-repository/seeking-information-in-pharmaceutical-robberies-070920.mp4/view) embeds the same [YouTube video](https://www.youtube.com/watch?v=TkS5CyFuumI) and provides a **Download Video File** link. It describes actual robbery footage, not a staged demo.

The YouTube player loads only after a click, using `youtube-nocookie.com`; until then no player request is made. A direct YouTube link remains available if embedding is blocked. The iframe uses `strict-origin-when-cross-origin` for player compatibility; other application requests keep the existing no-referrer policy.

The reference player is not analysed and does not trigger alarms or logs. Browser same-origin rules prevent extracting its video frames through the iframe. The application does not rip YouTube, claim a redistribution licence, or include the publisher's footage in Git or desktop bundles. The publisher's offered direct MP4 download returned HTTP 403 from this environment on 13 September 2026, so the real video has not been downloaded or tested by the analyser. An authorised local copy is still needed for that test. [YouTube player API](https://developers.google.com/youtube/iframe_api_reference), [same-origin policy](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy).

## Verification boundary

Automated tests use the repository's original geometric WebM, real browser decoding/playback, and an instrumented AudioContext to avoid generating noise in CI. API tests check branch scope, strict inputs, restart persistence, retries and concurrent duplicates. These checks do not certify physical audibility, a real pharmacy video, theft-detection accuracy or a pharmacy installation.
