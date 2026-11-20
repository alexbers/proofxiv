"""A paper's tier: the reviewers' significance scores as a word, "known" for a rediscovery."""

from __future__ import annotations

import json

from proofxiv_protocol.enums import NoveltyCategory, ReviewOrigin

from .conftest import Api, agent, login, make_bundle

MANIFEST = json.dumps(
    {
        "title": "A paper to be scored",
        "abstract": "An informal result, here only to receive review scores in a test.",
        "byline": "Claude (AI model by Anthropic) — run by tina",
        "claims": [{"key": "main", "kind": "informal", "statement": "Something informal."}],
        "novelty": {"what_is_new": "Something."},
        "files": {"pdf": "paper.pdf"},
    }
)


async def test_tier_from_scores_and_rediscovery(transport):
    from proofxiv_server.core import significance
    from proofxiv_server.db.models import Paper, Review
    from proofxiv_server.db.session import sessionmaker

    tina = await agent(transport, await login(transport, "tina"), "tina-agent")
    bundle = make_bundle({"paper.json": MANIFEST, "paper.pdf": b"%PDF tina"})
    paper_id = (await tina.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}))["paper_id"]

    async with sessionmaker()() as s:
        paper = await s.get(Paper, paper_id)
        assert paper is not None
        await significance.update(s, paper)
        assert paper.tier is None  # nobody has scored it yet

        def review(origin: ReviewOrigin, score: int) -> Review:
            return Review(paper_id=paper.id, version=paper.current_version, origin=origin,
                          reviewer_id=paper.submitter_id, body={"significance_score": score})

        s.add_all([review(ReviewOrigin.EXCHANGE, 5), review(ReviewOrigin.EXCHANGE, 5)])
        await s.flush()
        await significance.update(s, paper)
        assert (paper.tier, paper.tier_basis) == ("breakthrough", "mean 5.0/5 of 2 peer reviews (5, 5)")

        # decisive reviews take precedence over exchange ones
        s.add_all([review(ReviewOrigin.TRUSTED, 3), review(ReviewOrigin.TRUSTED, 3), review(ReviewOrigin.TRUSTED, 4)])
        await s.flush()
        await significance.update(s, paper)
        assert paper.tier == "solid" and paper.tier_basis == "mean 3.3/5 of 3 platform reviews (3, 3, 4)"

        paper.novelty_category = NoveltyCategory.REDISCOVERY
        await significance.update(s, paper)
        assert paper.tier == "known"
        await s.commit()

    listed = await Api(transport).get(f"/papers/{paper_id}")
    assert listed["tier"] == "known" and "already known" in listed["tier_basis"]
