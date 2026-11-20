"""Public read-only endpoints: artifacts, feeds, OAI-PMH, profiles, digests, stats."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse, Response
from proofxiv_protocol.signing import key_fingerprint
from sqlalchemy import func, select

from ...config import get_settings
from ...core import feeds, gamify, storage, upstream, views
from ...core.decisive import next_check_date
from ...core.errors import NotFound
from ...core.home import front_page
from ...core.importance import state_of_field
from ...db.models import Artifact, Paper, Problem, Review
from ..deps import Session
from ..schemas import (
    DigestOut,
    HomeOut,
    LeaderboardOut,
    OwnerKeyOut,
    ProfileOut,
    RootOfTrustOut,
    StateOut,
    StatsOut,
    TrustedOperatorOut,
)

router = APIRouter(tags=["public"])

SAFE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Cache-Control": "public, max-age=31536000, immutable",
}


def _disposition(kind: str, name: str) -> str:
    """Content-Disposition with an ASCII fallback and the exact UTF-8 name (RFC 6266)."""
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:150] or "file"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name[:300], safe='')}"


def _files_origin(request: Request) -> str | None:
    """The separate files origin to send a browser to when it asks the main site for a file:
    submitted content never runs on the origin that holds the site's cookies."""
    settings = get_settings()
    if not settings.artifacts_url:
        return None
    if request.headers.get("host") != urlparse(settings.public_url).netloc:
        return None  # already on the files origin, or an internal call (e.g. from Next.js)
    return settings.artifacts_url.rstrip("/")


@router.get("/artifacts/{sha256}", response_class=Response)
async def artifact(sha256: str, request: Request, session: Session, inline: bool = False) -> Response:
    files_origin = _files_origin(request)
    if files_origin is not None:
        query = "?inline=true" if inline else ""
        return RedirectResponse(f"{files_origin}/api/v1/artifacts/{sha256}{query}", status_code=307)
    meta = await session.get(Artifact, sha256)
    if meta is None or not storage.path_for(sha256).exists():
        raise NotFound("artifact not found")
    if meta.blocked:
        return Response(
            '{"error": "blocked", "message": "this file belongs to a hidden submission"}',
            status_code=451,
            media_type="application/json",
        )
    if meta.embargoed:
        return Response(
            '{"error": "embargoed", "message": "the transcript of a held-out review is published '
            'when its prompt is revealed"}',
            status_code=403,
            media_type="application/json",
        )
    media = meta.media_type
    disposition = "attachment"
    # PDFs may be shown inline (pdf.js); everything else is a download.
    if inline and media == "application/pdf":
        disposition = "inline"
    elif not media.startswith("text/plain"):
        media = "application/octet-stream" if media != "application/pdf" else media
    headers = dict(SAFE_HEADERS)
    headers["Content-Disposition"] = _disposition(disposition, meta.original_name)
    if request.method == "HEAD":
        headers["Content-Length"] = str(meta.size)
        return Response(None, media_type=media, headers=headers)
    return Response(storage.read_bytes(sha256), media_type=media, headers=headers)


@router.head("/artifacts/{sha256}", include_in_schema=False)
async def artifact_head(sha256: str, request: Request, session: Session, inline: bool = False) -> Response:
    return await artifact(sha256, request, session, inline)


@router.get("/root-of-trust", response_model=RootOfTrustOut)
async def root_of_trust() -> RootOfTrustOut:
    """Owner public keys (they sign role grants, key revocations, prompt approvals) and the
    accounts that act as owners on the website."""
    settings = get_settings()
    return RootOfTrustOut(
        keys=[
            OwnerKeyOut(public_key=k, fingerprint=key_fingerprint(k))
            for k in settings.owner_public_keys
        ],
        logins=list(settings.owner_logins),
    )


@router.get("/feeds/papers.json")
async def json_feed(session: Session, scope: str = Query("verified", pattern="^(verified|all)$")) -> dict:
    return await feeds.json_feed(session, scope)


@router.get("/feeds/problems.json")
async def problems_feed(session: Session) -> dict:
    """Confirmed changes of open problems' status (solved, new records), newest first."""
    return await upstream.status_feed(session)


@router.get("/feeds/problems.atom", response_class=Response)
async def problems_atom(session: Session) -> Response:
    """The same as Atom, for RSS readers and chat bots (a Zulip stream can subscribe to it)."""
    xml = upstream.status_atom(await upstream.status_items(session))
    return Response(xml, media_type="application/atom+xml; charset=utf-8")


@router.get("/upstream/formal-conjectures")
async def formal_conjectures_candidates(session: Session) -> list[dict]:
    """Statements settled here that formal-conjectures still tags open: the edit and the pull-request
    text. Opened by a person after reading it (`proofxiv owner upstream open`), never automatically."""
    return await upstream.fc_candidates(session)


@router.get("/oai", response_class=Response)
async def oai(
    session: Session,
    verb: str,
    identifier: str | None = None,
    metadataPrefix: str | None = None,  # noqa: N803 (OAI-PMH parameter name)
) -> Response:
    xml = await feeds.oai(session, verb, identifier, metadataPrefix)
    return Response(xml, media_type="text/xml; charset=utf-8")


@router.get("/trusted-operators", response_model=list[TrustedOperatorOut])
async def trusted_operators(session: Session) -> list[TrustedOperatorOut]:
    return [TrustedOperatorOut(**t) for t in await views.trusted_list(session)]


@router.get("/operators/{login}", response_model=ProfileOut)
async def profile(login: str, session: Session) -> ProfileOut:
    return ProfileOut(**await views.operator_profile(session, login))


@router.get("/leaderboard", response_model=LeaderboardOut)
async def leaderboard(
    session: Session, period: str = Query("week", pattern="^(week|month|all)$"), limit: int = Query(50, le=200)
) -> LeaderboardOut:
    """Points for what survived checking, ranks and achievements. A game on top of the science:
    points never change a trust level or the order of checks."""
    return LeaderboardOut(**await gamify.leaderboard(session, period, limit))


@router.get("/home", response_model=HomeOut)
async def home(session: Session) -> HomeOut:
    """The front page in one request: numbers, notable results, record board, solved and most
    wanted problems, latest papers and the last weekly digest."""
    return HomeOut(**await front_page(session))


@router.get("/state", response_model=StateOut)
async def state(session: Session, domain: str | None = None, days: int = Query(30, le=365)) -> StateOut:
    """State of the field: solved and improved results, active and resisting problems."""
    return StateOut(**await state_of_field(session, domain, days))


@router.get("/digests", response_model=list[DigestOut])
async def digests(session: Session) -> list[DigestOut]:
    return [DigestOut(**d) for d in await views.digests(session)]


@router.get("/stats", response_model=StatsOut)
async def stats(session: Session) -> StatsOut:
    async def grouped(column: Any, model: Any) -> dict[str, int]:
        rows = await session.execute(select(column, func.count()).select_from(model).group_by(column))
        return {getattr(k, "value", str(k)): n for k, n in rows.all()}

    nxt = await next_check_date(session)
    return StatsOut(
        problems=await grouped(Problem.status, Problem),
        papers=await grouped(Paper.level, Paper),
        reviews=await grouped(Review.origin, Review),
        next_weekly_run=nxt.isoformat() if nxt else None,
    )
