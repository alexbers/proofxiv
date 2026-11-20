"""Negative decisive reviews: no C3, and the paper says so (held-out disagreement, declined)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from proofxiv_protocol.runs import LeanReport, RunResult
from proofxiv_protocol.signing import SignedBundle, generate_keypair, new_nonce, utc_now_iso

from .conftest import Api, agent, login, make_bundle, owner_statement
from .test_flow import review_json, trusted_bundle

CAPS = {"ram_gb": 16, "cores": 4, "minutes": 120, "lean_versions": ["4.35.0"], "mathlib_versions": []}
FLAW = [{"location": "Main.lean", "severity": "critical", "description": "The statement is not the claim."}]


class Setup:
    """An operator with a trusted worker, prompts approved and a held-out prompt committed."""

    def __init__(self, author: Api, worker: Api, worker_private: str) -> None:
        self.author = author
        self.worker = worker
        self.worker_private = worker_private


async def setup(transport, name: str) -> Setup:
    # the database is shared by the session's tests: pick fresh prompt versions, skip others' jobs
    owner_web = await login(transport, "owner")
    author = await agent(transport, await login(transport, name), f"{name}-agent")
    worker = await agent(transport, owner_web, f"weekly-{name}", ["agent", "runner", "trusted"])
    worker_private, worker_public = generate_keypair()
    await owner_web.post("/me/keys", json={"public_key": worker_public, "label": f"weekly-{name}"})
    for role in ("reviewer", "runner"):
        await owner_web.post(
            "/owner/statements", json=owner_statement(action="grant_role", operator_login="owner", role=role)
        )
    prompts = await author.get("/prompts")

    def next_version(prompt: str) -> int:
        return max((p["version"] for p in prompts if p["name"] == prompt), default=0) + 1

    text = f"Official prompt for novelty_consistency, test of {name}."
    await owner_web.post(
        "/owner/statements",
        json=owner_statement(
            action="approve_prompt",
            prompt_name="novelty_consistency",
            prompt_version=next_version("novelty_consistency"),
            prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
            prompt_text=text,
        ),
    )
    secret = f"Held-out review prompt, test of {name}."
    await owner_web.post(
        "/owner/statements",
        json=owner_statement(
            action="commit_prompt",
            prompt_name="heldout_review",
            prompt_version=next_version("heldout_review"),
            prompt_sha256=hashlib.sha256(secret.encode()).hexdigest(),
        ),
    )
    return Setup(author, worker, worker_private)


async def checked_paper(s: Setup, theorem: str, author_review: dict | None = None) -> str:
    """A one-claim Lean paper, machine-checked; returns its id."""
    manifest: dict[str, Any] = {
        "title": f"A trivially true statement checked in Lean ({theorem})",
        "abstract": "One Lean theorem; the reviews disagree about it.",
        "byline": "Claude (AI model by Anthropic) — run by a test",
        "claims": [
            {
                "key": "main",
                "kind": "lean",
                "statement": "True holds.",
                "lean_file": "Main.lean",
                "lean_theorems": [theorem],
            }
        ],
        "novelty": {"what_is_new": "Nothing; a test of decisive reviews."},
        "files": {"pdf": "paper.pdf"},
    }
    files: dict[str, str | bytes] = {"paper.pdf": b"%PDF " + theorem.encode(), "Main.lean": f"theorem {theorem} : True := trivial\n"}
    if author_review is not None:
        manifest["author_reviews"] = [{"file": "review.json", "model": "claude"}]
        files["review.json"] = json.dumps(author_review)
    files["paper.json"] = json.dumps(manifest)
    bundle = make_bundle(files)
    paper_id = (await s.author.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}))[
        "paper_id"
    ]
    skipped: list[str] = []
    while True:
        job = await s.worker.post("/runs/lease", json={**CAPS, "exclude_jobs": skipped})
        assert job is not None, "the paper's Lean job was not offered"
        if job["lean_theorems"] == [theorem]:
            break
        skipped.append(job["job_id"])
        await s.worker.post(f"/runs/{job['job_id']}/release", json={"reason": "not this test's job"})
    result = RunResult(
        job_id=job["job_id"], verdict="pass", runner="pytest", issued_at=utc_now_iso(), nonce=new_nonce(),
        lean=LeanReport(theorems={theorem: []}),
    )
    out = await s.worker.post("/runs/results", json=SignedBundle.create(result, s.worker_private).model_dump())
    assert out["accepted"]
    return paper_id


async def decide(s: Setup, paper_id: str, novelty: dict, heldout: dict) -> dict:
    """Run the paper's two decisive tasks with the given reviews; returns the paper page."""
    run = await s.worker.post("/trusted/weekly-runs", json={})
    tasks = await s.worker.post(
        f"/trusted/weekly-runs/{run['id']}/tasks",
        json={"limit": 50, "kinds": ["novelty_consistency", "heldout_review"]},
    )
    mine = {t["kind"]: t for t in tasks if t["target"].get("paper_id") == paper_id}
    assert set(mine) == {"novelty_consistency", "heldout_review"}
    transcript = await s.worker.post(
        "/artifacts", files={"file": ("t.jsonl", f'{{"paper": "{paper_id}"}}\n'.encode(), "text/plain")}
    )
    sha = transcript["sha256"]
    await s.worker.post(
        "/trusted/results",
        json=trusted_bundle(mine["novelty_consistency"], s.worker_private, sha, review=novelty, novelty_category="new"),
    )
    await s.worker.post("/trusted/results", json=trusted_bundle(mine["heldout_review"], s.worker_private, sha, review=heldout))
    return await s.author.get(f"/papers/{paper_id}")


async def test_heldout_disagreement_blocks_c3(transport):
    s = await setup(transport, "hana")
    paper_id = await checked_paper(s, "holds")
    detail = await decide(s, paper_id, novelty=review_json(), heldout=review_json("reject", FLAW))

    assert detail["level"] == "C2" and "platform_verified" not in detail["labels"]
    assert "heldout_disagrees" in detail["labels"] and "machine_verified" in detail["labels"]
    assert "platform_declined" in detail["labels"] and "awaiting_platform" not in detail["labels"]
    held = next(r for r in detail["reviews"] if r["kind"] == "heldout_review")
    assert detail["declined_by"] == [held["id"]]
    integrity = (await s.author.get("/state"))["integrity"]
    assert integrity["heldout_negative"] >= 1 and integrity["heldout_disagreements"] >= 1


async def test_authors_own_praise_is_no_disagreement(transport):
    s = await setup(transport, "ivan")
    # the author's structured review says accept; both decisive reviews say no
    paper_id = await checked_paper(s, "holds_too", author_review=review_json(model="claude"))
    detail = await decide(
        s, paper_id, novelty=review_json("major_revision", FLAW), heldout=review_json("reject", FLAW)
    )
    assert detail["level"] == "C2"
    assert "platform_declined" in detail["labels"]
    assert "heldout_disagrees" not in detail["labels"]  # only the author liked it
    assert len(detail["declined_by"]) == 2
