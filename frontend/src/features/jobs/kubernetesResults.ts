// The Kubernetes diagnostics tool's structured result (backend: collectors/k8s_diagnostics.py, `details`).

export type KubeNode = {
  name: string;
  ready: boolean;
  ready_reason: string | null;
  pressure: string[];
  roles: string[];
  kubelet_version: string | null;
  cpu_capacity: string | null;
  memory_capacity: string | null;
};

export type KubeProblemPod = {
  namespace: string;
  name: string;
  phase: string;
  ready: string;            // "1/2"
  restarts: number;
  last_reason: string | null;
  last_exit_code: number | null;
  last_finished: string | null;
  node: string | null;
};

export type KubeEvent = {
  namespace: string;
  object: string;           // "Pod/axonerouter-1"
  reason: string | null;
  count: number;
  last_seen: string | null;
  message: string | null;   // personal data already masked by the backend
};

export type KubeNamespace = {
  name: string;
  read: boolean;
  pods: number;
  not_ready: number;
  restarted: number;
  warning_events: number;
};

export type KubeDetails = {
  namespaces: KubeNamespace[];
  nodes: KubeNode[];
  problem_pods: KubeProblemPod[];
  problem_pods_total: number;
  warning_events: KubeEvent[];
  warning_events_total: number;
  notes: string[];
};

/** The tool's details, or null when the result has none (failed run, older result). */
export function kubeDetails(result: unknown): KubeDetails | null {
  const d = (result as { details?: Partial<KubeDetails> } | undefined)?.details;
  if (!d || !Array.isArray(d.namespaces)) return null;
  return {
    namespaces: d.namespaces ?? [],
    nodes: d.nodes ?? [],
    problem_pods: d.problem_pods ?? [],
    problem_pods_total: d.problem_pods_total ?? (d.problem_pods ?? []).length,
    warning_events: d.warning_events ?? [],
    warning_events_total: d.warning_events_total ?? (d.warning_events ?? []).length,
    notes: d.notes ?? [],
  };
}

/** "Ready", or "Not ready (KubeletNotReady)", plus any pressure. */
export function nodeState(n: KubeNode): string {
  const base = n.ready ? "Ready" : `Not ready${n.ready_reason ? ` (${n.ready_reason})` : ""}`;
  return n.pressure.length ? `${base}, ${n.pressure.join(", ")}` : base;
}

/** "Restarted 3 times, last: OOMKilled (exit 137)" or "Not ready, no restarts". */
export function podProblem(p: KubeProblemPod): string {
  if (!p.restarts) return `Not ready (${p.ready} containers ready, ${p.phase})`;
  const times = `Restarted ${p.restarts} time${p.restarts === 1 ? "" : "s"}`;
  if (!p.last_reason) return times;
  return `${times}, last: ${p.last_reason}${p.last_exit_code != null ? ` (exit ${p.last_exit_code})` : ""}`;
}

/** Local date and time for an ISO timestamp, or a dash. */
export function when(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** "Showing 50 of 120" when the backend kept fewer rows than exist. */
export function shownOf(shown: number, total: number): string | null {
  return total > shown ? `Showing ${shown} of ${total}.` : null;
}
