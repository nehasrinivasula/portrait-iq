import { useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import GetLogs from "./GetLogs";
import LogTable from "./LogTable";
import { USE_MOCK } from "./logsApi";

export default function LogsPage() {
  const [tab, setTab] = useState("view");
  return (
    <>
      <h1>Logs</h1>
      {USE_MOCK && (
        <div className="alert warn" role="status">
          <strong>Sample data</strong>The log table is showing made-up events until the log search service is connected.
        </div>
      )}
      <Tabs.Root value={tab} onValueChange={setTab}>
        <Tabs.List className="tabs-list" aria-label="Logs">
          <Tabs.Trigger className="tabs-trigger" value="view">View logs</Tabs.Trigger>
          <Tabs.Trigger className="tabs-trigger" value="get">Get logs</Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="view"><LogTable /></Tabs.Content>
        <Tabs.Content value="get"><GetLogs /></Tabs.Content>
      </Tabs.Root>
    </>
  );
}
