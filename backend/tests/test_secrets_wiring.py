"""SCRUM-72: the secrets loader is actually wired into the app."""
import logging

from app import main  # noqa: F401  (importing installs log redaction)
from app.schemas import ConnectRequest
from app.secrets_loader import clear_registered_secrets, redact

FAKE_PASSWORD = "not-a-real-pw-123"


def test_connect_password_hidden_in_repr():
    req = ConnectRequest(ip_address="192.0.2.1", username="tester", password=FAKE_PASSWORD)
    assert FAKE_PASSWORD not in repr(req)
    assert FAKE_PASSWORD not in str(req.model_dump())
    assert req.password.get_secret_value() == FAKE_PASSWORD
    clear_registered_secrets()


def test_connect_password_masked_in_logs(caplog):
    ConnectRequest(ip_address="192.0.2.1", username="tester", password=FAKE_PASSWORD)
    try:
        assert FAKE_PASSWORD not in redact(f"login with {FAKE_PASSWORD}")
        with caplog.at_level(logging.INFO):
            logging.getLogger("wiring-test").info("connecting with %s", FAKE_PASSWORD)
        assert FAKE_PASSWORD not in caplog.text
    finally:
        clear_registered_secrets()
