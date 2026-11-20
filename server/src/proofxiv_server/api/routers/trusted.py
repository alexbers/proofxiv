"""The weekly trusted run, runners, official prompts and artifact upload."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, Response, UploadFile
from proofxiv_protocol.enums import DecisiveTaskKind
from proofxiv_protocol.runs import RunJobSpec
from proofxiv_protocol.signing import SignedBundle
from proofxiv_protocol.trusted import DecisiveTaskSpec
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...config import get_settings
from ...core import decisive, runs, storage
from ...core.bundles import refuse_executable
from ...core.errors import NotFound, TooLarge
from ...core.levels import job_claims
from ...core.security import SCOPE_TRUSTED
from ...db.models import Claim, OfficialPrompt
from ..deps import Session, User
from ..schemas import OkOut

router = APIRouter(tags=["trusted"])


class WeeklyStartIn(BaseModel):
    budget: dict[str, Any] = Field(default_factory=dict)


class WeeklyStartOut(BaseModel):
    id: str


@router.post("/trusted/weekly-runs", response_model=WeeklyStartOut)
async def start_weekly(body: WeeklyStartIn, user: User, session: Session) -> WeeklyStartOut:
    run = await decisive.start_weekly_run(session, user, body.budget)
    await session.commit()
    return WeeklyStartOut(id=str(run.id))


class LeaseTasksIn(BaseModel):
    limit: int = Field(default=5, ge=1, le=50)
    kinds: list[DecisiveTaskKind] = Field(default_factory=list)


@router.post("/trusted/weekly-runs/{run_id}/tasks", response_model=list[DecisiveTaskSpec])
async def lease_tasks(
    run_id: uuid.UUID, body: LeaseTasksIn, user: User, session: Session
) -> list[DecisiveTaskSpec]:
    specs = await decisive.lease_tasks(session, user, run_id, body.limit, body.kinds or None)
    await session.commit()
    return specs


class WeeklyFinishIn(BaseModel):
    stats: dict[str, Any] = Field(default_factory=dict)


@router.post("/trusted/weekly-runs/{run_id}/finish", response_model=OkOut)
async def finish_weekly(
    run_id: uuid.UUID, body: WeeklyFinishIn, user: User, session: Session
) -> OkOut:
    await decisive.finish_weekly_run(session, user, run_id, body.stats)
    await session.commit()
    return OkOut()


class TrustedResultOut(BaseModel):
    task_id: str
    status: str


@router.post("/trusted/results", response_model=TrustedResultOut)
async def trusted_result(bundle: SignedBundle, user: User, session: Session) -> TrustedResultOut:
    task = await decisive.apply_trusted_result(session, user, bundle)
    await session.commit()
    return TrustedResultOut(task_id=str(task.id), status=task.status.value)


class RunLeaseIn(BaseModel):
    ram_gb: float = Field(gt=0)
    cores: int = Field(ge=1)
    minutes: int = Field(ge=1)
    lean_versions: list[str] = Field(default_factory=list, description="Lean core toolchains")
    mathlib_versions: list[str] = Field(
        default_factory=list, description="Lean versions with a Mathlib project available"
    )
    checkers: bool = Field(default=False, description="reference checkers installed")
    references: bool = Field(
        default=False, description="can resolve bibliographies against arXiv and Crossref (network)"
    )
    exclude_jobs: list[uuid.UUID] = Field(
        default_factory=list, description="jobs this runner already gave back in this session"
    )


@router.post("/runs/lease", response_model=RunJobSpec | None)
async def lease_run(body: RunLeaseIn, user: User, session: Session, response: Response):
    job = await runs.lease_job(
        session,
        user,
        body.ram_gb,
        body.cores,
        body.minutes,
        body.lean_versions,
        body.mathlib_versions,
        body.checkers,
        body.exclude_jobs,
        body.references,
    )
    if job is None:
        response.status_code = 204
        return None
    claims = [await session.get(Claim, uuid.UUID(cid)) for cid in job_claims(job)]
    spec = runs.job_spec(job, [c for c in claims if c is not None])
    await session.commit()
    return spec


class ReleaseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/runs/{job_id}/release", response_model=OkOut)
async def release_run(job_id: uuid.UUID, body: ReleaseIn, user: User, session: Session) -> OkOut:
    """Give a leased job back (this runner's environment cannot run it); no verdict."""
    await runs.release_job(session, user, job_id, body.reason)
    await session.commit()
    return OkOut()


@router.post("/trusted/tasks/{task_id}/release", response_model=OkOut)
async def release_task(task_id: uuid.UUID, body: ReleaseIn, user: User, session: Session) -> OkOut:
    """Give a decisive task back to the queue (e.g. the model run failed)."""
    await decisive.release_task(session, user, task_id, body.reason)
    await session.commit()
    return OkOut()


class RunResultOut(BaseModel):
    run_id: str
    accepted: bool
    evaluation: str


@router.post("/runs/results", response_model=RunResultOut)
async def run_result(bundle: SignedBundle, user: User, session: Session) -> RunResultOut:
    run = await runs.submit_run(session, user, bundle)
    await session.commit()
    return RunResultOut(run_id=str(run.id), accepted=run.accepted, evaluation=run.result["evaluation"])


class PromptOut(BaseModel):
    name: str
    version: int
    sha256: str
    text: str | None = Field(description="None for a held-out prompt until it is revealed")
    active: bool
    held_out: bool = Field(description="committed by hash; the text is published after rotation")
    revealed_at: str | None


def prompt_out(p: OfficialPrompt) -> PromptOut:
    return PromptOut(
        name=p.name,
        version=p.version,
        sha256=p.sha256,
        text=p.text,
        active=p.active,
        held_out=p.text is None or p.revealed_at is not None,
        revealed_at=p.revealed_at.isoformat() if p.revealed_at else None,
    )


@router.get("/prompts", response_model=list[PromptOut])
async def list_prompts(session: Session) -> list[PromptOut]:
    rows = await session.execute(select(OfficialPrompt).order_by(OfficialPrompt.name, OfficialPrompt.version))
    return [prompt_out(p) for p in rows.scalars()]


@router.get("/prompts/{name}/{version}", response_model=PromptOut)
async def get_prompt(name: str, version: int, session: Session) -> PromptOut:
    prompt = (
        await session.execute(
            select(OfficialPrompt).where(OfficialPrompt.name == name, OfficialPrompt.version == version)
        )
    ).scalar_one_or_none()
    if prompt is None:
        raise NotFound("prompt not found")
    return prompt_out(prompt)


class ArtifactOut(BaseModel):
    sha256: str
    size: int


@router.post("/artifacts", response_model=ArtifactOut)
async def upload_artifact(
    file: UploadFile,
    user: User,
    session: Session,
    embargo: bool = Query(
        False, description="trusted run: a held-out review transcript, private until the prompt is revealed"
    ),
) -> ArtifactOut:
    """Upload a single file (e.g. a transcript or a run log); returns its sha256."""
    if embargo:
        user.require_scope(SCOPE_TRUSTED)
    data = await file.read()
    if len(data) > get_settings().max_file_mb * 1024 * 1024:
        raise TooLarge("file is too large")
    refuse_executable(file.filename or "upload.bin", data)
    sha = await storage.store_bytes(session, data, file.filename or "upload.bin", user.id, embargo)
    await session.commit()
    return ArtifactOut(sha256=sha, size=len(data))
