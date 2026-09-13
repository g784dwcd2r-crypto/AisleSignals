import { useState } from "react";
import { ExternalLink, Film } from "lucide-react";

export default function PharmacyVideoReference() {
  const [loaded, setLoaded] = useState(false);
  return (
    <section
      className="panel pharmacy-reference"
      aria-labelledby="pharmacy-reference-title"
    >
      <div className="panel-header">
        <div>
          <h2 id="pharmacy-reference-title">Real pharmacy CCTV reference</h2>
          <p>Published by the FBI on YouTube · actual robbery footage</p>
        </div>
        <Film size={23} />
      </div>
      <div className="playback-alarm-body">
        <h3>
          Seeking Information in Pharmaceutical Robberies in Maryland and
          Pennsylvania
        </h3>
        <p>
          Use this published footage to discuss the pharmacy workflow. The
          source describes real robberies; this is not a staged demonstration.
        </p>
        <div className="notice amber">
          <span>
            <strong>Reference playback only.</strong> A YouTube player cannot be
            scanned by the local analyser. No detection, classification, alarm
            or log is generated from this embedded video.
          </span>
        </div>
        {loaded ? (
          <>
            <iframe
              title="FBI pharmacy CCTV reference — no automatic analysis"
              src="https://www.youtube-nocookie.com/embed/TkS5CyFuumI?autoplay=0"
              referrerPolicy="strict-origin-when-cross-origin"
              allow="encrypted-media; picture-in-picture; fullscreen"
              allowFullScreen
            />
            <button
              className="button secondary"
              onClick={() => setLoaded(false)}
            >
              Close reference player
            </button>
          </>
        ) : (
          <button className="button secondary" onClick={() => setLoaded(true)}>
            <Film size={16} />
            Load YouTube reference
          </button>
        )}
        <p className="muted">
          Loading the player connects to YouTube. If playback is unavailable
          here, open the original video.
        </p>
        <div className="video-test-actions">
          <a
            className="button secondary"
            href="https://www.youtube.com/watch?v=TkS5CyFuumI"
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={16} />
            Watch on YouTube
          </a>
          <a
            className="button secondary"
            href="https://www.fbi.gov/video-repository/seeking-information-in-pharmaceutical-robberies-070920.mp4/view"
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={16} />
            Publisher’s video & download page
          </a>
        </div>
        <p className="muted">
          To run a real frame scan, choose an authorised MP4 or WebM file above.
          Use the publisher’s Download Video File link if available, then choose
          the downloaded file above. Video access is controlled by the
          publisher; this app does not extract or redistribute YouTube footage.
        </p>
      </div>
    </section>
  );
}
