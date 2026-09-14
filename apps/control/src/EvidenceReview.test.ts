import { describe, expect, it } from "vitest";
import {
  effectiveEvidenceState,
  evidenceLabel,
  evidenceSummary,
} from "./EvidenceReview";
import type { AlertEvidence } from "./types";

function evidence(changes: Partial<AlertEvidence> = {}): AlertEvidence {
  return {
    id: "e0655814-016a-420b-93ee-a42f677833c0",
    kind: "OVERVIEW",
    content_type: "image/jpeg",
    byte_count: 2048,
    state: "READY",
    expires_at: "2030-01-01T00:00:00Z",
    ...changes,
  };
}

describe("control evidence presentation contract", () => {
  it("uses evidence labels that make no object or theft claim", () => {
    expect(evidenceLabel("OVERVIEW")).toBe("Overview snapshot");
    expect(evidenceLabel("INTERACTION_CROP")).toBe("Interaction detail");
    expect(evidenceLabel("CLIP")).toBe("Short evidence clip");
  });

  it.each(["NONE", "PENDING", "PARTIAL", "READY", "EXPIRED"] as const)(
    "provides a bounded summary for %s",
    (state) => expect(evidenceSummary(state)).toBeTruthy(),
  );

  it("expires ready evidence using the local deadline without trusting stale aggregate state", () => {
    expect(
      effectiveEvidenceState(
        evidence({ expires_at: "2029-12-31T23:59:59Z" }),
        Date.parse("2030-01-01T00:00:00Z"),
      ),
    ).toBe("EXPIRED");
    expect(
      effectiveEvidenceState(
        evidence({ state: "DELETED" }),
        Date.parse("2020-01-01T00:00:00Z"),
      ),
    ).toBe("DELETED");
  });
});
