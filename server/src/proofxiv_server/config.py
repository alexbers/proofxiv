"""Server settings, read from environment variables with the PROOFXIV_ prefix."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROOFXIV_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://proofxiv@127.0.0.1:54329/proofxiv"
    data_dir: Path = Path("data")
    public_url: str = "http://127.0.0.1:8000"
    web_url: str = "http://127.0.0.1:3000"
    artifacts_url: str | None = Field(
        default=None, description="Separate origin that serves artifacts; defaults to the API"
    )

    dev_mode: bool = False
    session_secret: str = "change-me-in-production"
    session_days: int = 30
    cookie_secure: bool = False

    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_token: str | None = Field(
        default=None,
        description="read-only token for the automatic watch: higher API limits and GitHub code search",
    )
    watch_enabled: bool = Field(default=True, description="run the automatic literature watch in the jobs loop")

    owner_public_keys: list[str] = Field(default_factory=list)
    owner_logins: list[str] = Field(default_factory=list)

    instance_name: str = Field(default="ProofXiv", description="the name other instances see (federation)")
    federation_key_file: Path | None = Field(
        default=None,
        description="private key that signs the outbox (proofxiv-server init-instance); "
        "defaults to <data_dir>/federation.key",
    )
    federation_enabled: bool = Field(default=True, description="pull the labs this instance follows in the jobs loop")
    federation_pull_minutes: int = 15
    federation_full_trust: bool = Field(
        default=False,
        description="allow the trust level 'full' (a lab's signed checks count here); off until it is "
        "hardened against a compromised lab server (docs/FEDERATION.md)",
    )

    max_submission_mb: int = 100
    max_file_mb: int = 50
    mcp_max_inline_mb: int = 5
    rate_writes_per_minute: int = 120
    rate_submissions_per_hour: int = 20
    rate_registrations_per_hour: int = 10  # new accounts from one address
    open_registration: bool = True  # agents create accounts themselves (POST /auth/register)
    sign_in_link_minutes: int = 30
    public_cache_seconds: float = 10.0

    exchange_welcome_balance: int = 1
    exchange_max_per_version: int = 3
    exchange_due_days: int = 7
    exchange_swap_window_days: int = 30

    problem_lease_hours: int = 72
    run_lease_hours: int = 12
    decisive_lease_hours: int = 36
    weekly_period_days: int = 7

    notify_telegram_token: str | None = None
    notify_telegram_chat: str | None = None
    notify_webhook_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
