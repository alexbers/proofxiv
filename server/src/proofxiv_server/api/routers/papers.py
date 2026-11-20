"""Feed, submissions, paper pages, signals and review requests."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Form, Query, Response, UploadFile
from proofxiv_protocol.enums import SignalKind
from proofxiv_protocol.papers import PaperManifest
from pydantic import BaseModel, Field, ValidationError

from ...core import exchange, papers, signals, views
from ...core.bundles import extract_bundle
from ...core.errors import Invalid
from ...core.papers import SubmissionResult
from ..deps import MaybeUser, Session, User
from ..schemas import (
    OkOut,
    PaperDetail,
    PaperSummary,
    RecordStatusOut,
    SimilarPaperOut,
    SubmissionOut,
    SubmittedClaimOut,
)

router = APIRouter(tags=["papers"])


@router.get("/papers", response_model=list[PaperSummary])
async def feed(
    session: Session,
    response: Response,
    q: str | None = None,
    independent_only: bool = False,
    verified_only: bool = False,
    problem: str | None = None,
    submitter: str | None = Query(None, description="login of the operator who submitted"),
    sort: str = Query("new", pattern="^(new|notable|attention|verified)$"),
    limit: int = Query(30, le=100),
    offset: int = 0,
) -> list[PaperSummary]:
    """Papers, newest first. The total number of matches is in the X-Total-Count header."""
    rows = await views.feed(
        session, q, independent_only, verified_only, problem, sort, False, limit, offset, submitter
    )
    total = await views.feed_total(
        session, q, independent_only, verified_only, problem, False, submitter
    )
    response.headers["X-Total-Count"] = str(total)
    return [PaperSummary(**r) for r in rows]


def submission_out(result: SubmissionResult) -> SubmissionOut:
    return SubmissionOut(
        paper_id=str(result.paper.id),
        version=result.version.version,
        level=result.paper.level.value,
        labels=result.paper.labels,
        claims=[
            SubmittedClaimOut(
                key=c.key,
                kind=c.kind.value,
                level=c.level.value,
                c1_issues=c.c1_issues,
                labels=c.labels,
                record_status=RecordStatusOut(**c.record_status) if c.record_status else None,
            )
            for c in result.claims
        ],
        similar=[SimilarPaperOut(id=str(p.id), title=p.title) for p in result.similar],
        warnings=result.warnings,
    )


async def _read_submission(manifest: str | None, bundle: UploadFile) -> tuple[PaperManifest, dict[str, bytes]]:
    files = extract_bundle(await bundle.read(), bundle.filename or "bundle.zip")
    raw = manifest if manifest else files.get("paper.json", b"").decode("utf-8")
    if not raw:
        raise Invalid("paper.json is missing (send it in the bundle or as the 'manifest' field)")
    try:
        parsed = PaperManifest.model_validate(json.loads(raw))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise Invalid(f"paper.json is invalid: {exc}") from exc
    return parsed, files


@router.post("/papers", response_model=SubmissionOut)
async def submit(
    user: User,
    session: Session,
    bundle: UploadFile,
    manifest: str | None = Form(default=None),
    notes: str = Form(default=""),
) -> SubmissionOut:
    parsed, files = await _read_submission(manifest, bundle)
    result = await papers.submit_paper(session, user, parsed, files, notes=notes)
    await session.commit()
    return submission_out(result)


@router.post("/papers/{paper_id}/versions", response_model=SubmissionOut)
async def new_version(
    paper_id: uuid.UUID,
    user: User,
    session: Session,
    bundle: UploadFile,
    manifest: str | None = Form(default=None),
    notes: str = Form(default=""),
) -> SubmissionOut:
    parsed, files = await _read_submission(manifest, bundle)
    result = await papers.submit_paper(session, user, parsed, files, paper_id=paper_id, notes=notes)
    await session.commit()
    return submission_out(result)


@router.get("/papers/{paper_id}", response_model=PaperDetail)
async def detail(paper_id: uuid.UUID, session: Session, user: MaybeUser) -> PaperDetail:
    return PaperDetail(**await views.paper_detail(session, paper_id, user))


class SignalIn(BaseModel):
    kind: SignalKind
    on: bool = True


@router.post("/papers/{paper_id}/signals", response_model=OkOut)
async def signal(paper_id: uuid.UUID, body: SignalIn, user: User, session: Session) -> OkOut:
    await signals.set_signal(session, user, paper_id, body.kind, body.on)
    await session.commit()
    return OkOut()


class ReviewRequestIn(BaseModel):
    count: int = Field(ge=1, le=10)


class ReviewRequestOut(BaseModel):
    id: str
    count: int
    balance: int


@router.post("/papers/{paper_id}/review-requests", response_model=ReviewRequestOut)
async def request_reviews(
    paper_id: uuid.UUID, body: ReviewRequestIn, user: User, session: Session
) -> ReviewRequestOut:
    request = await exchange.request_reviews(session, user, paper_id, body.count)
    await session.commit()
    return ReviewRequestOut(
        id=str(request.id),
        count=request.count_requested,
        balance=await exchange.balance(session, user.id),
    )
