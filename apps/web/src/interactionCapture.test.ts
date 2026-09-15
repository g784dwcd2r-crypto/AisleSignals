import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ACKNOWLEDGED_CAMERA_QUIET_MS,
  freshInteractionAlarm,
  InteractionAttentionPolicy,
  InteractionFrameBuffer,
  safeInteractionFrameUrl,
  safeIncidentFrameUrl,
  interactionCropPixels,
  captureInteractionFrame,
  prepareInteractionSound,
  InteractionAlarmCommission,
} from "./interactionCapture";

describe("camera and aisle area sampling", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("crops a full-resolution CCTV tile before resizing it for inference", () => {
    expect(
      interactionCropPixels(1920, 1080, {
        x: 0.5,
        y: 0,
        width: 0.5,
        height: 0.5,
      }),
    ).toEqual({
      x: 960,
      y: 0,
      width: 960,
      height: 540,
      outputWidth: 768,
      outputHeight: 432,
    });
    expect(interactionCropPixels(1920, 1080)).toEqual({
      x: 0,
      y: 0,
      width: 1920,
      height: 1080,
      outputWidth: 768,
      outputHeight: 432,
    });
  });
  it("preserves narrow aisle geometry without inventing detail by upscaling", () => {
    expect(
      interactionCropPixels(1280, 720, {
        x: 0.25,
        y: 0.25,
        width: 0.25,
        height: 0.5,
      }),
    ).toEqual({
      x: 320,
      y: 180,
      width: 320,
      height: 360,
      outputWidth: 320,
      outputHeight: 360,
    });
  });
  it.each([
    { x: -0.1, y: 0, width: 0.5, height: 0.5 },
    { x: 0.8, y: 0.8, width: 0.5, height: 0.5 },
    { x: 0, y: 0, width: 0, height: 0.5 },
    { x: 0, y: 0, width: 0.03, height: 0.5 },
    { x: NaN, y: 0, width: 0.5, height: 0.5 },
  ])("rejects an invalid or empty selection %j", (crop) =>
    expect(() => interactionCropPixels(1920, 1080, crop)).toThrow(),
  );
  it("rejects regions that have too few source pixels even when percentages fit", () =>
    expect(() =>
      interactionCropPixels(320, 180, { x: 0, y: 0, width: 0.1, height: 0.1 }),
    ).toThrow("too small"));
  it("passes only the selected source rectangle to canvas, preserving source time", () => {
    const drawImage = vi.fn();
    const canvas = {
      width: 0,
      height: 0,
      getContext: () => ({ drawImage }),
      toDataURL: () => "data:image/jpeg;base64,c3ludGhldGlj",
    };
    vi.stubGlobal("document", { createElement: () => canvas });
    const video = {
      readyState: 4,
      videoWidth: 1920,
      videoHeight: 1080,
      paused: false,
      seeking: false,
      playbackRate: 1,
      currentTime: 4.25,
    } as HTMLVideoElement;
    const frame = captureInteractionFrame(video, 5000, {
      x: 0.5,
      y: 0.5,
      width: 0.5,
      height: 0.5,
    });
    expect(drawImage).toHaveBeenCalledWith(
      video,
      960,
      540,
      960,
      540,
      0,
      0,
      768,
      432,
    );
    expect(frame).toEqual({
      at_seconds: 4.25,
      capturedAt: 5000,
      jpeg_base64: "c3ludGhldGlj",
      width: 768,
      height: 432,
      sourceWidth: 960,
      sourceHeight: 540,
    });
    expect(canvas.width).toBe(768);
    expect(canvas.height).toBe(432);
  });
});

function sample(buffer: InteractionFrameBuffer, media: number, wall: number) {
  const action = buffer.observe(media, wall);
  if (action === "capture")
    buffer.add({
      at_seconds: media,
      capturedAt: wall,
      jpeg_base64: "synthetic-frame",
      width: 320,
      height: 180,
      sourceWidth: 320,
      sourceHeight: 180,
    });
  return action;
}

describe("interaction sequence sampling", () => {
  it("reports every partial frame while submission still requires four", () => {
    const buffer = new InteractionFrameBuffer();
    for (let i = 0; i <= 15; i++) {
      sample(buffer, i / 4, i * 250);
      const count = Math.min(4, Math.floor(i / 5) + 1);
      expect(buffer.snapshot(i * 250)).toHaveLength(count);
      expect(buffer.sequence(i * 250)).toHaveLength(count === 4 ? 4 : 0);
    }
  });
  it("freezes a snapshot independently of later samples and resets", () => {
    const buffer = new InteractionFrameBuffer();
    for (let i = 0; i <= 15; i++) sample(buffer, i / 4, i * 250);
    const submitted = buffer.sequence(3750);
    for (let i = 16; i <= 25; i++) sample(buffer, i / 4, i * 250);
    expect(buffer.snapshot(6250).at(-1)?.at_seconds).toBe(6.25);
    expect(submitted.map((frame) => frame.at_seconds)).toEqual([
      0, 1.25, 2.5, 3.75,
    ]);
    submitted[2].at_seconds = 99;
    expect(buffer.snapshot(6250)[0].at_seconds).toBe(2.5);
    buffer.reset();
    expect(buffer.snapshot(6250)).toEqual([]);
    expect(submitted).toHaveLength(4);
  });
  it.each([2001, -1, NaN, Infinity])(
    "does not display stale or invalid partial progress at %s",
    (now) => {
      const buffer = new InteractionFrameBuffer();
      sample(buffer, 0, 0);
      expect(buffer.snapshot(now)).toEqual([]);
    },
  );
  it("requires four chronological frames without depending on pose detections", () => {
    const buffer = new InteractionFrameBuffer();
    for (let i = 0; i <= 15; i++) sample(buffer, i / 4, i * 250);
    expect(buffer.sequence(3750).map((frame) => frame.at_seconds)).toEqual([
      0, 1.25, 2.5, 3.75,
    ]);
  });
  it("retains at most six frames and submits the most recent four", () => {
    const buffer = new InteractionFrameBuffer();
    for (let i = 0; i <= 50; i++) sample(buffer, i / 4, i * 250);
    expect(buffer.count).toBe(6);
    expect(buffer.sequence(12500).map((frame) => frame.at_seconds)).toEqual([
      8.75, 10, 11.25, 12.5,
    ]);
  });
  it.each([
    [1, 4000, "backward seek"],
    [20, 4000, "forward seek"],
    [4, 8000, "sleep or delayed callback"],
    [NaN, 4000, "invalid media time"],
  ])("discards the sequence on %s at %s (%s)", (media, wall) => {
    const buffer = new InteractionFrameBuffer();
    for (let i = 0; i <= 15; i++) sample(buffer, i / 4, i * 250);
    expect(sample(buffer, media, wall)).toBe("reset");
    expect(buffer.sequence(wall)).toEqual([]);
  });
  it("does not create fresh samples from a stalled media clock", () => {
    const buffer = new InteractionFrameBuffer();
    for (let i = 0; i <= 15; i++) sample(buffer, i / 4, i * 250);
    for (let i = 16; i <= 25; i++)
      expect(sample(buffer, 3.75, i * 250)).toBe("wait");
    expect(buffer.sequence(6250)).toEqual([]);
  });
  it("clears all frames when the user stops or changes source", () => {
    const buffer = new InteractionFrameBuffer();
    for (let i = 0; i <= 15; i++) sample(buffer, i / 4, i * 250);
    buffer.reset();
    expect(buffer.count).toBe(0);
    expect(buffer.sequence(3750)).toEqual([]);
  });
});

const validAlarm = () => ({
  enabled: true,
  sameRun: true,
  running: true,
  visible: true,
  playing: true,
  seeking: false,
  playbackRate: 1,
  now: 40000,
  lastFrameAt: 35000,
  lastProgressAt: 39900,
  lastAlarmAt: -Infinity,
  result: {
    alarm_eligible: true,
    action: "POSSIBLE_CONCEALMENT" as const,
    visibility: "clear" as const,
    person_visible: true,
    product_visible: true,
    sequence_observed: true,
  },
});
describe("product attention alarm freshness", () => {
  it("allows a fresh explicitly enabled observable sequence", () =>
    expect(freshInteractionAlarm(validAlarm())).toBe(true));
  it.each([
    { enabled: false },
    { sameRun: false },
    { running: false },
    { visible: false },
    { playing: false },
    { seeking: true },
    { playbackRate: 2 },
    { lastFrameAt: 24999 },
    { lastFrameAt: 41000 },
    { lastProgressAt: 37000 },
    { lastAlarmAt: 20000 },
    { now: NaN },
  ])(
    "blocks changed, stale, paused, hidden or rate-limited context: %j",
    (override) => {
      expect(freshInteractionAlarm({ ...validAlarm(), ...override })).toBe(
        false,
      );
    },
  );
  it.each([
    { alarm_eligible: false },
    { visibility: "partial" as const },
    { product_visible: false },
    { person_visible: false },
    { sequence_observed: false },
    { action: "NORMAL_SHOPPING" as const },
  ])("never treats model eligibility alone as sufficient: %j", (override) => {
    const input = validAlarm();
    expect(
      freshInteractionAlarm({
        ...input,
        result: { ...input.result, ...override },
      }),
    ).toBe(false);
  });
});

describe("staff product alarm commissioning", () => {
  const context = "branch-one:run-one:camera-one:crop-one:volume-one";
  function commissioned(recorded = false) {
    const gate = new InteractionAlarmCommission();
    const test = gate.beginTest(context);
    expect(gate.finishTest(test, context, 1000)).toBe(true);
    expect(gate.confirm(context, 2000)).toBe(true);
    expect(gate.arm(context, recorded)).toBe(true);
    return gate;
  }
  it("cannot arm from browser audio activation without a staff confirmation", () => {
    const gate = new InteractionAlarmCommission();
    const test = gate.beginTest(context);
    gate.finishTest(test, context, 1000);
    expect(gate.arm(context, true)).toBe(false);
    expect(gate.claim(context, "job-one", "CAMERA")).toBe(false);
  });
  it("ignores late sound activation after stopping or starting a newer test", () => {
    const gate = new InteractionAlarmCommission();
    const old = gate.beginTest(context);
    gate.invalidate();
    expect(gate.finishTest(old, context, 1000)).toBe(false);
    const latest = gate.beginTest(context);
    expect(gate.currentTest(old, context)).toBe(false);
    expect(gate.finishTest(old, context, 1000)).toBe(false);
    expect(gate.finishTest(latest, context, 1000)).toBe(true);
  });
  it("requires a timely sound confirmation for the exact source context", () => {
    const gate = new InteractionAlarmCommission();
    const test = gate.beginTest(context);
    gate.finishTest(test, context, 1000);
    expect(gate.confirm(context, 61001)).toBe(false);
    expect(gate.confirm("new-source", 2000)).toBe(false);
    expect(gate.confirm(context, NaN)).toBe(false);
    expect(gate.confirm(context, 999)).toBe(false);
  });
  it("requires separate recorded-video consent and never repeats the same job", () => {
    const gate = commissioned();
    expect(gate.claim(context, "recorded-job", "RECORDED_VIDEO")).toBe(false);
    expect(gate.claim(context, "live-job", "CAMERA")).toBe(true);
    expect(gate.claim(context, "live-job", "CAMERA")).toBe(false);
    expect(gate.claim("new-run", "next-job", "CAMERA")).toBe(false);
    gate.arm(context, true);
    expect(gate.claim(context, "recorded-job", "RECORDED_VIDEO")).toBe(true);
  });
  it("disarms on invalidation and retains delivered IDs through recommissioning", () => {
    const gate = commissioned();
    gate.claim(context, "job-one", "CAMERA");
    gate.invalidate();
    expect(gate.claim(context, "job-two", "CAMERA")).toBe(false);
    const test = gate.beginTest(context);
    gate.finishTest(test, context, 5000);
    gate.confirm(context, 6000);
    gate.arm(context, false);
    expect(gate.claim(context, "job-one", "CAMERA")).toBe(false);
    expect(gate.claim(context, "job-two", "CAMERA")).toBe(true);
  });

  it("keeps accepting commissioned jobs after its bounded history fills", () => {
    const gate = commissioned();
    for (let index = 0; index < 1001; index++)
      expect(gate.claim(context, `job-${index}`, "CAMERA")).toBe(true);
    expect(gate.claim(context, "job-1000", "CAMERA")).toBe(false);
  });

  it("does not consume attention or cooldown when commissioning rejects", () => {
    const attention = new InteractionAttentionPolicy();
    const gate = new InteractionAlarmCommission();
    expect(
      prepareInteractionSound(attention, gate, {
        id: "job-one",
        camera: "camera-1",
        now: 1000,
        context,
        source: "CAMERA",
      }),
    ).toBeNull();
    expect(attention.check("job-one", "camera-1", 1000)).toBe("REQUEST_SOUND");
    expect(attention.check("job-two", "camera-2", 1001)).toBe("REQUEST_SOUND");
    const revision = gate.beginTest(context);
    gate.finishTest(revision, context, 2000);
    gate.confirm(context, 2001);
    gate.arm(context, false);
    const commit = prepareInteractionSound(attention, gate, {
      id: "job-one",
      camera: "camera-1",
      now: 2002,
      context,
      source: "CAMERA",
    });
    expect(commit).not.toBeNull();
    expect(attention.check("job-one", "camera-1", 2002)).toBe("REQUEST_SOUND");
    expect(commit?.()).toBe(true);
    expect(commit?.()).toBe(false);
    expect(attention.check("job-two", "camera-2", 2003)).toBe(
      "GLOBAL_COOLDOWN",
    );
  });
});

describe("product attention duplicate and acknowledgement policy", () => {
  it("shares a global cooldown across cameras without losing later observations", () => {
    const policy = new InteractionAttentionPolicy();
    expect(policy.decide("one", "camera-1", 1000)).toBe("REQUEST_SOUND");
    expect(policy.decide("two", "camera-2", 2000)).toBe("GLOBAL_COOLDOWN");
    expect(policy.decide("two", "camera-2", 31000)).toBe("REQUEST_SOUND");
    expect(policy.decide("two", "camera-2", 62000)).toBe("ALREADY_DELIVERED");
  });

  it("acknowledgement quiets only that camera and expires deterministically", () => {
    const policy = new InteractionAttentionPolicy();
    expect(policy.acknowledge("camera-1", 5000)).toBe(true);
    expect(policy.decide("one", "camera-1", 6000)).toBe(
      "ACKNOWLEDGED_CAMERA_QUIET",
    );
    expect(policy.decide("two", "camera-2", 6000)).toBe("REQUEST_SOUND");
    expect(
      policy.decide("three", "camera-1", 5000 + ACKNOWLEDGED_CAMERA_QUIET_MS),
    ).toBe("REQUEST_SOUND");
  });

  it("resetting a run clears cooldowns but never replays an already delivered id", () => {
    const policy = new InteractionAttentionPolicy();
    expect(policy.decide("one", "camera-1", 1000)).toBe("REQUEST_SOUND");
    policy.acknowledge("camera-1", 2000);
    policy.resetRun();
    expect(policy.decide("one", "camera-1", 3000)).toBe("ALREADY_DELIVERED");
    expect(policy.decide("two", "camera-1", 3000)).toBe("REQUEST_SOUND");
  });

  it("keeps accepting new observations after the bounded duplicate history fills", () => {
    const policy = new InteractionAttentionPolicy();
    for (let index = 0; index < 1001; index++)
      expect(policy.decide(`job-${index}`, "camera-1", index * 30001)).toBe(
        "REQUEST_SOUND",
      );
    expect(policy.decide("job-1000", "camera-1", 1002 * 30001)).toBe(
      "ALREADY_DELIVERED",
    );
  });
});

describe("authenticated sampled frame URLs", () => {
  const id = "ed25eb7d-55d7-4aad-bd87-066a935f7245";
  it("allows only the current interaction's bounded frame route", () => {
    expect(
      safeInteractionFrameUrl(`/api/interactions/${id}/frames/3`, id),
    ).toBe(`/api/interactions/${id}/frames/3`);
    const incidentId = "ed25eb7d-55d7-4aad-bd87-066a935f7244";
    const viewId = "ed25eb7d-55d7-4aad-bd87-066a935f7243";
    expect(
      safeIncidentFrameUrl(
        `/api/incidents/${incidentId}/interaction-source/frames/3/views/${viewId}`,
        incidentId,
      ),
    ).toBe(
      `/api/incidents/${incidentId}/interaction-source/frames/3/views/${viewId}`,
    );
    for (const path of [
      `https://example.com/${id}`,
      `/api/interactions/${id}/frames/6`,
      `/api/interactions/${id}/frames/0?download=1`,
      `/api/interactions/ed25eb7d-55d7-4aad-bd87-066a935f7244/frames/0`,
      `/api/incidents/${incidentId}/interaction-source/frames/0`,
      "data:image/png;base64,aA==",
    ])
      expect(safeInteractionFrameUrl(path, id)).toBeNull();
  });

  it("allows only the current incident's bounded linked frame route", () => {
    const incidentId = "ed25eb7d-55d7-4aad-bd87-066a935f7244";
    const viewId = "ed25eb7d-55d7-4aad-bd87-066a935f7243";
    const allowed = `/api/incidents/${incidentId}/interaction-source/frames/3/views/${viewId}`;
    expect(safeIncidentFrameUrl(allowed, incidentId)).toBe(allowed);
    for (const path of [
      `https://example.com/${incidentId}`,
      `/api/incidents/${incidentId}/interaction-source/frames/6/views/${viewId}`,
      `/api/incidents/${incidentId}/interaction-source/frames/0/views/${viewId}?download=1`,
      `/api/incidents/${incidentId}/interaction-source/frames/0`,
      `/api/incidents/${incidentId}/interaction-source/frames/0/views/not-a-view-id`,
      `/api/incidents/ed25eb7d-55d7-4aad-bd87-066a935f7245/interaction-source/frames/0/views/${viewId}`,
      `/api/interactions/${id}/frames/0`,
      "data:image/png;base64,aA==",
    ])
      expect(safeIncidentFrameUrl(path, incidentId)).toBeNull();
  });
});
