export type Role = "MANAGER" | "REVIEWER";
export type User = { id: string; name: string; email: string; role: Role };
export type RuntimeMode = "synthetic" | "pilot";
export type Runtime = {
  mode: RuntimeMode;
  setup_required: boolean;
  local_only: boolean;
  authentication: "local_named_password" | "synthetic_demo";
  mfa_enabled: boolean;
};
export type AllowedSite = {
  id: string;
  name: string;
  organisation_id: string;
  organisation_name: string;
  role: Role;
};
export type Session = {
  user: User;
  csrf_token: string;
  mode: RuntimeMode;
  current_site_id: string;
  allowed_sites: AllowedSite[];
};
export type Site = {
  id: string;
  name: string;
  organisation_name: string;
  timezone: string;
  monthly_price_cents: number;
  shift_active: boolean;
};
export type Camera = {
  id: string;
  name: string;
  zone: string;
  status: "DEMO_ONLINE" | "OFFLINE" | "FROZEN" | "UNCONFIGURED";
  connection_kind: "SIMULATOR";
  last_seen_at: string | null;
  detail: string;
  version: number;
};
export type Candidate = {
  id: string;
  title: string;
  camera_id: string;
  camera_name: string;
  zone: string;
  occurred_at: string;
  received_at: string;
  status: "NEW" | "ACKNOWLEDGED" | "DISMISSED" | "CONVERTED";
  source: "SIMULATOR";
  scenario: string;
  event_label: string;
  summary: string;
  media_status: "AVAILABLE" | "MISSING";
  historical: boolean;
  version: number;
  incident_id: string | null;
  evidence_url: string | null;
};
export type Classification =
  | "UNASSESSED"
  | "BENIGN"
  | "INSUFFICIENT_EVIDENCE"
  | "SUSPECTED_INCIDENT"
  | "STORE_CONFIRMED_LOSS";
export type Outcome =
  | "UNRESOLVED"
  | "NO_LOSS_ESTABLISHED"
  | "GOODS_RETURNED"
  | "GOODS_PAID_FOR"
  | "LOSS_RECORDED";
export type HistoryEntry = {
  id: string;
  at: string;
  actor: string;
  action: string;
  detail: string;
};
export type Task = {
  id: string;
  title: string;
  assignee: string;
  due_at: string;
  done: boolean;
};
export type Incident = {
  id: string;
  reference: string;
  title: string;
  notes: string;
  candidate_id: string | null;
  interaction_source?: InteractionCaseSource;
  classification: Classification;
  status: "OPEN" | "CLOSED";
  outcome: Outcome;
  loss_cents: number | null;
  recovered_cents: number | null;
  version: number;
  created_at: string;
  updated_at: string;
  tasks: Task[];
  history: HistoryEntry[];
  draft: null | {
    text: string;
    engine: string;
    approved: boolean;
    created_at: string;
  };
};
export type InteractionCaseSource = {
  id: string;
  version: number;
  run_id: string;
  source_kind: "CAMERA" | "SCREEN_CAPTURE" | "RECORDED_VIDEO";
  source_label: string;
  created_at: string;
  expires_at: string;
  linked_at: string;
  linked_by: { id: string; name: string };
  observation: {
    action: string;
    reason: string;
    model: string;
    prompt_version?: string;
    provenance?: string;
    validated?: boolean;
  };
  review: { outcome: string; note: string; at: string; by: string };
  frames: { at_seconds: number; sha256: string; bytes: number }[];
  evidence_kind: "sampled_jpeg_derivatives";
  retention_notice: string;
};
export type Assistance = {
  id: string;
  reason: string;
  status: "REQUESTED" | "ACKNOWLEDGED" | "RESOLVED";
  created_at: string;
  requested_by: string;
};
export type Audit = HistoryEntry & {
  resource_type: string;
  resource_id: string;
};
export type Budget = {
  monthly_cap_cents: number;
  spent_cents: number;
  engine: "LOCAL_TEMPLATE";
  cloud_enabled: boolean;
};
export type Bootstrap = {
  mode?: RuntimeMode;
  current_site_id?: string;
  allowed_sites?: AllowedSite[];
  user: User;
  site: Site;
  cameras: Camera[];
  candidates: Candidate[];
  incidents: Incident[];
  assistance: Assistance[];
  audit: Audit[];
  budget: Budget;
};
export type Page =
  | "overview"
  | "live-detection"
  | "review"
  | "incidents"
  | "assistance"
  | "cameras"
  | "video-test"
  | "activity"
  | "administration"
  | "settings";
