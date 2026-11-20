"""Run jobs: leasing to runners, verifying signed results, unlocking decisive tasks (C2)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from proofxiv_protocol.enums import (
    DecisiveTaskStatus,
    Role,
    RunJobStatus,
    RunKind,
    RunVerdict,
)
from proofxiv_protocol.papers import Resources
from proofxiv_protocol.runs import RUN_RESULT_TYPE, ArtifactRef, RunJobSpec, RunResult, job_digest
from proofxiv_protocol.signing import SignedBundle
from sqlalchemy import Float, and_, cast, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Claim, DecisiveTask, Paper, Run, RunJob
from . import audit, federation
from .errors import Conflict, Forbidden, Invalid, NotFound
from .identity import Principal, active_roles, now, verify_operator_bundle
from .levels import recompute_paper
from .security import SCOPE_RUNNER


def digest_of(job: RunJob) -> str:
    return job_digest(job.kind.value, job.inputs, job.lean_theorems, job.certificate_type, job.command, job.image)


def job_spec(job: RunJob, claims: list[Claim]) -> RunJobSpec:
    return RunJobSpec(
        job_id=job.id,
        kind=job.kind,
        image=job.image,
        command=job.command,
        inputs=[ArtifactRef(**i) for i in job.inputs],
        resources=Resources(**job.resources),
        lean_theorems=job.lean_theorems,
        certificate_type=job.certificate_type,
        claim_statement="\n".join(c.statement for c in claims) or None,
    )


async def lease_job(
    session: AsyncSession,
    principal: Principal,
    ram_gb: float,
    cores: int,
    minutes: int,
    lean_versions: list[str] | None = None,
    mathlib_versions: list[str] | None = None,
    checkers: bool = False,
    exclude: list[uuid.UUID] | None = None,
    references: bool = False,
) -> RunJob | None:
    """Give the runner the oldest job it can actually run (resources and environment)."""
    principal.require_scope(SCOPE_RUNNER)
    if Role.RUNNER not in await active_roles(session, principal.id):
        raise Forbidden("only trusted runners receive jobs at this stage")
    res = RunJob.resources
    fits = and_(
        cast(res["ram_gb"].astext, Float) <= ram_gb,
        cast(res["cores"].astext, Float) <= cores,
        cast(res["minutes"].astext, Float) <= minutes,
    )
    is_mathlib = func.coalesce(res["mathlib"].as_boolean(), False)
    version = res["lean_version"].astext
    can_run = or_(
        and_(RunJob.kind == RunKind.LEAN, ~is_mathlib, version.in_(lean_versions or [])),
        and_(RunJob.kind == RunKind.LEAN, is_mathlib, version.in_(mathlib_versions or [])),
        and_(RunJob.kind == RunKind.CHECKER, true() if checkers else false()),
        and_(RunJob.kind == RunKind.REFERENCES, true() if references else false()),
    )
    stmt = (
        select(RunJob)
        .where(
            RunJob.cancelled.is_(False),
            RunJob.id.not_in(exclude or []),
            fits,
            can_run,
            or_(
                RunJob.status.in_([RunJobStatus.QUEUED, RunJobStatus.WAITING_RESOURCES]),
                and_(RunJob.status == RunJobStatus.LEASED, RunJob.lease_expires_at < now()),
            ),
        )
        .order_by(RunJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    job.status = RunJobStatus.LEASED
    job.leased_by = principal.id
    job.lease_expires_at = now() + timedelta(hours=get_settings().run_lease_hours)
    return job


async def release_job(
    session: AsyncSession, principal: Principal, job_id: uuid.UUID, reason: str
) -> RunJob:
    """The runner cannot run this job after all (environment problem): back to the queue,
    without any verdict about the submission."""
    principal.require_scope(SCOPE_RUNNER)
    job = await session.get(RunJob, job_id)
    if job is None:
        raise NotFound("job not found")
    if job.status is not RunJobStatus.LEASED or job.leased_by != principal.id:
        raise Conflict("job is not leased by you")
    job.status = RunJobStatus.QUEUED
    job.leased_by = None
    job.lease_expires_at = None
    audit.record(session, principal.id, "run.released", "run_job", job.id, reason=reason[:500])
    return job


def evaluate(job: RunJob, result: RunResult) -> tuple[bool, bool, str]:
    """Return (accepted, native_decide, reason)."""
    if result.verdict is not RunVerdict.PASS:
        return False, False, f"verdict {result.verdict}"
    if job.kind is RunKind.LEAN:
        lean = result.lean
        if lean is None:
            return False, False, "no Lean report"
        if lean.has_sorry or lean.errors:
            return False, False, "Lean reported sorry or errors"
        missing = [t for t in job.lean_theorems if not _theorem_present(t, lean.theorems)]
        if missing:
            return False, False, f"theorems not checked: {missing}"
        extra = lean.nonstandard_axioms()
        if extra:
            return False, False, f"nonstandard axioms: {sorted(extra)}"
        return True, lean.uses_native_decide(), "ok"
    if job.kind is RunKind.REFERENCES:
        if result.references is None:
            return False, False, "no references report"
        return True, False, "ok"
    checker = result.checker
    if checker is None or len(checker.implementations) < 2:
        return False, False, "need two independent checker implementations"
    if not all(checker.implementations.values()):
        return False, False, "checker implementations disagree or reject"
    return True, False, "ok"


def _theorem_present(name: str, theorems: dict[str, list[str]]) -> bool:
    return name in theorems or any(t.endswith("." + name) for t in theorems)


async def submit_run(session: AsyncSession, principal: Principal, bundle: SignedBundle) -> Run:
    principal.require_scope(SCOPE_RUNNER)
    if bundle.payload.get("type") != RUN_RESULT_TYPE:
        raise Invalid("not a run result")
    try:
        result = RunResult.model_validate(bundle.payload)
    except ValueError as exc:
        raise Invalid(f"bad run result: {exc}") from exc
    key = await verify_operator_bundle(session, bundle, principal.operator, Role.RUNNER)
    job = await session.get(RunJob, result.job_id)
    if job is None:
        raise NotFound("job not found")
    if job.status is not RunJobStatus.LEASED or job.leased_by != principal.id:
        raise Conflict("job is not leased by you")
    if result.job_sha256 is not None and result.job_sha256 != digest_of(job):
        raise Invalid("the result is signed for other files or theorems than this job")

    accepted, native, reason = evaluate(job, result)
    run = Run(
        job_id=job.id,
        runner_id=principal.id,
        signing_key_id=key.id,
        trusted=True,
        verdict=result.verdict,
        accepted=accepted,
        native_decide=native,
        result=result.model_dump(mode="json") | {"evaluation": reason},
        bundle=bundle.model_dump(mode="json"),
    )
    session.add(run)
    job.leased_by = None
    job.lease_expires_at = None
    if result.verdict is RunVerdict.INSUFFICIENT_RESOURCES:
        job.status = RunJobStatus.WAITING_RESOURCES
        resources = dict(job.resources)
        resources["ram_gb"] = max(float(resources.get("ram_gb", 4)) * 2, 8.0)
        job.resources = resources
    else:
        job.status = RunJobStatus.DONE
        job.finished_at = now()
    await session.flush()
    await federation.publish_run(session, job, run, principal.operator.login)
    await unlock_decisive(session, job.paper_id, job.version)
    paper = await session.get(Paper, job.paper_id)
    assert paper is not None
    await recompute_paper(session, paper)
    audit.record(
        session, principal.id, "run.submitted", "run_job", job.id, verdict=result.verdict.value
    )
    return run


async def unlock_decisive(session: AsyncSession, paper_id: uuid.UUID, version: int) -> None:
    """When all machine checks of a version finished, unblock or cancel its decisive tasks."""
    jobs = list(
        (
            await session.execute(
                select(RunJob).where(
                    RunJob.paper_id == paper_id,
                    RunJob.version == version,
                    RunJob.cancelled.is_(False),
                )
            )
        ).scalars()
    )
    if any(j.status is not RunJobStatus.DONE for j in jobs):
        return
    # the references check informs the reviewers but never cancels them
    jobs = [j for j in jobs if j.kind is not RunKind.REFERENCES]
    accepted_jobs = set(
        (
            await session.execute(
                select(Run.job_id).where(
                    Run.job_id.in_([j.id for j in jobs]),
                    Run.accepted.is_(True),
                    Run.invalidated.is_(False),
                )
            )
        ).scalars()
    )
    all_ok = all(j.id in accepted_jobs for j in jobs)
    tasks = (
        await session.execute(
            select(DecisiveTask).where(
                DecisiveTask.paper_id == paper_id,
                DecisiveTask.version == version,
                DecisiveTask.status == DecisiveTaskStatus.BLOCKED,
            )
        )
    ).scalars()
    for task in tasks:
        if all_ok:
            task.status = DecisiveTaskStatus.READY
            task.blocked_reason = None
        else:
            task.status = DecisiveTaskStatus.CANCELLED
            task.blocked_reason = "machine checks failed"
            task.done_at = now()
