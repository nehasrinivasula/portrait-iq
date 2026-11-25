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
    # CMS log snaps (/cms/sprsnap/*). Base URL only; auth is not known yet.
    cms_base_url: str | None = None
    # Pause between calls so a long pull doesn't load the live system.
    axone_request_delay_seconds: float = 0.5
    # Timezone the API's start/end timestamps are in.
    axone_timezone: str = "UTC"
    # Default range when no start time is given: one API call per hour.
    axone_default_lookback_hours: int = 24
    # Wait this long after an hour ends before pulling it, so logs that
    # devices upload a little late still land in that hour's pull.
    axone_settle_minutes: int = 15
    # CMS uses the same Log Export API as device logs, but GE has to confirm
    # its integer eventCategory before collection can be enabled. None keeps
    # the feature safely unavailable rather than guessing a category.
    axone_cms_event_category: int | None = None

    # --- Diagnostic tool plugins (SCRUM-39) ---
    # Tool pods go in our own namespace, never in Portrait's (pm) or GE's
    # (compute). In a pod the backend uses its service account; from a laptop
    # it uses this kubeconfig (or ~/.kube/config when unset).
    tools_namespace: str = "portrait-iq"
    # Comma-separated namespaces explicitly provisioned for diagnostic pods.
    tools_allowed_namespaces: str = ""
    # Optional private key mounted on the backend for read-only node diagnostics.
    # When unset, SSH uses the backend process's agent/default identities.
    firewall_ssh_identity_file: str | None = None
    # Namespaces the Kubernetes diagnostics tool reads (pods, events), comma-separated.
    # Empty: ours (tools_namespace) and Portrait's pm. Read-only RBAC for each is in
    # deploy/k8s/tool-rbac.yaml and deploy/k8s-portrait/.
    k8s_diagnostics_namespaces: str = ""
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
    # The shipped WAN DataReader QoS is self-inconsistent (max_samples finite
    # while max_samples_per_instance is unlimited), so rtiddsspy fails
    # create_datareader on most topics and floods its output (seen on the bench
    # 2026-11-17). When true, load a small override profile that inherits the
    # domain's profile and gives the DataReader a consistent history/resource
    # limits, so the readers create cleanly. SCRUM-39.
    rti_fix_reader_qos: bool = True
    # Where tshark and editcap are on the machine running the backend, when
    # they are not on PATH (the Windows installer does not add them), e.g.
    # C:\Program Files\Wireshark
    wireshark_bin_dir: str | None = None
    # True: delete tool pods left over from a crash when the backend starts,
    # without asking. False (default): leave them and let the engineer choose
    # to remove or keep them at the next sign-in (app/cleanup). Pods stop on
    # their own at activeDeadlineSeconds either way.
    sweep_tool_pods_on_startup: bool = False

    # Heavy work (log pull + ingest, PHI/PII scan) runs in a separate low-priority process, so Python's GIL and
    # long regex calls in it cannot stall /health. "inline" runs it inside the request, for tests and debugging.
    background_task_mode: str = "process"        # process | inline
    background_task_concurrency: int = 1         # tasks running at once; others wait as "queued"
    background_task_nice: int = 10               # worker CPU priority; higher = yields to the API
    # SQLite: wait this long for another process's write lock instead of failing at once, and let readers
    # and one writer work together (WAL). Turn WAL off only if the data volume cannot do shared memory.
    sqlite_busy_timeout_ms: int = 30000
    sqlite_wal: bool = True
    # Raw output of each job, one folder per job and plugin. Gitignored:
    # captures can contain PHI/PII.
    job_output_dir: str = "./data/jobs"
    # Files a field engineer hands in (e.g. a hospital's .pcap).
    upload_dir: str = "./data/uploads"
    upload_max_mb: int = 500
    # Run retention (SCRUM-105): a full Prometheus TSDB export is multi-GB, so
    # old run folders are pruned at the start of each run. The newest
    # job_retention_keep are always kept; beyond those, the oldest are deleted
    # until the total size of all run folders is under the cap. The cap stays
    # under the data PVC (40Gi) so the volume cannot fill. 0 keep / 0 cap
    # disables pruning. Every queued/running run is protected by the caller, and
    # a run younger than job_retention_min_age_seconds is never pruned, so a run
    # whose export is still being built or downloaded is not deleted under it.
    job_retention_keep: int = 5
    job_retention_max_total_mb: int = 32000
    job_retention_min_age_seconds: int = 900
    # SCRUM-105: Grafana / system-health collector. Read-only Grafana HTTP API,
    # reachable in-cluster with no internet (e.g. http://grafana.<ns>.svc:3000).
    # Empty base URL disables the tool. The token is a read-only Grafana
    # service-account / API token.
    grafana_base_url: str | None = None
    grafana_api_token: SecretStr | None = None
    grafana_timeout_seconds: float = 15.0
    # Snapshot size cap, also bounded by the job's max_output_mb limit.
    grafana_max_snapshot_mb: int = 16
    # Optional: pin the Prometheus data source by uid; empty auto-picks the
    # first Grafana data source of type "prometheus".
    grafana_datasource_uid: str = ""

    # SCRUM-105: Prometheus TSDB export. A full copy of the Prometheus
    # database can only be read from the node's filesystem (the admin
    # snapshot API is off and does not serve files, and the PVC lives in
    # another namespace). So a short-lived, privileged pod runs as root, reads
    # the Prometheus data dir from the node over a read-only hostPath, and
    # writes a compressed native-TSDB tar onto the backend's own data volume
    # -- never through the base64-exec path, which is for small files.
    # Empty hostPath leaves the collector off (like grafana_base_url).
    prometheus_tsdb_hostpath: str = ""
    # The backend's data PVC and its mount path: the pod mounts the same PVC
    # so the artifact it writes lands where the backend already reads it
    # (job_output_dir must live under this mount).
    prometheus_tsdb_data_pvc: str = "portrait-iq-data"
    prometheus_tsdb_data_mount: str = "/data"
    # "full" exports the whole database; a duration like "7d" is reserved for
    # a future windowed export and is rejected until implemented.
    prometheus_tsdb_scope: str = "full"
    # Compressor run inside the tool pod; must exist in the image. gzip ships
    # in the netshoot image; zstd does not, so it's the reliable default (the
    # TSDB is already compressed, so the ratio difference is small anyway).
    prometheus_tsdb_compressor: str = "gzip"       # "zstd" or "gzip"
    prometheus_tsdb_level: int = 3
    # Image for the export pod; empty uses netshoot_image (has tar + zstd).
    prometheus_tsdb_image: str = ""
    # Refuse rather than fill the node disk / package. The pod also checks the
    # data volume has room for the (uncompressed-sized) export before it runs.
    prometheus_tsdb_max_output_mb: int = 30000
    # tar + compress of a multi-GB TSDB can take many minutes.
    prometheus_tsdb_timeout_seconds: int = 3600

    class Config:
        env_file = ".env"


# Login settings that may also come from NAME_FILE, so a Kubernetes Secret
# volume can supply them (backend/SECRETS.md). Plain NAME / .env still work.
LOGIN_SECRET_NAMES = ("DEMO_AUTH_USERNAME", "DEMO_AUTH_PASSWORD", "AUTH_TOKEN_SECRET")
# Other file-backed secrets, loaded by the same mechanism. The Grafana Viewer
# token for the system-health collector (SCRUM-105).
FILE_SECRET_NAMES = LOGIN_SECRET_NAMES + ("GRAFANA_API_TOKEN",)


def load_login_secret_files(s: Settings) -> None:
    for name in FILE_SECRET_NAMES:
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


def diagnostics_namespaces() -> list[str]:
    """Namespaces the Kubernetes diagnostics tool reads, in order, without repeats."""
    raw = settings.k8s_diagnostics_namespaces.strip() or f"{settings.tools_namespace},pm"
    return list(dict.fromkeys(name.strip() for name in raw.split(",") if name.strip()))


def allowed_tools_namespaces() -> set[str]:
    """Namespaces explicitly provisioned for diagnostic pods, including the default."""
    return {settings.tools_namespace, *(
        name.strip() for name in settings.tools_allowed_namespaces.split(",") if name.strip()
    )}
