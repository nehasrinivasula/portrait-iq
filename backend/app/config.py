"""
Central settings. Everything here is a placeholder default for local/lab use —
override via a .env file before this touches anything resembling production.
"""
import os

from pydantic_settings import BaseSettings
from pydantic import SecretStr

from app.secrets_loader import AmbiguousSecretError, load_secret


class Settings(BaseSettings):
    app_name: str = "Portrait IQ"

    # SQLite is fine for the lab. Swap for Postgres before this handles
    # more than one field engineer at a time.
    database_url: str = "sqlite:///./portrait_iq.db"

    # Resource-limit defaults, per the requirements doc's "Reliability" section.
    # These are ceilings a collector run cannot exceed, not recommendations.
    default_cpu_limit_pct: int = 50
    default_mem_limit_mb: int = 1024
    default_max_output_mb: int = 2048

    # Session lock, per the requirements doc's "Security" section (5 minutes).
    session_idle_timeout_seconds: int = 300

    # Local-demo authentication only. These have no defaults so credentials and
    # signing material can never be accidentally committed to source control.
    # A future identity-provider integration can replace this configuration and
    # the token helpers in auth.py without changing protected routes.
    demo_auth_username: str | None = None
    demo_auth_password: SecretStr | None = None
    auth_token_secret: SecretStr | None = None
    auth_token_ttl_seconds: int = 3600

    # --- Log acquisition (SCRUM-43) ---
    # Where acquired files land before ingestion. Relative to where uvicorn
    # runs (backend/). Gitignored: acquired logs can contain PHI/PII.
    staging_dir: str = "./data/staging"
    # Objective 1: logs from "a user-selected time range within the last 30 days".
    acquire_max_days: int = 30
    # A ZIP with more entries than this is refused (zip-bomb guard).
    zip_max_members: int = 1000

    # Box (optional). Not secrets, so they live here and in .env. The client
    # ID / secret are secrets and come from secrets_loader (see SECRETS.md).
    # Client Credentials Grant needs exactly one of enterprise_id / user_id.
    box_folder_id: str | None = None
    box_enterprise_id: str | None = None
    box_user_id: str | None = None

    # Axone log export API (preferred source). Only reachable inside the
    # Kubernetes cluster: from a laptop use a port-forward + SSH tunnel and
    # http://localhost:9200; from a pod use
    # http://axone-log-export-tcp.<namespace>.svc.cluster.local:9200
    axone_base_url: str | None = None
    axone_timeout_seconds: int = 60
    # Pause between calls so a long pull doesn't load the live system.
    axone_request_delay_seconds: float = 0.5
    # Timezone the API's start/end timestamps are in.
    axone_timezone: str = "UTC"
    # Default range when no start time is given: one API call per hour.
    axone_default_lookback_hours: int = 24
    # Wait this long after an hour ends before pulling it, so logs that
    # devices upload a little late still land in that hour's pull.
    axone_settle_minutes: int = 15

    # --- Diagnostic tool plugins (SCRUM-39) ---
    # Tool pods go in our own namespace, never in Portrait's (pm) or GE's
    # (compute). In a pod the backend uses its service account; from a laptop
    # it uses this kubeconfig (or ~/.kube/config when unset).
    tools_namespace: str = "portrait-iq"
    kubeconfig_path: str | None = None
    # The lab has no internet: images are imported into containerd by hand,
    # so IfNotPresent never pulls there. Pin exact tags.
    tool_image_pull_policy: str = "IfNotPresent"
    tool_pod_start_timeout_seconds: int = 120
    netshoot_image: str = "nicolaka/netshoot:v0.13"
    tshark_image: str = "portrait-iq-tshark:0.1"   # built from deploy/images/tshark
    # Live capture needs a host-network pod with raw-socket rights on the
    # customer's node. Off until the site (and our sponsor) approve it;
    # importing a capture someone else made works either way.
    tshark_live_capture_enabled: bool = False
    # UMF RTI listens on GE's secured DDS domains with GE's rtitools image
    # (Connext Pro + the domain's security files). Off until that image is
    # on the bench and the mentors approve a listen-only participant. The
    # image name is the chart's default; on the bench it is probably the
    # 192.168.0.3:30003/spectro-images/... mirror path instead.
    rti_enabled: bool = False
    rti_image: str = "docker-axone-all/axone-umf-rtitools:branch-latest"
    # The Admin Console reaches each UMF domain over RTI Real-Time WAN through
    # the router's WAN NodePort, NOT plain UDP discovery -- the data-node QoS
    # blocks unknown peers, so plain UDP never completes (proven on the bench
    # 2026-11-08). The defaults below drive the WAN path; override to experiment.
    # QoS file in /opt/axone-tools/qos/ in the image. The -WithWAN variant
    # carries the umfwan_server_* profiles; empty uses the image's default.
    rti_qos_file: str = "/opt/axone-tools/qos/MDH-tools-RTIAdminConsole-WithWAN.xml"
    # Empty picks the WAN profile for the domain: umfwan_server_logger_qos (7),
    # _sourcepod_qos (12), _viewpod_qos (13). Set to force one profile.
    rti_qos_profile: str = ""
    # DDS_RTWAN_SERVER_PUBLIC_ADDRESS: the address the router sends WAN replies
    # to. Empty uses the tool pod's node IP (right for a host-network pod).
    rti_wan_public_address: str = ""
    # Where the router's WAN NodePorts are reachable. Empty uses the job's
    # target IP, then the node IP. On the bench the node, e.g. 192.168.0.3.
    rti_wan_router_host: str = ""
    # Our own WAN port = the router NodePort + this offset, so a host-network
    # pod does not collide with the router's NodePort on the node. Set 0 when
    # the collector has its own pod IP (non-host-network).
    rti_wan_local_port_offset: int = 100
    # A full -peer string used verbatim instead of the udpv4_wan peer built
    # from the router host and the domain's NodePort. For experiments.
    rti_peer: str = ""
    # DDS security files (identity cert and key, CA, governance, permissions)
    # for rtiddsspy, held in a Kubernetes Secret in the tools namespace and
    # mounted read-only over the image's own set at rti_sdds_dir. Empty keeps
    # the image's files. The Secret's keys must be the file names the image's
    # QoS looks for under that directory. SCRUM-39.
    rti_security_secret: str = ""
    rti_sdds_dir: str = "/opt/axone-tools/sdds"
    # Where tshark and editcap are on the machine running the backend, when
    # they are not on PATH (the Windows installer does not add them), e.g.
    # C:\Program Files\Wireshark
    wireshark_bin_dir: str | None = None
    # Delete tool pods left over from a crash when the backend starts.
    sweep_tool_pods_on_startup: bool = True
    # Raw output of each job, one folder per job and plugin. Gitignored:
    # captures can contain PHI/PII.
    job_output_dir: str = "./data/jobs"
    # Files a field engineer hands in (e.g. a hospital's .pcap).
    upload_dir: str = "./data/uploads"
    upload_max_mb: int = 500

    class Config:
        env_file = ".env"


# Login settings that may also come from NAME_FILE, so a Kubernetes Secret
# volume can supply them (backend/SECRETS.md). Plain NAME / .env still work.
LOGIN_SECRET_NAMES = ("DEMO_AUTH_USERNAME", "DEMO_AUTH_PASSWORD", "AUTH_TOKEN_SECRET")


def load_login_secret_files(s: Settings) -> None:
    for name in LOGIN_SECRET_NAMES:
        path = os.environ.get(f"{name}_FILE")
        if not path:
            continue
        # The pod starts before `make secrets` has run: a missing file leaves
        # login unconfigured (503 on protected routes), not a crash loop.
        if not os.path.exists(path):
            continue
        field = name.lower()
        if getattr(s, field) is not None:   # also catches a value from .env
            raise AmbiguousSecretError(f"Both {name} and {name}_FILE are set. Set only one.")
        value = load_secret(name).reveal()
        setattr(s, field, value if field == "demo_auth_username" else SecretStr(value))


settings = Settings()
load_login_secret_files(settings)
