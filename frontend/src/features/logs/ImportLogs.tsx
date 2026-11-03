import { useRef, useState } from "react";
import { ApiError } from "../../api";
import { useNotify } from "../../ui/Notifications";
import { importLogFiles } from "./logsApi";

// Demo path: hand the tool log files you already have (local import). Real
// runs bring their logs in automatically.
export default function ImportLogs({ onImported }: { onImported: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const notify = useNotify();
  const [busy, setBusy] = useState(false);

  async function onPick(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      const r = await importLogFiles([...files]);
      const events = r.files.reduce((n, f) => n + (f.events ?? 0), 0);
      if (r.failed > 0)
        notify({ kind: "warn", title: "Some files couldn't be imported", body: r.files.find((f) => f.error)?.error ?? `${r.failed} failed.` });
      else
        notify({ kind: "ok", title: "Logs imported", body: `${r.new_files} new file${r.new_files === 1 ? "" : "s"}, ${events} events${r.already_acquired ? `, ${r.already_acquired} already imported` : ""}.` });
      onImported();
    } catch (e) {
      notify({ kind: "error", title: "Import failed", body: e instanceof ApiError && e.status !== 0 ? e.message : "Can't reach the server." });
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <>
      <input ref={input} type="file" multiple hidden onChange={(e) => onPick(e.target.files)} aria-label="Choose log files to import" />
      <button className="btn ghost" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? "Importing…" : "Import log files"}
      </button>
    </>
  );
}
