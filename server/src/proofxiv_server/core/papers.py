"""Submissions: validation (C1), storage, claims, run jobs and decisive tasks per version."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from proofxiv_protocol.enums import (
    ClaimKind,
    DecisiveTaskKind,
    DecisiveTaskStatus,
    NoveltyStatus,
    ReviewKind,
    ReviewOrigin,
    RunJobStatus,
    RunKind,
    TrustLevel,
)
from proofxiv_protocol.federation import PeerTrust
from proofxiv_protocol.papers import ClaimSpec, PaperManifest, Resources
from proofxiv_protocol.reviews import ReviewBody
from proofxiv_protocol.signing import canonical_json, sha256_hex
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    Claim,
    DecisiveTask,
    ExchangeEntry,
    NoveltyChange,
    Paper,
    PaperProblem,
    PaperVersion,
    Peer,
    Problem,
    Quantity,
    Review,
    ReviewAssignment,
    ReviewRequest,
    RunJob,
)
from . import audit, federation, storage
from .bundle_safety import refuse_unsafe
from .bundles import refuse_executable
from .decisive import active_prompt
from .errors import Conflict, Forbidden, Invalid, NotFound, TooLarge
from .identity import Principal, now
from .leanscan import LeanFiles, lean_version
from .levels import recompute_paper
from .params import get_param
from .problems import compare_with_records
from .security import SCOPE_AGENT

LEAN_INPUT_SUFFIXES = (".lean",)
BIBLIOGRAPHY_SUFFIXES = (".bib", ".tex", ".bbl")
LEAN_PROJECT_FILES = ("lakefile.lean", "lakefile.toml", "lean-toolchain", "lake-manifest.json")


def lean_sources(files: dict[str, bytes]) -> dict[str, str]:
    return {
        path: data.decode("utf-8", errors="replace")
        for path, data in files.items()
        if path.endswith(LEAN_INPUT_SUFFIXES)
    }


@dataclass
class SubmissionResult:
    paper: Paper
    version: PaperVersion
    claims: list[Claim]
    similar: list[Paper] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _check_path(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not path or "\\" in path:
        raise Invalid(f"bad file path '{path}'")
    return str(p)


def _bundle_digest(manifest: PaperManifest, hashes: dict[str, str]) -> str:
    return sha256_hex(
        canonical_json({"manifest": manifest.model_dump(mode="json"), "files": hashes})
    )


async def submit_paper(
    session: AsyncSession,
    principal: Principal,
    manifest: PaperManifest,
    files: dict[str, bytes],
    paper_id: uuid.UUID | None = None,
    notes: str = "",
    origin: Peer | None = None,
) -> SubmissionResult:
    """A new paper or a new version. `origin`: the lab it came from (federation); from a fully
    trusted lab its machine checks and decisive reviews arrive signed instead of running here."""
    principal.require_scope(SCOPE_AGENT)
    settings = get_settings()
    files = {_check_path(p): data for p, data in files.items()}
    for path, data in files.items():
        refuse_executable(path, data)
    refuse_unsafe(files)
    total = sum(len(d) for d in files.values())
    if total > settings.max_submission_mb * 1024 * 1024:
        raise TooLarge(f"submission exceeds {settings.max_submission_mb} MB")
    for path, data in files.items():
        if len(data) > settings.max_file_mb * 1024 * 1024:
            raise TooLarge(f"file '{path}' exceeds {settings.max_file_mb} MB")
    missing = sorted(manifest.referenced_files() - files.keys())
    if missing:
        raise Invalid("files referenced in paper.json are missing", missing=missing)

    hashes = {path: sha256_hex(data) for path, data in sorted(files.items())}
    bundle_sha = _bundle_digest(manifest, hashes)
    duplicate = (
        await session.execute(
            select(PaperVersion.paper_id).where(PaperVersion.bundle_sha256 == bundle_sha)
        )
    ).first()
    if duplicate is not None:
        raise Conflict("an identical submission already exists", paper_id=str(duplicate[0]))

    problems = await _resolve_problems(session, manifest)

    if paper_id is not None:
        paper = await session.get(Paper, paper_id)
        if paper is None:
            raise NotFound("paper not found")
        if paper.submitter_id != principal.id:
            raise Forbidden("only the submitter can add versions")
        await _retire_version(session, paper)
        paper.current_version += 1
        paper.title, paper.abstract, paper.byline = (
            manifest.title,
            manifest.abstract,
            manifest.byline,
        )
        paper.ai_contribution = manifest.ai_contribution
        paper.human_involvement = manifest.human_involvement
        paper.verified_at = None
        similar: list[Paper] = []
    else:
        similar = await similar_papers(session, manifest.title)
        paper = Paper(
            submitter_id=principal.id,
            submitter_agent_id=principal.agent.id if principal.agent else None,
            title=manifest.title,
            abstract=manifest.abstract,
            byline=manifest.byline,
            ai_contribution=manifest.ai_contribution,
            human_involvement=manifest.human_involvement,
            current_version=1,
            origin_peer_id=origin.id if origin else None,
        )
        session.add(paper)
        await session.flush()

    for path, data in files.items():
        await storage.store_bytes(session, data, path, principal.id)

    version = PaperVersion(
        paper_id=paper.id,
        version=paper.current_version,
        manifest=manifest.model_dump(mode="json"),
        files=hashes,
        pdf_sha256=hashes.get(manifest.files.pdf) if manifest.files.pdf else None,
        bundle_sha256=bundle_sha,
        notes=notes,
    )
    session.add(version)
    linked = set(
        (await session.execute(select(PaperProblem.problem_id).where(PaperProblem.paper_id == paper.id))).scalars()
    )
    for problem in problems.values():
        if problem.id not in linked:
            session.add(PaperProblem(paper_id=paper.id, problem_id=problem.id))
    await session.flush()

    warnings: list[str] = []
    lean = LeanFiles(lean_sources(files))
    claims = [
        await _create_claim(session, paper, version, spec, lean, hashes, problems, warnings)
        for spec in manifest.claims
    ]
    await _create_author_reviews(session, principal, paper, version, manifest, files)
    external = origin is not None and origin.trust is PeerTrust.FULL
    checks_refs = await _create_run_jobs(session, paper, version, manifest, claims, hashes, lean, external)
    if not external:
        await _create_decisive_tasks(session, paper, version, manifest, claims, checks_refs)

    paper.novelty_status = NoveltyStatus.CLAIMED
    paper.novelty_category = None
    session.add(
        NoveltyChange(
            paper_id=paper.id,
            category=None,
            status=NoveltyStatus.CLAIMED,
            reason=f"author statement (version {version.version})",
        )
    )
    await session.flush()
    await recompute_paper(session, paper)
    federation.publish_paper_version(session, paper, version, list(problems.values()), principal)
    audit.record(
        session, principal.id, "paper.submitted", "paper", paper.id, version=version.version
    )
    return SubmissionResult(paper, version, claims, similar, warnings)


async def _resolve_problems(session: AsyncSession, manifest: PaperManifest) -> dict[str, Problem]:
    slugs = set(manifest.problem_slugs) | {c.problem_slug for c in manifest.claims if c.problem_slug}
    if not slugs:
        return {}
    rows = await session.execute(select(Problem).where(Problem.slug.in_(slugs)))
    found = {p.slug: p for p in rows.scalars()}
    unknown = sorted(slugs - found.keys())
    if unknown:
        raise Invalid("unknown problem slugs", unknown=unknown)
    return found


async def similar_papers(session: AsyncSession, title: str) -> list[Paper]:
    rows = await session.execute(
        select(Paper)
        .where(func.similarity(Paper.title, title) > 0.6)
        .order_by(func.similarity(Paper.title, title).desc())
        .limit(5)
    )
    return list(rows.scalars())


async def _create_claim(
    session: AsyncSession,
    paper: Paper,
    version: PaperVersion,
    spec: ClaimSpec,
    lean: LeanFiles,
    hashes: dict[str, str],
    problems: dict[str, Problem],
    warnings: list[str],
) -> Claim:
    issues: list[str] = []
    labels: list[str] = []
    problem = problems.get(spec.problem_slug) if spec.problem_slug else None
    quantity: Quantity | None = None
    if spec.quantity is not None:
        if problem is None:
            raise Invalid(f"claim {spec.key}: a quantity needs problem_slug")
        quantity = (
            await session.execute(
                select(Quantity).where(
                    Quantity.problem_id == problem.id, Quantity.key == spec.quantity
                )
            )
        ).scalar_one_or_none()
        if quantity is None:
            raise Invalid(f"claim {spec.key}: problem has no quantity '{spec.quantity}'")

    if spec.kind is ClaimKind.LEAN:
        assert spec.lean_file
        modules, missing = lean.modules(spec.lean_file)
        issues += missing
        checked = [spec.lean_file] + [path for _, path in modules]
        found_flags: set[str] = set()
        for path in checked:
            found_issues, flags = lean.scan(path)
            prefix = "" if path == spec.lean_file else f"{path}: "
            issues += [prefix + issue for issue in found_issues]
            found_flags |= flags
        labels += sorted(found_flags)
        for theorem in spec.lean_theorems:
            if not lean.declares(checked, theorem):
                issues.append(f"theorem `{theorem}` is not declared in {spec.lean_file} or its modules")

    record_status = None
    if quantity is not None and spec.value is not None and spec.bound is not None:
        record_status = await compare_with_records(session, quantity.id, spec.value, spec.bound)
        if record_status["status"] == "not_better":
            warnings.append(
                f"claim {spec.key}: {spec.value} does not beat the recorded best "
                f"{record_status['previous']}"
            )

    claim = Claim(
        paper_id=paper.id,
        version=version.version,
        key=spec.key,
        kind=spec.kind,
        statement=spec.statement,
        problem_id=problem.id if problem else None,
        quantity_id=quantity.id if quantity else None,
        value=spec.value,
        bound=spec.bound,
        lean_file_sha256=hashes.get(spec.lean_file) if spec.lean_file else None,
        lean_theorems=spec.lean_theorems,
        certificate_type=spec.certificate_type,
        certificate_sha256=hashes.get(spec.certificate_file) if spec.certificate_file else None,
        depends_on=spec.depends_on,
        level=TrustLevel.C0 if issues else TrustLevel.C1,
        labels=labels,
        c1_issues=issues,
        record_status=record_status,
    )
    session.add(claim)
    await session.flush()
    return claim


async def _create_author_reviews(
    session: AsyncSession,
    principal: Principal,
    paper: Paper,
    version: PaperVersion,
    manifest: PaperManifest,
    files: dict[str, bytes],
) -> None:
    for spec in manifest.author_reviews:
        raw = files[spec.file].decode("utf-8", errors="replace")
        body: dict[str, Any] | None = None
        body_md: str | None = raw
        if spec.file.endswith(".json"):
            try:
                body = ReviewBody.model_validate(json.loads(raw)).model_dump(mode="json")
                body_md = None
            except ValueError as exc:
                raise Invalid(f"author review {spec.file} is not a valid review: {exc}") from exc
        session.add(
            Review(
                paper_id=paper.id,
                version=version.version,
                origin=ReviewOrigin.AUTHOR,
                kind=ReviewKind.REVIEW,
                reviewer_id=principal.id,
                reviewer_agent_id=principal.agent.id if principal.agent else None,
                body=body,
                body_md=body_md,
                declared_model=spec.model or (body or {}).get("model"),
            )
        )


def _lean_inputs(hashes: dict[str, str], paths: list[str]) -> list[dict]:
    """The Lean file, its local modules and the bundle's Lean project files."""
    wanted = set(paths) | {p for p in hashes if PurePosixPath(p).name in LEAN_PROJECT_FILES}
    return [{"sha256": hashes[p], "path": p} for p in sorted(wanted)]


def _largest(resources: list[Resources | None]) -> dict[str, Any]:
    given = [r or Resources() for r in resources]
    return Resources(
        ram_gb=max(r.ram_gb for r in given),
        cores=max(r.cores for r in given),
        minutes=max(r.minutes for r in given),
    ).model_dump()


async def _create_run_jobs(
    session: AsyncSession,
    paper: Paper,
    version: PaperVersion,
    manifest: PaperManifest,
    claims: list[Claim],
    hashes: dict[str, str],
    lean: LeanFiles,
    external: bool = False,
) -> bool:
    """One job per Lean file (it checks the theorems of every claim made in that file), one
    per certificate, and one for the bibliography. Returns whether the bibliography is checked.
    `external`: the jobs wait for a fully trusted lab's signed results instead of our runners."""
    status = {"status": RunJobStatus.EXTERNAL} if external else {}
    specs = {c.key: c for c in manifest.claims}
    lean_image = manifest.lean_toolchain or await get_param(session, "default_lean_image")
    version_needed = lean_version(manifest.lean_toolchain) or await get_param(
        session, "default_lean_version"
    )
    checker_image = await get_param(session, "default_checker_image")
    runnable = [
        c for c in claims if c.level is TrustLevel.C1 and c.kind is not ClaimKind.INFORMAL
    ]
    by_file: dict[str, list[Claim]] = {}
    for claim in runnable:
        if claim.kind is ClaimKind.LEAN:
            lean_file = specs[claim.key].lean_file
            assert lean_file
            by_file.setdefault(lean_file, []).append(claim)
    for lean_file, group in by_file.items():
        modules, _ = lean.modules(lean_file)
        paths = [lean_file] + [path for _, path in modules]
        resources = _largest([specs[c.key].resources for c in group])
        # what environment a runner needs: matched against the runner's capabilities
        resources["lean_version"] = version_needed
        resources["mathlib"] = any(lean.uses_mathlib(p) for p in paths)
        theorems = list(dict.fromkeys(t for c in group for t in c.lean_theorems))
        session.add(
            RunJob(
                paper_id=paper.id,
                version=version.version,
                claim_id=group[0].id,
                claim_ids=[str(c.id) for c in group],
                kind=RunKind.LEAN,
                image=lean_image,
                command=["lean-check", lean_file],
                inputs=_lean_inputs(hashes, paths),
                resources=resources,
                lean_theorems=theorems,
                **status,
            )
        )
    for claim in runnable:
        if claim.kind is not ClaimKind.CERTIFICATE:
            continue
        spec = specs[claim.key]
        assert spec.certificate_file
        session.add(
            RunJob(
                paper_id=paper.id,
                version=version.version,
                claim_id=claim.id,
                claim_ids=[str(claim.id)],
                kind=RunKind.CHECKER,
                image=checker_image,
                command=["check-certificate", spec.certificate_type or "", spec.certificate_file],
                inputs=[{"sha256": hashes[spec.certificate_file], "path": spec.certificate_file}],
                resources=(spec.resources or Resources()).model_dump(),
                certificate_type=spec.certificate_type,
                **status,
            )
        )
    sources = sorted(
        p for p in hashes if p.endswith(BIBLIOGRAPHY_SUFFIXES) and (p in manifest.files.source or p.endswith(".bib"))
    )
    if not sources or not await get_param(session, "check_references"):
        return False
    session.add(
        RunJob(
            paper_id=paper.id,
            version=version.version,
            claim_id=None,
            claim_ids=[],
            kind=RunKind.REFERENCES,
            image="references",
            command=["check-references", *sources],
            inputs=[{"sha256": hashes[p], "path": p} for p in sources],
            resources=Resources(ram_gb=1, cores=1, minutes=30).model_dump(),
            **status,
        )
    )
    return True


async def create_missing_decisive_tasks(session: AsyncSession, paper: Paper) -> None:
    """The decisive tasks of the current version, when it has none (a paper from a lab that is no
    longer fully trusted: its checks are now done here)."""
    version = (
        await session.execute(
            select(PaperVersion).where(PaperVersion.paper_id == paper.id, PaperVersion.version == paper.current_version)
        )
    ).scalar_one()
    pending = (
        await session.execute(
            select(func.count())
            .select_from(DecisiveTask)
            .where(
                DecisiveTask.paper_id == paper.id,
                DecisiveTask.version == version.version,
                DecisiveTask.status != DecisiveTaskStatus.CANCELLED,
            )
        )
    ).scalar_one()
    if pending:
        return
    claims = list(
        (await session.execute(select(Claim).where(Claim.paper_id == paper.id, Claim.version == version.version))).scalars()
    )
    refs = (
        await session.execute(
            select(func.count())
            .select_from(RunJob)
            .where(RunJob.paper_id == paper.id, RunJob.version == version.version, RunJob.kind == RunKind.REFERENCES)
        )
    ).scalar_one()
    await _create_decisive_tasks(session, paper, version, PaperManifest.model_validate(version.manifest), claims, bool(refs))
    await session.flush()
    from .runs import unlock_decisive  # local import: runs imports levels

    await unlock_decisive(session, paper.id, version.version)


async def _create_decisive_tasks(
    session: AsyncSession,
    paper: Paper,
    version: PaperVersion,
    manifest: PaperManifest,
    claims: list[Claim],
    checks_references: bool = False,
) -> None:
    formal = [c for c in claims if c.kind is not ClaimKind.INFORMAL]
    if any(c.c1_issues for c in formal):
        return  # cannot reach C3 until a new version fixes the machine-checkable part
    kinds = await get_param(session, "priority_kind")
    blocked = bool(formal) or checks_references  # reviewers see the checks' results
    status = DecisiveTaskStatus.BLOCKED if blocked else DecisiveTaskStatus.READY
    reason = "waiting for machine checks" if blocked else None

    def task(kind: DecisiveTaskKind, adversarial: bool = False) -> DecisiveTask:
        return DecisiveTask(
            kind=kind,
            status=status,
            blocked_reason=reason,
            base_priority=kinds.get(kind.value, 40.0),
            paper_id=paper.id,
            version=version.version,
            adversarial=adversarial,
        )

    if manifest.is_fully_formal():
        session.add(task(DecisiveTaskKind.NOVELTY_CONSISTENCY))
    else:
        session.add(task(DecisiveTaskKind.NOVELTY_AUDIT))
        session.add(task(DecisiveTaskKind.STATEMENT_AUDIT))
        session.add(task(DecisiveTaskKind.ANCHOR_REVIEW))
        session.add(task(DecisiveTaskKind.ANCHOR_REVIEW, adversarial=True))
    # a held-out review only once the owner has committed a held-out prompt to run it with
    if await get_param(session, "heldout_review") and await active_prompt(session, "heldout_review"):
        session.add(task(DecisiveTaskKind.HELDOUT_REVIEW))


async def _retire_version(session: AsyncSession, paper: Paper) -> None:
    """A new version supersedes pending work on the old one."""
    old = paper.current_version
    await session.execute(
        update(RunJob)
        .where(
            RunJob.paper_id == paper.id,
            RunJob.version == old,
            RunJob.status.in_([RunJobStatus.QUEUED, RunJobStatus.LEASED, RunJobStatus.WAITING_RESOURCES]),
        )
        .values(cancelled=True, status=RunJobStatus.DONE, finished_at=now())
    )
    await session.execute(
        update(DecisiveTask)
        .where(
            DecisiveTask.paper_id == paper.id,
            DecisiveTask.version == old,
            DecisiveTask.status.in_(
                [DecisiveTaskStatus.BLOCKED, DecisiveTaskStatus.READY, DecisiveTaskStatus.LEASED]
            ),
        )
        .values(status=DecisiveTaskStatus.CANCELLED, done_at=now())
    )
    requests = (
        await session.execute(
            select(ReviewRequest).where(
                ReviewRequest.paper_id == paper.id,
                ReviewRequest.version == old,
                ReviewRequest.cancelled_at.is_(None),
            )
        )
    ).scalars()
    for request in requests:
        active = (
            await session.execute(
                select(func.count())
                .select_from(ReviewAssignment)
                .where(
                    ReviewAssignment.request_id == request.id,
                    ReviewAssignment.status == "assigned",
                )
            )
        ).scalar_one()
        unused = request.count_requested - request.count_fulfilled - active
        if unused > 0:
            session.add(
                ExchangeEntry(
                    operator_id=request.requester_id,
                    delta=unused,
                    reason="refund_new_version",
                    ref_id=request.id,
                )
            )
            request.count_requested -= unused
        if request.count_fulfilled + active >= request.count_requested:
            request.cancelled_at = now()
