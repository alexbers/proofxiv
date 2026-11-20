"""Lean claims: one machine-check job per file, local modules checked by the same rules."""

from __future__ import annotations

import json

from .conftest import agent, login, make_bundle

DEFS = "def table : List Nat := List.range 30\n\ntheorem table_sum : table.sum = 435 := by native_decide\n"
MAIN = (
    "import Proj.Defs\n\n"
    "theorem first : table.sum = 435 := table_sum\n"
    "theorem second : table.length = 30 := by decide\n"
)


def manifest(title: str, claims: list[dict]) -> str:
    return json.dumps(
        {
            "title": title,
            "abstract": "Two facts about a small table, checked in Lean.",
            "byline": "Claude (AI model by Anthropic) — run by gina",
            "claims": claims,
            "novelty": {"what_is_new": "Nothing; a test of the checking pipeline."},
            "files": {"pdf": "paper.pdf"},
        }
    )


def lean_claim(key: str, theorem: str) -> dict:
    return {
        "key": key,
        "kind": "lean",
        "statement": f"The {key} fact about the table.",
        "lean_file": "lean/Main.lean",
        "lean_theorems": [theorem],
    }


async def submit(api, title: str, claims: list[dict], files: dict[str, str], expect: int = 200) -> dict:
    bundle = make_bundle({"paper.json": manifest(title, claims), "paper.pdf": b"%PDF t", **files})
    return await api.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}, expect=expect)


async def test_one_job_per_lean_file(transport):
    from proofxiv_server.db.models import RunJob
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import select

    gina = await agent(transport, await login(transport, "gina"), "gina-agent")
    sub = await submit(
        gina,
        "Two facts about a table of thirty numbers",
        [lean_claim("first", "first"), lean_claim("second", "second")],
        {"lean/Main.lean": MAIN, "lean/Proj/Defs.lean": DEFS},
    )
    assert [c["level"] for c in sub["claims"]] == ["C1", "C1"], sub
    assert "native_decide" in sub["claims"][0]["labels"]  # found in the imported module
    async with sessionmaker()() as s:
        jobs = list((await s.execute(select(RunJob).where(RunJob.paper_id == sub["paper_id"]))).scalars())
    assert len(jobs) == 1
    job = jobs[0]
    assert job.lean_theorems == ["first", "second"] and len(job.claim_ids) == 2
    assert {i["path"] for i in job.inputs} == {"lean/Main.lean", "lean/Proj/Defs.lean"}
    detail = await gina.get(f"/papers/{sub['paper_id']}")
    assert all(c["job_status"] == "queued" for c in detail["claims"])


async def test_modules_follow_the_same_rules(transport):
    gina = await agent(transport, await login(transport, "gina"), "gina-agent-2")
    missing = await submit(
        gina, "A claim whose module is missing", [lean_claim("first", "first")], {"lean/Main.lean": MAIN}
    )
    assert missing["claims"][0]["level"] == "C0"
    assert any("not in the bundle" in i for i in missing["claims"][0]["c1_issues"])

    bad = await submit(
        gina,
        "A claim whose module adds an axiom",
        [lean_claim("first", "first")],
        {"lean/Main.lean": MAIN, "lean/Proj/Defs.lean": DEFS + "axiom extra : 1 = 2\n"},
    )
    issues = bad["claims"][0]["c1_issues"]
    assert any(i.startswith("lean/Proj/Defs.lean: Lean file declares an `axiom`") for i in issues), issues

    # code that runs when a module is compiled is refused outright: reviewers compile papers
    refused = await submit(
        gina,
        "A claim whose module runs code",
        [lean_claim("first", "first")],
        {"lean/Main.lean": MAIN, "lean/Proj/Defs.lean": DEFS + "#eval table.sum\n"},
        expect=422,
    )
    assert "lean/Proj/Defs.lean: runs code when compiled" in refused["message"], refused
