import { useEffect, useId, useMemo, useState } from "react";
import { FileWarning, Film, Image, RefreshCw, ShieldCheck } from "lucide-react";
import type { AlertEvidence, AlertEvidenceState } from "./types";
import { when } from "./ui";

type LoadState = "LOADING" | "READY" | "ERROR";

export function evidenceLabel(kind: AlertEvidence["kind"]) {
  if (kind === "CLIP") return "Short evidence clip";
  return kind === "INTERACTION_CROP"
    ? "Interaction detail"
    : "Overview snapshot";
}

export function effectiveEvidenceState(item: AlertEvidence, now: number) {
  const expires = Date.parse(item.expires_at);
  return item.state === "READY" && (!Number.isFinite(expires) || expires <= now)
    ? "EXPIRED"
    : item.state;
}

export function evidenceSummary(state: AlertEvidenceState) {
  const summaries: Record<AlertEvidenceState, string> = {
    NONE: "No media was supplied. Review the observation metadata and circumstances.",
    PENDING: "Evidence is still transferring from the pharmacy laptop.",
    PARTIAL:
      "Some evidence is available; another item is pending or unavailable.",
    READY: "Authorised evidence is available for staff review.",
    EXPIRED:
      "The evidence retention period ended. The observation metadata remains reviewable.",
  };
  return summaries[state];
}

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes < 0) return "Size unavailable";
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function EvidenceItem({
  alertId,
  item,
  now,
}: {
  alertId: string;
  item: AlertEvidence;
  now: number;
}) {
  const state = effectiveEvidenceState(item, now);
  const [load, setLoad] = useState<LoadState>("LOADING");
  const [attempt, setAttempt] = useState(0);
  const titleId = useId();
  const mediaUrl = `/control-api/alerts/${encodeURIComponent(alertId)}/evidence/${encodeURIComponent(item.id)}`;
  const supportedImage =
    item.kind !== "CLIP" &&
    ["image/jpeg", "image/png", "image/webp"].includes(item.content_type);
  const supportedVideo =
    item.kind === "CLIP" &&
    ["video/mp4", "video/webm"].includes(item.content_type);
  const unsupported = state === "READY" && !supportedImage && !supportedVideo;

  return (
    <article className="evidence-item" aria-labelledby={titleId}>
      <header>
        <span className="evidence-kind-icon" aria-hidden="true">
          {item.kind === "CLIP" ? <Film size={18} /> : <Image size={18} />}
        </span>
        <div>
          <h4 id={titleId}>{evidenceLabel(item.kind)}</h4>
          <p>
            {formatBytes(item.byte_count)} · retained until{" "}
            {when(item.expires_at)}
          </p>
        </div>
      </header>
      {state === "PENDING" && (
        <p className="evidence-placeholder" role="status">
          Transfer pending. You can review the metadata now or return later.
        </p>
      )}
      {state === "EXPIRED" && (
        <p className="evidence-placeholder">
          Evidence expired and is no longer available.
        </p>
      )}
      {state === "DELETED" && (
        <p className="evidence-placeholder">
          Evidence was deleted. The review record is unchanged.
        </p>
      )}
      {(state === "ERROR" || unsupported) && (
        <p className="evidence-placeholder error" role="alert">
          {unsupported
            ? "This evidence format cannot be displayed safely in the console."
            : "Evidence processing failed. Review the observation metadata or return later."}
        </p>
      )}
      {state === "READY" && !unsupported && (
        <div className="evidence-media" key={attempt}>
          {load === "LOADING" && (
            <p className="evidence-loading" role="status">
              Loading authorised {item.kind === "CLIP" ? "clip" : "snapshot"}…
            </p>
          )}
          {supportedImage && (
            <img
              src={mediaUrl}
              alt="Authorised CCTV snapshot for staff review"
              className={load === "READY" ? "" : "media-waiting"}
              onLoad={() => setLoad("READY")}
              onError={() => setLoad("ERROR")}
            />
          )}
          {supportedVideo && (
            <video
              src={mediaUrl}
              controls
              muted
              playsInline
              preload="metadata"
              aria-label="Authorised CCTV evidence clip for staff review"
              className={load === "READY" ? "" : "media-waiting"}
              onLoadedMetadata={() => setLoad("READY")}
              onError={() => setLoad("ERROR")}
            />
          )}
          {load === "ERROR" && (
            <div className="evidence-load-error" role="alert">
              <FileWarning size={18} />
              <span>
                Evidence could not be loaded. Check your access or try again.
              </span>
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  setLoad("LOADING");
                  setAttempt((value) => value + 1);
                }}
              >
                <RefreshCw size={14} /> Retry evidence
              </button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

export default function EvidenceReview({
  alertId,
  state,
  items,
}: {
  alertId: string;
  state: AlertEvidenceState;
  items: AlertEvidence[];
}) {
  const heading = useId();
  const deadlines = useMemo(
    () => items.map((item) => item.expires_at).join("|"),
    [items],
  );
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const future = deadlines
      .split("|")
      .map(Date.parse)
      .filter((value) => Number.isFinite(value) && value > Date.now());
    if (!future.length) return;
    const timer = window.setTimeout(
      () => setNow(Date.now()),
      Math.min(2_147_483_647, Math.min(...future) - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [deadlines, now]);

  return (
    <section className="evidence-review" aria-labelledby={heading}>
      <div className="evidence-heading">
        <span aria-hidden="true">
          <ShieldCheck size={19} />
        </span>
        <div>
          <h3 id={heading}>CCTV evidence for staff review</h3>
          <p>{evidenceSummary(state)}</p>
        </div>
      </div>
      <p className="evidence-privacy">
        Contains personal data. View only for the authorised pharmacy review; do
        not use this media to identify a person or infer intent.
      </p>
      {items.map((item) => (
        <EvidenceItem key={item.id} alertId={alertId} item={item} now={now} />
      ))}
    </section>
  );
}
