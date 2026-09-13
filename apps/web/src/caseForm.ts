import type { Classification, Incident, Outcome } from "./types";
import { cents, euroInput } from "./format";

export type CaseForm = {
  title: string;
  notes: string;
  classification: Classification;
  outcome: Outcome;
  loss: string;
  recovered: string;
};
export function caseFields(incident: Incident): CaseForm {
  return {
    title: incident.title,
    notes: incident.notes,
    classification: incident.classification,
    outcome: incident.outcome,
    loss: euroInput(incident.loss_cents),
    recovered: euroInput(incident.recovered_cents),
  };
}
export function buildCasePatch(
  incident: Incident,
  form: CaseForm,
  version: number,
  manager: boolean,
) {
  const patch: {
    expected_version: number;
    title?: string;
    notes?: string;
    classification?: Classification;
    outcome?: Outcome;
    loss_cents?: number | null;
    recovered_cents?: number | null;
  } = { expected_version: version };
  if (form.title !== incident.title) patch.title = form.title;
  if (form.notes !== incident.notes) patch.notes = form.notes;
  if (form.classification !== incident.classification)
    patch.classification = form.classification;
  if (form.outcome !== incident.outcome) patch.outcome = form.outcome;
  // A notes-only reviewer edit must not resend a manager-confirmed classification.
  // Monetary authority is checked again by the API; the UI only submits manager edits.
  if (manager) {
    const loss = cents(form.loss);
    const recovered = cents(form.recovered);
    if (loss !== incident.loss_cents) patch.loss_cents = loss;
    if (recovered !== incident.recovered_cents)
      patch.recovered_cents = recovered;
  }
  return patch;
}
