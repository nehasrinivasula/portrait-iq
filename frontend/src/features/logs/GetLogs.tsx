import { useEffect, useMemo, useState } from "react";
import * as RadioGroup from "@radix-ui/react-radio-group";
import { ApiError } from "../../api";
import TimeFramePicker from "./TimeFramePicker";
import { listSources, previewFiles, type RemoteFile, type Source } from "./sourcesApi";
import { defaultTimeFrame, resolveTimeFrame, toApi } from "./timeframe";

const mb = (n: number | null) => (n === null ? "" : `${(n / 1_048_576).toFixed(1)} MB`);

// Time frame and a read-only preview of what a pull would fetch. The pull
// itself (acquire, local upload) belongs to SCRUM-84.
export default function GetLogs() {
  const [frame, setFrame] = useState(defaultTimeFrame);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [sourceId, setSourceId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<RemoteFile[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listSources()
      .then((s) => { setSources(s); setSourceId(s.find((x) => x.configured)?.id ?? ""); })
      .catch(() => setError("Couldn't load the log sources. Check the connection to the server."));
  }, []);

  const resolved = useMemo(() => resolveTimeFrame(frame), [frame]);

  async function preview() {
    if (!resolved.ok || !sourceId) return;
    setBusy(true); setError(null); setFiles(null);
    try {
      const w = toApi(resolved);
      setFiles(await previewFiles(sourceId, w.since, w.until));
    } catch (e) {
      setError(e instanceof ApiError && e.status !== 0 ? e.message : "Couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 26, maxWidth: 760 }}>
      <TimeFramePicker value={frame} onChange={(f) => { setFrame(f); setFiles(null); }} />

      <section aria-labelledby="src-h">
        <h2 id="src-h" style={{ fontSize: 20 }}>Where from</h2>
        {!sources && !error && <p className="muted" role="status">Loading sources…</p>}
        {sources && (
          <RadioGroup.Root className="source-list" aria-labelledby="src-h" value={sourceId}
            onValueChange={(v) => { setSourceId(v); setFiles(null); }}>
            {sources.map((s) => (
              <RadioGroup.Item key={s.id} value={s.id} disabled={!s.configured} className="seg" style={{ textAlign: "left" }}>
                <span>{s.display_name}</span>
                {!s.configured && <span className="muted"> · Not set up{s.reason ? `: ${s.reason}` : ""}</span>}
              </RadioGroup.Item>
            ))}
          </RadioGroup.Root>
        )}
      </section>

      {error && <div className="alert error" role="alert"><strong>Something went wrong</strong>{error}</div>}

      <div>
        <button className="btn" disabled={!resolved.ok || !sourceId || busy} onClick={preview}>
          {busy ? "Checking…" : "Preview matching files"}
        </button>
      </div>

      {files && (
        <section aria-live="polite">
          <p><strong>{files.length}</strong> file{files.length === 1 ? "" : "s"} in this time frame
            {files.some((f) => f.already_acquired) && `, ${files.filter((f) => f.already_acquired).length} already pulled`}.</p>
          <ul>{files.slice(0, 20).map((f) => <li key={f.remote_id}>{f.name} <span className="muted">{mb(f.size)}</span></li>)}</ul>
          {files.length > 20 && <p className="muted">…and {files.length - 20} more.</p>}
        </section>
      )}
    </div>
  );
}
