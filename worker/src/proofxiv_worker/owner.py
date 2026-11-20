"""Owner statements, signed with the owner's root key and published in the public journal."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from proofxiv_protocol.owner import OwnerSignIn, OwnerStatement
from proofxiv_protocol.signing import SignedBundle, new_nonce, utc_now_iso

from . import prompts
from .client import Client


def statement(owner_private_key: str, **fields: Any) -> SignedBundle:
    payload = OwnerStatement(issued_at=utc_now_iso(), nonce=new_nonce(), **fields)
    return SignedBundle.create(payload, owner_private_key)


def send(client: Client, owner_private_key: str, **fields: Any) -> None:
    bundle = statement(owner_private_key, **fields)
    client.post("/owner/statements", json=bundle.model_dump(mode="json"))


def sign_in_link(client: Client, owner_private_key: str, login: str) -> str:
    """A one-time website sign-in link for an owner (no GitHub needed)."""
    request = OwnerSignIn(login=login, issued_at=utc_now_iso(), nonce=new_nonce())
    bundle = SignedBundle.create(request, owner_private_key)
    return client.post("/auth/owner-sign-in", json=bundle.model_dump(mode="json"))["url"]


def approve_prompts(client: Client, owner_private_key: str, names: list[str] | None = None) -> list[str]:
    """Approve the local official prompts that differ from the active server versions."""
    current = {p["name"]: p for p in client.get("/prompts") if p["active"]}
    approved = []
    for name in names or prompts.OFFICIAL_NAMES:
        text = prompts.official(name)
        digest = prompts.sha256(text)
        active = current.get(name)
        if active is not None and active["sha256"] == digest:
            continue
        version = (active["version"] + 1) if active else 1
        send(
            client,
            owner_private_key,
            action="approve_prompt",
            prompt_name=name,
            prompt_version=version,
            prompt_sha256=digest,
            prompt_text=text,
        )
        approved.append(f"{name} v{version}")
    return approved


def commit_heldout(client: Client, owner_private_key: str, secret: str, name: str = "heldout_review") -> tuple[int, Path]:
    """A new version of a held-out prompt: the server gets its hash, this machine keeps the text
    (the weekly run needs it: copy the file to that machine if it is another one)."""
    version = max((p["version"] for p in client.get("/prompts") if p["name"] == name), default=0) + 1
    text = prompts.compose_heldout(name, secret)
    path = prompts.save_heldout(name, version, text)  # first: a committed hash must not lose its text
    send(
        client,
        owner_private_key,
        action="commit_prompt",
        prompt_name=name,
        prompt_version=version,
        prompt_sha256=prompts.sha256(text),
    )
    return version, path


def reveal_heldout(client: Client, owner_private_key: str, version: int, name: str = "heldout_review") -> None:
    """Publish a held-out prompt's text; the transcripts of its reviews become public."""
    text = prompts.heldout_text(name, version)
    if text is None:
        raise FileNotFoundError(f"{prompts.heldout_path(name, version)} is not on this machine")
    send(client, owner_private_key, action="reveal_prompt", prompt_name=name, prompt_version=version, prompt_text=text)


def rotate_heldout(
    client: Client, owner_private_key: str, secret: str, name: str = "heldout_review"
) -> tuple[int, list[int]]:
    """Commit a new version, then reveal the older ones still held out."""
    version, _ = commit_heldout(client, owner_private_key, secret, name)
    revealed = []
    for p in client.get("/prompts"):
        if p["name"] == name and p["version"] < version and p["text"] is None:
            reveal_heldout(client, owner_private_key, p["version"], name)
            revealed.append(p["version"])
    return version, revealed
