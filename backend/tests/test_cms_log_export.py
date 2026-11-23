"""SCRUM-124: CMS collection must stay on the established Axone CSV path."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.auth import User, get_current_user
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import AuditLogEntry, NormalizedEvent
from app.sources import registry
from app.sources.axone_client import AxoneLogExportClient
from app.sources.axone_export import AxoneLogExportSource
from tests.axone_fake import FakeAxone, Row


NOW_HOUR = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
BASE = NOW_HOUR - timedelta(hours=4)


@pytest.fixture
def client(engine, staging, monkeypatch):
    Session = sessionmaker(bind=engine)

    def _db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: User("tester", "Tester", "test-session")
    monkeypatch.setattr("app.auth.is_locked", lambda *_: False)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_cms_acquire_requires_a_ge_confirmed_category(client, monkeypatch):
    monkeypatch.setattr(settings, "axone_cms_event_category", None)
    response = client.post("/api/sources/axone/cms/acquire", json={})
    assert response.status_code == 409
    assert "AXONE_CMS_EVENT_CATEGORY" in response.json()["detail"]


def test_cms_acquire_uses_configured_category_and_unified_ingestion(client, engine, monkeypatch):
    fake = FakeAxone(rows=[
        Row((BASE + timedelta(minutes=10)).replace(tzinfo=None), category=1, message="CMS service restarted"),
        Row((BASE + timedelta(minutes=20)).replace(tzinfo=None), category=6, message="not CMS"),
    ])
    source = AxoneLogExportSource(
        client_factory=lambda: AxoneLogExportClient("http://fake-axone:9200", transport=fake.transport()),
        sleep=lambda _: None,
    )
    monkeypatch.setattr(settings, "axone_base_url", "http://fake-axone:9200")
    monkeypatch.setattr(settings, "axone_timezone", "UTC")
    monkeypatch.setattr(settings, "axone_request_delay_seconds", 0)
    monkeypatch.setattr(settings, "axone_cms_event_category", 1)
    monkeypatch.setitem(registry._SOURCES, "axone", source)

    # The public source description allows the UI to offer CMS only when it
    # is configured, without exposing any credentials.
    sources = client.get("/api/sources").json()
    axone = next(item for item in sources if item["id"] == "axone")
    assert axone["options"]["cms_event_category"] == ["1"]

    response = client.post("/api/sources/axone/cms/acquire", json={
        "since": BASE.isoformat(), "until": (BASE + timedelta(hours=1)).isoformat(), "ingest": True,
        # A malicious/browser-supplied category cannot override CMS config.
        "options": {"event_category": "6"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_files"] == 1
    assert "configured Axone event category 1" in body["notes"][0]
    call = fake.calls("/getLogsByDateAndEventInCsv")[-1]
    assert call.url.params["eventCategory"] == "1"

    session = sessionmaker(bind=engine)()
    try:
        events = session.query(NormalizedEvent).all()
        assert len(events) == 1
        assert events[0].message == "CMS service restarted"
        # Ingestion, including privacy scanning, is shared with all sources.
        assert events[0].privacy_scan_version is not None
        audit = session.query(AuditLogEntry).filter_by(action="acquire_cms_logs").one()
        assert "category=1" in audit.detail
    finally:
        session.close()
