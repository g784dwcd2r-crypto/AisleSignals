import { cameraContextLabel, validateCameraContext } from "./cameraContext";
import { interactionCropPixels } from "./interactionCapture";

/** Historical browser-declared geometry, never a registered camera identity. */
export default function CameraContextDetails({
  context,
}: {
  context: unknown;
}) {
  if (context === undefined || context === null) return null;
  try {
    const camera = validateCameraContext(context);
    const crop = interactionCropPixels(
      camera.source_width,
      camera.source_height,
      camera.crop,
    );
    return (
      <p className="camera-context-details">
        <strong>{cameraContextLabel(camera)}</strong> · Source{" "}
        {camera.source_width}×{camera.source_height} px · Crop {crop.x},{" "}
        {crop.y}, {crop.width}×{crop.height} px. Camera position in the
        confirmed screen layout.
      </p>
    );
  } catch {
    return (
      <p role="status">Camera geometry is unavailable for this observation.</p>
    );
  }
}
