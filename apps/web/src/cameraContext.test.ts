import { describe, expect, it } from "vitest";
import {
  cameraContextId,
  cameraContextKey,
  cameraContextLabel,
  validateCameraContext,
} from "./cameraContext";
import { interactionCropPixels } from "./interactionCapture";

const context = () => ({
  source_id: "a94c30aa-bd3c-400c-a204-66bdcb8b9e15",
  epoch: 2,
  layout: "3x2" as const,
  camera_index: 4,
  source_width: 1920,
  source_height: 1080,
  crop: { x: 0.34, y: 0.51, width: 0.31, height: 0.47 },
});

describe("confirmed camera provenance", () => {
  it("retains measured geometry and creates an immutable independent snapshot", () => {
    const input = context();
    const saved = validateCameraContext(input);
    input.crop.x = 0;
    expect(saved.crop.x).toBe(0.34);
    expect(Object.isFrozen(saved)).toBe(true);
    expect(Object.isFrozen(saved.crop)).toBe(true);
    expect(cameraContextLabel(saved)).toBe("Camera 5 · 3x2");
    expect(cameraContextId(saved)).toBe(`${input.source_id}:2:3x2:4`);
    expect(
      interactionCropPixels(
        saved.source_width,
        saved.source_height,
        saved.crop,
      ),
    ).toEqual({
      x: 652,
      y: 550,
      width: 595,
      height: 508,
      outputWidth: 595,
      outputHeight: 508,
    });
  });

  it.each(["2x2", "3x2", "2x3"])(
    "accepts a valid position within %s",
    (layout) => {
      const camera_index = layout === "2x2" ? 3 : 5;
      expect(
        validateCameraContext({ ...context(), layout, camera_index })
          .camera_index,
      ).toBe(camera_index);
      expect(() =>
        validateCameraContext({
          ...context(),
          layout,
          camera_index: camera_index + 1,
        }),
      ).toThrow();
    },
  );

  it.each([
    null,
    [],
    {},
    { ...context(), organisation_id: "untrusted" },
    { ...context(), source_id: "blob:http://localhost/private-source" },
    { ...context(), source_id: context().source_id.toUpperCase() },
    { ...context(), epoch: 0 },
    { ...context(), epoch: true },
    { ...context(), epoch: "2" },
    { ...context(), epoch: 2147483648 },
    { ...context(), camera_index: 0.5 },
    { ...context(), camera_index: -1 },
    { ...context(), layout: "single" },
    { ...context(), source_width: 16385 },
    { ...context(), source_height: 47 },
    { ...context(), source_height: NaN },
    { ...context(), crop: null },
    { ...context(), crop: { x: 0, y: 0, width: 1 } },
    {
      ...context(),
      crop: { x: 0, y: 0, width: 1, height: 1, site_id: "untrusted" },
    },
    ...[Infinity, NaN, "0", false, -0.01, 1.0000005].map((x) => ({
      ...context(),
      crop: { ...context().crop, x },
    })),
    { ...context(), crop: { x: 0, y: 0, width: 1.0000005, height: 1 } },
    { ...context(), crop: { x: 0.8, y: 0, width: 0.3, height: 1 } },
    { ...context(), crop: { x: 0, y: 0, width: 0.04, height: 1 } },
    {
      ...context(),
      source_width: 100,
      crop: { x: 0, y: 0, width: 0.4, height: 1 },
    },
  ])("rejects malformed, ambiguous or out-of-source metadata (%#)", (input) => {
    expect(() => validateCameraContext(input)).toThrow();
  });

  it("uses half-up pixel rounding and the same 768-pixel resize boundary as the API", () => {
    const odd = validateCameraContext({
      ...context(),
      source_width: 101,
      source_height: 101,
      crop: { x: 0.5, y: 0.5, width: 0.5, height: 0.5 },
    });
    expect(interactionCropPixels(101, 101, odd.crop)).toEqual({
      x: 50,
      y: 50,
      width: 51,
      height: 51,
      outputWidth: 51,
      outputHeight: 51,
    });
    const large = validateCameraContext({
      ...context(),
      source_width: 3840,
      source_height: 2160,
      crop: { x: 0, y: 0.5, width: 0.5, height: 0.5 },
    });
    expect(
      interactionCropPixels(
        large.source_width,
        large.source_height,
        large.crop,
      ),
    ).toMatchObject({
      width: 1920,
      height: 1080,
      outputWidth: 768,
      outputHeight: 432,
    });
  });

  it("invalidates a source identity when any geometry, run or tile context changes", () => {
    const old = validateCameraContext(context());
    const key = cameraContextKey(old);
    const alternatives = [
      { ...old, source_id: "a94c30aa-bd3c-400c-a204-66bdcb8b9e16" },
      { ...old, epoch: 3 },
      { ...old, layout: "2x3" },
      { ...old, camera_index: 3 },
      { ...old, source_width: 1921 },
      { ...old, source_height: 1081 },
      ...["x", "y", "width", "height"].map((field) => ({
        ...old,
        crop: {
          ...old.crop,
          [field]: old.crop[field as keyof typeof old.crop] - 0.01,
        },
      })),
    ];
    for (const changed of alternatives) {
      expect(cameraContextKey(validateCameraContext(changed))).not.toBe(key);
    }
    expect(cameraContextKey(validateCameraContext(context()))).toBe(key);
  });
});
