"""Federation: other ProofXiv labs follow this instance, and this instance follows other labs.

Publishing. Everything this instance received itself (paper versions, machine checks, decisive
reviews, level changes, withdrawals) goes into its outbox, served in order and signed with the
federation key. The owner attests that key and the keys of trusted operators (`attest_key`), so a
follower checks every signature against the owner's key, not against our server.

Following. The owner adds a lab with a pinned owner key and a trust level (`add_peer`). The jobs
loop reads its owner journal (attested and revoked keys) and its outbox, checks the signatures and
applies the entries by trust:
- index: the paper is listed (precheck, problem pages), nothing is imported;
- review: the paper is submitted here by a shadow operator `login@host` and goes through our own
  machine checks and decisive reviews; the lab's reviews are kept as signals (origin `peer`);
- full: the paper comes with the lab's signed run results and decisive reviews; our own rules
  evaluate them (the same `evaluate` and level computation as for our runs and reviews).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from proofxiv_protocol.enums import (
    LEVEL_RANK,
    DecisiveTaskKind,
    DecisiveTaskStatus,
    NoveltyStatus,
    ReportTarget,
    ReviewKind,
    ReviewOrigin,
    Role,
    RunJobStatus,
    RunVerdict,
)
from proofxiv_protocol.federation import (
    BODY_FOR_KIND,
    WELL_KNOWN_PATH,
    DecisiveReviewBody,
    InstanceInfo,
    LevelBody,
    OutboxEntry,
    OutboxKind,
    OutboxPage,
    PaperVersionBody,
    PeerTrust,
    ProblemKey,
    RunResultBody,
    WithdrawalBody,
)
from proofxiv_protocol.owner import OwnerAction, OwnerStatement
from proofxiv_protocol.papers import MAX_MANIFEST_BYTES, PaperManifest
from proofxiv_protocol.runs import RunResult
from proofxiv_protocol.signing import (
    SignedBundle,
    canonical_json,
    generate_keypair,
    is_valid_public_key,
    key_fingerprint,
    public_key_of,
    sha256_hex,
    utc_now_iso,
)
from proofxiv_protocol.trusted import TrustedResult
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    Agent,
    AuditEvent,
    DecisiveTask,
    FederatedPaper,
    FederationEntry,
    NoveltyChange,
    Operator,
    Paper,
    PaperProblem,
    PaperVersion,
    Peer,
    PeerKey,
    Problem,
    Review,
    Run,
    RunJob,
    SigningKey,
)
from ..db.session import sessionmaker
from . import audit
from .errors import Conflict, Forbidden, Invalid, NotFound
from .identity import Principal, now

log = structlog.get_logger()

MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_FILES = 500  # files of one paper version
PAPERS_PER_PULL = 50  # imported papers (with their files) per peer and pull; the rest waits for the next
PAPERS_PER_DAY = 200  # paper versions per peer and day (listed or imported): each costs storage or checks
MAX_PROBLEMS = 20  # problems a paper names
PULL_DEADLINE = 300.0  # seconds for one peer's pull, whatever its server does
PAGE = 100  # outbox entries asked for at once; fewer when a page is larger than MAX_JSON_BYTES
PAGES_PER_PULL = 20
_RANK = {level.value: rank for level, rank in LEVEL_RANK.items()}  # "C1" -> 1: the levels a scope can ask for
ALL_AREAS = "*"  # a scope's area that takes every paper of the lab
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
REVIEW_TASK_KINDS = {
    DecisiveTaskKind.NOVELTY_CONSISTENCY,
    DecisiveTaskKind.NOVELTY_AUDIT,
    DecisiveTaskKind.ANCHOR_REVIEW,
    DecisiveTaskKind.STATEMENT_AUDIT,
    DecisiveTaskKind.HELDOUT_REVIEW,
}


# --- this instance ----------------------------------------------------------------------------


def key_path() -> Path:
    settings = get_settings()
    return settings.federation_key_file or settings.data_dir / "federation.key"


def private_key() -> str | None:
    path = key_path()
    return path.read_text().strip() if path.exists() else None


def init_key() -> str:
    """Create the federation key if there is none; return its public key."""
    path = key_path()
    if not path.exists():
        private, _ = generate_keypair()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(private + "\n")
        path.chmod(0o600)
    return public_key_of(path.read_text().strip())


def base_url() -> str:
    return get_settings().public_url.rstrip("/")


def instance_info() -> InstanceInfo:
    settings = get_settings()
    key = private_key()
    artifacts = (settings.artifacts_url or settings.public_url).rstrip("/") + "/api/v1/artifacts/{sha256}"
    return InstanceInfo(
        name=settings.instance_name,
        base_url=base_url(),
        owner_keys=list(settings.owner_public_keys),
        federation_key=public_key_of(key) if key else None,
        artifacts=artifacts,
    )


# --- publishing ---------------------------------------------------------------------------------


def publish(session: AsyncSession, kind: OutboxKind, body: BaseModel) -> None:
    session.add(FederationEntry(kind=kind.value, body=body.model_dump(mode="json")))


def problem_key(problem: Problem) -> ProblemKey:
    return ProblemKey(
        slug=problem.slug, title=problem.title, variant=problem.variant, upstream=problem.upstream, domain=problem.domain or None
    )


def _paper_url(paper_id: uuid.UUID) -> str:
    return f"{get_settings().web_url.rstrip('/')}/papers/{paper_id}"


def publish_paper_version(
    session: AsyncSession, paper: Paper, version: PaperVersion, problems: list[Problem], principal: Principal
) -> None:
    if paper.origin_peer_id is not None:
        return  # no transitivity: a lab publishes only what it received itself
    publish(
        session,
        OutboxKind.PAPER_VERSION,
        PaperVersionBody(
            paper_id=str(paper.id),
            version=version.version,
            url=_paper_url(paper.id),
            manifest=version.manifest,
            files=version.files,
            problems=[problem_key(p) for p in problems],
            submitter=principal.operator.login,
            agent_name=principal.agent.name if principal.agent else None,
            agent_model=principal.agent.declared_model if principal.agent else None,
            submitted_at=utc_now_iso(),
        ),
    )


async def publish_run(session: AsyncSession, job: RunJob, run: Run, runner_login: str) -> None:
    paper = await session.get(Paper, job.paper_id)
    if paper is None or paper.origin_peer_id is not None or run.verdict is RunVerdict.INSUFFICIENT_RESOURCES:
        return
    publish(
        session,
        OutboxKind.RUN_RESULT,
        RunResultBody(
            paper_id=str(job.paper_id),
            version=job.version,
            job={
                "kind": job.kind.value,
                "inputs": job.inputs,
                "lean_theorems": job.lean_theorems,
                "certificate_type": job.certificate_type,
            },
            runner=runner_login,
            bundle=run.bundle,
        ),
    )


async def publish_review(session: AsyncSession, task: DecisiveTask, reviewer_login: str) -> None:
    if task.paper_id is None or task.version is None or task.materials is None or task.bundle is None:
        return  # without the materials a follower could not tie the signed result to the files
    paper = await session.get(Paper, task.paper_id)
    if paper is None or paper.origin_peer_id is not None:
        return
    publish(
        session,
        OutboxKind.DECISIVE_REVIEW,
        DecisiveReviewBody(
            paper_id=str(task.paper_id),
            version=task.version,
            kind=task.kind.value,
            adversarial=task.adversarial,
            reviewer=reviewer_login,
            bundle=task.bundle,
            materials=task.materials,
        ),
    )


def publish_level(session: AsyncSession, paper: Paper) -> None:
    if paper.origin_peer_id is None:
        publish(
            session,
            OutboxKind.LEVEL,
            LevelBody(paper_id=str(paper.id), version=paper.current_version, level=paper.level.value),
        )


def publish_withdrawal(session: AsyncSession, paper: Paper, hidden: bool, reason: str) -> None:
    if paper.origin_peer_id is None:
        publish(session, OutboxKind.WITHDRAWAL, WithdrawalBody(paper_id=str(paper.id), hidden=hidden, reason=reason[:1000]))


async def outbox_page(session: AsyncSession, after: int, limit: int) -> OutboxPage:
    key = private_key()
    if key is None:
        raise NotFound("federation is not set up on this instance (proofxiv-server init-instance)")
    rows = list(
        (
            await session.execute(
                select(FederationEntry).where(FederationEntry.seq > after).order_by(FederationEntry.seq).limit(limit)
            )
        ).scalars()
    )
    last = (await session.execute(select(func.max(FederationEntry.seq)))).scalar_one() or 0
    # a paper hidden by moderation (malware, illegal content, spam) is not served to followers:
    # every entry about it reads as a withdrawal
    ids = {str(row.body.get("paper_id")) for row in rows if row.body.get("paper_id")}
    hidden = {
        str(pid)
        for pid in (
            await session.execute(
                select(Paper.id).where(Paper.id.in_([uuid.UUID(i) for i in ids]), Paper.hidden.is_(True))
            )
        ).scalars()
    }

    # entries written before problem keys carried the area get it from the catalog (signed when served)
    slugs = {
        str(k.get("slug"))
        for row in rows
        if row.kind == OutboxKind.PAPER_VERSION.value
        for k in row.body.get("problems") or []
        if isinstance(k, dict) and "domain" not in k
    }
    areas = dict((await session.execute(select(Problem.slug, Problem.domain).where(Problem.slug.in_(slugs)))).all()) if slugs else {}

    def served(row: FederationEntry) -> tuple[OutboxKind, dict[str, Any]]:
        paper_id = str(row.body.get("paper_id"))
        if paper_id in hidden and row.kind != OutboxKind.WITHDRAWAL.value:
            return OutboxKind.WITHDRAWAL, WithdrawalBody(paper_id=paper_id, hidden=True).model_dump(mode="json")
        if row.kind == OutboxKind.PAPER_VERSION.value and any("domain" not in k for k in row.body.get("problems") or []):
            problems = [k if "domain" in k else {**k, "domain": areas.get(k.get("slug")) or None} for k in row.body["problems"]]
            return OutboxKind(row.kind), {**row.body, "problems": problems}
        return OutboxKind(row.kind), row.body

    entries = []
    for row in rows:
        kind, body = served(row)
        entry = OutboxEntry(instance=base_url(), seq=row.seq, kind=kind, created_at=row.created_at.isoformat(), body=body)
        entries.append(SignedBundle.create(entry, key).model_dump(mode="json"))
    return OutboxPage(entries=entries, next_after=rows[-1].seq if rows else after, last_seq=last)


async def backfill(session: AsyncSession) -> int:
    """Publish the papers that arrived before federation existed (versions, run results, levels)."""
    if (await session.execute(select(func.count()).select_from(FederationEntry))).scalar_one():
        raise Conflict("the outbox is not empty")
    count = 0
    papers = (
        await session.execute(select(Paper).where(Paper.origin_peer_id.is_(None), Paper.hidden.is_(False)).order_by(Paper.created_at))
    ).scalars()
    for paper in list(papers):
        submitter = await session.get(Operator, paper.submitter_id)
        agent = await session.get(Agent, paper.submitter_agent_id) if paper.submitter_agent_id else None
        assert submitter is not None
        principal = Principal(operator=submitter, agent=agent)
        versions = (
            await session.execute(select(PaperVersion).where(PaperVersion.paper_id == paper.id).order_by(PaperVersion.version))
        ).scalars()
        for version in list(versions):
            problems = await _paper_problems(session, paper.id)
            publish_paper_version(session, paper, version, problems, principal)
            count += 1
            jobs = (
                await session.execute(select(RunJob).where(RunJob.paper_id == paper.id, RunJob.version == version.version))
            ).scalars()
            for job in list(jobs):
                run = (
                    await session.execute(
                        select(Run)
                        .where(Run.job_id == job.id, Run.invalidated.is_(False))
                        .order_by(Run.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if run is not None:
                    runner = await session.get(Operator, run.runner_id)
                    await publish_run(session, job, run, runner.login if runner else "")
                    count += 1
            tasks = (
                await session.execute(
                    select(DecisiveTask).where(
                        DecisiveTask.paper_id == paper.id,
                        DecisiveTask.version == version.version,
                        DecisiveTask.status == DecisiveTaskStatus.DONE,
                        DecisiveTask.kind.in_(list(REVIEW_TASK_KINDS)),
                    )
                )
            ).scalars()
            for task in list(tasks):
                if task.materials is not None and task.leased_by is not None:
                    reviewer = await session.get(Operator, task.leased_by)
                    await publish_review(session, task, reviewer.login if reviewer else "")
                    count += 1
        publish_level(session, paper)
        count += 1
    return count


async def _paper_problems(session: AsyncSession, paper_id: uuid.UUID) -> list[Problem]:
    rows = await session.execute(
        select(Problem).join(PaperProblem, PaperProblem.problem_id == Problem.id).where(PaperProblem.paper_id == paper_id)
    )
    return list(rows.scalars())


# --- owner statements about federation ------------------------------------------------------------

TRUST_RANK = {PeerTrust.INDEX: 0, PeerTrust.REVIEW: 1, PeerTrust.FULL: 2}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _normal_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise Invalid("a peer URL is https://host[/path]")
    return url.strip().rstrip("/")


async def apply_statement(session: AsyncSession, st: OwnerStatement) -> None:
    """The federation part of an owner statement (called from identity's dispatcher)."""
    match st.action:
        case OwnerAction.ATTEST_KEY:
            # only a record in the public journal: followers read it there
            assert st.public_key and st.role
            if st.role is Role.FEDERATION:
                own = private_key()
                if own is None or public_key_of(own) != st.public_key:
                    raise Invalid("this is not the federation key of this instance")
            else:
                key = (
                    await session.execute(select(SigningKey).where(SigningKey.public_key == st.public_key))
                ).scalar_one_or_none()
                owner = await session.get(Operator, key.operator_id) if key else None
                if key is None or owner is None or owner.peer_id is not None or owner.login != st.operator_login:
                    raise Invalid("no such key registered to that operator")
        case OwnerAction.ADD_PEER:
            assert st.peer_url and st.peer_owner_key and st.trust
            _trust_allowed(st.trust)
            if not is_valid_public_key(st.peer_owner_key):
                raise Invalid("peer_owner_key is not an ed25519 public key")
            url = _normal_url(st.peer_url)
            if url == base_url():
                raise Invalid("an instance does not follow itself")
            peer = (await session.execute(select(Peer).where(Peer.base_url == url))).scalar_one_or_none()
            if peer is None:
                domains, level = _scope(st)
                session.add(
                    Peer(base_url=url, owner_key=st.peer_owner_key, trust=st.trust, status="new", domains=domains, min_level=level)
                )
                return
            if st.peer_domains is not None or st.peer_min_level is not None:
                peer.domains, peer.min_level = _scope(st)
                await _rescope(session, peer)
            if peer.owner_key != st.peer_owner_key:
                # a new root of trust: what the old owner key attested no longer counts
                await _reclaim(session, peer)
                await session.execute(delete(PeerKey).where(PeerKey.peer_id == peer.id))
            elif peer.trust is PeerTrust.FULL and st.trust is not PeerTrust.FULL:
                await _reclaim(session, peer)
            peer.owner_key, peer.trust, peer.removed_at, peer.status = st.peer_owner_key, st.trust, None, "new"
        case OwnerAction.SET_PEER_TRUST:
            assert st.peer_url and st.trust
            _trust_allowed(st.trust)
            peer = await _peer(session, st.peer_url)
            if peer.trust is PeerTrust.FULL and st.trust is not PeerTrust.FULL:
                await _reclaim(session, peer)
            peer.trust = st.trust
        case OwnerAction.SET_PEER_SCOPE:
            assert st.peer_url
            peer = await _peer(session, st.peer_url)
            peer.domains, peer.min_level = _scope(st)
            await _rescope(session, peer)  # what came into the scope is imported on the next pull
        case OwnerAction.REMOVE_PEER:
            assert st.peer_url
            peer = await _peer(session, st.peer_url)
            if peer.trust is PeerTrust.FULL:
                await _reclaim(session, peer)
            peer.removed_at = now()
            peer.status = "removed"


def _scope(st: OwnerStatement) -> tuple[list[str], str | None]:
    """The areas (case-folded) and the minimum level of a statement's scope; none: no filter."""
    domains = sorted({d.strip().casefold() for d in st.peer_domains or [] if d.strip()})
    if len(domains) > 50 or any(len(d) > 80 for d in domains):
        raise Invalid("at most 50 areas of at most 80 characters each")
    level = (st.peer_min_level or "").strip().upper() or None
    if level not in (None, "C1", "C2", "C3"):
        raise Invalid("the minimum level is C1, C2 or C3")
    return domains, level


async def _rescope(session: AsyncSession, peer: Peer) -> None:
    """The scope changed: which of the papers listed so far are inside it now. Papers already
    imported stay here; nothing is downloaded in a statement."""
    for fed in (await session.execute(select(FederatedPaper).where(FederatedPaper.peer_id == peer.id))).scalars():
        fed.in_scope = await _in_scope(session, peer, fed)


def _trust_allowed(trust: PeerTrust) -> None:
    if trust is PeerTrust.FULL and not get_settings().federation_full_trust:
        raise Invalid(
            "the trust level 'full' is not enabled on this instance yet: a compromised lab server could "
            "still steer which of its checks count (docs/FEDERATION.md); use 'review'"
        )


async def _peer(session: AsyncSession, url: str) -> Peer:
    peer = (await session.execute(select(Peer).where(Peer.base_url == _normal_url(url)))).scalar_one_or_none()
    if peer is None:
        raise NotFound("not a peer of this instance")
    return peer


async def _reclaim(session: AsyncSession, peer: Peer) -> None:
    """The lab is no longer fully trusted: what counted on its signatures is taken back, and its
    papers are checked here like any submission (our runners, our decisive reviews)."""
    from .cascade import invalidate_key  # local import: cascade imports much of the core
    from .papers import create_missing_decisive_tasks

    keys = (
        await session.execute(
            select(SigningKey)
            .join(Operator, Operator.id == SigningKey.operator_id)
            .where(Operator.peer_id == peer.id, SigningKey.revoked_at.is_(None))
        )
    ).scalars()
    for key in list(keys):
        key.revoked_at, key.revoke_from, key.revoke_reason = now(), EPOCH, "the lab is no longer fully trusted here"
        await invalidate_key(session, key)
    papers = list((await session.execute(select(Paper).where(Paper.origin_peer_id == peer.id))).scalars())
    for paper in papers:
        jobs = (
            await session.execute(
                select(RunJob).where(RunJob.paper_id == paper.id, RunJob.status == RunJobStatus.EXTERNAL)
            )
        ).scalars()
        for job in jobs:
            job.status = RunJobStatus.QUEUED
        await create_missing_decisive_tasks(session, paper)
    audit.record(session, None, "federation.reclaimed", "peer", peer.id, papers=len(papers))


# --- following ------------------------------------------------------------------------------------


class PullError(Exception):
    pass


class ResponseTooLarge(PullError):
    pass


def _clean(text: str, limit: int) -> str:
    """Text from a peer that this instance shows (errors): printable, short."""
    return re.sub(r"[^\x20-\x7e\n]", "?", text)[:limit]


def _peer_url(peer: Peer, path: str) -> str:
    """A path the peer names (outbox, journal), on the peer's own address and nowhere else."""
    if not path.startswith("/") or path.startswith("//") or any(c in path for c in "@\\#?") or "://" in path:
        raise PullError(f"the instance names an odd path: {_clean(path, 100)}")
    url = httpx.URL(peer.base_url + path)
    base = httpx.URL(peer.base_url)
    if url.host != base.host or url.scheme != base.scheme or url.port != base.port:
        raise PullError("the instance names a path on another host")
    return str(url)


async def _public_host(host: str) -> bool:
    """Every address the name resolves to is public: files are fetched from where the peer says,
    so it must not point the jobs container at the internal network (SSRF). A name that changes
    its addresses between this check and the request (DNS rebinding) is a residual risk."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    return bool(addresses) and all(a.is_global for a in addresses)


async def _artifact_url(peer: Peer, info: InstanceInfo, sha: str, allowed: set[str]) -> str:
    if "{sha256}" not in info.artifacts or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise Invalid("bad artifact reference")
    url = httpx.URL(info.artifacts.replace("{sha256}", sha))
    base = httpx.URL(peer.base_url)
    if url.host == base.host:
        if url.scheme != base.scheme or url.port != base.port:
            raise PullError("files are served from another port or scheme of the lab's host")
    elif url.host not in allowed:
        if url.scheme != "https" or url.port not in (None, 443) or not await _public_host(url.host):
            raise PullError(f"files are served from a host that is not public: {_clean(url.host, 100)}")
        allowed.add(url.host)
    return str(url)


async def _read_capped(client: httpx.AsyncClient, url: str, cap: int) -> tuple[int, bytes]:
    """(status, body) with the body read in chunks and refused past `cap` bytes."""
    async with client.stream("GET", url, timeout=TIMEOUT) as resp:
        if resp.status_code != 200:
            return resp.status_code, b""
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            raise ResponseTooLarge(f"GET {url}: response too large")
        chunks, size = [], 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > cap:
                raise ResponseTooLarge(f"GET {url}: response too large")
            chunks.append(chunk)
        return 200, b"".join(chunks)


async def _get(client: httpx.AsyncClient, url: str) -> Any:
    status, body = await _read_capped(client, url, MAX_JSON_BYTES)
    if status != 200:
        raise PullError(f"GET {url}: HTTP {status}")
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise PullError(f"GET {url}: not JSON") from exc


async def rewind(session: AsyncSession) -> int:
    """Read the followed labs' feeds again from the first entry: after an update that accepts what
    was skipped, or after raising a lab from index to review. Entries already applied are no-ops."""
    peers = list((await session.execute(select(Peer).where(Peer.removed_at.is_(None)))).scalars())
    for peer in peers:
        peer.cursor = 0
    return len(peers)


async def pull_due(client: httpx.AsyncClient | None = None, force: bool = False) -> dict[str, Any]:
    """Pull every peer whose turn has come (the jobs loop calls this every minute)."""
    stats: dict[str, Any] = {}
    period = timedelta(minutes=get_settings().federation_pull_minutes)
    async with sessionmaker()() as session:
        peers = list((await session.execute(select(Peer).where(Peer.removed_at.is_(None)))).scalars())
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=False, headers={"User-Agent": "ProofXiv federation"})
    try:
        for peer in peers:
            if not force and peer.last_pulled_at is not None and now() - peer.last_pulled_at < period:
                continue
            async with sessionmaker()() as session:
                current = await session.get(Peer, peer.id)
                assert current is not None
                try:
                    deadline = time.monotonic() + PULL_DEADLINE
                    result = stats[current.base_url] = await pull_peer(session, current, client, deadline)
                    if result["skipped"]:
                        current.status = "skipped_entries"
                        current.last_error = _clean("\n".join(result["skipped"]), 4000)
                    else:
                        current.status, current.last_error = "ok", None
                except Exception as exc:  # one lab must not stop the others, nor the jobs loop
                    message = _clean(str(exc) or type(exc).__name__, 2000)
                    stats[peer.base_url] = {"error": message[:300]}
                    log.warning("federation pull failed", peer=peer.base_url, error=message[:300])
                    try:  # release this session's locks first: the failure is recorded in a fresh one
                        await session.rollback()
                    except Exception:
                        await session.invalidate()
                    await _record_failure(peer.id, message)
                    continue
                current.last_pulled_at = now()
                await session.commit()
    finally:
        if own:
            await client.aclose()
    return stats


async def _record_failure(peer_id: uuid.UUID, message: str) -> None:
    """In a fresh session: the failed one may be unusable."""
    try:
        async with sessionmaker()() as session:
            peer = await session.get(Peer, peer_id)
            if peer is not None:
                if peer.status != "key_mismatch":
                    peer.status = "error"
                peer.last_error, peer.last_pulled_at = message, now()
                await session.commit()
    except Exception:
        log.exception("federation: could not record a failed pull")


async def pull_peer(
    session: AsyncSession, peer: Peer, client: httpx.AsyncClient, deadline: float | None = None
) -> dict[str, Any]:
    info = InstanceInfo.model_validate(await _get(client, _peer_url(peer, WELL_KNOWN_PATH)))
    if info.base_url.rstrip("/") != peer.base_url:
        raise PullError(f"the instance calls itself {_clean(info.base_url, 300)}, not {peer.base_url}")
    if peer.owner_key not in info.owner_keys:
        peer.status = "key_mismatch"
        await session.commit()
        raise PullError("the pinned owner key is no longer listed: the owner of this instance decides what to do")
    peer.name = _clean(info.name, 200)
    await _read_journal(session, peer, client, info)
    federation = await _live_key(session, peer, info.federation_key, Role.FEDERATION)
    if federation is None:
        raise PullError("the outbox key is not attested by the owner")
    applied = 0
    skipped: list[str] = []
    allowed: set[str] = set()  # file hosts checked in this pull
    imported = (  # paper versions from this lab in the last day, listed or imported
        await session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "federation.paper",
                AuditEvent.data["peer"].astext == peer.base_url,
                AuditEvent.created_at > now() - timedelta(days=1),
            )
        )
    ).scalar_one()
    this_pull = 0
    size = PAGE
    for _ in range(PAGES_PER_PULL):
        try:
            page = OutboxPage.model_validate(
                await _get(client, _peer_url(peer, info.outbox) + f"?after={peer.cursor}&limit={size}")
            )
        except ResponseTooLarge:
            if size == 1:
                raise  # one entry is larger than any page may be: the feed stops here
            size = max(1, size // 10)  # large papers (a long paper.json): ask for fewer at once
            continue
        if not page.entries:
            break
        for raw in page.entries[:size]:
            if deadline is not None and time.monotonic() > deadline:
                return {"applied": applied, "skipped": skipped, "cursor": peer.cursor, "more": True}
            bundle = SignedBundle.model_validate(raw)
            if not bundle.verify(federation.public_key):
                raise PullError("an outbox entry is not signed by the attested federation key")
            entry = OutboxEntry.model_validate(bundle.payload)
            if entry.instance.rstrip("/") != peer.base_url or entry.seq <= peer.cursor:
                raise PullError(f"unexpected outbox entry {entry.seq}")
            if entry.kind is OutboxKind.PAPER_VERSION:
                if this_pull >= PAPERS_PER_PULL or imported >= PAPERS_PER_DAY:
                    return {"applied": applied, "skipped": skipped, "cursor": peer.cursor, "more": True}
                this_pull += 1
                imported += 1
            try:
                async with session.begin_nested():
                    await _apply(session, peer, info, entry, client, allowed)
                applied += 1
            except (PullError, httpx.HTTPError):
                raise  # the network or the lab's server: retry from this entry next time
            except Exception as exc:
                # signed by the lab but refused here (a key it did not attest, a review of other
                # files, a malformed body): skip it and keep the reason
                skipped.append(_clean(f"entry {entry.seq} ({entry.kind.value}): {exc or type(exc).__name__}", 500))
            peer.cursor = entry.seq
            if entry.kind in (OutboxKind.PAPER_VERSION, OutboxKind.LEVEL):
                # right away, so that the lab's reviews that follow find the paper here
                skipped += await _import_due(session, peer, info, client, allowed, str(entry.body.get("paper_id")))
            await session.commit()
        if page.next_after >= page.last_seq:
            break
    skipped += await _import_due(session, peer, info, client, allowed)  # the scope or the trust changed
    await session.commit()
    return {"applied": applied, "skipped": skipped, "cursor": peer.cursor}


async def _read_journal(session: AsyncSession, peer: Peer, client: httpx.AsyncClient, info: InstanceInfo) -> None:
    """Attested and revoked keys, from the peer's owner journal, checked against the pinned key.
    Attestations first, then revocations, whatever the order the server lists them in."""
    statements: list[OwnerStatement] = []
    items = await _get(client, _peer_url(peer, info.journal))
    if not isinstance(items, list):
        raise PullError("the owner journal is not a list")
    for item in items:
        try:
            bundle = SignedBundle(payload=item["payload"], key_fingerprint=item["key_fingerprint"], signature=item["signature"])
            if bundle.verify(peer.owner_key):  # else signed by another owner key of theirs: not the one we pinned
                statements.append(OwnerStatement.model_validate(bundle.payload))
        except (KeyError, TypeError, ValueError):
            continue
    for st in statements:
        if st.action is OwnerAction.ATTEST_KEY and st.public_key and st.role and is_valid_public_key(st.public_key):
            await _attest(session, peer, st)
    for st in statements:
        issued = _aware(datetime.fromisoformat(st.issued_at))
        if st.action is OwnerAction.REVOKE_KEY and st.key_fingerprint:
            since = _aware(datetime.fromisoformat(st.revoke_from)) if st.revoke_from else issued
            keys = (
                await session.execute(
                    select(PeerKey).where(PeerKey.peer_id == peer.id, PeerKey.fingerprint == st.key_fingerprint)
                )
            ).scalars()
            for key in list(keys):
                await _revoke_peer_key(session, peer, key, issued, since)
        elif st.action is OwnerAction.REVOKE_ROLE and st.operator_login and st.role:
            keys = (
                await session.execute(
                    select(PeerKey).where(
                        PeerKey.peer_id == peer.id, PeerKey.operator_login == st.operator_login, PeerKey.role == st.role
                    )
                )
            ).scalars()
            for key in list(keys):
                await _revoke_peer_key(session, peer, key, issued, issued)
    await session.flush()


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def _attest(session: AsyncSession, peer: Peer, st: OwnerStatement) -> None:
    assert st.public_key and st.role
    fp = key_fingerprint(st.public_key)
    # a key that is ours, or another lab's, is never attested on behalf of this lab
    owner = (
        await session.execute(
            select(Operator.peer_id).join(SigningKey, SigningKey.operator_id == Operator.id).where(SigningKey.fingerprint == fp)
        )
    ).first()
    if owner is not None and owner[0] != peer.id:
        return
    known = (
        await session.execute(
            select(PeerKey).where(PeerKey.peer_id == peer.id, PeerKey.fingerprint == fp, PeerKey.role == st.role)
        )
    ).scalar_one_or_none()
    if known is None:
        session.add(
            PeerKey(
                peer_id=peer.id,
                public_key=st.public_key,
                fingerprint=fp,
                role=st.role,
                operator_login=(st.operator_login or "")[:64] or None,
                attested_at=_aware(datetime.fromisoformat(st.issued_at)),
            )
        )
        await session.flush()


async def _revoke_peer_key(session: AsyncSession, peer: Peer, key: PeerKey, issued: datetime, since: datetime) -> None:
    """A revocation counts from the earliest time any statement names."""
    if key.revoke_from is not None and key.revoke_from <= since:
        return
    key.revoked_at = key.revoked_at or issued
    key.revoke_from = since
    await _revoke_mirror(session, peer, key)


async def _live_key(session: AsyncSession, peer: Peer, public_key: str | None, role: Role) -> PeerKey | None:
    """The attested key, if it is not revoked now."""
    if not public_key:
        return None
    key = (
        await session.execute(
            select(PeerKey).where(
                PeerKey.peer_id == peer.id, PeerKey.fingerprint == key_fingerprint(public_key), PeerKey.role == role
            )
        )
    ).scalar_one_or_none()
    if key is None or (key.revoke_from is not None and now() >= key.revoke_from):
        return None
    return key


async def _signed_by(session: AsyncSession, peer: Peer, bundle: SignedBundle, role: Role) -> PeerKey:
    """The attested, unrevoked key that signed the bundle. Revocation counts by the time we receive
    a signature, never by the time the signer claims: a leaked key cannot backdate."""
    key = (
        await session.execute(
            select(PeerKey).where(
                PeerKey.peer_id == peer.id, PeerKey.fingerprint == bundle.key_fingerprint, PeerKey.role == role
            )
        )
    ).scalar_one_or_none()
    if key is None:
        raise Forbidden(f"signed by a key the lab's owner did not attest for {role}")
    if key.revoke_from is not None and now() >= key.revoke_from:
        raise Forbidden("signed by a revoked key")
    if not bundle.verify(key.public_key):
        raise Forbidden("bad signature")
    return key


def _host(peer: Peer) -> str:
    """The lab's address in shadow logins: host[:port], shortened with a hash when long."""
    parsed = urlparse(peer.base_url)
    netloc = (parsed.netloc or "peer").lower()
    if parsed.path.strip("/") or len(netloc) > 40:  # two labs on one host differ by their path
        return f"{netloc[:31]}~{sha256_hex(peer.base_url.encode())[:8]}"
    return netloc


async def _shadow(
    session: AsyncSession, peer: Peer, login: str, agent_name: str | None = None, agent_model: str | None = None
) -> Principal:
    """The operator here that stands for a participant of the peer (login@host); it can never act
    here: it has no token, no session and no role."""
    host = _host(peer)
    local = re.sub(r"[^A-Za-z0-9_.-]", "-", login)[: 63 - len(host)] or "user"
    name = f"{local}@{host}"  # an @ never occurs in a login here, so nobody can take this name
    operator = (await session.execute(select(Operator).where(Operator.login == name))).scalar_one_or_none()
    if operator is None:
        operator = Operator(login=name, display_name=name, peer_id=peer.id)
        session.add(operator)
        await session.flush()
    elif operator.peer_id != peer.id:
        raise Conflict(f"the login {name} belongs to someone else here")
    agent = None
    if agent_name:
        agent = (
            await session.execute(select(Agent).where(Agent.operator_id == operator.id, Agent.name == agent_name[:100]))
        ).scalar_one_or_none()
        if agent is None:
            agent = Agent(operator_id=operator.id, name=agent_name[:100], declared_model=(agent_model or "")[:200] or None)
            session.add(agent)
            await session.flush()
    return Principal(operator=operator, agent=agent, via="federation")


async def _mirror_key(session: AsyncSession, peer: Peer, peer_key: PeerKey, operator: Operator) -> SigningKey:
    """The peer's key as a signing key of its shadow operator, so that a revocation reaches what it
    signed through the usual cascade. Never one of our keys, nor another lab's."""
    key = (
        await session.execute(select(SigningKey).where(SigningKey.fingerprint == peer_key.fingerprint))
    ).scalar_one_or_none()
    if key is None:
        key = SigningKey(
            operator_id=operator.id,
            public_key=peer_key.public_key,
            fingerprint=peer_key.fingerprint,
            label=f"attested by the owner of {peer.base_url}",
            revoked_at=peer_key.revoked_at,
            revoke_from=peer_key.revoke_from,
        )
        session.add(key)
        await session.flush()
        return key
    holder = await session.get(Operator, key.operator_id)
    if holder is None or holder.peer_id != peer.id:
        raise Forbidden("this key belongs to someone else here")
    return key


async def _revoke_mirror(session: AsyncSession, peer: Peer, peer_key: PeerKey) -> None:
    key = (
        await session.execute(
            select(SigningKey)
            .join(Operator, Operator.id == SigningKey.operator_id)
            .where(SigningKey.fingerprint == peer_key.fingerprint, Operator.peer_id == peer.id)
        )
    ).scalar_one_or_none()
    if key is None:
        return
    if key.revoke_from is not None and peer_key.revoke_from is not None and key.revoke_from <= peer_key.revoke_from:
        return
    key.revoked_at = key.revoked_at or now()
    key.revoke_from = peer_key.revoke_from
    key.revoke_reason = "revoked by the lab's owner"
    from .cascade import invalidate_key  # local import: cascade imports much of the core

    await invalidate_key(session, key)


# --- applying entries ------------------------------------------------------------------------------


async def _apply(
    session: AsyncSession, peer: Peer, info: InstanceInfo, entry: OutboxEntry, client: httpx.AsyncClient, allowed: set[str]
) -> None:
    body = BODY_FOR_KIND[entry.kind].model_validate(entry.body)
    if isinstance(body, PaperVersionBody):
        await _on_paper_version(session, peer, info, body, client, allowed)
        return
    fed = (
        await session.execute(
            select(FederatedPaper).where(FederatedPaper.peer_id == peer.id, FederatedPaper.remote_id == body.paper_id)
        )
    ).scalar_one_or_none()
    if fed is None:
        return  # about a paper we never saw (e.g. one that came to them from elsewhere)
    if isinstance(body, LevelBody):
        if body.version == fed.version:
            fed.level = body.level
            fed.in_scope = await _in_scope(session, peer, fed)  # a minimum level may be reached now
        return
    if isinstance(body, WithdrawalBody):
        await _on_withdrawal(session, peer, fed, body)
        return
    if fed.paper_id is None or body.version != fed.version or fed.imported_version != fed.version:
        return  # about a version that is not here (outside the scope, or not imported yet)
    paper = await session.get(Paper, fed.paper_id)
    if paper is None or paper.origin_peer_id != peer.id:
        return  # only the copy this lab's entry created, never a paper submitted here
    if isinstance(body, RunResultBody) and peer.trust is PeerTrust.FULL:
        await _on_run(session, peer, paper, body)
    elif isinstance(body, DecisiveReviewBody):
        await _on_review(session, peer, paper, fed, body)


async def _match_problems(session: AsyncSession, keys: list[ProblemKey]) -> dict[str, Problem]:
    """The peer's problem slug -> our problem: by the upstream statement, else by the same slug
    and title (instances that imported the same catalog)."""
    found: dict[str, Problem] = {}
    for key in keys[:MAX_PROBLEMS]:
        name = (key.upstream or {}).get("name")
        problem = None
        if isinstance(name, str) and name:
            problem = (
                await session.execute(select(Problem).where(Problem.upstream["name"].astext == name).limit(1))
            ).scalar_one_or_none()
        if problem is None:
            problem = (
                await session.execute(select(Problem).where(Problem.slug == key.slug, Problem.title == key.title))
            ).scalar_one_or_none()
        if problem is not None and not problem.hidden:
            found[key.slug] = problem
    return found


def _local_manifest(manifest: dict[str, Any], mapping: dict[str, Problem]) -> PaperManifest:
    """The peer's paper.json with its problem slugs replaced by ours (unknown problems dropped).
    Record claims lose their quantity here: which of our problems' tables a value belongs to is
    not something the lab's operators signed, so imported papers do not enter record tables."""
    data = dict(manifest)
    slugs = {remote: problem.slug for remote, problem in mapping.items()}
    data["problem_slugs"] = [slugs[s] for s in manifest.get("problem_slugs", []) if s in slugs]
    data["settles"] = [slugs[s] for s in manifest.get("settles", []) if s in slugs]
    claims = []
    for claim in manifest.get("claims", []):
        claim = dict(claim)
        claim["problem_slug"] = slugs.get(claim.get("problem_slug") or "")
        claim.update(quantity=None, value=None, bound=None)
        claims.append(claim)
    data["claims"] = claims
    return PaperManifest.model_validate(data)


async def _download(
    client: httpx.AsyncClient, peer: Peer, info: InstanceInfo, files: dict[str, str], allowed: set[str]
) -> dict[str, bytes]:
    settings = get_settings()
    if len(files) > MAX_FILES:
        raise Invalid(f"more than {MAX_FILES} files")
    out: dict[str, bytes] = {}
    total = 0
    for path, sha in files.items():
        url = await _artifact_url(peer, info, sha, allowed)
        status, data = await _read_capped(client, url, settings.max_file_mb * 1024 * 1024)
        if status in (403, 404, 410):
            raise Invalid(f"file {path} is not available at the lab (HTTP {status})")
        if status != 200:
            raise PullError(f"file {path}: HTTP {status}")
        if sha256_hex(data) != sha:
            raise Invalid(f"file {path}: content does not match its hash")
        total += len(data)
        if total > settings.max_submission_mb * 1024 * 1024:
            raise Invalid("the paper exceeds the submission size limit")
        out[path] = data
    return out


async def _on_paper_version(
    session: AsyncSession,
    peer: Peer,
    info: InstanceInfo,
    body: PaperVersionBody,
    client: httpx.AsyncClient,
    allowed: set[str],
) -> None:
    if len(canonical_json(body.manifest)) > MAX_MANIFEST_BYTES or len(body.problems) > MAX_PROBLEMS:
        raise Invalid("the paper.json or its problem list is too large")
    mapping = await _match_problems(session, body.problems)
    fed = (
        await session.execute(
            select(FederatedPaper).where(FederatedPaper.peer_id == peer.id, FederatedPaper.remote_id == body.paper_id)
        )
    ).scalar_one_or_none()
    if fed is not None and body.version == fed.version:
        # the feed read again: only the matching and the scope (the catalog may have grown)
        fed.problem_ids = [str(p.id) for p in mapping.values()]
        fed.in_scope = await _in_scope(session, peer, fed)
        return
    if fed is not None and body.version < fed.version:
        raise Invalid(f"version {body.version} is older than version {fed.version} here")
    audit.record(session, None, "federation.paper", "peer", peer.id, peer=peer.base_url, remote=body.paper_id)
    if fed is None:
        fed = FederatedPaper(peer_id=peer.id, remote_id=body.paper_id, withdrawn=False)
        session.add(fed)
    fed.version = body.version
    fed.title = str(body.manifest.get("title", ""))[:300]
    fed.abstract = str(body.manifest.get("abstract", ""))[:5000]
    fed.url = body.url
    fed.manifest, fed.files = body.manifest, body.files
    fed.problems = [k.model_dump(mode="json") for k in body.problems]
    fed.problem_ids = [str(p.id) for p in mapping.values()]
    fed.submitted_at = _aware(datetime.fromisoformat(body.submitted_at))
    fed.sender = {"submitter": body.submitter, "agent_name": body.agent_name, "agent_model": body.agent_model}
    fed.level, fed.import_error = None, None
    fed.in_scope = await _in_scope(session, peer, fed)
    # imported (review, full) right after this entry, by _import_due, when inside the scope


async def _in_scope(session: AsyncSession, peer: Peer, fed: FederatedPaper) -> bool:
    """Whether the follower takes this paper: on a problem in one of the peer's areas (by the area
    the lab gives, or ours for a problem we have), else on a problem in our catalog; and at or
    above the peer's minimum level at the lab."""
    if peer.min_level and _RANK.get(fed.level or "", -1) < _RANK[peer.min_level]:
        return False
    if not peer.domains:
        return bool(fed.problem_ids)
    if ALL_AREAS in peer.domains:
        return True  # everything the lab publishes, also papers that name no problem
    areas = {str(k.get("domain") or "").casefold() for k in fed.problems if isinstance(k, dict)}
    if fed.problem_ids:
        ours = await session.execute(
            select(Problem.domain).where(Problem.id.in_([uuid.UUID(i) for i in fed.problem_ids]))
        )
        areas |= {d.casefold() for d in ours.scalars() if d}
    return bool(areas & set(peer.domains))


async def _import(
    session: AsyncSession, peer: Peer, info: InstanceInfo, fed: FederatedPaper, client: httpx.AsyncClient, allowed: set[str]
) -> None:
    """Submit the listed version here, as the lab's shadow operator: our checks follow."""
    from .papers import submit_paper  # local import: papers imports much of the core

    mapping = await _match_problems(session, [ProblemKey.model_validate(k) for k in fed.problems])
    fed.problem_ids = [str(p.id) for p in mapping.values()]
    sender = fed.sender or {}
    principal = await _shadow(
        session, peer, str(sender.get("submitter") or "unknown"), sender.get("agent_name"), sender.get("agent_model")
    )
    files = await _download(client, peer, info, fed.files, allowed)
    manifest = _local_manifest(fed.manifest, mapping)
    note = f"from {peer.name or peer.base_url} ({fed.url}), version {fed.version} there"
    try:
        async with session.begin_nested():
            result = await submit_paper(session, principal, manifest, files, paper_id=fed.paper_id, notes=note, origin=peer)
        local_id = result.paper.id
    except Conflict as exc:
        # the same bundle is already here: a first version submitted to both labs is linked to the
        # paper here (nothing imported); any other duplicate is refused
        existing = exc.details.get("paper_id")
        if fed.paper_id is not None or not existing:
            raise Invalid("this version is identical to a paper already here") from exc
        local_id = uuid.UUID(str(existing))
    fed.paper_id, fed.imported_version = local_id, fed.version


async def _import_due(
    session: AsyncSession,
    peer: Peer,
    info: InstanceInfo,
    client: httpx.AsyncClient,
    allowed: set[str],
    remote_id: str | None = None,
    limit: int = PAPERS_PER_PULL,
) -> list[str]:
    """Import the listed versions inside the scope that are not here yet (one paper, or any: after
    a level rose to the minimum, the scope changed or the trust was raised). A refused import is
    noted on the paper and not tried again until a new version; returns the reasons."""
    if peer.trust is PeerTrust.INDEX:
        return []
    query = select(FederatedPaper).where(
        FederatedPaper.peer_id == peer.id,
        FederatedPaper.in_scope.is_(True),
        FederatedPaper.withdrawn.is_(False),
        FederatedPaper.import_error.is_(None),
        or_(FederatedPaper.imported_version.is_(None), FederatedPaper.imported_version < FederatedPaper.version),
    )
    if remote_id is not None:
        query = query.where(FederatedPaper.remote_id == remote_id)
    refused = []
    for fed in list((await session.execute(query.order_by(FederatedPaper.created_at).limit(limit))).scalars()):
        fed_id, about = fed.id, f"paper {fed.remote_id} v{fed.version}"  # fed expires if the savepoint rolls back
        try:
            async with session.begin_nested():
                await _import(session, peer, info, fed, client, allowed)
        except (PullError, httpx.HTTPError):
            raise  # the network or the lab's server: tried again on the next pull
        except Exception as exc:
            reason = _clean(str(exc) or type(exc).__name__, 1000)
            await session.execute(update(FederatedPaper).where(FederatedPaper.id == fed_id).values(import_error=reason))
            refused.append(_clean(f"{about} not imported: {reason}", 500))
    return refused


async def _on_run(session: AsyncSession, peer: Peer, paper: Paper, body: RunResultBody) -> None:
    from .levels import recompute_paper
    from .runs import digest_of, evaluate, unlock_decisive  # local import: runs imports levels

    bundle = SignedBundle.model_validate(body.bundle)
    result = RunResult.model_validate(bundle.payload)
    peer_key = await _signed_by(session, peer, bundle, Role.RUNNER)
    if result.verdict is RunVerdict.INSUFFICIENT_RESOURCES:
        return  # the lab will run it again
    if (await session.execute(select(Run.id).where(Run.bundle["signature"].astext == bundle.signature))).first():
        return  # the same signed result again
    inputs = sorted((str(i.get("path")), str(i.get("sha256"))) for i in body.job.get("inputs", []))
    jobs = (
        await session.execute(
            select(RunJob).where(
                RunJob.paper_id == paper.id,
                RunJob.version == paper.current_version,
                RunJob.status == RunJobStatus.EXTERNAL,
                RunJob.cancelled.is_(False),
            )
        )
    ).scalars()
    job = next(
        (
            j
            for j in jobs
            if j.kind.value == body.job.get("kind")
            and sorted((i["path"], i["sha256"]) for i in j.inputs) == inputs
            and sorted(j.lean_theorems) == sorted(body.job.get("lean_theorems", []))
        ),
        None,
    )
    if job is None:
        return  # a job we do not have (another toolchain, or already filled)
    if result.job_sha256 != digest_of(job):
        raise Invalid("the runner's signature does not cover this job's files and theorems")
    runner = await _shadow(session, peer, peer_key.operator_login or body.runner)
    key = await _mirror_key(session, peer, peer_key, runner.operator)
    accepted, native, reason = evaluate(job, result)
    session.add(
        Run(
            job_id=job.id,
            runner_id=runner.id,
            signing_key_id=key.id,
            trusted=True,
            verdict=result.verdict,
            accepted=accepted,
            native_decide=native,
            result=result.model_dump(mode="json") | {"evaluation": reason, "peer": peer.base_url},
            bundle=bundle.model_dump(mode="json"),
        )
    )
    job.status, job.finished_at = RunJobStatus.DONE, now()
    await session.flush()
    await unlock_decisive(session, paper.id, paper.current_version)
    await recompute_paper(session, paper)


async def _on_review(
    session: AsyncSession, peer: Peer, paper: Paper, fed: FederatedPaper, body: DecisiveReviewBody
) -> None:
    from .decisive import REVIEW_KIND
    from .levels import recompute_paper
    from .params import get_param

    bundle = SignedBundle.model_validate(body.bundle)
    result = TrustedResult.model_validate(bundle.payload)
    peer_key = await _signed_by(session, peer, bundle, Role.REVIEWER)
    if result.kind not in REVIEW_TASK_KINDS or result.review is None:
        raise Invalid("the signed result is not a review")
    # the signature covers the materials' hash: what the reviewer saw is exactly this
    if sha256_hex(canonical_json(body.materials)) != result.materials_sha256:
        raise Invalid("the materials do not match the signed result")
    task_seen = body.materials.get("task") or {}
    seen = body.materials.get("paper") or {}
    if task_seen.get("kind") != result.kind.value:
        raise Invalid("the signed materials are for another kind of task")
    if seen.get("id") != body.paper_id or seen.get("version") != body.version:
        raise Invalid("the signed review is about another paper or version")
    files = {f.get("path"): f.get("sha256") for f in seen.get("files", []) if isinstance(f, dict)}
    copy = (
        await session.execute(
            select(PaperVersion.files).where(PaperVersion.paper_id == paper.id, PaperVersion.version == paper.current_version)
        )
    ).scalar_one()
    if files != fed.files or files != copy or seen.get("manifest") != fed.manifest:
        raise Invalid("the signed review is about other files than this version")
    if result.vendor not in await get_param(session, "allowed_vendors") or not result.transcript_sha256:
        raise Invalid("the review does not meet this platform's rules for decisive reviews (vendor, transcript)")
    adversarial = bool(task_seen.get("adversarial"))  # from the signed materials, not from the entry
    reviewers = select(Operator.id).where(Operator.peer_id == peer.id)
    if (
        await session.execute(
            select(Review.id).where(
                Review.reviewer_id.in_(reviewers),
                Review.paper_id == paper.id,
                Review.bundle["payload"]["task_id"].astext == str(result.task_id),
            )
        )
    ).first():
        return  # one review per task of the lab, however often it is published
    reviewer = await _shadow(session, peer, peer_key.operator_login or body.reviewer, result.model)
    key = await _mirror_key(session, peer, peer_key, reviewer.operator)
    kind = REVIEW_KIND[result.kind]
    if result.kind is DecisiveTaskKind.ANCHOR_REVIEW and adversarial:
        kind = ReviewKind.ADVERSARIAL_REVIEW
    full = peer.trust is PeerTrust.FULL
    task = None
    if full:
        # which of our problems it settles: from the problems the reviewer saw (signed), never
        # from the entry; the signed original stays in the bundle
        signed = [
            ProblemKey(slug=str(p.get("slug", "")), title=str(p.get("title", "")), variant=p.get("variant"), upstream=p.get("upstream"))
            for p in seen.get("problems", [])[:MAX_PROBLEMS]
            if isinstance(p, dict)
        ]
        mapping = await _match_problems(session, signed)
        local = result.model_dump(mode="json")
        local["resolves_problem_slugs"] = [mapping[s].slug for s in result.resolves_problem_slugs if s in mapping]
        linked = set(
            (await session.execute(select(PaperProblem.problem_id).where(PaperProblem.paper_id == paper.id))).scalars()
        )
        for slug in result.resolves_problem_slugs:
            if slug in mapping and mapping[slug].id not in linked:
                session.add(PaperProblem(paper_id=paper.id, problem_id=mapping[slug].id))
                linked.add(mapping[slug].id)
        task = DecisiveTask(
            kind=result.kind,
            status=DecisiveTaskStatus.DONE,
            base_priority=0.0,
            paper_id=paper.id,
            version=paper.current_version,
            adversarial=adversarial,
            leased_by=reviewer.id,
            materials_sha256=result.materials_sha256,
            prompt_sha256=result.prompt_sha256,
            materials=body.materials,
            result=local,
            bundle=bundle.model_dump(mode="json"),
            vendor=result.vendor,
            signing_key_id=key.id,
            done_at=now(),  # when we received it: a revocation cascade counts from here
        )
        session.add(task)
        await session.flush()
    session.add(
        Review(
            paper_id=paper.id,
            version=paper.current_version,
            origin=ReviewOrigin.TRUSTED if full else ReviewOrigin.PEER,
            kind=kind,
            reviewer_id=reviewer.id,
            reviewer_agent_id=reviewer.agent.id if reviewer.agent else None,
            body=result.review.model_dump(mode="json"),
            declared_model=result.model,
            harness=result.harness,
            vendor=result.vendor,
            adversarial=adversarial,
            transcript_sha256=result.transcript_sha256,
            bundle=bundle.model_dump(mode="json"),
            signing_key_id=key.id,
            decisive_task_id=task.id if task else None,
        )
    )
    if full and result.kind in (DecisiveTaskKind.NOVELTY_CONSISTENCY, DecisiveTaskKind.NOVELTY_AUDIT) and result.novelty_category:
        paper.novelty_category = result.novelty_category
        paper.novelty_status = NoveltyStatus.PLATFORM_CONFIRMED
        session.add(
            NoveltyChange(
                paper_id=paper.id,
                category=result.novelty_category,
                status=NoveltyStatus.PLATFORM_CONFIRMED,
                reason=f"novelty audit at {peer.name or peer.base_url}: {result.review.novelty.summary[:900]}",
            )
        )
    await session.flush()
    await recompute_paper(session, paper)


WITHDRAWN = "withdrawn at the lab it came from"


async def _on_withdrawal(session: AsyncSession, peer: Peer, fed: FederatedPaper, body: WithdrawalBody) -> None:
    fed.withdrawn = body.hidden
    if fed.paper_id is None:
        return
    paper = await session.get(Paper, fed.paper_id)
    if paper is None or paper.origin_peer_id != peer.id:
        return
    if peer.trust is PeerTrust.FULL:
        from .identity import set_hidden

        # the lab hides and restores only what it hid: a decision of our moderation stays
        ours = paper.hidden and not (paper.hidden_reason or "").startswith(WITHDRAWN)
        if body.hidden and not paper.hidden:
            reason = f"{WITHDRAWN} ({peer.base_url}): {_clean(body.reason, 300)}"
            await set_hidden(session, ReportTarget.PAPER, paper.id, True, reason, actor="federation", actor_ref=None)
        elif not body.hidden and paper.hidden and not ours:
            await set_hidden(session, ReportTarget.PAPER, paper.id, False, "restored at the lab", actor="federation", actor_ref=None)
    else:
        label = "withdrawn_at_source"
        labels = [x for x in paper.moderation_labels if x != label]
        paper.moderation_labels = [*labels, label] if body.hidden else labels


# --- reading ----------------------------------------------------------------------------------------


async def papers_for_problem(session: AsyncSession, problem_id: uuid.UUID) -> list[dict[str, Any]]:
    """Papers of the labs this instance follows about a problem (all trust levels)."""
    rows = await session.execute(
        select(FederatedPaper, Peer)
        .join(Peer, Peer.id == FederatedPaper.peer_id)
        .where(
            FederatedPaper.problem_ids.contains([str(problem_id)]),
            FederatedPaper.in_scope.is_(True),
            Peer.removed_at.is_(None),
        )
        .order_by(FederatedPaper.submitted_at)
        .limit(50)
    )
    return [
        {
            "lab": peer.name or peer.base_url,
            "lab_url": peer.base_url,
            "trust": peer.trust.value,
            "title": fed.title,
            "url": fed.url,
            "level": fed.level,
            "withdrawn": fed.withdrawn,
            "submitted_at": fed.submitted_at.isoformat() if fed.submitted_at else None,
            "paper_id": str(fed.paper_id) if fed.paper_id else None,
        }
        for fed, peer in rows.all()
    ]


async def origin_of(session: AsyncSession, paper: Paper) -> dict[str, Any] | None:
    if paper.origin_peer_id is None:
        return None
    peer = await session.get(Peer, paper.origin_peer_id)
    fed = (
        await session.execute(select(FederatedPaper).where(FederatedPaper.paper_id == paper.id).limit(1))
    ).scalar_one_or_none()
    if peer is None:
        return None
    return {
        "lab": peer.name or peer.base_url,
        "lab_url": peer.base_url,
        "trust": peer.trust.value,
        "url": fed.url if fed else None,
        "withdrawn": fed.withdrawn if fed else False,
    }


async def overview(session: AsyncSession) -> dict[str, Any]:
    info = instance_info()
    counts = {
        peer_id: (total, in_scope, imported)
        for peer_id, total, in_scope, imported in (
            await session.execute(
                select(
                    FederatedPaper.peer_id,
                    func.count(),
                    func.count().filter(FederatedPaper.in_scope.is_(True)),
                    func.count(FederatedPaper.paper_id),
                ).group_by(FederatedPaper.peer_id)
            )
        ).all()
    }
    peers = (await session.execute(select(Peer).where(Peer.removed_at.is_(None)).order_by(Peer.created_at))).scalars()
    outbox = (await session.execute(select(func.max(FederationEntry.seq)))).scalar_one() or 0
    return {
        "instance": info.model_dump(mode="json")
        | {"federation_key_fingerprint": key_fingerprint(info.federation_key) if info.federation_key else None},
        "outbox_entries": outbox,
        "full_trust_enabled": get_settings().federation_full_trust,
        "peers": [
            {
                "name": p.name or p.base_url,
                "base_url": p.base_url,
                "trust": p.trust.value,
                "status": p.status,
                "last_error": p.last_error,
                "last_pulled_at": p.last_pulled_at.isoformat() if p.last_pulled_at else None,
                "owner_key_fingerprint": key_fingerprint(p.owner_key),
                "papers": counts.get(p.id, (0, 0, 0))[0],
                "papers_in_scope": counts.get(p.id, (0, 0, 0))[1],
                "papers_imported": counts.get(p.id, (0, 0, 0))[2],
                "domains": list(p.domains or []),
                "min_level": p.min_level,
            }
            for p in peers
        ],
    }
