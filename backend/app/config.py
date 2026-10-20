"""
Central settings. Everything here is a placeholder default for local/lab use —
override via a .env file before this touches anything resembling production.
"""
from pydantic_settings import BaseSettings


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

    class Config:
        env_file = ".env"


settings = Settings()
