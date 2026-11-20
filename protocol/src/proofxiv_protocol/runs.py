"""Run jobs (machine checks executed by runners) and their signed results."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .enums import RunKind, RunVerdict
from .leanscan import is_native_decide_axiom
from .papers import Resources
from .signing import canonical_json, sha256_hex

RUN_RESULT_TYPE = "proofxiv.run_result.v1"

STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})


class ArtifactRef(BaseModel):
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    path: str = Field(description="Path inside the job's input directory")


def job_digest(
    kind: str,
    inputs: list[dict[str, Any]],
    lean_theorems: list[str],
    certificate_type: str | None,
    command: list[str],
    image: str,
) -> str:
    """What a job checks: its kind, command (the file it builds), image, input files and theorems.
    The runner signs it with the result, so that the result cannot be attached to another job
    (a follower of another lab checks it)."""
    files = sorted({(str(i["path"]), str(i["sha256"])) for i in inputs})
    return sha256_hex(
        canonical_json(
            {
                "kind": kind,
                "command": list(command),
                "image": image,
                "inputs": [{"path": p, "sha256": s} for p, s in files],
                "lean_theorems": sorted(lean_theorems),
                "certificate_type": certificate_type,
            }
        )
    )


class RunJobSpec(BaseModel):
    job_id: UUID
    kind: RunKind
    image: str = Field(description="Image name or digest the runner must use")
    command: list[str]
    inputs: list[ArtifactRef]
    resources: Resources
    lean_theorems: list[str] = Field(default_factory=list)
    certificate_type: str | None = None
    claim_statement: str | None = None

    def digest(self) -> str:
        return job_digest(
            self.kind.value,
            [i.model_dump() for i in self.inputs],
            self.lean_theorems,
            self.certificate_type,
            self.command,
            self.image,
        )


class LeanReport(BaseModel):
    theorems: dict[str, list[str]] = Field(
        default_factory=dict, description="theorem name -> axioms it depends on"
    )
    has_sorry: bool = False
    errors: list[str] = Field(default_factory=list)

    def uses_native_decide(self) -> bool:
        return any(
            is_native_decide_axiom(axiom) for axioms in self.theorems.values() for axiom in axioms
        )

    def nonstandard_axioms(self) -> set[str]:
        """Axioms beyond Lean's standard three and those of `native_decide` (which only the
        file's own `native_decide` can introduce: the file may not declare axioms or run code)."""
        return {
            axiom
            for axioms in self.theorems.values()
            for axiom in axioms
            if axiom not in STANDARD_AXIOMS and not is_native_decide_axiom(axiom)
        }


class CheckerReport(BaseModel):
    checker: str
    implementations: dict[str, bool] = Field(
        default_factory=dict, description="implementation name -> verdict"
    )
    details: str = ""


class ReferenceItem(BaseModel):
    key: str = Field(description="bib key or position in the bibliography")
    text: str = Field(max_length=600, description="the reference as written (shortened)")
    status: str = Field(description="verified | mismatch | not_found | unchecked")
    found_title: str | None = None
    source: str | None = Field(default=None, description="arxiv | crossref | datacite | zbmath | doi.org")
    url: str | None = None
    note: str = ""


class ReferenceReport(BaseModel):
    """The bibliography resolved against arXiv and Crossref (no model involved)."""

    items: list[ReferenceItem] = Field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for i in self.items if i.status == status)


class RunResult(BaseModel):
    """Payload of the signed bundle a runner submits."""

    type: str = RUN_RESULT_TYPE
    job_id: UUID
    job_sha256: str | None = Field(
        default=None, description="job_digest of the job's kind, input files and theorems (RunJobSpec.digest)"
    )
    verdict: RunVerdict
    outputs: dict[str, str] = Field(default_factory=dict, description="output name -> sha256")
    lean: LeanReport | None = None
    checker: CheckerReport | None = None
    references: ReferenceReport | None = None
    log_sha256: str | None = None
    peak_memory_mb: int | None = None
    duration_s: float | None = None
    runner: str = Field(description="Runner software and sandbox description")
    image_digest: str | None = None
    issued_at: str
    nonce: str
