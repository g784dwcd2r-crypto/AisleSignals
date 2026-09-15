# Pharmacy layout calibration

## Purpose and safety boundary

The calibration map records what each confirmed CCTV tile can see. A pharmacy manager marks entrances, exits, cashier areas, shelves, blind areas and ignored recorder overlays. Coordinates are normalised to the individual camera tile, so the map remains usable when the monitor resolution changes.

Calibration is contextual evidence only. It has `alarm_authority: false` and cannot arm sound, label theft, or turn a model inference into an incident. During local person processing, a person's ground point inside a blind or ignored polygon keeps the observed person box on screen but removes pose landmarks from rule processing. This is a one-way safety gate.

## On-site workflow

1. Connect the real CCTV monitor or recording in **Live Detection**.
2. Confirm the four-camera `2 × 2` or six-camera `3 × 2` / `2 × 3` layout. Correct any recorder or browser borders first.
3. Open **Pharmacy layout calibration** and start a map for that confirmed layout.
4. For every camera, capture a representative current frame. Mark each visible entrance, exit, cashier, shelf, blind area and recorder overlay. Use **Ignored area** for timestamps, controls or neighbouring views that must never contribute pose evidence.
5. Give the monitor a display label. Never paste RTSP/ONVIF URLs, passwords or tokens.
6. Save. The server calculates completeness and reports missing zone types or cameras with no declared coverage.
7. On a later connection, select **Use saved map with this source**. This explicit attachment is required once per connected-source session so a map cannot silently attach to a different recorder with the same grid shape.
8. A manager and a second staff member walk the real site and confirm every polygon against day and night views. Treat “Ready for site acceptance” as schema completeness until this physical check is signed off.

Maps are scoped by the authenticated organisation and active branch. Camera entries are scoped by confirmed layout and zero-based tile index. A branch switch rotates session authority; the request body cannot select another branch. Managers can write, reviewers can read, and an optimistic version check prevents one manager silently overwriting another.

## Required real-site input

- The exact recorder grid for each branch: `2 × 2`, `3 × 2`, or `2 × 3`.
- A stable name for each camera and which tile it occupies.
- Day and night reference frames with no customers used for the calibration session only; footage is not uploaded by this workflow.
- Entrance and exit lines, payment counter footprint, shelf faces, mirrors, privacy-sensitive areas, camera occlusions, recorder controls and permanent overlays.
- A walk test by two staff members, including camera gaps between shelves, cashier and exits.
- Recalibration after cameras move, recorder layouts change, shelving changes materially, or the monitor crop changes.

## Current limitations

- The editor creates rectangular four-point polygons. The API accepts validated simple polygons with 3–12 points, but free-form polygon editing is not yet exposed in the browser.
- Camera identity is the confirmed tile position within the saved monitor layout. It does not yet bind to an ONVIF hardware identifier. Reordering recorder tiles requires recalibration.
- Completeness proves required labels and camera coverage are present; it does not prove physical accuracy, remove occlusion, or validate model performance.
- Entrance, exit, cashier and shelf zones are rendered as context. Only blind and ignored zones currently change detection, and only by withholding pose-rule evidence.
- The workflow needs real pharmacy acceptance testing across all six branches before production use.
