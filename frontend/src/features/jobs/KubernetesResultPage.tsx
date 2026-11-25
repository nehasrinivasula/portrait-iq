import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJob, getResults, isFinished, type Job, type ToolResult } from "../plugins/api";
import { runHeadline } from "../plugins/progress";
import { stepLabel } from "./runStatus";
import { kubeDetails, nodeState, podProblem, shownOf, when } from "./kubernetesResults";
import "./jobResults.css";

const POLL_MS = 2000;
const TOOL = "kubernetes";
const fileName = (path: string) => path.split(/[\\/]/).pop() || path;

// Kubernetes diagnostics for one run: nodes, pods that restarted or aren't ready, warning events.
export default function KubernetesResultPage() {
  const { jobId = "" } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [results, setResults] = useState<ToolResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadKey, setLoadKey] = useState(0);

  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    async function refresh() {
      try {
        const [j, r] = await Promise.all([getJob(encodeURIComponent(jobId)), getResults(encodeURIComponent(jobId))]);
        if (!live) return;
        setJob(j); setResults(r); setError(null);
        if (!isFinished(j.status)) timer = window.setTimeout(refresh, POLL_MS);
      } catch {
        if (live) setError("Couldn't load this run. Check the link, or try again.");
      }
    }
    void refresh();
    return () => { live = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [jobId, loadKey]);

  const mine = results?.find((r) => r.tool === TOOL);
  const others = (results ?? []).filter((r) => r.tool !== TOOL);
  const running = job !== null && !isFinished(job.status);
  const [kind, headline] = job ? runHeadline(job.status) : ["info", ""];
  const failed = mine !== undefined && "error" in mine;
  const ok = mine !== undefined && !("error" in mine) ? mine : null;
  const d = ok ? kubeDetails(ok) : null;

  return (
    <div className="job-results">
      <Link to="/run">Back to Run diagnostics</Link>
      <h1>Kubernetes diagnostics</h1>
      <p className="muted">Run <code>{jobId.slice(0, 8)}</code> · a read-only snapshot of the cluster's nodes, pods and events.</p>

      {error && (
        <div className="alert error" role="alert">
          <strong>{error}</strong>
          <button type="button" className="btn ghost" onClick={() => setLoadKey((k) => k + 1)}>Try again</button>
        </div>
      )}
      {!error && !job && <p className="muted" role="status">Loading…</p>}
      {job && (
        <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"}>
          <strong>{headline}</strong>
          {running && job.current_step && stepLabel(job.current_step)}
        </div>
      )}

      {failed && mine && "error" in mine && (
        <div className="alert error" role="alert">
          <strong>Kubernetes diagnostics could not run{stepLabel(mine.step) ? ` while ${stepLabel(mine.step)!.toLowerCase()}` : ""}</strong>
          {mine.error}
        </div>
      )}
      {!mine && job && !error && (
        <p className="muted">{running ? "Kubernetes diagnostics has not finished yet…"
          : "Kubernetes diagnostics produced no result in this run. It may not have been selected, or the run stopped before it started."}</p>
      )}

      {ok && <h2>{ok.summary || "Kubernetes diagnostics finished"}</h2>}

      {d && d.notes.length > 0 && (
        <section aria-labelledby="k8s-notes">
          <h2 id="k8s-notes">What couldn't be read</h2>
          <ol className="job-results-list">
            {d.notes.map((n) => <li key={n} className="job-check warn"><span>{n}</span><strong>Note</strong></li>)}
          </ol>
        </section>
      )}

      {d && (
        <section aria-labelledby="k8s-nodes">
          <h2 id="k8s-nodes">Nodes</h2>
          {d.nodes.length === 0 ? <p className="muted">No node status was read.</p> : (
            <table className="log-table">
              <thead><tr><th scope="col">Node</th><th scope="col">State</th><th scope="col">Roles</th><th scope="col">Kubelet</th><th scope="col">CPU</th><th scope="col">Memory</th></tr></thead>
              <tbody>
                {d.nodes.map((n) => (
                  <tr key={n.name}>
                    <td>{n.name}</td>
                    <td>{nodeState(n)}</td>
                    <td>{n.roles.join(", ")}</td>
                    <td>{n.kubelet_version ?? "-"}</td>
                    <td>{n.cpu_capacity ?? "-"}</td>
                    <td>{n.memory_capacity ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {d && (
        <section aria-labelledby="k8s-namespaces">
          <h2 id="k8s-namespaces">Namespaces</h2>
          <table className="log-table">
            <thead><tr><th scope="col">Namespace</th><th scope="col">Pods</th><th scope="col">Not ready</th><th scope="col">Restarted</th><th scope="col">Warning events</th></tr></thead>
            <tbody>
              {d.namespaces.map((ns) => (
                <tr key={ns.name}>
                  <td>{ns.name}</td>
                  {ns.read ? (<>
                    <td>{ns.pods}</td><td>{ns.not_ready}</td><td>{ns.restarted}</td><td>{ns.warning_events}</td>
                  </>) : <td colSpan={4}>Not read (see above)</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {d && (
        <section aria-labelledby="k8s-pods">
          <h2 id="k8s-pods">Pods that restarted or aren't ready</h2>
          {d.problem_pods.length === 0 ? <p className="muted">None: every pod read is ready and has not restarted.</p> : (<>
            <ol className="job-results-list">
              {d.problem_pods.map((p) => (
                <li key={`${p.namespace}/${p.name}`} className={`job-check ${p.restarts ? "warn" : "fail"}`}>
                  <span>{p.namespace}/{p.name}</span>
                  <strong>{p.restarts ? `${p.restarts} restart${p.restarts === 1 ? "" : "s"}` : "Not ready"}</strong>
                  <p>{podProblem(p)}{p.last_finished ? `, at ${when(p.last_finished)}` : ""}{p.node ? ` · node ${p.node}` : ""}</p>
                </li>
              ))}
            </ol>
            {shownOf(d.problem_pods.length, d.problem_pods_total) && <p className="muted">{shownOf(d.problem_pods.length, d.problem_pods_total)} The full list is in describe.txt in the export.</p>}
          </>)}
        </section>
      )}

      {d && (
        <section aria-labelledby="k8s-events">
          <h2 id="k8s-events">Warning events, newest first</h2>
          {d.warning_events.length === 0 ? <p className="muted">No warning events.</p> : (<>
            <table className="log-table">
              <thead><tr><th scope="col">Last seen</th><th scope="col">Where</th><th scope="col">Reason</th><th scope="col">Count</th><th scope="col">Message</th></tr></thead>
              <tbody>
                {d.warning_events.map((e, i) => (
                  <tr key={`${i}-${e.object}-${e.reason}`}>
                    <td>{when(e.last_seen)}</td>
                    <td>{e.namespace}/{e.object}</td>
                    <td>{e.reason ?? "-"}</td>
                    <td>{e.count}</td>
                    <td>{e.message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {shownOf(d.warning_events.length, d.warning_events_total) && <p className="muted">{shownOf(d.warning_events.length, d.warning_events_total)} All kept events are in events.txt in the export.</p>}
          </>)}
        </section>
      )}

      {ok && ok.artifact_paths.length > 0 && (
        <section aria-labelledby="k8s-files">
          <h2 id="k8s-files">Saved files</h2>
          <ol className="job-results-list">
            {ok.artifact_paths.map((p) => <li key={p} className="job-check pass"><span>{fileName(p)}</span><strong>Saved</strong></li>)}
          </ol>
          <p className="muted">These go in the export zip under tools/kubernetes/.</p>
        </section>
      )}

      {others.length > 0 && (
        <section aria-labelledby="others-head">
          <h2 id="others-head">Other tools in this run</h2>
          <ol className="job-results-list">
            {others.map((r) => (
              <li key={r.tool} className={`job-check ${"error" in r ? "fail" : "pass"}`}>
                <Link to={`/jobs/${encodeURIComponent(jobId)}/results/${encodeURIComponent(r.tool)}`}>{r.tool}</Link>
                <strong>{"error" in r ? "Fail" : "Pass"}</strong>
              </li>
            ))}
          </ol>
        </section>
      )}
      {job && isFinished(job.status) && <p><Link to={`/jobs/${encodeURIComponent(jobId)}/export?from=run`}>Export results</Link></p>}
    </div>
  );
}
