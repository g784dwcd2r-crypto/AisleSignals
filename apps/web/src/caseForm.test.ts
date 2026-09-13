import { describe, expect, it } from "vitest";
import { buildCasePatch, caseFields } from "./caseForm";
import type { Incident } from "./types";

const confirmed: Incident = {
  id: "synthetic-case",
  reference: "AS-DEMO",
  title: "Synthetic reviewed case",
  notes: "Original reviewed facts.",
  candidate_id: null,
  classification: "STORE_CONFIRMED_LOSS",
  status: "OPEN",
  outcome: "LOSS_RECORDED",
  loss_cents: 2500,
  recovered_cents: 500,
  version: 5,
  created_at: "2026-09-13T10:00:00Z",
  updated_at: "2026-09-13T10:00:00Z",
  tasks: [],
  history: [],
  draft: null,
};
describe("role-aware case patch", () => {
  it("allows a reviewer notes-only edit on a manager-confirmed loss without resending privileged fields", () => {
    const form = {
      ...caseFields(confirmed),
      notes: "Further synthetic reviewed facts.",
    };
    expect(buildCasePatch(confirmed, form, 5, false)).toEqual({
      expected_version: 5,
      notes: "Further synthetic reviewed facts.",
    });
  });
  it("does not send monetary fields from a reviewer even if client form state is altered", () => {
    const patch = buildCasePatch(
      confirmed,
      { ...caseFields(confirmed), loss: "999.00", recovered: "0.00" },
      5,
      false,
    );
    expect(patch).not.toHaveProperty("loss_cents");
    expect(patch).not.toHaveProperty("recovered_cents");
  });
  it("sends explicit null when a manager clears known values", () => {
    expect(
      buildCasePatch(
        confirmed,
        {
          ...caseFields(confirmed),
          loss: "",
          recovered: "",
          classification: "BENIGN",
          outcome: "NO_LOSS_ESTABLISHED",
        },
        5,
        true,
      ),
    ).toEqual({
      expected_version: 5,
      loss_cents: null,
      recovered_cents: null,
      classification: "BENIGN",
      outcome: "NO_LOSS_ESTABLISHED",
    });
  });
});
