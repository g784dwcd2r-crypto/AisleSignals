import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";

export default function AuthenticatorQrCode({ uri }: { uri: string }) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const target = canvas.current;
    if (!target) return;

    let cancelled = false;
    setFailed(false);
    void QRCode.toCanvas(target, uri, {
      errorCorrectionLevel: "M",
      margin: 2,
      width: 196,
      color: { dark: "#294331", light: "#ffffff" },
    }).catch(() => {
      if (!cancelled) setFailed(true);
    });

    return () => {
      cancelled = true;
      const context = target.getContext("2d");
      context?.clearRect(0, 0, target.width, target.height);
    };
  }, [uri]);

  if (failed) {
    return (
      <p className="muted small" role="status">
        The QR code could not be displayed. Use the setup key below instead.
      </p>
    );
  }

  return (
    <figure className="authenticator-qr">
      <canvas
        ref={canvas}
        role="img"
        aria-label="Authenticator setup QR code"
      />
      <figcaption>Scan with your authenticator app</figcaption>
    </figure>
  );
}
