"""Open feeds: JSON (slopXiv-style) and a minimal OAI-PMH endpoint (oai_dc)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from xml.sax.saxutils import escape

from proofxiv_protocol.enums import TrustLevel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Paper

REPOSITORY_NAME = "ProofXiv"
OAI_PREFIX = "oai:proofxiv:"


def _paper_url(paper: Paper) -> str:
    return f"{get_settings().web_url}/papers/{paper.id}"


def _record(paper: Paper) -> dict[str, Any]:
    return {
        "id": str(paper.id),
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": paper.byline,
        "url": _paper_url(paper),
        "level": paper.level.value,
        "labels": paper.labels,
        "novelty_category": paper.novelty_category.value if paper.novelty_category else None,
        "version": paper.current_version,
        "submitted": paper.created_at.isoformat(),
        "verified": paper.verified_at.isoformat() if paper.verified_at else None,
        "note": "Verified on ProofXiv by machine checks and AI review run by trusted operators; "
        "not reviewed by human experts."
        if paper.level is TrustLevel.C3
        else "Not verified by the platform yet; see labels for who reviewed it.",
    }


async def json_feed(session: AsyncSession, scope: str = "verified", limit: int = 200) -> dict:
    stmt = select(Paper).where(Paper.hidden.is_(False))
    if scope == "verified":
        stmt = stmt.where(Paper.level == TrustLevel.C3).order_by(Paper.verified_at.desc())
    else:
        stmt = stmt.order_by(Paper.created_at.desc())
    papers = (await session.execute(stmt.limit(limit))).scalars()
    return {
        "source": REPOSITORY_NAME,
        "scope": scope,
        "generated": datetime.now(UTC).isoformat(),
        "items": [_record(p) for p in papers],
    }


def _oai_header(verb: str, args: str) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = escape(get_settings().public_url + "/oai")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
        f"<responseDate>{now}</responseDate>"
        f'<request verb="{escape(verb)}"{args}>{base}</request>'
    )


def _dc(paper: Paper) -> str:
    stamp = (paper.verified_at or paper.created_at).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"<record><header><identifier>{OAI_PREFIX}{paper.id}</identifier>"
        f"<datestamp>{stamp}</datestamp></header><metadata>"
        '<oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{escape(paper.title)}</dc:title>"
        f"<dc:creator>{escape(paper.byline)}</dc:creator>"
        f"<dc:description>{escape(paper.abstract)}</dc:description>"
        f"<dc:date>{paper.created_at.date().isoformat()}</dc:date>"
        f"<dc:identifier>{escape(_paper_url(paper))}</dc:identifier>"
        "<dc:type>Preprint</dc:type>"
        "</oai_dc:dc></metadata></record>"
    )


async def oai(session: AsyncSession, verb: str, identifier: str | None, prefix: str | None) -> str:
    if verb == "Identify":
        return (
            _oai_header(verb, "")
            + "<Identify>"
            + f"<repositoryName>{REPOSITORY_NAME}</repositoryName>"
            + f"<baseURL>{escape(get_settings().public_url)}/oai</baseURL>"
            + "<protocolVersion>2.0</protocolVersion>"
            + "<earliestDatestamp>2026-01-01T00:00:00Z</earliestDatestamp>"
            + "<deletedRecord>no</deletedRecord><granularity>YYYY-MM-DDThh:mm:ssZ</granularity>"
            + "</Identify></OAI-PMH>"
        )
    if verb == "ListMetadataFormats":
        return (
            _oai_header(verb, "")
            + "<ListMetadataFormats><metadataFormat><metadataPrefix>oai_dc</metadataPrefix>"
            + "<schema>http://www.openarchives.org/OAI/2.0/oai_dc.xsd</schema>"
            + "<metadataNamespace>http://www.openarchives.org/OAI/2.0/oai_dc/</metadataNamespace>"
            + "</metadataFormat></ListMetadataFormats></OAI-PMH>"
        )
    if verb in ("ListRecords", "GetRecord") and prefix != "oai_dc":
        return _oai_header(verb, "") + '<error code="cannotDisseminateFormat"/></OAI-PMH>'
    if verb == "ListRecords":
        papers = (
            await session.execute(
                select(Paper)
                .where(Paper.hidden.is_(False), Paper.level == TrustLevel.C3)
                .order_by(Paper.verified_at)
            )
        ).scalars()
        body = "".join(_dc(p) for p in papers)
        return _oai_header(verb, ' metadataPrefix="oai_dc"') + f"<ListRecords>{body}</ListRecords></OAI-PMH>"
    if verb == "GetRecord" and identifier and identifier.startswith(OAI_PREFIX):
        import uuid

        try:
            paper = await session.get(Paper, uuid.UUID(identifier[len(OAI_PREFIX):]))
        except ValueError:
            paper = None
        if paper is None or paper.hidden:
            return _oai_header(verb, "") + '<error code="idDoesNotExist"/></OAI-PMH>'
        return _oai_header(verb, ' metadataPrefix="oai_dc"') + f"<GetRecord>{_dc(paper)}</GetRecord></OAI-PMH>"
    return _oai_header(verb, "") + '<error code="badVerb"/></OAI-PMH>'
