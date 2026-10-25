"""
Device registry for picking devices in a log pull (SCRUM-70).

GE's log export API can filter by device (deviceuuid) but cannot list
devices, so the tool keeps its own list:

  1. Harvested: every Portrait-format CSV the acquisition service stages
     (log export API, ZIP / folder import, Box) is scanned for its
     sourceUUID, friendlyName and hostName columns. No extra calls to the
     live system, and it works offline.
  2. Manual: an engineer can add a device ID the tool has not seen yet
     (e.g. a hub that was off during every pull so far).

Harvested data always wins over a manual name, and the newest friendly
name wins over older ones, since devices get renamed.
"""
import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.parsers.base import open_text
from app.ingestion.schema import parse_timestamp
from app.models import KnownDevice
from app.sources.base import SourceError

# sourceUUIDs are UUIDs in every sample so far, but nothing documents that,
# so accept any plain ID. It ends up in API query strings and in manifest
# scope ids, so nothing beyond letters, digits and dashes.
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{2,63}$")
_PLACEHOLDER_NAMES = {"", "unsetFriendlyName"}
_REQUIRED = ("sourceUUID", "timeStampAndOffset")


def validate_device_id(raw: str) -> str:
    device_id = (raw or "").strip()
    if not DEVICE_ID_RE.match(device_id):
        raise SourceError(
            f"'{device_id[:70]}' is not a valid device ID (letters, digits and dashes, 3 to 64 characters)."
        )
    return device_id


@dataclass
class Sighting:
    friendly_name: str | None
    host_name: str | None
    first_seen: datetime
    last_seen: datetime


def harvest_csv(path: Path) -> dict[str, Sighting]:
    """Distinct devices in one Portrait-format CSV, with the name each had at
    its latest log line. Files without sourceUUID (e.g. Kubernetes pod logs)
    return {}. Rows with a bad ID or timestamp are skipped: ingestion already
    accounts for every row, this is only a device list."""
    found: dict[str, Sighting] = {}
    with open_text(path) as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            return found
        cols = {h.strip(): i for i, h in enumerate(header)}
        if any(c not in cols for c in _REQUIRED):
            return found
        i_id, i_ts = cols["sourceUUID"], cols["timeStampAndOffset"]
        i_name, i_host = cols.get("friendlyName"), cols.get("hostName")
        width = len(header)
        for row in reader:
            if len(row) != width:
                continue
            device_id = row[i_id].strip()
            if not DEVICE_ID_RE.match(device_id):
                continue
            try:
                ts = parse_timestamp(row[i_ts])
            except ValueError:
                continue
            name = row[i_name].strip() if i_name is not None else ""
            name = None if name in _PLACEHOLDER_NAMES else name
            host = (row[i_host].strip() or None) if i_host is not None else None
            seen = found.get(device_id)
            if seen is None:
                found[device_id] = Sighting(name, host, ts, ts)
                continue
            seen.first_seen = min(seen.first_seen, ts)
            if ts >= seen.last_seen:
                seen.last_seen = ts
                seen.friendly_name = name or seen.friendly_name
                seen.host_name = host or seen.host_name
    return found


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def record_devices(db: Session, found: dict[str, Sighting], learned_from: str) -> int:
    """Upsert harvested devices. Returns how many were new."""
    new = 0
    for device_id, s in found.items():
        first, last = _naive_utc(s.first_seen), _naive_utc(s.last_seen)
        row = db.get(KnownDevice, device_id)
        if row is None:
            db.add(KnownDevice(device_uuid=device_id, friendly_name=s.friendly_name, host_name=s.host_name,
                               first_seen=first, last_seen=last, learned_from=learned_from))
            new += 1
            continue
        row.first_seen = min(row.first_seen, first) if row.first_seen else first
        # Newest sighting sets the names (a manual entry has no last_seen yet).
        if row.last_seen is None or last >= row.last_seen:
            row.last_seen = last
            row.friendly_name = s.friendly_name or row.friendly_name
            row.host_name = s.host_name or row.host_name
        if row.learned_from == "manual":
            row.learned_from = learned_from   # now confirmed by real logs
    db.commit()
    return new


def add_manual(db: Session, device_id: str, name: str | None = None) -> tuple[KnownDevice, bool]:
    """Add a device by hand. Returns (device, created). An existing device is
    left as it is: harvested data is more reliable than a typed name."""
    device_id = validate_device_id(device_id)
    row = db.get(KnownDevice, device_id)
    if row is not None:
        return row, False
    label = (name or "").strip()[:100] or None
    row = KnownDevice(device_uuid=device_id, friendly_name=label, learned_from="manual")
    db.add(row)
    db.commit()
    return row, True


def list_known(db: Session) -> list[KnownDevice]:
    rows = db.query(KnownDevice).all()
    return sorted(rows, key=lambda d: (display_name(d).lower(), d.device_uuid))


def display_name(d: KnownDevice) -> str:
    return d.friendly_name or d.host_name or d.device_uuid
