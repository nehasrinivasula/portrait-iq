"""
Log source for GE's Axone log export API (see axone_client.py). This is the
preferred way to get logs off a Portrait Mobile system: no website clicking,
no manual download, no Keycloak login.

How a pull works (SCRUM-44: exactly the window asked for, nothing outside it):
  - The requested range is cut into windows the API accepts, keeping its
    exact edges. All devices: cut at every hour boundary, because both
    timestamps must be in the same hour, so 09:30 to 11:15 becomes
    09:30-10:00, 10:00-11:00 and 11:00-11:15. One device: chunks of at most
    7 days.
  - The end is held back by AXONE_SETTLE_MINUTES (default 15): at 14:40 a
    pull reaches 14:25 at most, leaving time for logs devices upload late.
  - Each window is one CSV request, saved as a file and handed to the normal
    acquisition flow. The service skips time already pulled (by comparing
    windows, so overlapping requests never fetch the same minutes twice) and
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
from app.sources.base import (
    ONE_MS, LogSource, RemoteFile, SourceError, SourceOptions, SourceStatus, window_file,
)
from app.sources.devices import validate_device_id

# A pull for many devices is many requests (one per device per 7 days); cap
# it so one click can't hammer the live system. "All devices" has no cap.
MAX_DEVICES_PER_PULL = 50


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _to_ms(dt: datetime) -> datetime:
    """The API works in milliseconds; drop anything finer."""
    return dt.replace(microsecond=dt.microsecond // 1000 * 1000)


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

    def effective_until(self, until: datetime) -> datetime:
        return min(until, self._clock() - timedelta(minutes=settings.axone_settle_minutes))

    def hold_back_reason(self) -> str:
        return f"the last {settings.axone_settle_minutes} minutes are held back for late-arriving logs"

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
        # Free-form; empty means all devices / all categories. device_uuids is
        # a comma-separated list (the acquire request's device_uuids field).
        return SourceOptions(fields={"device_uuids": [], "event_category": []})

    @staticmethod
    def _parse_options(options: dict[str, str]) -> tuple[list[str], int | None]:
        """Devices (empty list = all devices) and event category."""
        raw_devices = ",".join(filter(None, [options.get("device_uuids"), options.get("device_uuid")]))
        devices: list[str] = []
        for part in raw_devices.split(","):
            if part.strip():
                device = validate_device_id(part)
                if device not in devices:
                    devices.append(device)
        if len(devices) > MAX_DEVICES_PER_PULL:
            raise SourceError(
                f"{len(devices)} devices selected; pull at most {MAX_DEVICES_PER_PULL} at a time, "
                "or choose all devices."
            )
        raw = (options.get("event_category") or "").strip()
        if not raw:
            return devices, None
        try:
            return devices, int(raw)
        except ValueError:
            raise SourceError("Event category must be a number (the API uses integer codes).") from None

    def list_available(self, since: datetime, until: datetime, options: dict[str, str]) -> list[RemoteFile]:
        devices, category = self._parse_options(options or {})
        start = _to_ms(since)
        end = _to_ms(self.effective_until(until))

        # All devices: one set of hour windows. Selected devices (SCRUM-70):
        # one set of up-to-7-day windows per device, each with its own scope,
        # so "already pulled" is tracked per device.
        windows: list[RemoteFile] = []
        for device in devices or [None]:
            scope = _scope(device, category)
            cursor = start
            while cursor < end:
                if device:
                    piece_end = min(cursor + MAX_DEVICE_RANGE, end)
                else:
                    # Stop at the next hour boundary: start and end must share an hour.
                    piece_end = min(_floor_hour(cursor) + timedelta(hours=1), end)
                windows.append(window_file(self.id, scope, cursor, piece_end))
                cursor = piece_end
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
