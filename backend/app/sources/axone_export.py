"""
Log source for GE's Axone log export API (see axone_client.py). This is the
preferred way to get logs off a Portrait Mobile system: no website clicking,
no manual download, no Keycloak login.

How a pull works:
  - The requested range is split into windows the API accepts: 1 hour for
    all-device queries (always HH:00:00.000 to HH:59:59.999, because both
    timestamps must be in the same hour), up to 7 days for one device.
  - Only complete, settled windows are pulled. An hour is pulled once it has
    ended plus AXONE_SETTLE_MINUTES (default 15), so at 15:10 the 14:00 hour
    is left for later and at 15:20 it is pulled. Pulling earlier would record
    the hour as done and miss logs devices upload a little late.
  - Each window is one CSV request, saved as a file and handed to the normal
    acquisition flow, so the manifest skips windows already pulled and
    retries windows that failed.
  - Requests go one at a time with a short pause, to keep load off the live
    system that is monitoring patients.
"""
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from app.config import settings
from app.sources.axone_client import MAX_DEVICE_RANGE, AxoneLogExportClient
from app.sources.base import LogSource, RemoteFile, SourceError, SourceOptions, SourceStatus

ONE_MS = timedelta(milliseconds=1)


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _scope(device_uuid: str | None, category: int | None) -> str:
    if device_uuid and category is not None:
        return f"dev{device_uuid}-cat{category}"
    if device_uuid:
        return f"dev{device_uuid}"
    if category is not None:
        return f"cat{category}"
    return "alldevices"


class AxoneLogExportSource(LogSource):
    id = "axone"
    display_name = "Portrait log export API"
    needs_network = True

    def __init__(self, client_factory: Callable[[], AxoneLogExportClient] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        # Settings are read at call time so the tool starts without them.
        # sleep and clock are injectable so tests need no real waiting or time.
        self._client_factory = client_factory
        self._client: AxoneLogExportClient | None = None
        self._sleep = sleep
        self._clock = clock

    @property
    def default_lookback(self) -> timedelta:  # type: ignore[override]
        # 24 one-hour calls by default; the global 30 days would be 720.
        return timedelta(hours=settings.axone_default_lookback_hours)

    def is_configured(self) -> SourceStatus:
        if self._client_factory is None and not settings.axone_base_url:
            return SourceStatus(ok=False, reason="Not set up: set AXONE_BASE_URL (e.g. http://localhost:9200).")
        return SourceStatus(ok=True)

    def _get_client(self) -> AxoneLogExportClient:
        if self._client is None:
            status = self.is_configured()
            if not status.ok:
                raise SourceError(status.reason)
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                self._client = AxoneLogExportClient(
                    settings.axone_base_url,
                    timeout=settings.axone_timeout_seconds,
                    tz=settings.axone_timezone,
                )
        return self._client

    def test_connection(self) -> SourceStatus:
        """One cheap real call: all-device logs for the last complete minute."""
        try:
            client = self._get_client()
            end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - ONE_MS
            start = end.replace(second=0, microsecond=0)
            with tempfile.TemporaryDirectory() as tmp:
                client.all_device_logs_csv(start, end, Path(tmp) / "probe.csv")
        except SourceError as e:
            self._client = None  # rebuild next time, e.g. after the tunnel is fixed
            return SourceStatus(ok=False, reason=str(e))
        return SourceStatus(ok=True, reason=f"Connected to the log export API at {client.base_url}.")

    def options(self) -> SourceOptions:
        # Free-form; empty means all devices / all categories.
        return SourceOptions(fields={"device_uuid": [], "event_category": []})

    @staticmethod
    def _parse_options(options: dict[str, str]) -> tuple[str | None, int | None]:
        device = (options.get("device_uuid") or "").strip() or None
        raw = (options.get("event_category") or "").strip()
        if not raw:
            return device, None
        try:
            return device, int(raw)
        except ValueError:
            raise SourceError("Event category must be a number (the API uses integer codes).") from None

    def list_available(self, since: datetime, until: datetime, options: dict[str, str]) -> list[RemoteFile]:
        device, category = self._parse_options(options or {})
        scope = _scope(device, category)
        start = _floor_hour(since)
        # Complete, settled windows only (see module docstring).
        cutoff = self._clock() - timedelta(minutes=settings.axone_settle_minutes)
        end = min(_floor_hour(until), _floor_hour(cutoff))

        step = MAX_DEVICE_RANGE if device else timedelta(hours=1)
        windows: list[RemoteFile] = []
        cursor = start
        while cursor < end:
            window_end = min(cursor + step, end)
            windows.append(RemoteFile(
                source=self.id,
                remote_id=f"{scope}|{cursor.isoformat()}",
                name=f"axone_{scope}_{cursor.strftime('%Y-%m-%dT%H')}.csv",
                scope=scope,
                window_start=cursor,
                window_end=window_end - ONE_MS,
            ))
            cursor = window_end
        return windows

    def fetch(self, remote: RemoteFile, dest_dir: Path) -> Path:
        client = self._get_client()
        scope = remote.scope or "alldevices"
        device, category = self._scope_parts(scope)
        start, end = remote.window_start, remote.window_end
        if start is None or end is None:
            raise SourceError(f"{remote.name} has no time window.")
        out = dest_dir / remote.name
        try:
            if device and category is not None:
                client.device_and_event_logs_csv(device, category, start, end, out)
            elif device:
                client.device_logs_csv(device, start, end, out)
            elif category is not None:
                client.date_and_event_logs_csv(category, start, end, out)
            else:
                client.all_device_logs_csv(start, end, out)
        finally:
            # Pause between calls, success or not, to keep load off the live system.
            if settings.axone_request_delay_seconds > 0:
                self._sleep(settings.axone_request_delay_seconds)
        return out

    @staticmethod
    def _scope_parts(scope: str) -> tuple[str | None, int | None]:
        if scope == "alldevices":
            return None, None
        if scope.startswith("cat"):
            return None, int(scope[3:])
        device, _, cat = scope[3:].partition("-cat")
        return device, (int(cat) if cat else None)
