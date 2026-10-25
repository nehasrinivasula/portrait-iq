import { useJobs } from "../jobs/JobsContext";
import JobPanel from "./JobPanel";
import PluginPicker from "./PluginPicker";

export default function RunPage() {
  const { job, track } = useJobs();
  return (
    <>
      <h1>Run diagnostics</h1>
      {job ? <JobPanel /> : <PluginPicker onStarted={track} />}
    </>
  );
}
