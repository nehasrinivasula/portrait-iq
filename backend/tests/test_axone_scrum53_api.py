"""One synthetic settled Axone hour through acquisition, ingest and SCRUM-53 API."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.auth import User, get_current_user
from app.db import get_db
from app.main import app
from app.models import AcquiredFileRecord, AuditLogEntry, NormalizedEvent
from app.sources import registry
from app.sources.axone_client import AxoneLogExportClient
from app.sources.axone_export import AxoneLogExportSource
from tests.axone_fake import FakeAxone, Row


def test_axone_acquisition_to_scrum53_filters(engine, staging, monkeypatch):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    fake = FakeAxone(rows=[
        Row(start.replace(tzinfo=None) + timedelta(minutes=1), level="ERROR", category=6),
        Row(start.replace(tzinfo=None) + timedelta(minutes=2), level="WARN", category=3),
        Row(start.replace(tzinfo=None) + timedelta(minutes=3), level="INFO", category=6),
    ])
    source = AxoneLogExportSource(
        client_factory=lambda: AxoneLogExportClient(
            "http://fake-axone:9200", transport=fake.transport(), max_bytes=1024 * 1024,
        ),
        sleep=lambda _: None,
        clock=lambda: now,
    )
    monkeypatch.setitem(registry._SOURCES, "axone", source)
    session_factory = sessionmaker(bind=engine)

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(id="tester", name="Tester")
    try:
        client = TestClient(app)  # no startup: never touch the development database or real cluster
        payload = {"since": start.isoformat(), "until": end.isoformat(), "ingest": True}
        response = client.post("/api/sources/axone/acquire", json=payload)
        assert response.status_code == 200
        metadata = response.json()
        assert metadata["new_files"] == 1
        assert metadata["failed"] == metadata["over_budget"] == 0
        assert metadata["bytes_staged"] > 0
        assert [(f["status"], f["events"]) for f in metadata["files"]] == [("ingested", 3)]
        assert len(fake.calls("/getAllDeviceLogsInCSV")) == 1

        window = {"start": start.isoformat(), "end": end.isoformat()}
        assert client.get("/api/logs", params=window).json()["total"] == 3
        assert client.get("/api/logs", params={**window, "severity": "ERROR"}).json()["total"] == 1
        assert client.get("/api/logs", params={**window, "category": "General"}).json()["total"] == 2
        assert client.get("/api/logs", params={**window, "device_id": "missing"}).json()["total"] == 0
        assert client.get("/api/logs", params={**window, "severity": "INVALID"}).status_code == 422
        assert client.get("/api/logs", params={"start": end.isoformat(), "end": start.isoformat()}).status_code == 422

        pages = [client.get("/api/logs", params={**window, "limit": 1, "offset": n}).json() for n in range(3)]
        assert [p["total"] for p in pages] == [3, 3, 3]
        assert len({p["items"][0]["id"] for p in pages}) == 3

        again = client.post("/api/sources/axone/acquire", json=payload)
        assert again.status_code == 200
        assert again.json()["new_files"] == 0
        assert again.json()["already_acquired"] == 1
        assert len(fake.calls("/getAllDeviceLogsInCSV")) == 1
        with session_factory() as db:
            assert db.scalar(select(func.count()).select_from(NormalizedEvent)) == 3
            assert db.scalar(select(func.count()).select_from(AcquiredFileRecord)) == 1
            assert db.scalar(select(func.count()).select_from(AuditLogEntry)) >= 2
    finally:
        app.dependency_overrides.clear()
