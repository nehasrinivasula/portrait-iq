import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Package } from "./exportApi";
import { formatBytes, type ReturnTarget } from "./exportView";
import { parseServerTime } from "../plugins/progress";

interface Props {
  pkg: Package;
  fileName: string;
  back: ReturnTarget;
  onClose: () => void;
}

/** Confirms a finished download with what was saved, so the engineer can check
 * the file before sharing it. A native modal dialog: focus moves in, Esc closes it. */
export default function DownloadDialog({ pkg, fileName, back, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [copied, setCopied] = useState(false);

  // No close() on cleanup: it fires "close", which would unmount the dialog
  // straight after React's development double-mount. Removing it is enough.
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  const copy = async () => {
    if (!pkg.sha256) return;
    try {
      await navigator.clipboard.writeText(pkg.sha256);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const exportedAt = parseServerTime(pkg.finished_at);
  return (
    <dialog ref={ref} className="export-dialog" aria-labelledby="dl-title" onClose={onClose}>
      <h2 id="dl-title">Download complete</h2>
      <p className="muted">Saved to your browser's downloads folder.</p>
      <dl className="export-facts">
        <dt>File</dt><dd><code>{fileName}</code></dd>
        <dt>Size</dt><dd>{formatBytes(pkg.size_bytes)}</dd>
        <dt>SHA-256</dt>
        <dd>
          <code className="export-hash">{pkg.sha256}</code>{" "}
          <button type="button" className="btn ghost small" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
          <span className="hint">Compare it with the file you send, to be sure it arrived unchanged.</span>
        </dd>
        <dt>PHI / PII</dt><dd>{pkg.masked ? "Flagged values are masked." : "Not masked: the zip holds flagged values as they are."}</dd>
        <dt>Exported</dt><dd>{exportedAt ? new Date(exportedAt).toLocaleString() : "—"} by {pkg.requested_by}</dd>
      </dl>
      {pkg.warnings.length > 0 && (
        <div className="alert warn" role="status">
          <strong>Check before sharing</strong>
          <ul className="export-list">{pkg.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      <div className="export-actions">
        <Link to={back.to} className="btn" autoFocus>{back.label}</Link>
        <button type="button" className="btn ghost" onClick={() => ref.current?.close()}>Stay on this page</button>
      </div>
    </dialog>
  );
}
