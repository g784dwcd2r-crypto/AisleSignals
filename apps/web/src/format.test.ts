import { describe, expect, it } from "vitest";
import { cents, euroInput, getAlertCount, money } from "./format";
import { safeEvidenceUrl, actionFingerprint } from "./api";
describe("financial input preserves unknown and precise cents", () => {
  it("distinguishes unknown from zero", () => {
    expect(cents("")).toBeNull();
    expect(cents("0")).toBe(0);
    expect(money(null)).toBe("Not established");
    expect(euroInput(null)).toBe("");
  });
  it("converts without floating-point rounding", () => {
    expect(cents("12.29")).toBe(1229);
    expect(cents("0.01")).toBe(1);
    expect(cents("4.2")).toBe(420);
  });
  it.each([
    "-1",
    "1.234",
    "1e2",
    "NaN",
    "Infinity",
    "1,20",
    "9007199254740992",
  ])("rejects invalid money %s", (value) =>
    expect(() => cents(value)).toThrow(),
  );
});
describe("review provenance and authenticated evidence", () => {
  it("historical events never count as new attention", () =>
    expect(
      getAlertCount([
        { historical: true, status: "NEW" },
        { historical: false, status: "NEW" },
        { historical: false, status: "CONVERTED" },
      ]),
    ).toBe(1));
  it("rejects remote, injected and token-bearing evidence URLs", () => {
    for (const value of [
      "https://example.com/a.svg",
      "javascript:alert(1)",
      "//example.com/api/evidence/test",
      "/api/evidence/123?token=secret",
      null,
    ])
      expect(safeEvidenceUrl(value)).toBeNull();
    expect(
      safeEvidenceUrl("/api/evidence/123e4567-e89b-12d3-a456-426614174000"),
    ).not.toBeNull();
  });
  it("differentiates changed review actions for idempotency", () =>
    expect(
      actionFingerprint("/review", "POST", { decision: "DISMISS" }),
    ).not.toBe(
      actionFingerprint("/review", "POST", { decision: "OPEN_INCIDENT" }),
    ));
});
