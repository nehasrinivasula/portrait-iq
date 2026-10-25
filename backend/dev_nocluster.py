"""Run the backend on a laptop with no Kubernetes cluster, for UI work.

    make dev-nocluster          (or: python dev_nocluster.py from backend/)

What is real and what is faked:
  - Real: login, sources, local upload and ingestion, the job state machine,
    the log search API (an empty dev database gets 420 made-up events),
    tshark "import" mode (needs tshark/editcap on PATH or WIRESHARK_BIN_DIR).
  - Faked: the tool-pod runner. Netshoot "runs" its checks against a stand-in
    that answers instantly, with traceroute failing so the warning path shows.
    Nothing here talks to a cluster or a device.

Never use this on the lab box: the fake runner reports success for checks that
never ran. Login for the UI is printed at startup. Not for production.
"""
import os
import secrets
import tempfile
from pathlib import Path

# Must be set before app.config is imported.
_data = Path(os.environ.get("DEV_DATA_DIR", Path(tempfile.gettempdir()) / "portrait-iq-dev"))
_data.mkdir(parents=True, exist_ok=True)


def _in_dotenv(name: str) -> bool:
    """True if backend/.env sets name. A process env var outranks .env, so
    setting a default here would silently override the engineer's own login."""
    env = Path(__file__).with_name(".env")
    return env.exists() and any(
        line.split("=", 1)[0].strip() == name for line in env.read_text().splitlines() if "=" in line
    )


_own_login = _in_dotenv("DEMO_AUTH_USERNAME") and _in_dotenv("DEMO_AUTH_PASSWORD")
if not _own_login:
    os.environ.setdefault("DEMO_AUTH_USERNAME", "demo")
    os.environ.setdefault("DEMO_AUTH_PASSWORD", "demo-password")
if not _in_dotenv("AUTH_TOKEN_SECRET"):
    os.environ.setdefault("AUTH_TOKEN_SECRET", secrets.token_urlsafe(48))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_data / 'dev.db'}")
os.environ.setdefault("JOB_OUTPUT_DIR", str(_data / "jobs"))
os.environ.setdefault("UPLOAD_DIR", str(_data / "uploads"))
os.environ.setdefault("STAGING_DIR", str(_data / "staging"))
os.environ["SWEEP_TOOL_PODS_ON_STARTUP"] = "false"   # no cluster to sweep

import time  # noqa: E402

import uvicorn  # noqa: E402

from app.collectors import k8s  # noqa: E402


class FakePodRunner:
    """Same surface the plugins use (create / wait_running / exec / delete)."""

    DELAY_S = float(os.environ.get("DEV_FAKE_DELAY_S", "0.6"))

    def create(self, **kw):
        time.sleep(self.DELAY_S)

    def wait_running(self, name, timeout_s):
        time.sleep(self.DELAY_S)

    def exec(self, name, argv, timeout_s):
        time.sleep(self.DELAY_S / 2)
        if argv[0] == "traceroute":
            return k8s.ExecResult(1, "", "traceroute: simulated failure (dev_nocluster)")
        return k8s.ExecResult(0, f"{' '.join(argv)}: simulated ok\n", "")

    def delete(self, name):
        time.sleep(self.DELAY_S / 2)


k8s.get_pod_runner = lambda: FakePodRunner()

def _seed_sample_events() -> None:
    """Synthetic events, only into an empty dev database, so the log page has
    something to filter. Made-up text, never real log content."""
    from datetime import datetime, timedelta

    from app.db import SessionLocal, init_db
    from app.models import NormalizedEvent

    init_db()
    with SessionLocal() as db:
        if db.query(NormalizedEvent).first() is not None:
            return
        levels = ["INFO", "INFO", "INFO", "DEBUG", "WARN", "ERROR"]
        devices = ["viewer-01", "hub-01", "server-01"]
        processes = ["vitals-svc", "alarm-svc", "gateway", "kubelet"]
        messages = [
            "Heartbeat received from device", "Configuration reloaded",
            "Connection to gateway timed out after 30s", "Alarm limits updated for bed assignment",
            "Retrying upload of waveform batch (attempt 2)", "Certificate expires in 12 days",
        ]
        now = datetime.utcnow().replace(microsecond=0)
        db.add_all(
            NormalizedEvent(
                timestamp=now - timedelta(seconds=97 * i), device_id=devices[i % 3],
                source="kubernetes" if i % 5 == 0 else "portrait", source_file="dev-sample.csv", source_line=i + 1,
                category="PerfData" if i % 3 == 0 else "General", severity=levels[(i * 7) % 6],
                process=processes[i % 4], message=f"{messages[(i * 3) % 6]} (#{i + 1})",
            )
            for i in range(420)
        )
        db.commit()


if __name__ == "__main__":
    from app.main import app

    _seed_sample_events()

    print(f"\nPortrait IQ backend, NO CLUSTER (fake tool pods). Data in {_data}")
    if _own_login:
        print("Sign in with the DEMO_AUTH_USERNAME / DEMO_AUTH_PASSWORD from backend/.env\n")
    else:
        print(f"No login in backend/.env; sign in with {os.environ['DEMO_AUTH_USERNAME']} / {os.environ['DEMO_AUTH_PASSWORD']}\n")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), access_log=False)
