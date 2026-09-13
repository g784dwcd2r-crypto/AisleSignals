# Camera layouts in a shared CCTV window

Live Detection can suggest a four-camera (2 × 2) or six-camera (3 × 2 or 2 × 3) layout from visible separators in the selected video. The check runs locally on downsampled pixels; it makes no cloud or language-model request. Its separator score is evidence for a proposed layout, not a probability of correct detection or theft recognition.

## Select and confirm the camera

1. Select an authorised CCTV window, camera or local recording in **LIVE DETECTION**.
2. Under **Choose the camera to analyse**, inspect the proposed boxes and camera numbers. **Use this layout** confirms their geometry.
3. Select the camera tile for product analysis. Camera numbers run from left to right, then top to bottom. Results retain the camera number and layout in the source label.
4. Enable product interaction analysis and choose manual or automatic analysis. The local interaction model must be available. Sound has its separate speaker check and arming controls.

If the separator check cannot establish a layout, choose **Single camera**, a manual four/six-camera layout, or a custom area explicitly. The **Exclude recorder or browser borders** controls define the board that contains the actual camera pictures, as percentages of the entire source. Inspect the preview before confirming it.

If the same shared window changes from an introduction, advertisement or single camera to a recorder grid, select **Detect layout again**. This clears the previous camera selection and requires confirmation of the new proposal. The application does not silently switch a confirmed camera while analysis runs.

The preview uses the same measured source coordinates as the image sampler. A proposal does not silently start product analysis on an entire mosaic. Changing the source, board, layout or selected camera cancels pending analysis, clears the old frame sequence and disarms product sound. Confirm the new selection before continuing. Editing the existing custom-area percentages switches back to an explicitly custom source area.

## Current coverage

Product analysis processes **one selected camera tile at a time**. The other tiles are not simultaneously product-analysed. Body keypoints still use the full source view; for the clearest pose tracking, share a single camera view. These two statuses are displayed separately so body tracking cannot imply that product classification is available or enabled.

An all-camera scheduler is not implemented in this change. It would require separate bounded frame buffers and immutable result/alarm context per camera, while keeping one model job in flight. With the current global ten-second submission interval, four/six-camera round-robin analysis would check an individual camera at best about every forty/sixty seconds, and potentially slower. That is not continuous all-camera coverage.

## Layout limits

The pixel check looks for approximately even, straight horizontal and vertical separator gutters. Dark and light separators, small position offsets and a confirmed inset board are supported. Blank or transparent images, weak boundaries and competing grid patterns cause abstention. Borderless or irregular mosaics, recorder controls, advertisements, architectural window grids and camera views that change inside a shared window still require staff judgement.

The check cannot identify which physical camera or pharmacy a tile belongs to. Numbered tiles are positions in the selected source. Layout confirmation does not commission speakers, validate camera freshness or establish pharmacy detection accuracy. A screen capture can continue refreshing even if the upstream recorder picture is frozen; inspect its timestamp and view.

## Verification scope

Pure layout tests cover separated four/six-camera patterns, offset boards, source-coordinate mapping, invalid/ambiguous inputs and conservative abstention. Three additional synthetic WebM clips were decoded and correctly proposed as 2 × 2, 3 × 2 and 2 × 3. These synthetic checks establish software behaviour only; authorised client footage is still needed to measure real recorder compatibility and product-interaction accuracy.

The coordinating lead also exercised the six-camera WebM in the visible protected local browser on 13 September 2026: automatic 3 × 2 proposal, explicit confirmation, Camera 2 selection, four fresh sampled frames, a real local Qwen3-VL job, and saved Camera 2 frame review. The model returned `UNCLEAR`, with no person/product/interaction sequence reported, in 1.9 seconds of inference. The clip contains labelled synthetic patterns, not people; this is a pipeline and crop check, not an accuracy evaluation. No product sound was armed. The current local walkthrough launcher has the product model enabled; an earlier casework-only launch had disabled it.
