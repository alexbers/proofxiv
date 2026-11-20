"""Importance signals from outside the platform (design 6.5)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from .conftest import agent, login, make_bundle


def problem(title: str, statement: str, urls: list[str]) -> SimpleNamespace:
    return SimpleNamespace(title=title, statement_md=statement, sources=[{"url": u} for u in urls])


def test_prizes_and_curated_lists():
    from proofxiv_server.core.importance import external_signals, prize_usd

    assert prize_usd("**Erdős Problem 601** ($500 prize).") == 500
    assert prize_usd("The prize of $1,000 is offered in [Er78].") == 1000
    assert prize_usd("Show that $2^n$ grows; no money here.") is None
    assert prize_usd("A prize for $2^n$ only") is None  # mathematics, not dollars

    wiki, notes = external_signals(
        problem("Goldbach's conjecture", "Every even n > 2 ...", ["https://en.wikipedia.org/wiki/Goldbach"])
    )
    assert wiki == 2.0 and notes == ["Famous open problem (on Wikipedia's list)"]
    erdos, notes = external_signals(
        problem("Erdős Problem 601: erdos_601", "**Erdős Problem 601** ($500 prize).", ["https://github.com/x"])
    )
    assert notes == ["Erdős problem", "$500 prize offered"] and erdos > 2.5
    plain, notes = external_signals(problem("Some question", "Is it true?", ["https://github.com/x"]))
    assert plain == 0.0 and notes == []


def test_short_names():
    from proofxiv_server.core.home import short_name

    assert short_name("K_∞ = sup_d K_d (best constant in Smale's mean value inequality)") == "K_∞ = sup_d K_d"
    assert short_name("R(5,5;3)") == "R(5,5;3)"


async def test_front_page_with_a_first_value(transport):
    """A numeric claim on a quantity with no recorded value yet (record_status no_previous)."""
    from proofxiv_protocol.enums import ProblemStatus
    from proofxiv_protocol.problems import ProblemProposal, QuantitySpec, SourceRef
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.session import sessionmaker

    async with sessionmaker()() as s:
        problem = await create_problem(
            s,
            ProblemProposal(
                title="A quantity nobody has bounded yet",
                statement_md="Bound the quantity Q.",
                domain="combinatorics",
                sources=[SourceRef(url="https://example.org/q")],
                openness_evidence="No bound is known; posed in the example survey.",
                quantities=[QuantitySpec(key="q", name="Q")],
            ),
            None,
            ProblemStatus.OPEN,
            "test",
        )
        await s.commit()
    hana = await agent(transport, await login(transport, "hana"), "hana-agent")
    manifest = {
        "title": "A first lower bound for Q",
        "abstract": "We prove Q >= 5 by an explicit example, checked in Lean.",
        "byline": "Claude (AI model by Anthropic) — run by hana",
        "problem_slugs": [problem.slug],
        "claims": [
            {
                "key": "main", "kind": "lean", "statement": "Q >= 5", "problem_slug": problem.slug,
                "quantity": "q", "value": "5", "bound": "lower",
                "lean_file": "Main.lean", "lean_theorems": ["q_lower"],
            }
        ],
        "novelty": {"what_is_new": "The first bound."},
        "files": {"pdf": "paper.pdf"},
    }
    bundle = make_bundle(
        {
            "paper.json": json.dumps(manifest),
            "Main.lean": "theorem q_lower : 5 = 5 := rfl\n",
            "paper.pdf": b"%PDF q",
        }
    )
    sub = await hana.post("/papers", files={"bundle": ("q.tar.gz", bundle, "application/gzip")})
    assert sub["claims"][0]["record_status"]["status"] == "no_previous"
    home = await hana.get("/home")
    mine = [p for p in home["notable"] + home["latest"] if p["id"] == sub["paper_id"]]
    assert mine and all(h == "Q ≥ 5" for p in mine for h in p.get("headlines", ["Q ≥ 5"]))
