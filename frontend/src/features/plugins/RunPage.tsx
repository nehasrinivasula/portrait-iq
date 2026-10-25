import { useJobs } from "../jobs/JobsContext";
import JobPanel from "./JobPanel";
import PluginPicker from "./PluginPicker";
import { isFinished } from "./api";

// While a run is going, show only its progress. Once it ends, the picker comes
// back for the next run and the finished run stays below as "Last run".
export default function RunPage() {
  const { job, track } = useJobs();
  const running = job !== null && !isFinished(job.status);
  return (
    <>
      <h1>Run diagnostics</h1>
      {running ? (
        <JobPanel />
      ) : (
        <>
          <PluginPicker onStarted={track} />
          {job && (
            <section aria-labelledby="last-run" style={{ marginTop: 36 }}>
              <h2 id="last-run" style={{ fontSize: 22 }}>Last run</h2>
              <JobPanel />
            </section>
          )}
        </>
      )}
    </>
  );
}
