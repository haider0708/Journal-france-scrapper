"""Application configuration, loaded from environment variables (and .env locally)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Legifrance / PISTE API ---------------------------------------------
    piste_client_id: str = ""
    piste_client_secret: str = ""
    piste_sandbox: bool = False

    # -- What to search for -------------------------------------------------
    # ";"-separated list of names. A full or partial token match triggers an alert.
    search_names: str = ""
    # How many recent JO editions to look back over (only unprocessed ones run).
    lookback_editions: int = 15
    # Politeness delay between API calls, in seconds.
    request_delay_s: float = 0.3

    # -- Email alerts (Resend) ----------------------------------------------
    email_enabled: bool = False
    resend_api_key: str = ""
    email_from: str = "onboarding@resend.dev"
    email_to: str = ""  # ","-separated list of recipients

    # -- State persistence --------------------------------------------------
    # If set, a Postgres DSN is used (required on Render -- ephemeral FS).
    # If empty, falls back to local files (fine for development).
    database_url: str = ""
    state_file: str = "state.json"
    matches_file: str = "matches.jsonl"

    # -- Web service security -----------------------------------------------
    # Shared secret required to call POST /scan (Bearer token or ?token=).
    cron_secret: str = ""

    # -- Internal scheduler (for always-on hosts like a VPS/Docker) ---------
    # When enabled, the app triggers its own scans on an interval -- no
    # external cron needed. Disable on Render (use the GitHub Actions trigger).
    scheduler_enabled: bool = True
    scan_interval_minutes: int = 720  # twice a day
    scan_on_startup: bool = True

    # -- Misc ---------------------------------------------------------------
    log_level: str = "INFO"

    @property
    def names(self) -> list[str]:
        return [n.strip() for n in self.search_names.split(";") if n.strip()]

    @property
    def recipients(self) -> list[str]:
        return [a.strip() for a in self.email_to.split(",") if a.strip()]

    @property
    def token_url(self) -> str:
        host = "sandbox-oauth" if self.piste_sandbox else "oauth"
        return f"https://{host}.piste.gouv.fr/api/oauth/token"

    @property
    def api_base(self) -> str:
        host = "sandbox-api" if self.piste_sandbox else "api"
        return f"https://{host}.piste.gouv.fr/dila/legifrance/lf-engine-app"


@lru_cache
def get_settings() -> Settings:
    return Settings()
