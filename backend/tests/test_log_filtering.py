"""SCRUM-53: every matching event, and no non-matching event, is returned."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import User, get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import Job, NormalizedEvent


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add_all([Job(id="job-a"), Job(id="job-b")])
        rows = [
            ("job-a", "scanner-a", "ERROR", "General", 0),
            ("job-a", "scanner-a", "WARN", "PerfData", 1),
            ("job-a", "scanner-b", "ERROR", "General", 1),
            ("job-a", None, "INFO", None, 2),
            ("job-b", "scanner-a", "ERROR", "General", 1),
        ]
        for job_id, device, severity, category, minute in rows:
            db.add(NormalizedEvent(
                job_id=job_id,
                timestamp=datetime(2024, 6, 2, 2, minute),
                device_id=device,
                source="portrait",
                category=category,
                severity=severity,
                message=f"event {job_id} {device} {minute}",
            ))
        db.commit()

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User("engineer", "Engineer")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_unfiltered_includes_null_fields_and_all_jobs(client):
    response = client.get("/api/logs")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    assert any(item["device_id"] is None and item["category"] is None for item in body["items"])
    assert all(item["timestamp"].endswith("Z") for item in body["items"])


@pytest.mark.parametrize(
    ("params", "expected_count"),
    [
        ({"job_id": "job-a"}, 4),
        ({"device_id": "scanner-a"}, 3),
        ({"severity": "ERROR"}, 3),
        ({"category": "General"}, 3),
        ({"job_id": "job-a", "device_id": "scanner-a", "severity": "ERROR", "category": "General"}, 1),
        ({"device_id": "scanner"}, 0),
        ({"start": "2024-06-02T03:01:00+01:00"}, 4),
        ({"end": "2024-06-02T02:01:00Z"}, 1),
    ],
)
def test_filters_match_exactly(client, params, expected_count):
    body = client.get("/api/logs", params=params).json()
    assert body["total"] == expected_count
    assert len(body["items"]) == expected_count


def test_combined_filters_and_pagination_have_no_missing_or_extra_ids(client):
    params = {"job_id": "job-a", "category": "General", "start": "2024-06-02T02:00:00Z", "end": "2024-06-02T02:02:00Z"}
    full = client.get("/api/logs", params=params).json()
    first = client.get("/api/logs", params={**params, "limit": 1}).json()
    second = client.get("/api/logs", params={**params, "limit": 1, "offset": 1}).json()
    assert full["total"] == first["total"] == second["total"] == 2
    assert [item["id"] for item in full["items"]] == [
        first["items"][0]["id"], second["items"][0]["id"]
    ]
    assert [item["device_id"] for item in full["items"]] == ["scanner-b", "scanner-a"]


@pytest.mark.parametrize("params", [
    {"start": "2024-06-02T02:00:00"},
    {"end": "2024-06-02T02:00:00"},
    {"start": "2024-06-02T02:01:00Z", "end": "2024-06-02T02:01:00Z"},
    {"limit": 0},
    {"offset": -1},
])
def test_invalid_ranges_and_pages_are_rejected(client, params):
    assert client.get("/api/logs", params=params).status_code == 422
