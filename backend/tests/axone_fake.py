"""A fake Axone log export API for tests, built on httpx.MockTransport.

It enforces the rules from the API guide (timestamp format, end after start,
1 hour + same hour for by-date queries, 7 days for by-device queries, integer
eventCategory) and answers 400 with a JSON list of messages like the real
service, so a client that breaks a rule fails here too. No network."""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

CSV_HEADER = ("timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
              "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$")
CATEGORIES = {1: "Security", 3: "Exception", 6: "General"}

DATE_PATHS = {"/getLogsByDate", "/getAllDeviceLogsInCSV", "/getLogsByDateAndEvent", "/getLogsByDateAndEventInCsv"}
DEVICE_PATHS = {"/getLogsByDevice", "/getSpecificDeviceLogsInCSV", "/getLogsByDeviceAndEvent",
                "/getLogsByDeviceAndEventInCsv"}
EVENT_PATHS = {"/getLogsByDeviceAndEvent", "/getLogsByDeviceAndEventInCsv", "/getLogsByDateAndEvent",
               "/getLogsByDateAndEventInCsv"}
CSV_PATHS = {"/getSpecificDeviceLogsInCSV", "/getLogsByDeviceAndEventInCsv", "/getAllDeviceLogsInCSV",
             "/getLogsByDateAndEventInCsv"}


@dataclass
class Row:
    when: datetime            # naive, in the API's (UTC) clock
    device: str = "d4e5f6a7-1234-5678-9abc-def012345678"
    category: int = 6
    level: str = "INFO"
    message: str = "Heartbeat received"


@dataclass
class FakeAxone:
    rows: list[Row] = field(default_factory=list)
    fail_hours: set[datetime] = field(default_factory=set)   # hour starts that answer 500
    requests: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, path: str | None = None) -> list[httpx.Request]:
        return [r for r in self.requests if path is None or r.url.path == path]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, q = request.url.path, dict(request.url.params)
        errors = []
        for name in ("startTimestamp", "endTimestamp"):
            if not TS.match(q.get(name, "")):
                errors.append("Invalid timestamp Format")
        if errors:
            return httpx.Response(400, json=sorted(set(errors)))
        start = datetime.fromisoformat(q["startTimestamp"])
        end = datetime.fromisoformat(q["endTimestamp"])
        if end <= start:
            errors.append("endTimestamp must be after startTimestamp")
        if path in DATE_PATHS:
            if end - start > timedelta(hours=1):
                errors.append("Time range too large")
            if start.hour != end.hour:
                errors.append("Hour of startTimestamp and endTimestamp must match")
        elif path in DEVICE_PATHS:
            if not q.get("deviceuuid"):
                errors.append("deviceuuid is required")
            if end - start > timedelta(days=7):
                errors.append("Time range too large")
        else:
            return httpx.Response(404, json=["Not found"])
        category = None
        if path in EVENT_PATHS:
            try:
                category = int(q.get("eventCategory", ""))
            except ValueError:
                errors.append("Unrecognized eventCategory")
        if errors:
            return httpx.Response(400, json=errors)
        if start.replace(minute=0, second=0, microsecond=0) in self.fail_hours:
            return httpx.Response(500, text="db down")

        picked = [r for r in self.rows
                  if start <= r.when <= end
                  and (category is None or r.category == category)
                  and (not q.get("deviceuuid") or r.device == q["deviceuuid"])]
        if path in CSV_PATHS:
            lines = [CSV_HEADER] + [
                f"{r.when.isoformat(timespec='milliseconds')}+00:00,1,1,{r.level},axone-node-01,{r.device},"
                f"Hub-01,axone-log-export:4521:main,Svc.java:10,{CATEGORIES.get(r.category, 'General')},{r.message}"
                for r in picked
            ]
            return httpx.Response(200, text="\n".join(lines) + "\n", headers={"content-type": "text/csv"})
        body = [{"timeStamp": r.when.isoformat() + "Z", "sourceUUID": r.device, "logLevel": r.level,
                 "eventCategory": CATEGORIES.get(r.category, "General"), "description": r.message}
                for r in picked]
        return httpx.Response(200, content=json.dumps(body), headers={"content-type": "application/json"})
