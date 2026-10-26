"""
Client for GE's Axone log export API: 8 GET endpoints on port 9200 that
return Portrait Mobile logs as JSON or CSV.

The API is only reachable from inside the Kubernetes cluster (no public
ingress). From a laptop, use kubectl port-forward plus an SSH tunnel and set
AXONE_BASE_URL=http://localhost:9200; from a pod in the cluster, use
http://axone-log-export-tcp.<namespace>.svc.cluster.local:9200.

Rules from the API guide, checked here before sending so the user gets a
clear message instead of a bare 400:
  - endTimestamp strictly after startTimestamp
  - by-device queries: at most 7 days
  - by-date queries: at most 1 hour, and both timestamps in the same hour
  - eventCategory is an integer code (no known name mapping yet)
  - timestamps are ISO-local with milliseconds and no zone,
    e.g. 2024-01-15T09:00:00.000
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.sources.base import SourceError

MAX_DEVICE_RANGE = timedelta(days=7)
MAX_DATE_RANGE = timedelta(hours=1)
CHUNK = 1024 * 1024


class AxoneLogExportClient:
    def __init__(self, base_url: str, *, timeout: float = 60, tz: str = "UTC",
                 transport: httpx.BaseTransport | None = None,
                 max_bytes: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.tz = ZoneInfo(tz)
        self.max_bytes = max_bytes
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    # --- by device (max 7 days) -------------------------------------------------

    def logs_by_device(self, device_uuid: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._get_json("/getLogsByDevice", self._device_params(device_uuid, start, end))

    def device_logs_csv(self, device_uuid: str, start: datetime, end: datetime, out: Path) -> Path:
        return self._stream_csv("/getSpecificDeviceLogsInCSV", self._device_params(device_uuid, start, end), out)

    def logs_by_device_and_event(self, device_uuid: str, event_category: int,
                                 start: datetime, end: datetime) -> list[dict[str, Any]]:
        params = self._device_params(device_uuid, start, end, event_category)
        return self._get_json("/getLogsByDeviceAndEvent", params)

    def device_and_event_logs_csv(self, device_uuid: str, event_category: int,
                                  start: datetime, end: datetime, out: Path) -> Path:
        params = self._device_params(device_uuid, start, end, event_category)
        return self._stream_csv("/getLogsByDeviceAndEventInCsv", params, out)

    # --- by date, all devices (max 1 hour, same hour) ------------------------------

    def logs_by_date(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._get_json("/getLogsByDate", self._date_params(start, end))

    def all_device_logs_csv(self, start: datetime, end: datetime, out: Path) -> Path:
        return self._stream_csv("/getAllDeviceLogsInCSV", self._date_params(start, end), out)

    def logs_by_date_and_event(self, event_category: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._get_json("/getLogsByDateAndEvent", self._date_params(start, end, event_category))

    def date_and_event_logs_csv(self, event_category: int, start: datetime, end: datetime, out: Path) -> Path:
        return self._stream_csv("/getLogsByDateAndEventInCsv", self._date_params(start, end, event_category), out)

    # --- parameters and rule checks -------------------------------------------------

    def format_ts(self, dt: datetime) -> str:
        """Aware datetime -> API timezone -> 2024-01-15T09:00:00.000 (no zone).
        Naive datetimes are taken as UTC, like the rest of the tool."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(self.tz)
        return local.strftime("%Y-%m-%dT%H:%M:%S.") + f"{local.microsecond // 1000:03d}"

    def _check_order(self, start: datetime, end: datetime) -> None:
        if end <= start:
            raise SourceError("The end time must be after the start time.")

    def _device_params(self, device_uuid: str, start: datetime, end: datetime,
                       event_category: int | None = None) -> dict[str, str]:
        if not device_uuid:
            raise SourceError("A device UUID is required for per-device log queries.")
        self._check_order(start, end)
        if end - start > MAX_DEVICE_RANGE:
            raise SourceError("Per-device log queries can cover at most 7 days.")
        params = {"deviceuuid": device_uuid, **self._range(start, end)}
        return self._with_category(params, event_category)

    def _date_params(self, start: datetime, end: datetime, event_category: int | None = None) -> dict[str, str]:
        self._check_order(start, end)
        if end - start > MAX_DATE_RANGE:
            raise SourceError("All-device log queries can cover at most 1 hour.")
        params = self._range(start, end)
        if params["startTimestamp"][:13] != params["endTimestamp"][:13]:
            raise SourceError("All-device log queries must start and end within the same hour (e.g. 09:00 to 09:59).")
        return self._with_category(params, event_category)

    def _range(self, start: datetime, end: datetime) -> dict[str, str]:
        return {"startTimestamp": self.format_ts(start), "endTimestamp": self.format_ts(end)}

    @staticmethod
    def _with_category(params: dict[str, str], event_category: int | None) -> dict[str, str]:
        if event_category is not None:
            try:
                params["eventCategory"] = str(int(event_category))
            except (TypeError, ValueError):
                raise SourceError("Event category must be a number (the API uses integer codes).") from None
        return params

    # --- HTTP ------------------------------------------------------------------------

    def _get_json(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        try:
            resp = self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise self._transport_error(e) from None
        self._raise_for_status(resp)
        try:
            return resp.json()
        except ValueError:
            raise SourceError(f"The log export API returned something that isn't JSON for {path}.") from None

    def _stream_csv(self, path: str, params: dict[str, str], out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        try:
            with self._http.stream("GET", path, params=params) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._raise_for_status(resp)
                with tmp.open("wb") as f:
                    received = 0
                    for chunk in resp.iter_bytes(CHUNK):
                        received += len(chunk)
                        if self.max_bytes is not None and received > self.max_bytes:
                            raise SourceError(
                                "The log export exceeds the configured size limit. Narrow the time range or scope."
                            )
                        f.write(chunk)
            tmp.replace(out)
        except httpx.HTTPError as e:
            tmp.unlink(missing_ok=True)
            raise self._transport_error(e) from None
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return out

    def _transport_error(self, e: httpx.HTTPError) -> SourceError:
        if isinstance(e, httpx.TimeoutException):
            return SourceError(f"The log export API took longer than {self.timeout:g} seconds to answer.")
        if isinstance(e, httpx.ConnectError):
            return SourceError(
                f"Can't reach the log export API at {self.base_url}. "
                "Is the port-forward / in-cluster address right?"
            )
        return SourceError(f"Network error talking to the log export API ({type(e).__name__}).")

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        if resp.status_code == 400:
            # The API returns a JSON array of message strings, e.g. ["Invalid timestamp Format"].
            try:
                body = resp.json()
                detail = "; ".join(str(m) for m in body) if isinstance(body, list) else str(body)
            except ValueError:
                detail = ""
            raise SourceError(f"The log export API rejected the request: {detail or 'bad request'}.")
        if resp.status_code >= 500:
            raise SourceError("The log export service had an internal error. Try again later.")
        raise SourceError(f"The log export API returned HTTP {resp.status_code}.")
