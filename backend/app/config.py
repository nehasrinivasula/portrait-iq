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

    class Config:
        env_file = ".env"


settings = Settings()
