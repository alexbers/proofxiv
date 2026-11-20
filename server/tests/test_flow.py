"""End-to-end scenario of the first stage, through the public REST API."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from proofxiv_protocol.enums import ProblemStatus
from proofxiv_protocol.problems import ProblemProposal, QuantitySpec, SourceRef
from proofxiv_protocol.runs import (
    CheckerReport,
    LeanReport,
    ReferenceItem,
    ReferenceReport,
    RunResult,
)
from proofxiv_protocol.signing import SignedBundle, generate_keypair, new_nonce, utc_now_iso
from proofxiv_protocol.trusted import TrustedResult

from .conftest import agent, login, make_bundle, owner_statement

PROMPTS = [
    "novelty_consistency",
    "novelty_audit",
    "anchor_review",
    "adversarial_review",
    "statement_audit",
    "moderation",
    "confirm_problem",
    "confirm_event",
    "sync",
    "digest",
]


def review_json(recommendation: str = "accept", issues: list | None = None, **extra) -> dict:
    body = {
        "summary": "The paper improves the lower bound by an explicit colouring; the Lean file "
        "checks the colouring and the statement matches the survey definition.",
        "correctness": "Kernel-checked certificate; I re-derived the key counting step.",
        "statement_faithfulness": "The Lean statement matches the problem definition.",
        "novelty": {"summary": "Improves the survey table.", "category": "record_improvement"},
        "significance": "A ten-point jump in a classical table.",
        "reproducibility": "Bundle builds with the pinned toolchain.",
        "issues": issues or [],
        "recommendation": recommendation,
        "confidence": 4,
        "model": "test-model",
        "harness": "pytest",
    }
    body.update(extra)
    return body


def trusted_bundle(task: dict, key: str, transcript: str, **extra) -> dict:
    payload = TrustedResult(
        task_id=task["task_id"],
        kind=task["kind"],
        materials_sha256=extra.pop("materials_sha256", task["materials_sha256"]),
        prompt_sha256=task["prompt_sha256"],
        vendor=extra.pop("vendor", "anthropic"),
        model="claude-test",
        harness="claude-code",
        transcript_sha256=transcript,
        client_version="0.1.0",
        issued_at=utc_now_iso(),
        nonce=new_nonce(),
        **extra,
    )
    return SignedBundle.create(payload, key).model_dump(mode="json")


def by_kind(tasks: list[dict], kind: str, paper_id: str | None = None) -> list[dict]:
    out = [t for t in tasks if t["kind"] == kind]
    if paper_id is not None:
        out = [t for t in out if t["target"]["paper_id"] == paper_id]
    return out


async def test_first_stage_flow(transport):
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.session import sessionmaker

    owner_web = await login(transport, "owner")
    alice_web = await login(transport, "alice")
    bob_web = await login(transport, "bob")
    carol_web = await login(transport, "carol")
    alice = await agent(transport, alice_web, "alice-agent")
    bob = await agent(transport, bob_web, "bob-agent")
    carol = await agent(transport, carol_web, "carol-agent")
    worker = await agent(transport, owner_web, "weekly", ["agent", "runner", "trusted"])
    worker_private, worker_public = generate_keypair()
    key = await owner_web.post("/me/keys", json={"public_key": worker_public, "label": "weekly"})

    # --- owner statements: roles and official prompts -----------------------------------
    for role in ("reviewer", "runner"):
        await owner_web.post(
            "/owner/statements",
            json=owner_statement(action="grant_role", operator_login="owner", role=role),
        )
    for name in PROMPTS:
        text = f"Official prompt for {name}, version 1."
        await owner_web.post(
            "/owner/statements",
            json=owner_statement(
                action="approve_prompt",
                prompt_name=name,
                prompt_version=1,
                prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                prompt_text=text,
            ),
        )
    me = await owner_web.get("/me")
    assert set(me["roles"]) == {"reviewer", "runner"} and me["is_owner"]
    forged_private, _ = generate_keypair()
    forged = SignedBundle.create(
        {
            "type": "proofxiv.owner_statement.v1",
            "action": "grant_role",
            "operator_login": "alice",
            "role": "reviewer",
            "issued_at": utc_now_iso(),
            "nonce": new_nonce(),
        },
        forged_private,
    )
    await alice_web.post("/owner/statements", json=forged.model_dump(), expect=403)
    journal = await alice_web.get("/owner/journal")
    # (other tests add federation statements to the same journal)
    assert len([j for j in journal if j["action"] in ("grant_role", "approve_prompt")]) == 2 + len(PROMPTS)

    # the owner commits a held-out prompt: its hash now, its text after rotation
    heldout_text = "Held-out review prompt, version 1: judge independently."
    heldout_sha = hashlib.sha256(heldout_text.encode()).hexdigest()
    await owner_web.post(
        "/owner/statements",
        json=owner_statement(
            action="commit_prompt", prompt_name="heldout_review", prompt_version=1, prompt_sha256=heldout_sha
        ),
    )
    listed = next(p for p in await carol.get("/prompts") if p["name"] == "heldout_review")
    assert listed["text"] is None and listed["held_out"] and listed["sha256"] == heldout_sha

    # --- catalog --------------------------------------------------------------------------
    async with sessionmaker()() as s:
        problem = await create_problem(
            s,
            ProblemProposal(
                title="Lower bounds for the hypergraph Ramsey number R(5,5;3)",
                statement_md="Improve the lower bound for R(5,5;3).",
                domain="combinatorics",
                sources=[SourceRef(url="https://example.org/ds1.18")],
                openness_evidence="Listed as open in the dynamic survey, 2024 revision.",
                quantities=[QuantitySpec(key="r553", name="R(5,5;3)", best_lower="88")],
            ),
            None,
            ProblemStatus.OPEN,
            "import",
        )
        await s.commit()
    slug = problem.slug
    proposed = await alice.post(
        "/problems",
        json={
            "title": "Symmetric Hadamard matrices of order 260",
            "statement_md": "Does a symmetric Hadamard matrix of order 260 exist?",
            "domain": "combinatorics",
            "sources": [{"url": "https://example.org/dokovic2022"}],
            "openness_evidence": "Listed as open in Đoković (2022); no later paper found.",
            "quantities": [
                {
                    "key": "smallest_open",
                    "name": "Smallest order without a known example",
                    "best_lower": "260",
                    "source": "https://example.org/dokovic2022",
                }
            ],
        },
    )
    assert proposed["problem"]["status"] == "proposed"
    hadamard = proposed["problem"]["slug"]
    proposal_record = (await carol.get(f"/problems/{hadamard}"))["quantities"][0]["best"]["lower"]
    assert proposal_record["value"] == "260" and not proposal_record["verified"]
    found = await carol.get("/problems", params={"q": "hypergraph Ramsey"})
    assert any(p["slug"] == slug for p in found)
    listing = await carol.client.get("/problems", params={"domain": "combinatorics", "limit": 1})
    total = int(listing.headers["x-total-count"])
    assert total >= 2 and len(listing.json()) == 1
    domains = await carol.get("/problems/domains")
    assert {"domain": "combinatorics", "problems": total} in domains
    lease = await alice.post(f"/problems/{slug}/claim", json={"note": "trying affine colourings"})
    assert lease["others_working"] == 0
    # the map of approaches: a dead end must say where it stops
    random_colourings = {
        "name": "Random colourings",
        "status": "stalled",
        "summary_md": "Random 2-colourings with local search reach 90 vertices.",
    }
    await alice.post(f"/problems/{slug}/approaches", json=random_colourings, expect=422)
    random_colourings["barrier_md"] = "Local search finds no colouring above 90 in 10^9 steps."
    mapped = await alice.post(f"/problems/{slug}/approaches", json=random_colourings)
    assert mapped["name"] == "Random colourings" and not mapped["confirmed"]
    await alice.post(
        f"/problems/{slug}/attempts",
        json={
            "outcome": "partial",
            "notes_md": "Random colourings stall at 90; trying affine ones.",
            "approach": "random colourings",
        },
    )
    page = await carol.get(f"/problems/{slug}")
    attempt = page["attempts"][0]
    assert attempt["operator_login"] == "alice" and attempt["agent_name"] == "alice-agent"
    assert attempt["approach"] == "Random colourings"  # spelled as in the map
    assert [(a["name"], a["status"], a["attempts"]) for a in page["approaches"]] == [
        ("Random colourings", "stalled", 1)
    ]

    # --- submission -------------------------------------------------------------------------
    manifest = {
        "title": "New lower bounds for R(5,5;3) from affine-invariant colourings",
        "abstract": "We give a colouring showing R(5,5;3) >= 98 and certify it in Lean.",
        "byline": "Claude (AI model by Anthropic) — run by alice",
        "problem_slugs": [slug],
        "claims": [
            {
                "key": "main",
                "kind": "lean",
                "statement": "R(5,5;3) >= 98",
                "problem_slug": slug,
                "quantity": "r553",
                "value": "98",
                "bound": "lower",
                "lean_file": "Main.lean",
                "lean_theorems": ["r553_lower"],
            },
            {
                "key": "cert",
                "kind": "certificate",
                "statement": "The colouring has no monochromatic K5^(3).",
                "certificate_type": "hypergraph_ramsey_coloring",
                "certificate_file": "colouring.json",
            },
        ],
        "novelty": {"what_is_new": "Lower bound 98 (was 88).", "sources_searched": ["DS1.18"]},
        "files": {"pdf": "paper.pdf", "source": ["main.tex"]},
        "author_reviews": [{"file": "review.md", "model": "claude"}],
    }
    files = {
        "paper.json": json.dumps(manifest),
        "Main.lean": "-- a comment mentioning sorry\ntheorem r553_lower : True := trivial\n",
        "colouring.json": '{"n": 97}',
        "paper.pdf": b"%PDF-1.4 test",
        "main.tex": "\\documentclass{article}",
        "review.md": "# Independent review\nThe construction looks right.",
    }
    bundle = make_bundle(files)
    sub = await alice.post("/papers", files={"bundle": ("paper.tar.gz", bundle, "application/gzip")})
    paper_id = sub["paper_id"]
    assert sub["level"] == "C1", sub
    assert {"author_reviews_only", "one_review", "awaiting_platform"} <= set(sub["labels"])
    main = next(c for c in sub["claims"] if c["key"] == "main")
    assert main["record_status"]["status"] == "new_record"
    await alice.post(
        "/papers", files={"bundle": ("paper.tar.gz", bundle, "application/gzip")}, expect=409
    )

    # --- review exchange --------------------------------------------------------------------
    request = await alice.post(f"/papers/{paper_id}/review-requests", json={"count": 1})
    assert request["balance"] == 0
    await alice.post(f"/papers/{paper_id}/review-requests", json={"count": 1}, expect=422)
    assignment = await bob.post("/exchange/assignments/next", json={})
    assert assignment["paper_id"] == paper_id
    assert "reviews" not in assignment["materials"]
    assert "lakefile.lean" in assignment["safety_check"] and "malware" in assignment["safety_check"]
    await bob.post(
        f"/exchange/assignments/{assignment['id']}/review",
        json=review_json(summary="Too short."),
        expect=422,
    )
    major = [{"location": "Section 3", "severity": "major", "description": "Explain the symmetry."}]
    submitted = await bob.post(
        f"/exchange/assignments/{assignment['id']}/review",
        json=review_json("minor_revision", major),
    )
    assert submitted["balance"] == 2
    bob_review_id = submitted["review_id"]
    detail = await alice.get(f"/papers/{paper_id}")
    assert "exchange_reviews" in detail["labels"]
    assert "author_reviews_only" not in detail["labels"] and "one_review" not in detail["labels"]
    assert detail["novelty_status"] == "peer_confirmed"
    independent = await carol.get("/papers", params={"independent_only": True})
    assert [p["id"] for p in independent if p["origin_lab"] is None] == [paper_id]  # not counting other tests' imports
    await alice.post(f"/reviews/{bob_review_id}/rate", json={"useful": True})
    await bob.post(f"/reviews/{bob_review_id}/rate", json={"useful": True}, expect=403)

    bob_manifest = {
        "title": "A parity obstruction for polygons with sides on n lines",
        "abstract": "We prove an informal parity obstruction for n = 1, 7 mod 8.",
        "byline": "Claude (AI model by Anthropic) — run by bob",
        "claims": [{"key": "parity", "kind": "informal", "statement": "No polygon exists for n = 7 mod 8."}],
        "novelty": {"what_is_new": "The parity argument."},
        "files": {"pdf": "paper.pdf"},
    }
    bob_sub = await bob.post(
        "/papers",
        files={
            "bundle": (
                "b.tar.gz",
                make_bundle({"paper.json": json.dumps(bob_manifest), "paper.pdf": b"%PDF bob"}),
                "application/gzip",
            )
        },
    )
    bob_paper = bob_sub["paper_id"]
    assert "no_reviews" in bob_sub["labels"] and "informal_steps" in bob_sub["labels"]
    await bob.post(f"/papers/{bob_paper}/review-requests", json={"count": 1})
    # alice may not review bob's paper: bob has just reviewed hers (no direct swap)
    await alice.post("/exchange/assignments/next", json={}, expect=204)
    carol_assignment = await carol.post("/exchange/assignments/next", json={})
    assert carol_assignment["paper_id"] == bob_paper

    # --- machine checks by the trusted runner ----------------------------------------------
    before_runs = datetime.now(UTC) - timedelta(seconds=1)
    caps = {"lean_versions": ["4.35.0"], "mathlib_versions": [], "checkers": True}
    # a runner without the right environment gets nothing; a released job gets no verdict
    await worker.post("/runs/lease", json={"ram_gb": 16, "cores": 4, "minutes": 120}, expect=204)
    job = await worker.post("/runs/lease", json={"ram_gb": 16, "cores": 4, "minutes": 120, **caps})
    await worker.post(f"/runs/{job['job_id']}/release", json={"reason": "no toolchain here"})
    await worker.post(
        "/runs/lease",
        json={"ram_gb": 16, "cores": 4, "minutes": 120, **caps, "exclude_jobs": [job["job_id"]]},
    )  # the other job
    await worker.post(
        "/runs/lease",
        json={"ram_gb": 16, "cores": 4, "minutes": 120, **caps, "exclude_jobs": [job["job_id"]]},
        expect=204,
    )
    async with sessionmaker()() as s:  # put the second lease back for the scenario below
        from proofxiv_server.db.models import RunJob
        from sqlalchemy import update

        await s.execute(
            update(RunJob).where(RunJob.status == "leased").values(status="queued", leased_by=None, lease_expires_at=None)
        )
        await s.commit()
    caps_refs = {**caps, "references": True}  # this runner also resolves bibliographies
    for _ in range(3):
        job = await worker.post("/runs/lease", json={"ram_gb": 16, "cores": 4, "minutes": 120, **caps_refs})
        if job["kind"] == "lean":
            extra = {"lean": LeanReport(theorems={"r553_lower": ["propext"]})}
        elif job["kind"] == "checker":
            extra = {
                "checker": CheckerReport(
                    checker=job["certificate_type"], implementations={"python": True, "rust": True}
                )
            }
        else:
            assert job["inputs"] == [{"sha256": job["inputs"][0]["sha256"], "path": "main.tex"}]
            item = ReferenceItem(key="ds", text="Radziszowski, Small Ramsey numbers", status="verified")
            extra = {"references": ReferenceReport(items=[item])}
        result = RunResult(
            job_id=job["job_id"],
            verdict="pass",
            runner="pytest",
            issued_at=utc_now_iso(),
            nonce=new_nonce(),
            **extra,
        )
        out = await worker.post(
            "/runs/results", json=SignedBundle.create(result, worker_private).model_dump()
        )
        assert out["accepted"], out
    await worker.post("/runs/lease", json={"ram_gb": 16, "cores": 4, "minutes": 120, **caps_refs}, expect=204)
    detail = await alice.get(f"/papers/{paper_id}")
    assert detail["level"] == "C2" and "machine_verified" in detail["labels"]
    assert "references_verified" in detail["labels"] and detail["references"]["counts"]["verified"] == 1
    assert sorted(t["kind"] for t in detail["pending_tasks"]) == ["heldout_review", "novelty_consistency"]
    assert {t["status"] for t in detail["pending_tasks"]} == {"ready"}

    # --- weekly trusted run ------------------------------------------------------------------
    run = await worker.post("/trusted/weekly-runs", json={"budget": {"anthropic": 1_000_000}})
    tasks = await worker.post(f"/trusted/weekly-runs/{run['id']}/tasks", json={"limit": 50})
    kinds = {t["kind"] for t in tasks}
    assert {"novelty_consistency", "confirm_problem", "sync", "digest", "anchor_review"} <= kinds
    transcript = await worker.post(
        "/artifacts", files={"file": ("transcript.jsonl", b'{"role": "assistant"}\n', "text/plain")}
    )
    sha = transcript["sha256"]
    held_file = {"file": ("heldout.jsonl", b'{"prompt": "held-out"}\n', "text/plain")}
    await carol.post("/artifacts", params={"embargo": True}, files=held_file, expect=403)  # not trusted
    secret = await worker.post("/artifacts", params={"embargo": True}, files=held_file)
    await carol.get(f"/artifacts/{secret['sha256']}", expect=403)  # private from the upload on

    nc = by_kind(tasks, "novelty_consistency", paper_id)[0]
    accept = review_json()
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(nc, worker_private, sha, review=accept, novelty_category="record_improvement"),
        expect=422,  # does not answer bob's major issue
    )
    answered = review_json(
        improvement_source="new_idea",
        responses=[
            {
                "review_id": bob_review_id,
                "issue_index": 0,
                "verdict": "accepted",
                "reasoning": "The symmetry argument should be expanded; minor.",
            }
        ]
    )
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(
            nc, worker_private, sha, review=answered, novelty_category="record_improvement",
            materials_sha256="0" * 64,
        ),
        expect=422,
    )
    await carol.post(f"/papers/{paper_id}/signals", json={"kind": "follow"})
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(
            nc, worker_private, sha, review=answered, novelty_category="record_improvement",
            resolves_problem_slugs=[slug],
        ),
    )
    assert (await carol.get(f"/papers/{paper_id}"))["level"] == "C2"  # the held-out review is missing
    held = by_kind(tasks, "heldout_review", paper_id)[0]
    assert held["prompt_sha256"] == heldout_sha and held["materials"]["paper"]["reviews"] == []
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(held, worker_private, secret["sha256"], review=review_json()),
    )
    detail = await carol.get(f"/papers/{paper_id}")
    assert detail["level"] == "C3" and "platform_verified" in detail["labels"]
    heldout_review = next(r for r in detail["reviews"] if r["kind"] == "heldout_review")
    assert heldout_review["transcript_embargoed"]
    await carol.get(f"/artifacts/{secret['sha256']}", expect=403)
    problem_page = await carol.get(f"/problems/{slug}")
    assert problem_page["status"] == "solved_on_platform"
    # the platform-verified value enters the record table, linked to the paper
    best_lower = problem_page["quantities"][0]["best"]["lower"]
    assert best_lower["value"] == "98" and best_lower["paper_id"] == paper_id and best_lower["verified"]
    followed = await carol.get("/me/notifications")
    assert any(n["kind"] == "paper_level" and n["payload"]["to"] == "C3" for n in followed)
    state = await carol.get("/state")
    assert any(p["slug"] == slug for p in state["recently_solved"])
    integrity = state["integrity"]
    assert integrity["machine_checks_passed"] >= 2 and integrity["references_verified"] == 1
    assert (integrity["heldout_reviews"], integrity["heldout_negative"]) == (1, 0)
    assert (integrity["prompts_held_out"], integrity["prompts_revealed"]) == (1, 0)
    # an improvement is the paper's 98, not the starting value 88 typed into the import
    improved = [r for r in state["record_improvements"] if r["problem"] == slug]
    assert [r["value"] for r in improved] == ["98"] and improved[0]["paper_id"] == paper_id

    # the front page: why the result matters, in words, and the record board
    home = await carol.get("/home")
    assert home["stats"]["platform_verified"] >= 1 and home["stats"]["records_broken"] >= 1
    top = home["notable"][0]
    assert top["id"] == paper_id and top["significance"] > 0
    assert top["headlines"][0] == "R(5,5;3) ≥ 98 (previous best 88)"
    assert top["significance_notes"][:2] == [
        f"Settles “{problem.title}”",
        "New record for R(5,5;3): 88 → 98 (11% higher); a new idea, not just more search (platform reviews)",
    ]
    board = next(r for r in home["records"] if r["problem_slug"] == slug)
    assert (board["previous"], board["value"], board["gain"], board["kind"]) == ("88", "98", 10.0, "platform")
    assert any(s["slug"] == slug and s["paper_id"] == paper_id for s in home["solved"])
    notable = await carol.get("/papers", params={"sort": "notable"})
    assert notable[0]["id"] == paper_id

    # the game on top: points for what survived checking, achievements, the leaderboard
    board = await carol.get("/leaderboard", params={"period": "all"})
    leader = board["rows"][0]
    settled_xp = round(50 * (1 + problem_page["importance"]))
    verified_xp = 25 + round(10 * top["significance"])
    groundwork = 5 + 1  # alice's mapped dead end and her attempt note
    assert leader["login"] == "alice" and leader["agents"] == ["alice-agent"]
    assert leader["xp"] == 10 + verified_xp + 30 + settled_xp + groundwork
    assert {"first_blood", "verified", "record_breaker", "dragon_slayer"} <= {b["id"] for b in leader["badges"]}
    bob_row = next(r for r in board["rows"] if r["login"] == "bob")
    assert bob_row["xp"] == 5 + 10  # an exchange review the author rated useful
    assert home["leaders"][0]["login"] == "alice"
    assert any(h["kind"] == "settled" and h["login"] == "alice" for h in home["happenings"])
    assert any(h["kind"] == "achievement" and "Dragon slayer" in h["text"] for h in home["happenings"])
    assert all(w["xp_bounty"] >= 125 for w in home["wanted"])
    progress = (await carol.get("/operators/alice"))["progress"]
    assert progress["xp"] == leader["xp"] and progress["rank"]["level"] >= 2
    assert "dragon_slayer" in {a["id"] for a in progress["achievements"]}
    assert "cartographer" in {a["id"] for a in progress["locked"]}
    assert detail["novelty_category"] == "record_improvement"
    assert detail["novelty_status"] == "platform_confirmed"

    confirm = next(t for t in by_kind(tasks, "confirm_problem") if t["materials"]["problem"]["slug"] == hadamard)
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(
            confirm, worker_private, sha,
            confirm={
                "confirmed": True,
                "reasoning": "No solution found in the literature.",
                "records_verified": True,
            },
        ),
    )
    hadamard_page = await carol.get(f"/problems/{hadamard}")
    assert hadamard_page["quantities"][0]["best"]["lower"]["verified"]
    assert (await carol.get(f"/problems/{proposed['problem']['slug']}"))["status"] == "open"

    found_approaches = {
        slug: [
            {
                "name": "Cyclic colourings",
                "status": "refuted",
                "summary_md": "Colourings invariant under Z_n were searched exhaustively.",
                "barrier_md": "No cyclic colouring exists on 89 or more vertices.",
                "sources": [{"kind": "literature", "ref": "https://arxiv.org/abs/2601.00002"}],
            }
        ],
        "no-such-problem": [{"name": "Ignored", "status": "promising", "summary_md": "Should not appear."}],
    }
    sync_tasks = by_kind(tasks, "sync")
    assert slug in {t["materials"]["problem"]["slug"] for t in sync_tasks}
    for task in sync_tasks:
        own = task["materials"]["problem"]["slug"] == slug
        await worker.post(
            "/trusted/results",
            json=trusted_bundle(
                task, worker_private, sha,
                sync={
                    "problem_slugs": [slug],
                    "sources_checked": ["arXiv math.CO"],
                    "events": [],
                    # a task only writes its own problem's map, so the other tasks change nothing
                    "approaches": found_approaches if own else {slug: [{**found_approaches[slug][0], "status": "promising"}]},
                },
            ),
        )
    mapped = {a["name"]: a for a in (await carol.get(f"/problems/{slug}"))["approaches"]}
    assert mapped["Cyclic colourings"]["confirmed"] and mapped["Cyclic colourings"]["status"] == "refuted"
    assert not mapped["Random colourings"]["confirmed"]

    # bob's informal paper: audits and two anchor reviews (same vendor -> single_vendor label)
    for kind in ("novelty_audit", "statement_audit", "anchor_review"):
        for task in by_kind(tasks, kind, bob_paper):
            extra = {"novelty_category": "new"} if kind == "novelty_audit" else {}
            await worker.post(
                "/trusted/results",
                json=trusted_bundle(task, worker_private, sha, review=review_json(), **extra),
            )
    for task in by_kind(tasks, "heldout_review", bob_paper):
        await worker.post(
            "/trusted/results",
            json=trusted_bundle(task, worker_private, secret["sha256"], review=review_json()),
        )
    bob_detail = await carol.get(f"/papers/{bob_paper}")
    assert bob_detail["level"] == "C3"
    assert {"single_vendor", "informal_steps", "platform_verified"} <= set(bob_detail["labels"])
    assert any(r["adversarial"] for r in bob_detail["reviews"])
    assert "heldout_disagrees" not in bob_detail["labels"]

    # rotation: the owner reveals the held-out text, the transcripts become public
    await owner_web.post(
        "/owner/statements",
        json=owner_statement(
            action="reveal_prompt", prompt_name="heldout_review", prompt_version=1,
            prompt_text="a different text",
        ),
        expect=422,
    )
    await owner_web.post(
        "/owner/statements",
        json=owner_statement(
            action="reveal_prompt", prompt_name="heldout_review", prompt_version=1, prompt_text=heldout_text
        ),
    )
    assert (await carol.client.get(f"/artifacts/{secret['sha256']}")).status_code == 200
    revealed = next(p for p in await carol.get("/prompts") if p["name"] == "heldout_review")
    assert revealed["text"] == heldout_text and revealed["revealed_at"]

    digest = by_kind(tasks, "digest")[0]
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(digest, worker_private, sha, digest_md="# Week\nTwo papers verified."),
    )
    await worker.post(f"/trusted/weekly-runs/{run['id']}/finish", json={"stats": {"tasks": len(tasks)}})
    digests = await carol.get("/digests")
    assert digests[0]["digest_md"].startswith("# Week") and digests[0]["signed"]

    # --- reports and appeals -------------------------------------------------------------------
    await carol.post(
        "/reports",
        json={"target_type": "paper", "target_id": bob_paper, "reason": "false_claim", "evidence_md": "n=7 has a polygon"},
    )
    run2 = await worker.post("/trusted/weekly-runs", json={})
    moderation = await worker.post(
        f"/trusted/weekly-runs/{run2['id']}/tasks", json={"limit": 5, "kinds": ["moderation"]}
    )
    report_id = moderation[0]["target"]["report_id"]
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(
            moderation[0], worker_private, sha,
            moderation={"report_id": report_id, "upheld": True, "action": "label", "reasoning": "The counterexample is valid."},
        ),
    )
    assert (await carol.get(f"/papers/{bob_paper}"))["level"] == "D"
    await bob.post(f"/reports/{report_id}/appeal", json={"text": "The counterexample has a crossing."})
    appeal_task = await worker.post(
        f"/trusted/weekly-runs/{run2['id']}/tasks", json={"limit": 5, "kinds": ["moderation"]}
    )
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(
            appeal_task[0], worker_private, sha,
            moderation={
                "report_id": appeal_task[0]["target"]["report_id"],
                "upheld": False,
                "action": "none",
                "reasoning": "The polygon in the report self-intersects.",
            },
        ),
    )
    assert (await carol.get(f"/papers/{bob_paper}"))["level"] == "C3"

    urgent = await carol.post(
        "/reports",
        json={"target_type": "paper", "target_id": bob_paper, "reason": "malware", "evidence_md": "data.bin is an ELF"},
    )
    assert urgent["urgent"]
    notes = await owner_web.get("/me/notifications")
    assert any(n["kind"] == "urgent_report" for n in notes)
    await alice_web.post(
        "/owner/hide", json={"target_type": "paper", "target_id": bob_paper, "reason": "not an owner"}, expect=403
    )
    await owner_web.post(
        "/owner/hide", json={"target_type": "paper", "target_id": bob_paper, "reason": "malware report"}
    )
    feed = await carol.get("/papers")
    assert all(p["id"] != bob_paper for p in feed)
    hidden_page = await carol.get(f"/papers/{bob_paper}")
    assert "hidden" in hidden_page["labels"]
    bob_pdf = hidden_page["pdf_sha256"]
    await carol.get(f"/artifacts/{bob_pdf}", expect=451)
    log = await carol.get("/moderation-log")
    assert log[0]["action"] == "hide" and log[0]["actor"] == "owner"
    assert log[0]["paper_id"] == bob_paper and log[0]["title"] == bob_manifest["title"]
    await owner_web.post(
        "/owner/statements",
        json=owner_statement(action="unhide", target_type="paper", target_id=bob_paper, reason="false alarm"),
    )
    assert (await carol.client.get(f"/artifacts/{bob_pdf}")).status_code == 200
    assert "hidden" not in (await carol.get(f"/papers/{bob_paper}"))["labels"]
    head = await carol.client.head(f"/artifacts/{bob_pdf}")
    assert head.status_code == 200 and head.headers["content-length"] == str(len(b"%PDF bob"))
    assert not head.content
    mine = await carol.client.get("/papers", params={"submitter": "bob"})
    assert [p["id"] for p in mine.json()] == [bob_paper] and mine.headers["x-total-count"] == "1"

    # --- literature sync by an ordinary agent ------------------------------------------------
    await carol.post(
        "/sync/reports",
        json={
            "problem_slugs": [slug],
            "sources_checked": ["arXiv"],
            "events": [
                {
                    "kind": "record_improved",
                    "problem_slug": slug,
                    "source_url": "https://arxiv.org/abs/2611.00001",
                    "summary": "A new colouring gives R(5,5;3) >= 99.",
                    "quantity": "r553",
                    "value": "99",
                    "bound": "lower",
                }
            ],
        },
    )
    problem_page = await carol.get(f"/problems/{slug}")
    assert problem_page["events"][0]["confirmed"] is None
    confirm_tasks = await worker.post(
        f"/trusted/weekly-runs/{run2['id']}/tasks", json={"limit": 5, "kinds": ["confirm_event"]}
    )
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(
            confirm_tasks[0], worker_private, sha,
            confirm={"confirmed": True, "reasoning": "The arXiv paper states the new bound."},
        ),
    )
    problem_page = await carol.get(f"/problems/{slug}")
    assert problem_page["quantities"][0]["best"]["lower"]["value"] == "99"
    assert "superseded" in (await carol.get(f"/papers/{paper_id}"))["labels"]
    assert any(n["kind"] == "problem_event" for n in await alice.get("/me/notifications"))

    # --- feeds --------------------------------------------------------------------------------
    json_feed = await carol.get("/feeds/papers.json")
    assert {i["id"] for i in json_feed["items"]} >= {paper_id}
    oai = await carol.get("/oai", params={"verb": "ListRecords", "metadataPrefix": "oai_dc"})
    assert paper_id in oai
    stats = await carol.get("/stats")
    assert stats["papers"]["C3"] >= 2

    # --- key revocation cascades to everything the key signed -------------------------------
    await owner_web.post(
        "/owner/statements",
        json=owner_statement(
            action="revoke_key",
            key_fingerprint=key["fingerprint"],
            revoke_from=before_runs.isoformat(),
            reason="test compromise",
        ),
    )
    detail = await carol.get(f"/papers/{paper_id}")
    assert detail["level"] == "C1" and "recheck" in detail["labels"]
    assert all(r["removed"] for r in detail["reviews"] if r["origin"] == "trusted")
    job = await worker.post("/runs/lease", json={"ram_gb": 16, "cores": 4, "minutes": 120, **caps})
    retry = RunResult(
        job_id=job["job_id"], verdict="pass", runner="pytest", issued_at=utc_now_iso(), nonce=new_nonce(),
        lean=LeanReport(theorems={"r553_lower": []}),
    )
    await worker.post(
        "/runs/results", json=SignedBundle.create(retry, worker_private).model_dump(), expect=403
    )

    # what the revoked key confirmed is taken back, so the re-check applies it exactly once
    r553 = await carol.get(f"/problems/{slug}")
    assert r553["status"] == "open"  # its solving paper is no longer platform-verified
    best_lower = r553["quantities"][0]["best"]["lower"]
    assert best_lower["value"] == "98" and not best_lower["verified"]  # the paper's, re-checked
    assert r553["events"][0]["confirmed"] is None
    cyclic = next(a for a in r553["approaches"] if a["name"] == "Cyclic colourings")
    assert not cyclic["confirmed"] and cyclic["barrier_md"]  # the text stays, the mark goes
    assert "superseded" not in (await carol.get(f"/papers/{paper_id}"))["labels"]
    hadamard_page = await carol.get(f"/problems/{hadamard}")
    assert hadamard_page["status"] == "proposed"
    assert not hadamard_page["quantities"][0]["best"]["lower"]["verified"]
    assert await carol.get("/digests") == []

    new_private, new_public = generate_keypair()
    await owner_web.post("/me/keys", json={"public_key": new_public, "label": "weekly-2"})
    recheck = await worker.post(
        f"/trusted/weekly-runs/{run2['id']}/tasks", json={"limit": 5, "kinds": ["confirm_event"]}
    )
    await worker.post(
        "/trusted/results",
        json=trusted_bundle(
            recheck[0], new_private, sha,
            confirm={"confirmed": True, "reasoning": "The arXiv paper states the new bound."},
        ),
    )
    r553 = await carol.get(f"/problems/{slug}")
    assert r553["quantities"][0]["best"]["lower"]["value"] == "99"
    assert [h["value"] for h in r553["quantities"][0]["history"]].count("99") == 1

    # a new version starts without independent reviews of its own
    manifest_v2 = {**manifest, "abstract": manifest["abstract"] + " Second version."}
    bundle_v2 = make_bundle({**files, "paper.json": json.dumps(manifest_v2)})
    await alice.post(
        f"/papers/{paper_id}/versions", files={"bundle": ("v2.tar.gz", bundle_v2, "application/gzip")}
    )
    independent = await carol.get("/papers", params={"independent_only": True})
    assert paper_id not in [p["id"] for p in independent]
