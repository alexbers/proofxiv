"""Worker configuration: $PROOFXIV_HOME/config.json (default ~/.config/proofxiv)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class HarnessConfig(BaseModel):
    type: str = Field(description="claude-code | command | stub")
    vendor: str = "anthropic"
    model: str | None = None
    command: list[str] = Field(default_factory=list, description="for type=command")
    max_turns: int = 40
    max_budget_usd: float | None = None
    isolated: bool = Field(default=True, description="claude-code: --safe-mode (no user customizations)")


class Config(BaseModel):
    server: str = "http://127.0.0.1:8000"
    token: str | None = None
    key_file: str | None = None
    owner_key_file: str | None = None
    sandbox: str = Field(default="bwrap", description="bwrap | podman | none")
    lean_toolchains: dict[str, str] = Field(
        default_factory=dict, description="Lean version -> toolchain dir containing bin/lean"
    )
    lean_projects: dict[str, str] = Field(
        default_factory=dict, description="Lean version -> lake project dir with Mathlib built"
    )
    mathlib_commits: dict[str, str] = Field(
        default_factory=dict,
        description="Lean version -> the Mathlib commit its project must be at (checked before each job)",
    )
    rust_checker: str | None = Field(default=None, description="path to the proofxiv-check binary")
    harnesses: dict[str, HarnessConfig] = Field(
        default_factory=lambda: {"anthropic": HarnessConfig(type="claude-code", vendor="anthropic")}
    )
    check_references: bool = Field(
        default=True, description="resolve bibliographies against arXiv and Crossref (needs network)"
    )
    contact_email: str | None = Field(
        default=None, description="sent to Crossref and arXiv with bibliography lookups (polite pool)"
    )
    runner_ram_gb: float = 8
    runner_cores: int = 2
    runner_minutes: int = 60


def home() -> Path:
    """Not created here: reading the config must work where home is not writable (a reviewer
    subagent's sandbox). Whoever writes into it creates it."""
    return Path(os.environ.get("PROOFXIV_HOME", Path.home() / ".config" / "proofxiv"))


def config_path() -> Path:
    return home() / "config.json"


def token_path() -> Path:
    """Where agents keep their token between sessions (see start.md)."""
    return home() / "token"


def save_token(token: str) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(token + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load() -> Config:
    path = config_path()
    config = Config.model_validate_json(path.read_text()) if path.exists() else Config()
    if not config.token and token_path().exists():  # a token saved by an agent
        config.token = token_path().read_text().strip() or None
    return config


def save(config: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = config
    if config.token and token_path().exists() and token_path().read_text().strip() == config.token:
        saved = config.model_copy(update={"token": None})  # it lives in the token file: one copy only
    tmp = path.with_suffix(".part")
    tmp.write_text(saved.model_dump_json(indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def update(**fields: Any) -> Config:
    config = load()
    for key, value in fields.items():
        setattr(config, key, value)
    save(config)
    return config


def workdir(*parts: str) -> Path:
    path = home().joinpath("work", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
