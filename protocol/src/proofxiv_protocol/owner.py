"""Owner statements: signed by an owner key, they change roles, keys, prompts and parameters."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, model_validator

from .enums import ReportTarget, Role
from .federation import PeerTrust

OWNER_STATEMENT_TYPE = "proofxiv.owner_statement.v1"
OWNER_SIGN_IN_TYPE = "proofxiv.owner_sign_in.v1"


class OwnerSignIn(BaseModel):
    """Signed with an owner key: a one-time link to sign in to the website as that owner (no
    GitHub needed, and nobody else can take an owner's login). Not a statement: not journaled."""

    type: str = OWNER_SIGN_IN_TYPE
    login: str
    issued_at: str
    nonce: str


class OwnerAction(StrEnum):
    GRANT_ROLE = "grant_role"
    REVOKE_ROLE = "revoke_role"
    REVOKE_KEY = "revoke_key"
    APPROVE_PROMPT = "approve_prompt"
    COMMIT_PROMPT = "commit_prompt"  # a held-out prompt: only its hash, the text stays private
    REVEAL_PROMPT = "reveal_prompt"  # the text of a committed prompt, after its rotation
    SET_PARAM = "set_param"
    HIDE = "hide"
    UNHIDE = "unhide"
    # federation: the owner vouches for a key (so that other instances can check signatures), and
    # chooses the labs this instance follows and how much it trusts them
    ATTEST_KEY = "attest_key"
    ADD_PEER = "add_peer"
    SET_PEER_TRUST = "set_peer_trust"
    REMOVE_PEER = "remove_peer"


class OwnerStatement(BaseModel):
    type: str = OWNER_STATEMENT_TYPE
    action: OwnerAction
    operator_login: str | None = None
    role: Role | None = None
    key_fingerprint: str | None = None
    revoke_from: str | None = None
    prompt_name: str | None = None
    prompt_version: int | None = None
    prompt_sha256: str | None = None
    prompt_text: str | None = None
    param: str | None = None
    value: Any = None
    target_type: ReportTarget | None = None
    target_id: str | None = None
    public_key: str | None = None
    peer_url: str | None = None
    peer_owner_key: str | None = None
    trust: PeerTrust | None = None
    reason: str = ""
    issued_at: str
    nonce: str

    @model_validator(mode="after")
    def _fields(self) -> OwnerStatement:
        a = self.action
        need: dict[OwnerAction, tuple[str, ...]] = {
            OwnerAction.GRANT_ROLE: ("operator_login", "role"),
            OwnerAction.REVOKE_ROLE: ("operator_login", "role"),
            OwnerAction.REVOKE_KEY: ("key_fingerprint",),
            OwnerAction.APPROVE_PROMPT: (
                "prompt_name",
                "prompt_version",
                "prompt_sha256",
                "prompt_text",
            ),
            OwnerAction.COMMIT_PROMPT: ("prompt_name", "prompt_version", "prompt_sha256"),
            OwnerAction.REVEAL_PROMPT: ("prompt_name", "prompt_version", "prompt_text"),
            OwnerAction.SET_PARAM: ("param",),
            OwnerAction.HIDE: ("target_type", "target_id"),
            OwnerAction.UNHIDE: ("target_type", "target_id"),
            OwnerAction.ATTEST_KEY: ("role", "public_key"),
            OwnerAction.ADD_PEER: ("peer_url", "peer_owner_key", "trust"),
            OwnerAction.SET_PEER_TRUST: ("peer_url", "trust"),
            OwnerAction.REMOVE_PEER: ("peer_url",),
        }
        missing = [f for f in need[a] if getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(f"{a} requires {', '.join(missing)}")
        return self
