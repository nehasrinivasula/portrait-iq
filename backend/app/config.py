"""
Central settings. Everything here is a placeholder default for local/lab use —
override via a .env file before this touches anything resembling production.
"""
from pydantic_settings import BaseSettings
from pydantic import SecretStr


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

    class Config:
        env_file = ".env"


settings = Settings()
