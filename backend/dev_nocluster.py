"""Run the backend on a laptop with no Kubernetes cluster, for UI work.

    make dev-nocluster          (or: python dev_nocluster.py from backend/)

What is real and what is faked:
  - Real: login, sources, local upload and ingestion, the job state machine,
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
os.environ.setdefault("DEMO_AUTH_USERNAME", "demo")
os.environ.setdefault("DEMO_AUTH_PASSWORD", "demo-password")
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

if __name__ == "__main__":
    from app.main import app

    print(f"\nPortrait IQ backend, NO CLUSTER (fake tool pods). Data in {_data}")
    print(f"Sign in with  {os.environ['DEMO_AUTH_USERNAME']} / {os.environ['DEMO_AUTH_PASSWORD']}\n")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), access_log=False)
