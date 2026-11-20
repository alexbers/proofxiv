"""Prior art found by a literature sync disputes a paper's novelty and queues a new novelty audit;
only a decisive check that names the category settles it."""

from __future__ import annotations

import json

from proofxiv_protocol.enums import (
    DecisiveTaskKind,
    DecisiveTaskStatus,
    ExternalEventKind,
    NoveltyCategory,
    NoveltyStatus,
)

from .conftest import agent, login, make_bundle

MANIFEST = json.dumps(
    {
        "title": "A paper whose special case is classical",
        "abstract": "An informal result; a literature sync later finds its special case in older work.",
        "byline": "Claude (AI model by Anthropic) — run by petra",
        "claims": [{"key": "main", "kind": "informal", "statement": "A bound with a classical special case."}],
        "novelty": {"what_is_new": "The general case."},
        "files": {"pdf": "paper.pdf"},
    }
)


async def test_sync_prior_art_disputes_and_queues_an_audit(transport):
    from proofxiv_server.core import events
    from proofxiv_server.core.decisive import paper_materials
    from proofxiv_server.db.models import DecisiveTask, ExternalEvent, Paper
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import select

    petra = await agent(transport, await login(transport, "petra"), "petra-agent")
    bundle = make_bundle({"paper.json": MANIFEST, "paper.pdf": b"%PDF petra"})
    paper_id = (await petra.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}))["paper_id"]

    async def audits(s) -> list[DecisiveTaskStatus]:
        rows = await s.execute(
            select(DecisiveTask.status).where(
                DecisiveTask.paper_id == paper_id, DecisiveTask.kind == DecisiveTaskKind.NOVELTY_AUDIT
            )
        )
        return sorted(rows.scalars(), key=str)

    async with sessionmaker()() as s:
        paper = await s.get(Paper, paper_id)
        assert paper is not None
        # the novelty audit is done: new with precedents
        for task in (await s.execute(select(DecisiveTask).where(DecisiveTask.paper_id == paper_id))).scalars():
            if task.kind is DecisiveTaskKind.NOVELTY_AUDIT:
                task.status = DecisiveTaskStatus.DONE
        paper.novelty_category = NoveltyCategory.NEW_WITH_PRECEDENTS
        paper.novelty_status = NoveltyStatus.PLATFORM_CONFIRMED

        def prior_art(url: str) -> ExternalEvent:
            event = ExternalEvent(kind=ExternalEventKind.PRIOR_ART, paper_id=paper.id, source_url=url,
                                  summary="The special case lambda = 0 is classical.", reported_by=paper.submitter_id)
            s.add(event)
            return event

        first = prior_art("https://arxiv.org/abs/1209.4581")
        await s.flush()
        await events.apply_event(s, first, True, "found by the trusted weekly run")
        await s.flush()
        # not a rediscovery: the category stays until a new audit weighs the event
        assert (paper.novelty_category, paper.novelty_status) == (
            NoveltyCategory.NEW_WITH_PRECEDENTS,
            NoveltyStatus.DISPUTED,
        )
        assert await audits(s) == [DecisiveTaskStatus.DONE, DecisiveTaskStatus.READY]
        materials = await paper_materials(s, paper, paper.current_version)
        assert [e["source_url"] for e in materials["literature_events"]] == ["https://arxiv.org/abs/1209.4581"]

        # a second find while the audit waits adds no second audit
        second = prior_art("https://arxiv.org/abs/1507.02063")
        await s.flush()
        await events.apply_event(s, second, True, "found by the trusted weekly run")
        await s.flush()
        assert await audits(s) == [DecisiveTaskStatus.DONE, DecisiveTaskStatus.READY]

        # a decisive check of an event that names the category settles it at once
        third = prior_art("https://example.org/earlier-paper")
        await s.flush()
        await events.apply_event(s, third, True, "Checked.", NoveltyCategory.REDISCOVERY)
        assert (paper.novelty_category, paper.novelty_status) == (
            NoveltyCategory.REDISCOVERY,
            NoveltyStatus.PLATFORM_CONFIRMED,
        )
        await s.rollback()
