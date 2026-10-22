"""SCRUM-72: the secrets loader is actually wired into the app."""
import logging

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main  # noqa: F401  (importing installs log redaction)
from app.auth import User
from app.db import Base
from app.orchestrator import routes
from app.schemas import ConnectRequest
from app.secrets_loader import _snapshot, clear_registered_secrets

FAKE_PASSWORD = "not-a-real-pw-123"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    clear_registered_secrets()


def _connect(db, password=FAKE_PASSWORD):
    req = ConnectRequest(ip_address="192.0.2.1", username="tester", password=password)
    with pytest.raises(HTTPException):  # discovery is still a 501 stub
        routes.connect(req, db=db, user=User(id="tester", name="Tester"))


def test_connect_password_hidden_in_repr():
    req = ConnectRequest(ip_address="192.0.2.1", username="tester", password=FAKE_PASSWORD)
    assert FAKE_PASSWORD not in repr(req)
    assert FAKE_PASSWORD not in str(req.model_dump())
    assert req.password.get_secret_value() == FAKE_PASSWORD


def test_connect_password_masked_in_logs_during_the_request(db, caplog, monkeypatch):
    # Stands in for discovery code that logs carelessly while handling the request.
    monkeypatch.setattr(
        routes, "touch_session",
        lambda _uid: logging.getLogger("wiring-test").info("connecting with %s", FAKE_PASSWORD),
    )
    with caplog.at_level(logging.INFO):
        _connect(db)
    assert "connecting with ***" in caplog.text
    assert FAKE_PASSWORD not in caplog.text


def test_connect_passwords_are_forgotten_after_the_request(db):
    for i in range(50):
        _connect(db, password=f"{FAKE_PASSWORD}-{i}")
    assert _snapshot() == {}  # the registry does not grow with every login
