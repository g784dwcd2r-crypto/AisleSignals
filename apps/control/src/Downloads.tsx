import {
  Apple,
  ArrowRight,
  Check,
  Download,
  KeyRound,
  Laptop,
  Monitor,
  PackageCheck,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import type { Session } from "./types";
import { Button } from "./ui";

type DownloadsProps = {
  session: Session;
  navigate: (page: "laptops") => void;
};

const platforms = [
  {
    name: "macOS",
    icon: Apple,
    terminal: "Terminal",
    command: "python3 cloud_companion.py",
    requirement: "Python 3",
  },
  {
    name: "Windows",
    icon: Monitor,
    terminal: "PowerShell",
    command: "py -3 cloud_companion.py",
    requirement: "Python 3 with the py launcher",
  },
] as const;

export default function Downloads({ session, navigate }: DownloadsProps) {
  const canConnect = session.user.role !== "REVIEWER";
  return (
    <>
      <div className="section-heading downloads-heading">
        <div>
          <span className="eyebrow">PHARMACY LAPTOP SOFTWARE</span>
          <h1>Download AisleSignals</h1>
          <p>
            Start with the connection utility, then pair each laptop to its
            pharmacy using a private one-use code.
          </p>
        </div>
        {canConnect && (
          <Button className="primary" onClick={() => navigate("laptops")}>
            <KeyRound size={17} />
            Pair a laptop
          </Button>
        )}
      </div>

      <div className="downloads-availability" role="status">
        <PackageCheck size={21} />
        <div>
          <strong>
            Connection utility available now — this is not an installer
          </strong>
          <p>
            The ZIP contains two Python files that securely report a laptop as
            online. It does not install the complete AisleSignals desktop app,
            select CCTV, start monitoring or configure its speaker.
          </p>
        </div>
      </div>

      <div className="download-grid">
        {platforms.map((platform) => (
          <article className="download-card" key={platform.name}>
            <header>
              <span className="download-platform-icon">
                <platform.icon size={25} />
              </span>
              <div>
                <span className="download-state available">
                  <Check size={12} /> Available
                </span>
                <h2>{platform.name} connection utility</h2>
              </div>
            </header>
            <p>
              Download one ZIP containing the AisleSignals companion and its
              secure credential adapter.
            </p>
            <dl className="download-details">
              <div>
                <dt>Requires</dt>
                <dd>{platform.requirement}</dd>
              </div>
              <div>
                <dt>Run from</dt>
                <dd>{platform.terminal}</dd>
              </div>
              <div>
                <dt>Starts with</dt>
                <dd>{platform.command}</dd>
              </div>
            </dl>
            <a
              className="button primary download-action"
              href="/downloads/cloud-companion.zip"
              download
              aria-label={`Download ${platform.name} connection utility ZIP`}
            >
              <Download size={17} />
              Download connection utility (.zip)
            </a>
          </article>
        ))}
      </div>

      <section className="panel downloads-steps-panel">
        <header className="panel-heading">
          <div>
            <span className="eyebrow">SECURE SETUP</span>
            <h3>Connect a pharmacy laptop</h3>
          </div>
          {canConnect && (
            <button className="text-button" onClick={() => navigate("laptops")}>
              Open Laptops
              <ArrowRight size={15} />
            </button>
          )}
        </header>
        <ol className="download-steps">
          <li>
            <span>1</span>
            <div>
              <strong>Download and extract</strong>
              <p>Keep both Python files together on the pharmacy laptop.</p>
            </div>
          </li>
          <li>
            <span>2</span>
            <div>
              <strong>Create a private code</strong>
              <p>
                In Laptops, choose the pharmacy, laptop name and operating
                system. The code expires after ten minutes.
              </p>
            </div>
          </li>
          <li>
            <span>3</span>
            <div>
              <strong>Run the shown commands</strong>
              <p>Paste the one-use code only when the utility asks for it.</p>
            </div>
          </li>
          <li>
            <span>4</span>
            <div>
              <strong>Confirm the heartbeat</strong>
              <p>
                Return to Laptops and check that the connection changes to
                Online.
              </p>
            </div>
          </li>
        </ol>
      </section>

      <section
        className="installer-roadmap"
        aria-labelledby="installer-heading"
      >
        <div className="installer-roadmap-copy">
          <span className="download-platform-icon muted-icon">
            <Laptop size={24} />
          </span>
          <div>
            <span className="eyebrow">DESKTOP APPLICATION</span>
            <h2 id="installer-heading">
              Desktop installation files are not released yet
            </h2>
            <p>
              The macOS .dmg and Windows .msi still need signing, automatic
              updates and branch-laptop acceptance before customer download.
              This page will publish the real installers when those checks pass.
            </p>
          </div>
        </div>
        <div className="installer-checks" aria-label="Installer release checks">
          <span>
            <TerminalSquare size={16} /> macOS .dmg: not released
          </span>
          <span>
            <ShieldCheck size={16} /> Windows .msi: not released
          </span>
        </div>
      </section>
    </>
  );
}
