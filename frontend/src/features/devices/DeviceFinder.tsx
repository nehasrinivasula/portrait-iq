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
      const r = await discover(length);
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
        <span className="filter-label" id={`${uid}-len`}>Find devices on this system</span>
        <RadioGroup.Root className="segmented" aria-labelledby={`${uid}-len`} value={String(length)}
          onValueChange={(v) => setLength(Number(v))} disabled={busy}>
          {SEARCH_LENGTHS.map((l) => (
            <RadioGroup.Item key={l.s} value={String(l.s)} className="seg"><span>{l.label}</span></RadioGroup.Item>
          ))}
        </RadioGroup.Root>
        <button type="button" className="btn" onClick={search} disabled={busy}>
          {busy ? "Searching…" : "Find devices"}
        </button>
      </div>
      <div className="hint" role="status">
        {busy ? `Watching the system's network for about ${length} seconds…`
          : "Lists the hub, viewers and other devices the system talks to. Only addresses and ports are kept."}
      </div>

      {error && <div className="alert error" role="alert"><strong>Couldn't search for devices</strong>{error}</div>}
      {notes.map((n) => <div key={n} className="alert warn" role="status">{n}</div>)}

      {list.length > 0 && (
        <ul className="device-list" aria-label="Devices found">
          {list.map((d) => (
            <li key={d.id} className={d.ip_address === selectedIp.trim() ? "device selected" : "device"}>
              <span className={`kind kind-${d.kind}`}>{KIND_LABELS[d.kind]}</span>
              <span className="device-main">
                <strong>{d.name}</strong> <code>{d.ip_address}</code>
                {d.evidence && d.evidence.length > 0 && <span className="muted device-why">{d.evidence.join(" · ")}</span>}
              </span>
              <span className="device-actions">
                <Label.Root htmlFor={`${uid}-k-${d.id}`} className="visually-hidden">Role of {d.ip_address}</Label.Root>
                <select id={`${uid}-k-${d.id}`} value={d.kind} onChange={(e) => setKind(d, e.target.value as DeviceKind)}>
                  {(Object.keys(KIND_LABELS) as DeviceKind[]).map((k) => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
                </select>
                <button type="button" className="btn ghost" onClick={() => onUse(d.ip_address)}
                  disabled={d.ip_address === selectedIp.trim()}>
                  {d.ip_address === selectedIp.trim() ? "Selected" : "Use this device"}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {devices && list.length === 0 && !busy && !error && notes.length === 0 && (
        <p className="muted">No devices found yet. Search to list what's on the network.</p>
      )}
    </div>
  );
}
