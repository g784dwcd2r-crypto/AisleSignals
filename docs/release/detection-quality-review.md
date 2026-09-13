# Detection quality review and improvements

Prepared for Jawahir Q. · 13 September 2026

## What the recordings establish

Three supplied screen recordings (approximately 10, 65 and 68 seconds) show retail videos playing inside a captured browser, with AisleSignals drawing overlays. Several visible people lack usable pose tracks; some skeletons extend onto counters; IDs change rapidly and duplicate/overlap warnings appear. Product analysis is off for much of the footage, then enabled in the third recording. The selected full-window image includes browser chrome, player borders and recommendations, reducing useful camera detail. One shared window changes dimensions and monitoring correctly stops for reconfiguration.

These are recordings of the application and its overlays, not clean, labelled CCTV evaluation clips. They do not establish whether theft took place or measure taking/concealment/return accuracy. Source recordings and extracted frames remain private and are excluded from Git.

## Implemented corrections

| Problem | Changed behaviour |
|---|---|
| Nearby stationary tracks receive new numbers every frame | Uniquely matched display tracks keep their IDs. Ambiguous crossings still discard behavioural history; matching is local to the current run, not cross-visit identity. |
| Duplicate poses and unsupported limbs clutter the picture | Near-identical torso proposals collapse into one whole observation. Low-confidence and anatomically implausible limbs are masked. Display smoothing never supplies coordinates to the behaviour rules or restores an unobserved joint. |
| Selecting one camera only crops the product model | Both body tracking and product sampling now crop the confirmed camera before resizing. Pose overlays map back to exact source pixels; restricted zones apply only where they intersect the selected camera. Changing area stops the pose worker and requires an explicit restart, preserving the selected capture. |
| Browser borders waste available image detail | **Choose camera area** appears beside the monitor. **Draw camera area** selects the actual picture with a pointer; percentage controls remain available. Confirm the box, then start detection. |
| Sensitivity choices behave identically at 4 fps | The sustained-sample requirement now differs at the actual 250 ms cadence. Balanced remains more conservative. The changed rules are logged as `pose-rules-v2`; historical v1 events remain readable. |
| Counter remains at 0/4 while samples are collected | Fresh partial samples display 1/4, 2/4, 3/4, then 4/4. Submission still requires a complete, fresh sequence. |
| Staff cannot see what the model actually received | Live samples and the frozen submitted sequence show the exact JPEGs, timestamps and dimensions. Last analysed interval and automatic countdown expose the sampling gaps. Cancellation, camera changes, expiry and deletion clear the applicable preview. |

The pose count is now labelled as usable body tracks rather than the number of all people present. Sound still requires the existing explicit arming/commissioning controls. These changes do not enable simultaneous analysis of every grid tile.

## Model investigation

The underlying pose family was developed for body landmarks. The [official BlazePose model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf) describes fitness-oriented use and limitations for surveillance, multiple people, distance and occlusion. Modern [Pose Landmarker configuration](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/web_js) supports multiple pose outputs, but that option does not validate crowded pharmacy performance. Full and Lite use the same published input dimensions; switching to Full is not a resolution upgrade.

A separate local experiment compared [EfficientDet-Lite2](https://developers.google.com/edge/mediapipe/solutions/vision/object_detector) with the current pose configuration on three private 548 × 308 camera crops. At an experimental person score threshold of 0.25, it returned 0, 2 and 3 person boxes, while the pose configuration produced one accepted body track in each. The first image still missed all visible people. Lowering the detector cutoff to 0.10 recovered people but added false boxes on bags/fittings and duplicates. No accuracy percentage is warranted from three stills containing existing overlays.

The person detector took approximately 134–136 ms on repeated stills on this Mac. That measurement is neither continuous-video throughput nor Windows acceptance. The candidate has not replaced the production model; lowering a threshold would trade misses for false alarms without resolving the task.

## Next detection milestone

Use clean, authorised video from actual intended camera views, with labelled ordinary shopping and staged pickup, return, basket placement and concealment-like sequences. Compare person proposals and tracking independently from product interaction classification. Measure missed people, duplicate boxes, ID changes, action coverage, false review alerts per camera-hour and end-to-end latency on the actual Windows/Mac laptops. Train or select an interaction model against held-out pharmacy examples rather than treating skeleton quality as theft recognition.

The four-frame product sampler and ten-second minimum submission interval remain unchanged here. Short actions between samples/jobs can be missed. A future bounded temporal pipeline should retain preceding context and evaluate denser/event-triggered windows before changing operational alarm thresholds. Pharmacy accuracy and six-branch physical acceptance remain unmeasured.
