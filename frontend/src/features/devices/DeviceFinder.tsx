import { useEffect, useId, useState } from "react";
import * as Label from "@radix-ui/react-label";
import * as RadioGroup from "@radix-ui/react-radio-group";
import { ApiError } from "../../api";
import {
  discover, KIND_LABELS, listDevices, SEARCH_LENGTHS, sortDevices, updateDevice,
  type Device, type DeviceKind,
} from "./devicesApi";
import "./devices.css";

// "Find devices" on the Run page (D-3): lists the hub, viewers and other
// devices on the system's network, so the engineer picks one instead of
// typing its address. Roles are a best guess; the engineer can correct
// them (D-7) and later searches keep the correction.
export default function DeviceFinder({ selectedIp, onUse }: { selectedIp: string; onUse: (ip: string) => void }) {
  const uid = useId();
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [length, setLength] = useState<number>(SEARCH_LENGTHS[0].s);
  const [sweep, setSweep] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);

  // Devices from earlier searches, so the list is there right away.
  useEffect(() => {
    listDevices().then((d) => setDevices(d.filter((x) => x.reachable))).catch(() => setDevices([]));
  }, []);

  async function search() {
    setBusy(true); setError(null); setNotes([]);
    try {
      const r = await discover(length, sweep);
      setDevices(r.devices);
      setNotes(r.notes);
    } catch (e) {
      setError(e instanceof ApiError && e.status !== 0 ? e.message : "Can't reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function setKind(d: Device, kind: DeviceKind) {
    try {
      const saved = await updateDevice(d.id, { kind });
      setDevices((list) => list?.map((x) => (x.id === d.id ? { ...saved, evidence: x.evidence } : x)) ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save the change.");
    }
  }

  const list = devices ? sortDevices(devices) : [];

  return (
    <div className="device-finder">
      <div className="finder-head">
        <span className="filter-label">Devices on this system</span>
        <button type="button" className="btn ghost" onClick={search} disabled={busy}>
          {busy ? "Searching…" : list.length ? "Search again" : "Find devices"}
        </button>
      </div>
      {busy && <div className="hint" role="status">Watching the system's network for about {length} seconds…</div>}
      <details className="finder-options">
        <summary>Search options</summary>
        <div className="finder-options-body">
          <RadioGroup.Root className="segmented" aria-labelledby={`${uid}-len`} value={String(length)}
            onValueChange={(v) => setLength(Number(v))} disabled={busy}>
            <span className="visually-hidden" id={`${uid}-len`}>Search length</span>
            {SEARCH_LENGTHS.map((l) => (
              <RadioGroup.Item key={l.s} value={String(l.s)} className="seg"><span>{l.label}</span></RadioGroup.Item>
            ))}
          </RadioGroup.Root>
          <label className="sweep-option">
            <input type="checkbox" checked={sweep} disabled={busy} onChange={(e) => setSweep(e.target.checked)} />
            <span>
              Also check every address on the network
              <span className="muted"> Finds quiet devices by sending one small packet to each address. Use only where the site allows network scanning.</span>
            </span>
          </label>
        </div>
      </details>

      {error && <div className="alert error" role="alert"><strong>Couldn't search for devices</strong>{error}</div>}
      {notes.map((n) => <div key={n} className="alert warn" role="status">{n}</div>)}

      {list.length > 0 && (
        <ul className="device-list" aria-label="Devices found">
          {list.map((d) => (
            <li key={d.id} className={d.ip_address === selectedIp.trim() ? "device selected" : "device"}>
              <span className="device-main" title={d.evidence?.join(" · ") || undefined}>
                <strong>{d.name}</strong> <code>{d.ip_address}</code>
              </span>
              <span className="device-actions">
                <Label.Root htmlFor={`${uid}-k-${d.id}`} className="visually-hidden">Role of {d.ip_address}</Label.Root>
                <select id={`${uid}-k-${d.id}`} value={d.kind} onChange={(e) => setKind(d, e.target.value as DeviceKind)}>
                  {(Object.keys(KIND_LABELS) as DeviceKind[]).map((k) => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
                </select>
                <button type="button" className="btn ghost" onClick={() => onUse(d.ip_address)} aria-label={`Use ${d.name}, ${d.ip_address}`}
                  disabled={d.ip_address === selectedIp.trim()}>
                  {d.ip_address === selectedIp.trim() ? "Selected" : "Use"}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {devices && list.length === 0 && !busy && !error && notes.length === 0 && (
        <p className="muted">None found yet.</p>
      )}
    </div>
  );
}
