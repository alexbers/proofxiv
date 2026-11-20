"""The automatic literature watch (no network: parsers and the effects of what they find) and the
precheck agents ask before working on a problem."""

from __future__ import annotations

from proofxiv_protocol.enums import ProblemStatus
from proofxiv_protocol.problems import ProblemProposal, SourceRef
from proofxiv_server.core import watch

from .conftest import Api

FILE = '''
/-! # Erdős Problem 999 -/
namespace Erdos999

/-- The main problem. -/
@[category research open, AMS 11]
theorem erdos_999 : True := by
  sorry

section Variants
/-- A solved part. -/
@[category research solved, AMS 11, formal_proof using lean4 at
  "https://palomar-registry.org/entry.html?id=PALOMAR-2026-01-01-000001"]
theorem erdos_999.variants.easy : True := by
  trivial
end Variants

-- @[category research open] theorem commented_out : True := sorry
end Erdos999

@[category research open]
theorem _root_.Other.top : True := by
  sorry
'''

ERDOS_YAML = '''- number: "999"
  status:
    state: "proved (Lean)"
    last_update: "2026-10-01"
  tags: ["number theory"]

- number: "998"
  status:
    state: "open"
    last_update: "2025-08-31"
'''


def test_parse_upstream_and_erdos():
    decls = {d.name: d for d in watch.parse_upstream("FormalConjectures/ErdosProblems/999.lean", FILE)}
    assert set(decls) == {"erdos_999", "erdos_999.variants.easy", "_root_.Other.top"}
    assert decls["erdos_999"].qualified == "Erdos999.erdos_999"
    assert decls["erdos_999"].category == "research open"
    easy = decls["erdos_999.variants.easy"]
    assert easy.category == "research solved" and easy.formal_proof and "PALOMAR-2026-01-01-000001" in easy.formal_proof
    assert decls["_root_.Other.top"].qualified == "Other.top"
    assert watch.parse_erdos(ERDOS_YAML) == {"999": ("proved (Lean)", "2026-10-01"), "998": ("open", "2025-08-31")}


def proposal(name: str) -> ProblemProposal:
    blob = "https://github.com/google-deepmind/formal-conjectures/blob/abc/FormalConjectures/ErdosProblems/999.lean"
    return ProblemProposal(
        title="Erdős Problem 999",
        variant=name,
        statement_md="A statement imported for the watch test.",
        domain="number theory",
        sources=[SourceRef(url=blob, title="formal-conjectures")],
        openness_evidence="Tagged research open upstream (test).",
        formalizable=True,
        lean_statement=f"theorem {name} : True := by\n  sorry",
    )


async def test_leads_events_and_precheck(transport):
    from proofxiv_server.core.gamify import ledger
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.models import DecisiveTask, ExternalEvent, Operator, ProblemLead
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import func, select

    async with sessionmaker()() as s:
        main = await create_problem(s, proposal("erdos_999"), None, ProblemStatus.OPEN, origin="formal-conjectures")
        easy = await create_problem(s, proposal("erdos_999.variants.easy"), None, ProblemStatus.OPEN, origin="formal-conjectures")
        await s.commit()
        main_slug, easy_slug = main.slug, easy.slug

    decls = {(d.path, d.name): d for d in watch.parse_upstream("FormalConjectures/ErdosProblems/999.lean", FILE)}
    for _ in range(2):  # running twice adds no second lead or event
        async with sessionmaker()() as s:
            stats = await watch.apply_formal_conjectures(s, decls, "c0ffee" * 6)
            await watch.apply_erdos(s, watch.parse_erdos(ERDOS_YAML))
            await watch.apply_palomar(s, [{"id": "PALOMAR-2026-02-02-000002", "title": "A proof", "trust": {"level": "high"},
                                           "formalization": {"theorem_names": ["Erdos999.erdos_999"]}}])
            await s.commit()
    assert stats["statements"] >= 2

    async with sessionmaker()() as s:
        leads = {(lead.source, lead.strong) for lead in (await s.execute(select(ProblemLead))).scalars()}
        assert ("formal-conjectures", True) in leads  # the solved variant
        assert ("erdosproblems", True) in leads  # the main statement: proved (Lean)
        assert ("palomar", True) in leads
        events = list((await s.execute(select(ExternalEvent).where(ExternalEvent.auto_source.is_not(None)))).scalars())
        assert len(events) == 3 and all(e.confirmed is None for e in events)
        tasks = (await s.execute(select(func.count()).select_from(DecisiveTask).where(DecisiveTask.event_id.in_([e.id for e in events])))).scalar_one()
        assert tasks == 3
        # confirmed by the weekly run, an automatic find still earns nobody points
        for e in events:
            e.confirmed = True
        await s.commit()
        bot = (await s.execute(select(Operator.id).where(Operator.login == watch.WATCH_LOGIN))).scalar_one()
        assert not [ev for ev in await ledger(s) if ev.operator_id == bot]

    anon = Api(transport)
    listed = await anon.get("/problems", params={"q": "Erdős Problem 999"})
    assert {p["slug"]: p["possibly_settled"] for p in listed}[main_slug] is True
    detail = await anon.get(f"/problems/{easy_slug}")
    assert detail["possibly_settled"] and detail["leads"][0]["source"] == "formal-conjectures"
    check = await anon.get(f"/problems/{main_slug}/precheck")
    assert check["verdict"] == "possibly_settled"
    assert {lead["source"] for lead in check["leads"]} >= {"erdosproblems", "palomar"}
    assert check["upstream"]["name"] == "Erdos999.erdos_999" and check["upstream"]["category"] == "research open"
    assert "not a literature search" in check["advice"]


async def test_pull_and_code_leads(transport):
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.models import ProblemLead
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import select

    async with sessionmaker()() as s:
        p = await create_problem(s, proposal("erdos_999.parts.ii"), None, ProblemStatus.OPEN, origin="formal-conjectures")
        p.upstream = {"path": "FormalConjectures/ErdosProblems/999.lean", "name": "Erdos999.erdos_999.parts.ii"}
        await s.commit()
        pull = {"number": 7885, "title": "Prove erdos_999.parts.ii", "state": "open", "merged_at": None, "html_url": "https://github.com/x/pull/7885"}
        files = [{"filename": "FormalConjectures/ErdosProblems/999.lean", "patch": "-@[category research open]\n+@[category research solved]\n theorem erdos_999.parts.ii"}]
        assert await watch.apply_pull(s, pull, files) >= 1
        assert watch.code_needle(p) == "erdos_999.parts.ii"
        hits = [
            {"repository": {"full_name": "someone/formal-conjectures"}, "path": "FormalConjectures/ErdosProblems/999.lean", "html_url": "https://github.com/a"},
            {"repository": {"full_name": "solver/breakthroughs"}, "path": "erdos999/Main.lean", "html_url": "https://github.com/solver/b"},
        ]
        assert await watch.apply_code_hits(s, p, "erdos_999.parts.ii", hits) == 1  # the fork is skipped
        await s.commit()
        sources = {(lead.source, lead.strong) for lead in (await s.execute(select(ProblemLead).where(ProblemLead.problem_id == p.id))).scalars()}
        assert sources == {("formal-conjectures-pr", False), ("github", False)}


async def test_upstream_candidates_and_feed(transport):
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.models import ExternalEvent, Operator
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import select

    palomar = "https://palomar-registry.org/entry.html?id=PALOMAR-2026-03-03-000003"
    async with sessionmaker()() as s:
        p = await create_problem(s, proposal("erdos_999.variants.hard"), None, ProblemStatus.SOLVED_EXTERNALLY, origin="formal-conjectures")
        p.upstream = {"repo": watch.FC_REPO, "path": "FormalConjectures/ErdosProblems/999.lean", "name": "Erdos999.erdos_999.variants.hard", "category": "research open"}
        reporter = (await s.execute(select(Operator.id).limit(1))).scalar_one()
        s.add(ExternalEvent(kind="solved", problem_id=p.id, source_url=palomar, source_title="Palomar entry",
                            summary="A Lean proof is registered.", reported_by=reporter, confirmed=True,
                            confirm_reason="The entry proves exactly this statement."))
        await s.commit()
        slug = p.slug

    anon = Api(transport)
    candidates = {c["slug"]: c for c in await anon.get("/upstream/formal-conjectures")}
    c = candidates[slug]
    assert c["name"] == "erdos_999.variants.hard" and c["qualified"] == "Erdos999.erdos_999.variants.hard"
    assert c["edit"] == {"category": ["research open", "research solved"], "formal_proof": palomar}
    assert palomar in c["pr_body"] and "not by human experts" in c["pr_body"]
    feed = await anon.get("/feeds/problems.json")
    assert any(item["problem"] == slug and item["title"].endswith(": solved") for item in feed["items"])
    atom = await anon.client.get("/feeds/problems.atom")
    assert atom.status_code == 200 and "<feed xmlns" in atom.text and "erdos_999.variants.hard" in atom.text


async def test_settled_problem_gets_the_palomar_link_without_an_event(transport):
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.models import ExternalEvent, Operator
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import func, select

    async with sessionmaker()() as s:
        p = await create_problem(s, proposal("erdos_999.variants.github"), None, ProblemStatus.SOLVED_EXTERNALLY, origin="formal-conjectures")
        p.upstream = {"path": "FormalConjectures/ErdosProblems/999.lean", "name": "Erdos999.erdos_999.variants.github", "category": "research open"}
        reporter = (await s.execute(select(Operator.id).limit(1))).scalar_one()
        s.add(ExternalEvent(kind="solved", problem_id=p.id, source_url="https://github.com/solver/b", summary="Solved in a repo.",
                            reported_by=reporter, confirmed=True, confirm_reason="Checked."))
        await s.commit()
        before = (await s.execute(select(func.count()).select_from(ExternalEvent))).scalar_one()
        await watch.apply_palomar(s, [{"id": "PALOMAR-2026-04-04-000004", "title": "Proof", "formalization": {"theorem_names": ["Erdos999.erdos_999.variants.github"]}}])
        await s.commit()
        assert (await s.execute(select(func.count()).select_from(ExternalEvent))).scalar_one() == before
        slug = p.slug
    candidates = {c["slug"]: c for c in await Api(transport).get("/upstream/formal-conjectures")}
    assert candidates[slug]["edit"]["formal_proof"].endswith("PALOMAR-2026-04-04-000004")


async def test_decision_rewrites_the_watch_summary(transport):
    """The site and the digest must not call an event the weekly run checked "not checked yet"."""
    from proofxiv_server.core import events
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.models import ExternalEvent
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import select

    async with sessionmaker()() as s:
        yes = await create_problem(s, proposal("erdos_999.variants.yes"), None, ProblemStatus.OPEN, origin="formal-conjectures")
        no = await create_problem(s, proposal("erdos_999.variants.no"), None, ProblemStatus.OPEN, origin="formal-conjectures")
        for p in (yes, no):
            await watch.record_lead(s, p, "palomar", f"https://palomar.example/{p.slug}", "A proof", "Registered.", strong=True)
        await s.commit()
        found = {e.problem_id: e for e in (await s.execute(select(ExternalEvent).where(ExternalEvent.problem_id.in_([yes.id, no.id])))).scalars()}
        assert all(e.summary == events.WATCH_PREFIX[None] + "Registered." for e in found.values())
        await events.apply_event(s, found[yes.id], True, "Checked.")
        await events.apply_event(s, found[no.id], False, "A variant only.")
        assert found[yes.id].summary == events.WATCH_PREFIX[True] + "Registered."
        assert found[no.id].summary == events.WATCH_PREFIX[False] + "Registered."
        await events.revert_event(s, found[yes.id])  # its signing key was revoked: waits for a new check
        assert found[yes.id].summary == events.WATCH_PREFIX[None] + "Registered."
        await s.rollback()
