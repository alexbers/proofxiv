"""Prompt texts. Official prompts are approved (signed) by the owners and versioned on the server."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..config import home

HERE = Path(__file__).parent
OFFICIAL_NAMES = [
    "novelty_consistency",
    "novelty_audit",
    "anchor_review",
    "adversarial_review",
    "statement_audit",
    "moderation",
    "confirm_problem",
    "confirm_event",
    "sync",
    "digest",
]
# held-out prompts: the role text (official/heldout_review.md) is public, the owner's further
# instructions are not; the server keeps only the hash of the full text until it is revealed
HELDOUT_NAMES = ["heldout_review"]
HELDOUT_HEADING = "## Further instructions (held out: published after rotation)"


def official(name: str) -> str:
    """The full official prompt for a role: common rules + role instructions."""
    common = (HERE / "official" / "_common.md").read_text()
    role = (HERE / "official" / f"{name}.md").read_text()
    return common.rstrip() + "\n\n" + role


def compose_heldout(name: str, secret: str) -> str:
    """The full held-out prompt: the public role text plus the owner's private instructions."""
    return official(name).rstrip() + "\n\n" + HELDOUT_HEADING + "\n\n" + secret.strip() + "\n"


def heldout_path(name: str, version: int) -> Path:
    return home() / "heldout" / f"{name}.v{version}.md"


def save_heldout(name: str, version: int, text: str) -> Path:
    path = heldout_path(name, version)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def heldout_text(name: str, version: int) -> str | None:
    """The private text of a held-out prompt kept on this machine, if any."""
    path = heldout_path(name, version)
    return path.read_text() if path.exists() else None


def default(name: str) -> str:
    return (HERE / "default" / f"{name}.md").read_text()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
