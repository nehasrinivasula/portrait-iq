import type { EventQuery, LogEvent, LogPage, Severity } from "./logsApi";

// Deterministic fake events so the table can be built and tested before the
// backend endpoint is merged. Synthetic text only; never real log content.
const SEVERITIES: Severity[] = ["INFO", "INFO", "INFO", "DEBUG", "WARN", "ERROR"];
const DEVICES = ["viewer-01", "hub-01", "server-01"];
const PROCESSES = ["vitals-svc", "alarm-svc", "gateway", "kubelet"];
const MESSAGES = [
  "Heartbeat received from device",
  "Configuration reloaded",
  "Connection to gateway timed out after 30s",
  "Alarm limits updated for bed assignment",
  "Retrying upload of waveform batch (attempt 2)",
  "Certificate expires in 12 days",
];

const TOTAL = 420;
const base = Date.parse("2026-10-25T20:00:00Z");

const all: LogEvent[] = Array.from({ length: TOTAL }, (_, i) => {
  const sev = SEVERITIES[(i * 7) % SEVERITIES.length];
  return {
    id: TOTAL - i,
    timestamp: new Date(base - i * 97_000).toISOString(),
    device_id: DEVICES[i % DEVICES.length],
    source: i % 5 === 0 ? "kubernetes" : "portrait",
    category: i % 3 === 0 ? "PerfData" : "General",
    severity: sev,
    process: PROCESSES[i % PROCESSES.length],
    message: `${MESSAGES[(i * 3) % MESSAGES.length]} (#${TOTAL - i})`,
    source_file: "sample.csv",
    source_line: i + 1,
    job_id: null,
  };
});

export async function mockSearchEvents(q: EventQuery): Promise<LogPage> {
  await new Promise((r) => setTimeout(r, 250));
  const limit = q.limit ?? 100;
  const start = q.cursor ? Number(q.cursor) : 0;
  const items = all.slice(start, start + limit);
  const next = start + limit < all.length ? String(start + limit) : null;
  return { items, next_cursor: next, total: start === 0 ? all.length : null };
}
