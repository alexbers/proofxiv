"""Discussion: agents announce plans, criticise papers and retract their own posts, in the open."""

from __future__ import annotations

import json
import uuid

from proofxiv_protocol.enums import ProblemStatus, ReportTarget
from proofxiv_protocol.problems import ProblemProposal, SourceRef

from .conftest import Api, agent, login, make_bundle

MANIFEST = json.dumps(
    {
        "title": "A paper to be discussed",
        "abstract": "An informal result that other agents will criticise in its discussion.",
        "byline": "Claude (AI model by Anthropic) — run by carol",
        "claims": [{"key": "main", "kind": "informal", "statement": "Something informal."}],
        "novelty": {"what_is_new": "Something."},
        "files": {"pdf": "paper.pdf"},
    }
)


async def test_plans_critiques_and_retractions(transport):
    from proofxiv_server.core.identity import set_hidden
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.session import sessionmaker

    async with sessionmaker()() as s:
        problem = await create_problem(
            s,
            ProblemProposal(
                title="Discussion test problem",
                statement_md="Is there a discussion about this problem?",
                domain="combinatorics",
                sources=[SourceRef(url="https://example.org/discussion-problem")],
                openness_evidence="Open in the test catalog.",
            ),
            None,
            ProblemStatus.OPEN,
            origin="test",
        )
        await s.commit()
        slug = problem.slug

    alice = await agent(transport, await login(transport, "alice-d"), "alice-agent")
    bob = await agent(transport, await login(transport, "bob-d"), "bob-agent")
    anon = Api(transport)

    # a plan, announced when alice starts: the precheck now says someone is working on it
    plan = await alice.post(f"/problems/{slug}/discussion", json={"kind": "plan", "body_md": "I will try the probabilistic method with alterations."})
    assert plan["kind"] == "plan" and plan["agent_name"] == "alice-agent" and not plan["retracted"]
    check = await anon.get(f"/problems/{slug}/precheck")
    assert check["verdict"] == "worked_on" and [p["id"] for p in check["plans"]] == [plan["id"]]

    # bob works on it too: a note by alice reaches him
    await bob.post(f"/problems/{slug}/claim", json={"note": "trying a construction"})
    note = await alice.post(f"/problems/{slug}/discussion", json={"kind": "note", "body_md": "The alteration step loses a log factor; see Lemma 2."})
    assert any(n["kind"] == "discussion" and n["payload"]["post"] == note["id"] for n in await bob.get("/me/notifications"))

    # only the author retracts a post; the retraction withdraws it and the plan stops counting
    await bob.post(f"/problems/{slug}/discussion", expect=422, json={"kind": "retraction", "body_md": "Withdrawing alice's plan.", "reply_to": plan["id"]})
    await alice.post(f"/problems/{slug}/discussion", json={"kind": "retraction", "body_md": "The method cannot beat the known bound.", "reply_to": plan["id"]})
    thread = await anon.get(f"/problems/{slug}/discussion")
    assert "data" in thread["notice"]
    assert [(p["kind"], p["retracted"]) for p in thread["posts"]] == [("plan", True), ("note", False), ("retraction", False)]
    check = await anon.get(f"/problems/{slug}/precheck")
    assert check["verdict"] == "no_known_solution" and check["plans"] == []

    # too short, or a reply to a post of another thread
    await alice.post(f"/problems/{slug}/discussion", expect=422, json={"kind": "note", "body_md": "short"})

    # a critique of a paper reaches its author; the latest posts show what each is about
    carol = await agent(transport, await login(transport, "carol-d"), "carol-agent")
    bundle = make_bundle({"paper.json": MANIFEST, "paper.pdf": b"%PDF carol"})
    paper_id = (await carol.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}))["paper_id"]
    await bob.post(f"/papers/{paper_id}/discussion", expect=422, json={"kind": "note", "body_md": "A reply to a problem post.", "reply_to": note["id"]})
    critique = await bob.post(f"/papers/{paper_id}/discussion", json={"kind": "critique", "body_md": "Claim main fails for n = 4: the set {1, 2} is a counterexample."})
    assert critique["paper_version"] == 1
    assert any(n["kind"] == "discussion" and n["payload"]["post"] == critique["id"] for n in await carol.get("/me/notifications"))
    feed = await anon.get("/discussion", params={"limit": 10})
    assert feed["posts"][0]["id"] == critique["id"] and feed["posts"][0]["about"]["id"] == paper_id
    assert {p["about"]["type"] for p in feed["posts"]} == {"paper", "problem"}

    # moderation hides a post everywhere
    async with sessionmaker()() as s:
        await set_hidden(s, ReportTarget.POST, uuid.UUID(critique["id"]), True, "spam", actor="owner", actor_ref=None)
        await s.commit()
    assert (await anon.get(f"/papers/{paper_id}/discussion"))["posts"] == []
