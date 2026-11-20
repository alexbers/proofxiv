"""Federation of ProofXiv instances: a lab runs its own ProofXiv, others subscribe to it.

The subscriber pulls; nothing is pushed into anyone's database. Trust is placed in the lab owner's
key, not in its server: every decisive record (run results, decisive reviews) is already signed by
an operator key, and the owner attests those keys with signed statements (`attest_key`), so a
subscriber checks the whole chain itself. The outbox lists only what the lab itself received
(no transitivity).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

FEDERATION_PROTOCOL = "proofxiv-federation/1"
OUTBOX_ENTRY_TYPE = "proofxiv.outbox_entry.v1"
WELL_KNOWN_PATH = "/.well-known/proofxiv"


class PeerTrust(StrEnum):
    """How much a subscriber trusts a lab it follows."""

    INDEX = "index"  # only the listing: title, abstract, problems, link; the precheck sees it
    REVIEW = "review"  # the paper itself; machine checks and decisive reviews are done here
    FULL = "full"  # the paper with the lab's signed machine checks and decisive reviews


class OutboxKind(StrEnum):
    PAPER_VERSION = "paper_version"
    RUN_RESULT = "run_result"
    DECISIVE_REVIEW = "decisive_review"
    LEVEL = "level"
    WITHDRAWAL = "withdrawal"


class InstanceInfo(BaseModel):
    """What `/.well-known/proofxiv` says about an instance."""

    protocol: str = FEDERATION_PROTOCOL
    name: str = Field(max_length=200)
    base_url: str = Field(max_length=300)
    owner_keys: list[str] = Field(max_length=10, description="owner public keys (ed25519, base64)")
    federation_key: str | None = Field(
        default=None, description="the key that signs outbox entries (attested by an owner statement)"
    )
    outbox: str = Field(default="/api/v1/federation/outbox", max_length=300)
    journal: str = Field(default="/api/v1/owner/journal", max_length=300)
    artifacts: str = Field(default="/api/v1/artifacts/{sha256}", max_length=500)


class ProblemKey(BaseModel):
    """How a paper's problem is matched to the subscriber's catalog: by the upstream statement
    (repository and qualified Lean name) when there is one, else shown by title only."""

    slug: str = Field(max_length=300)
    title: str = Field(max_length=300)
    variant: str | None = Field(default=None, max_length=200)
    upstream: dict[str, Any] | None = None


class PaperVersionBody(BaseModel):
    paper_id: str = Field(max_length=64)
    version: int = Field(ge=1, le=10_000)
    url: str = Field(max_length=2000)
    manifest: dict[str, Any]
    files: dict[str, str] = Field(description="path -> sha256")
    problems: list[ProblemKey] = Field(default_factory=list)
    submitter: str = Field(max_length=64, description="login at the lab")
    agent_name: str | None = Field(default=None, max_length=100)
    agent_model: str | None = Field(default=None, max_length=200)
    submitted_at: str = Field(max_length=40)


class RunResultBody(BaseModel):
    paper_id: str = Field(max_length=64)
    version: int = Field(ge=1, le=10_000)
    job: dict[str, Any] = Field(description="kind, inputs, lean_theorems, certificate_type of the lab's job")
    runner: str = Field(max_length=64, description="login of the runner at the lab")
    bundle: dict[str, Any] = Field(description="the runner's SignedBundle with a RunResult")


class DecisiveReviewBody(BaseModel):
    paper_id: str = Field(max_length=64)
    version: int = Field(ge=1, le=10_000)
    kind: str = Field(max_length=40, description="decisive task kind")
    adversarial: bool = False
    reviewer: str = Field(max_length=64, description="login of the trusted operator at the lab")
    bundle: dict[str, Any] = Field(description="the operator's SignedBundle with a TrustedResult")
    materials: dict[str, Any] = Field(description="what the reviewer saw; its hash is in the signed result")


class LevelBody(BaseModel):
    paper_id: str = Field(max_length=64)
    version: int = Field(ge=1, le=10_000)
    level: str = Field(max_length=20)


class WithdrawalBody(BaseModel):
    paper_id: str = Field(max_length=64)
    hidden: bool
    reason: str = Field(default="", max_length=2000)


BODY_FOR_KIND: dict[OutboxKind, type[BaseModel]] = {
    OutboxKind.PAPER_VERSION: PaperVersionBody,
    OutboxKind.RUN_RESULT: RunResultBody,
    OutboxKind.DECISIVE_REVIEW: DecisiveReviewBody,
    OutboxKind.LEVEL: LevelBody,
    OutboxKind.WITHDRAWAL: WithdrawalBody,
}


class OutboxEntry(BaseModel):
    """One record of a lab's outbox; served as a SignedBundle signed by its federation key."""

    type: str = OUTBOX_ENTRY_TYPE
    instance: str = Field(description="base URL of the lab")
    seq: int
    kind: OutboxKind
    created_at: str
    body: dict[str, Any]


class OutboxPage(BaseModel):
    entries: list[dict[str, Any]] = Field(description="SignedBundles with OutboxEntry payloads")
    next_after: int = Field(description="pass as ?after= to get the next page")
    last_seq: int
