"""Focused coverage for the local-demo login and route protection contract."""
import secrets

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app import auth
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import AuditLogEntry, SessionActivity


@pytest.fixture()
def demo_credentials(monkeypatch):
    credentials = {
        "username": f"demo-{secrets.token_hex(4)}",
        "password": secrets.token_urlsafe(24),
    }
    monkeypatch.setattr(settings, "demo_auth_username", credentials["username"])
    monkeypatch.setattr(settings, "demo_auth_password", SecretStr(credentials["password"]))
    monkeypatch.setattr(settings, "auth_token_secret", SecretStr(secrets.token_urlsafe(32)))
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", 3600)
    return credentials


def _use_database(engine) -> None:
    Session = sessionmaker(bind=engine)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db


@pytest.fixture()
def client(engine):
    # Each test gets its own SQLite file (conftest). No `with`: that would run
    # the app's startup against the real portrait_iq.db.
    _use_database(engine)
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    return now


def login(client: TestClient, credentials: dict[str, str]) -> str:
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200
    return response.json()["access_token"]


def test_valid_login_and_me(client: TestClient, demo_credentials):
    token = login(client, demo_credentials)
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["username"] == demo_credentials["username"]


def test_invalid_login_is_rejected(client: TestClient, demo_credentials):
    response = client.post(
        "/api/auth/login",
        json={"username": demo_credentials["username"], "password": secrets.token_urlsafe(24)},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password.", "code": "invalid_credentials"}


def test_protected_route_rejects_missing_token(client: TestClient, demo_credentials):
    response = client.get("/api/devices")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required.", "code": "auth_required"}


def test_authenticated_protected_route_is_allowed(client: TestClient, demo_credentials):
    token = login(client, demo_credentials)
    response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_health_is_public(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_not_configured_returns_503(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "demo_auth_username", None)
    response = client.post("/api/auth/login", json={"username": "a", "password": "b"})

    assert response.status_code == 503
    assert response.json()["code"] == "not_configured"


def test_tampered_token_is_rejected(client: TestClient, demo_credentials):
    token = login(client, demo_credentials)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered}"})

    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


def test_expired_token_is_rejected(client: TestClient, demo_credentials, monkeypatch):
    monkeypatch.setattr(settings, "auth_token_ttl_seconds", -10)
    token = login(client, demo_credentials)
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


# Idle lock (S-2): starting jobs and uploading count as activity -------------

def test_jobs_and_uploads_keep_the_session_unlocked(client: TestClient, demo_credentials, monkeypatch):
    from app import auth
    clock = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    headers = {"Authorization": f"Bearer {login(client, demo_credentials)}"}
    idle = settings.session_idle_timeout_seconds

    def upload():   # a refused file: still an action the lock guards
        return client.post("/api/uploads?kind=pcap", headers=headers,
                           files={"file": ("x.pcap", b"not a capture")}).status_code

    def start_job():
        return client.post("/api/jobs?target_ip=192.0.2.10", headers=headers,
                           json={"tools": ["no-such-tool"]}).status_code

    clock[0] += idle - 10
    assert upload() == 400
    clock[0] += idle - 10      # longer than the timeout since login, not since the upload
    assert start_job() == 400

    clock[0] += idle + 1       # really idle
    assert client.get("/api/jobs/no-such-job", headers=headers).status_code == 423
    assert upload() == 423
    assert start_job() == 423

    # Logging in again unlocks: a new session, so a new token.
    headers["Authorization"] = f"Bearer {login(client, demo_credentials)}"
    assert upload() == 400


# Idle lock enforced on the server, per session (SCRUM-34) -------------------

LOCKED = {"detail": "Session locked. Log in again to unlock.", "code": "session_locked"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def upload(client: TestClient, headers) -> int:   # refused file: 400 when unlocked
    return client.post("/api/uploads?kind=pcap", headers=headers,
                       files={"file": ("x.pcap", b"not a capture")}).status_code


def test_idle_session_is_refused_on_get_and_post(client, demo_credentials, clock, db):
    headers = bearer(login(client, demo_credentials))
    clock[0] += settings.session_idle_timeout_seconds + 1

    for response in (
        client.get("/api/devices", headers=headers),
        client.get("/api/logs/events", headers=headers),
        client.post("/api/uploads?kind=pcap", headers=headers, files={"file": ("x.pcap", b"x")}),
    ):
        assert response.status_code == 423
        assert response.json() == LOCKED
    # A page refresh restores the session through /me: it reports the lock too.
    assert client.get("/api/auth/me", headers=headers).status_code == 423

    locks = db.query(AuditLogEntry).filter_by(action="session_lock").all()
    assert len(locks) == 1     # audited once, not on every refused request
    assert locks[0].actor == demo_credentials["username"]
    assert locks[0].detail.startswith("reason=idle")


def test_polling_does_not_keep_the_session_alive(client, demo_credentials, clock):
    headers = bearer(login(client, demo_credentials))
    idle = settings.session_idle_timeout_seconds

    for _ in range(3):         # polling a job, well inside the timeout each time
        clock[0] += idle / 3
        assert client.get("/api/jobs/no-such-job", headers=headers).status_code == 404
        assert client.get("/api/auth/me", headers=headers).status_code == 200
    clock[0] += 1              # just past the timeout since login

    assert client.get("/api/jobs/no-such-job", headers=headers).status_code == 423
    assert client.get("/api/devices", headers=headers).status_code == 423


def test_user_requests_keep_the_session_alive(client, demo_credentials, clock):
    headers = bearer(login(client, demo_credentials))
    idle = settings.session_idle_timeout_seconds
    for _ in range(3):
        clock[0] += idle - 10
        assert client.get("/api/devices", headers=headers).status_code == 200


def test_lock_state_survives_a_restart(client, demo_credentials, clock, engine):
    idle = settings.session_idle_timeout_seconds
    left_alone = bearer(login(client, demo_credentials))
    in_use = bearer(login(client, demo_credentials))
    clock[0] += idle - 10
    assert client.get("/api/devices", headers=in_use).status_code == 200

    # Restart: nothing in process memory survives, only the database file.
    engine.dispose()
    _use_database(engine)
    restarted = TestClient(app)
    clock[0] += 20             # past the timeout for left_alone, not for in_use

    assert restarted.get("/api/devices", headers=left_alone).status_code == 423
    assert restarted.get("/api/devices", headers=in_use).status_code == 200


def test_a_session_without_an_activity_record_is_locked(client, demo_credentials, clock, db):
    headers = bearer(login(client, demo_credentials))
    db.query(SessionActivity).delete()   # e.g. the database was replaced
    db.commit()

    assert client.get("/api/devices", headers=headers).status_code == 423
    assert client.get("/api/devices", headers=headers).status_code == 423
    locks = db.query(AuditLogEntry).filter_by(action="session_lock").all()
    assert [e.detail for e in locks] == ["reason=no_activity_record"]


def test_two_sessions_for_one_user_lock_independently(client, demo_credentials, clock):
    idle = settings.session_idle_timeout_seconds
    first = bearer(login(client, demo_credentials))
    second = bearer(login(client, demo_credentials))
    clock[0] += idle - 10
    assert upload(client, second) == 400     # activity on the second session only
    clock[0] += 20

    assert upload(client, first) == 423
    assert upload(client, second) == 400


def test_logging_in_again_unlocks_with_a_new_session(client, demo_credentials, clock):
    old = bearer(login(client, demo_credentials))
    clock[0] += settings.session_idle_timeout_seconds + 1
    assert upload(client, old) == 423

    new = bearer(login(client, demo_credentials))
    assert upload(client, new) == 400
    assert client.get("/api/auth/me", headers=new).status_code == 200
    assert upload(client, old) == 423        # the locked session stays locked


def _dependencies(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependencies(sub)


def test_every_signed_in_route_checks_the_idle_lock():
    protected = [r for r in app.routes if isinstance(r, APIRoute)
                 and auth.get_current_user in set(_dependencies(r.dependant))]
    assert protected   # the walk found the routes
    unchecked = [r.path for r in protected if auth.get_unlocked_user not in set(_dependencies(r.dependant))]
    assert unchecked == []


def test_diagnostic_status_survives_idle_lock_and_relogin(client: TestClient, db, demo_credentials, monkeypatch):
    """A run belongs to the server, not to the browser token that started it."""
    from app import auth
    from app.db import get_db
    from app.orchestrator import routes

    clock = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    monkeypatch.setattr(routes, "_run_in_background", lambda *_args: None)

    def use_test_db():
        yield db

    app.dependency_overrides[get_db] = use_test_db
    try:
        first_token = login(client, demo_credentials)
        first_headers = {"Authorization": f"Bearer {first_token}"}
        started = client.post(
            "/api/jobs?target_ip=192.0.2.10",
            headers=first_headers,
            json={"tools": []},
        )
        assert started.status_code == 200
        job = started.json()
        assert job["status"] == "queued"

        # The idle lock refuses the old token (SCRUM-34) but never cancels the
        # run: it stays on the server for the next sign-in to pick up.
        clock[0] += settings.session_idle_timeout_seconds + 1
        locked_view = client.get(f"/api/jobs/{job['id']}", headers=first_headers)
        assert locked_view.status_code == 423

        # A new browser token after sign-in can read the same persisted job.
        second_token = login(client, demo_credentials)
        resumed = client.get(f"/api/jobs/{job['id']}", headers={"Authorization": f"Bearer {second_token}"})
        assert resumed.status_code == 200
        assert resumed.json()["id"] == job["id"]
        assert resumed.json()["status"] == "queued"
    finally:
        app.dependency_overrides.pop(get_db, None)

# Login settings from files (Kubernetes Secret volume) -----------------------

def _write(path, value):
    path.write_text(value + "\n")
    path.chmod(0o600)
    return str(path)


def test_login_settings_load_from_files(tmp_path, monkeypatch):
    from app.config import LOGIN_SECRET_NAMES, Settings, load_login_secret_files
    for name in LOGIN_SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(f"{name}_FILE", _write(tmp_path / name, f"fake-{name.lower()}"))
    s = Settings(_env_file=None)
    load_login_secret_files(s)
    assert s.demo_auth_username == "fake-demo_auth_username"
    assert s.demo_auth_password.get_secret_value() == "fake-demo_auth_password"
    assert s.auth_token_secret.get_secret_value() == "fake-auth_token_secret"


def test_login_setting_in_both_places_is_refused(tmp_path, monkeypatch):
    from app.config import Settings, load_login_secret_files
    from app.secrets_loader import AmbiguousSecretError
    monkeypatch.setenv("DEMO_AUTH_PASSWORD", "fake-from-env")
    monkeypatch.setenv("DEMO_AUTH_PASSWORD_FILE", _write(tmp_path / "pw", "fake-from-file"))
    s = Settings(_env_file=None)
    with pytest.raises(AmbiguousSecretError):
        load_login_secret_files(s)


def test_world_readable_login_file_is_refused(tmp_path, monkeypatch):
    from app.config import Settings, load_login_secret_files
    from app.secrets_loader import SecretError
    monkeypatch.delenv("AUTH_TOKEN_SECRET", raising=False)
    path = _write(tmp_path / "key", "fake-key")
    (tmp_path / "key").chmod(0o644)
    monkeypatch.setenv("AUTH_TOKEN_SECRET_FILE", path)
    with pytest.raises(SecretError):
        load_login_secret_files(Settings(_env_file=None))


def test_missing_login_file_leaves_login_unconfigured(tmp_path, monkeypatch):
    from app.config import Settings, load_login_secret_files
    monkeypatch.delenv("DEMO_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("DEMO_AUTH_PASSWORD_FILE", str(tmp_path / "not-there"))
    s = Settings(_env_file=None)
    load_login_secret_files(s)
    assert s.demo_auth_password is None
