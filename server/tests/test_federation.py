"""Federation: this instance publishes an outbox, and follows other ProofXiv labs with the trust
its owner chose (index, review, full), checking every signature against the lab owner's key."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from proofxiv_protocol.enums import ProblemStatus
from proofxiv_protocol.federation import (
    DecisiveReviewBody,
    InstanceInfo,
    OutboxEntry,
    OutboxPage,
    PaperVersionBody,
    ProblemKey,
    RunResultBody,
    WithdrawalBody,
)
from proofxiv_protocol.owner import OwnerStatement
from proofxiv_protocol.problems import ProblemProposal, SourceRef
from proofxiv_protocol.reviews import ReviewBody
from proofxiv_protocol.runs import LeanReport, RunResult, job_digest
from proofxiv_protocol.signing import (
    SignedBundle,
    canonical_json,
    generate_keypair,
    new_nonce,
    sha256_hex,
    utc_now_iso,
)
from proofxiv_protocol.trusted import TrustedResult

from .conftest import Api, agent, login, make_bundle, owner_statement
from .test_flow import review_json

MANIFEST = json.dumps(
    {
        "title": "A paper this instance publishes",
        "abstract": "An informal result that the labs following this instance will see in its outbox.",
        "byline": "Claude (AI model by Anthropic) — run by fedora",
        "claims": [{"key": "main", "kind": "informal", "statement": "Something informal."}],
        "novelty": {"what_is_new": "Something."},
        "files": {"pdf": "paper.pdf"},
    }
)


class Lab:
    """Another ProofXiv instance, as its followers see it over HTTP."""

    def __init__(self, host: str) -> None:
        self.base = f"https://{host}"
        self.owner_private, self.owner_public = generate_keypair()
        self.federation_private, self.federation_public = generate_keypair()
        self.reviewer_private, self.reviewer_public = generate_keypair()
        self.runner_private, self.runner_public = generate_keypair()
        self.journal: list[dict[str, Any]] = []
        self.entries: list[tuple[str, dict[str, Any], str | None]] = []
        self.blobs: dict[str, bytes] = {}
        self.artifacts = self.base + "/api/v1/artifacts/{sha256}"
        self.outbox_path = "/api/v1/federation/outbox"
        self.submitter = "lab-alice"
        self.papers: dict[str, dict[str, Any]] = {}  # what a reviewer at the lab sees of each paper
        self.limits: list[int] = []  # page sizes followers asked for
        self.statement(action="attest_key", role="federation", public_key=self.federation_public)
        self.statement(action="attest_key", role="reviewer", public_key=self.reviewer_public, operator_login="weekly")
        self.statement(action="attest_key", role="runner", public_key=self.runner_public, operator_login="runner")

    def statement(self, **fields: Any) -> None:
        bundle = SignedBundle.create(OwnerStatement(issued_at=utc_now_iso(), nonce=new_nonce(), **fields), self.owner_private)
        self.journal.append(
            {
                "id": str(uuid.uuid4()),
                "action": fields["action"],
                "payload": bundle.payload,
                "signature": bundle.signature,
                "key_fingerprint": bundle.key_fingerprint,
                "created_at": utc_now_iso(),
            }
        )

    def publish(self, kind: str, body: Any, signed_with: str | None = None) -> None:
        self.entries.append((kind, body.model_dump(mode="json"), signed_with))

    def paper(self, title: str, problem: ProblemKey) -> tuple[str, dict[str, str]]:
        pdf = f"%PDF {title}".encode()
        self.blobs[sha256_hex(pdf)] = pdf
        files = {"paper.pdf": sha256_hex(pdf)}
        paper_id = str(uuid.uuid4())
        manifest = {
            "title": title,
            "abstract": f"An informal result about {problem.title}, submitted to {self.base}.",
            "byline": "Claude (AI model by Anthropic) — run by lab-alice",
            "claims": [{"key": "main", "kind": "informal", "statement": "The problem is settled."}],
            "novelty": {"what_is_new": "A solution."},
            "files": {"pdf": "paper.pdf"},
            "problem_slugs": [problem.slug],
            "settles": [problem.slug],
        }
        self.papers[paper_id] = {
            "manifest": manifest,
            "problems": [{"slug": problem.slug, "title": problem.title, "variant": problem.variant, "upstream": problem.upstream}],
        }
        self.publish(
            "paper_version",
            PaperVersionBody(
                paper_id=paper_id,
                version=1,
                url=f"{self.base}/papers/{paper_id}",
                manifest=manifest,
                files=files,
                problems=[problem],
                submitter=self.submitter,
                agent_name="Claude Fable 5.2 (Claude Code)",
                agent_model="Claude Fable 5.2",
                submitted_at=utc_now_iso(),
            ),
        )
        return paper_id, files

    def review(
        self,
        paper_id: str,
        files: dict[str, str],
        kind: str,
        adversarial: bool = False,
        resolves: list[str] | None = None,
        key: str | None = None,
        task_id: uuid.UUID | None = None,
    ) -> None:
        # the shape of build_materials: the task, and the paper as the reviewer saw it
        task_id = task_id or uuid.uuid4()
        materials = {
            "task": {"kind": kind, "adversarial": adversarial, "id": str(task_id)},
            "paper": {
                "id": paper_id,
                "version": 1,
                "files": [{"path": p, "sha256": s, "size": len(self.blobs[s])} for p, s in files.items()],
                **self.papers[paper_id],
            },
        }
        result = TrustedResult(
            task_id=task_id,
            kind=kind,
            materials_sha256=sha256_hex(canonical_json(materials)),
            prompt_sha256="0" * 64,
            vendor="openai" if adversarial else "anthropic",
            model="claude-test",
            harness="claude-code",
            transcript_sha256="a" * 64,
            client_version="0.1.0",
            review=ReviewBody.model_validate(review_json()),
            novelty_category="new" if kind == "novelty_audit" else None,
            resolves_problem_slugs=resolves or [],
            issued_at=utc_now_iso(),
            nonce=new_nonce(),
        )
        bundle = SignedBundle.create(result, key or self.reviewer_private)
        self.publish(
            "decisive_review",
            DecisiveReviewBody(
                paper_id=paper_id,
                version=1,
                kind=kind,
                adversarial=adversarial,
                reviewer="weekly",
                bundle=bundle.model_dump(mode="json"),
                materials=materials,
            ),
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/proofxiv":
            info = InstanceInfo(
                name=f"Lab {request.url.host}",
                base_url=self.base,
                owner_keys=[self.owner_public],
                federation_key=self.federation_public,
                artifacts=self.artifacts,
                outbox=self.outbox_path,
            )
            return httpx.Response(200, json=info.model_dump(mode="json"))
        if path == "/api/v1/owner/journal":
            return httpx.Response(200, json=self.journal)
        if path == "/api/v1/federation/outbox":
            after = int(request.url.params.get("after", "0"))
            limit = int(request.url.params.get("limit", "100"))
            self.limits.append(limit)
            out = []
            for seq, (kind, body, signed_with) in enumerate(self.entries, start=1):
                if seq > after and len(out) < limit:
                    entry = OutboxEntry(instance=self.base, seq=seq, kind=kind, created_at=utc_now_iso(), body=body)
                    out.append(SignedBundle.create(entry, signed_with or self.federation_private).model_dump(mode="json"))
            page = OutboxPage(entries=out, next_after=after + len(out), last_seq=len(self.entries))
            return httpx.Response(200, json=page.model_dump(mode="json"))
        if path.startswith("/api/v1/artifacts/"):
            blob = self.blobs.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, content=blob) if blob is not None else httpx.Response(404)
        return httpx.Response(404)


@pytest.fixture(autouse=True)
def full_trust():
    """The trust level 'full' is off by default (not hardened enough yet); these tests exercise it."""
    from proofxiv_server.config import get_settings

    get_settings().federation_full_trust = True
    yield
    get_settings().federation_full_trust = False


def client_for(*labs: Lab) -> httpx.AsyncClient:
    by_host = {httpx.URL(lab.base).host: lab for lab in labs}
    def route(request: httpx.Request) -> httpx.Response:
        lab = by_host.get(request.url.host)
        return lab.handle(request) if lab else httpx.Response(503)  # a peer of another test

    return httpx.AsyncClient(transport=httpx.MockTransport(route))


async def _problem(upstream_name: str, title: str) -> tuple[str, ProblemKey]:
    from proofxiv_server.core.problems import create_problem
    from proofxiv_server.db.session import sessionmaker

    async with sessionmaker()() as s:
        problem = await create_problem(
            s,
            ProblemProposal(
                title=title,
                statement_md=f"The statement of {title}.",
                domain="combinatorics",
                sources=[SourceRef(url="https://example.org/" + upstream_name)],
                openness_evidence="Open in the test catalog.",
            ),
            None,
            ProblemStatus.OPEN,
            origin="formal-conjectures",
        )
        problem.upstream = {"name": upstream_name, "category": "research open"}
        await s.commit()
        slug = problem.slug
    # the lab imported the same statement under its own slug
    return slug, ProblemKey(slug=f"lab-{slug}", title=title, upstream={"name": upstream_name})


async def test_outbox_is_signed_and_attested(transport):
    from proofxiv_server.core import federation

    public = federation.init_key()
    owner = await login(transport, "owner")
    await owner.post("/owner/statements", json=owner_statement(action="attest_key", role="federation", public_key=public))
    info = (await httpx.AsyncClient(transport=transport, base_url="http://testserver").get("/.well-known/proofxiv")).json()
    assert info["federation_key"] == public and info["owner_keys"]

    fedora = await agent(transport, await login(transport, "fedora"), "fedora-agent")
    bundle = make_bundle({"paper.json": MANIFEST, "paper.pdf": b"%PDF fedora"})
    paper_id = (await fedora.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}))["paper_id"]
    page = await Api(transport).get("/federation/outbox", params={"after": 0})
    entries = [SignedBundle.model_validate(e) for e in page["entries"]]
    assert all(e.verify(public) for e in entries)
    mine = [OutboxEntry.model_validate(e.payload) for e in entries]
    assert any(e.kind == "paper_version" and e.body["paper_id"] == paper_id and e.body["submitter"] == "fedora" for e in mine)
    journal = await Api(transport).get("/owner/journal")
    assert any(j["action"] == "attest_key" and j["payload"]["public_key"] == public for j in journal)

    # hidden by moderation: followers get a withdrawal instead of the paper (title, files)
    await owner.post("/owner/statements", json=owner_statement(action="hide", target_type="paper", target_id=paper_id, reason="malware"))
    page = await Api(transport).get("/federation/outbox", params={"after": 0})
    about = [OutboxEntry.model_validate(e["payload"]) for e in page["entries"]]
    about = [e for e in about if e.body.get("paper_id") == paper_id]
    assert about and {e.kind for e in about} == {"withdrawal"}
    assert all("manifest" not in e.body for e in about)


async def test_following_labs_by_trust(transport):
    from proofxiv_server.core import federation

    owner = await login(transport, "owner")
    anon = Api(transport)
    full_slug, full_key = await _problem("Lab.full_problem", "Federation full problem")
    review_slug, review_key = await _problem("Lab.review_problem", "Federation review problem")
    index_slug, index_key = await _problem("Lab.index_problem", "Federation index problem")

    trusted, checked, listed, forged = Lab("full.example"), Lab("review.example"), Lab("index.example"), Lab("forged.example")
    paper_id, files = trusted.paper("A solution verified at the trusted lab", full_key)
    for kind, adversarial in (("statement_audit", False), ("novelty_audit", False), ("anchor_review", False), ("anchor_review", True)):
        trusted.review(paper_id, files, kind, adversarial, resolves=[full_key.slug])
    review_paper, review_files = checked.paper("A solution to check here", review_key)
    checked.review(review_paper, review_files, "statement_audit")
    listed.paper("A solution only listed here", index_key)
    forged.paper("A paper signed by a key nobody attested", index_key)
    forged.entries[-1] = (forged.entries[-1][0], forged.entries[-1][1], generate_keypair()[0])

    for lab, trust in ((trusted, "full"), (checked, "review"), (listed, "index"), (forged, "full")):
        await owner.post("/owner/statements", json=owner_statement(action="add_peer", peer_url=lab.base, peer_owner_key=lab.owner_public, trust=trust))
    async with client_for(trusted, checked, listed, forged) as client:
        stats = await federation.pull_due(client=client, force=True)
    assert stats[trusted.base]["applied"] == 5 and stats[checked.base]["applied"] == 2 and stats[listed.base]["applied"] == 1
    assert "not signed by the attested federation key" in stats[forged.base]["error"]

    # full: the lab's signed reviews count here, under our own rules; the problem is solved in the federation
    page = await anon.get(f"/problems/{full_slug}")
    assert page["status"] == "solved_in_federation"
    local = page["federation"][0]["paper_id"]
    paper = await anon.get(f"/papers/{local}")
    assert paper["level"] == "C3" and paper["origin"]["trust"] == "full" and paper["origin"]["lab"] == "Lab full.example"
    assert {r["origin"] for r in paper["reviews"]} == {"trusted"} and len(paper["reviews"]) == 4

    # review: the paper is here and waits for our own checks; the lab's review is only a signal
    page = await anon.get(f"/problems/{review_slug}")
    assert page["status"] == "open"
    paper = await anon.get(f"/papers/{page['federation'][0]['paper_id']}")
    assert paper["level"] != "C3" and paper["origin"]["trust"] == "review"
    assert [r["origin"] for r in paper["reviews"]] == ["peer"]
    assert {t["status"] for t in paper["pending_tasks"]} == {"ready"}

    # index: nothing imported, but the precheck sends agents to the lab's paper
    check = await anon.get(f"/problems/{index_slug}/precheck")
    assert check["verdict"] == "claimed_in_federation"
    assert [f["lab_url"] for f in check["federation"]] == [listed.base] and check["federation"][0]["paper_id"] is None

    # the trusted lab withdraws its paper, then its owner revokes the reviewer key
    trusted.publish("withdrawal", WithdrawalBody(paper_id=paper_id, hidden=True, reason="an error in Lemma 2"))
    async with client_for(trusted, checked, listed, forged) as client:
        await federation.pull_due(client=client, force=True)
    assert (await anon.get(f"/papers/{local}"))["hidden"] is True
    trusted.statement(action="revoke_key", key_fingerprint=SignedBundle.create({}, trusted.reviewer_private).key_fingerprint, revoke_from="2000-01-01T00:00:00+00:00")
    async with client_for(trusted, checked, listed, forged) as client:
        await federation.pull_due(client=client, force=True)
    paper = await anon.get(f"/papers/{local}")
    assert paper["level"] != "C3" and all(r["removed"] for r in paper["reviews"])

    # each lab keeps its own game: authors and reviewers of other labs earn no points here
    board = await anon.get("/leaderboard", params={"period": "all"})
    assert not [row for row in board["rows"] if "@" in row["login"]]

    overview = await anon.get("/federation")
    assert {p["base_url"]: p["trust"] for p in overview["peers"]}[trusted.base] == "full"
    assert {p["base_url"]: p["status"] for p in overview["peers"]}[forged.base] == "error"


async def test_full_trust_imports_machine_checks(transport):
    """A Lean claim from a fully trusted lab: its runner's signed result fills our job, and our own
    acceptance rules decide. The runner's signature must cover the job's files and theorems."""
    from proofxiv_server.core import federation
    from proofxiv_server.core.params import get_param
    from proofxiv_server.db.session import sessionmaker

    owner = await login(transport, "owner")
    async with sessionmaker()() as s:
        image = await get_param(s, "default_lean_image")  # what our job will name; the lab's runner signs it
    lab = Lab("lean.example")
    lean = b"theorem lab_lemma : True := trivial\n"
    other = b"theorem lab_lemma : True := sorry\n"
    pdf = b"%PDF lean lab"
    lab.blobs.update({sha256_hex(lean): lean, sha256_hex(pdf): pdf})
    files = {"Main.lean": sha256_hex(lean), "paper.pdf": sha256_hex(pdf)}

    def paper(title: str, theorem: str, signed_for: bytes | None) -> None:
        paper_id = str(uuid.uuid4())
        manifest = {
            "title": title,
            "abstract": "A machine-checked statement from a lab this instance trusts fully.",
            "byline": "Claude (AI model by Anthropic) — run by lab-bob",
            "claims": [{"key": "main", "kind": "lean", "statement": "True.", "lean_file": "Main.lean", "lean_theorems": [theorem]}],
            "novelty": {"what_is_new": "A lemma."},
            "files": {"pdf": "paper.pdf"},
        }
        lab.publish("paper_version", PaperVersionBody(paper_id=paper_id, version=1, url=f"{lab.base}/papers/{paper_id}", manifest=manifest,
                                                     files=files, submitter="lab-bob", submitted_at=utc_now_iso()))
        job = {"kind": "lean", "inputs": [{"sha256": files["Main.lean"], "path": "Main.lean"}], "lean_theorems": [theorem]}
        digest = None
        if signed_for is not None:
            digest = job_digest("lean", [{"sha256": sha256_hex(signed_for), "path": "Main.lean"}], [theorem], None,
                                ["lean-check", "Main.lean"], image)
        result = RunResult(job_id=uuid.uuid4(), job_sha256=digest, verdict="pass", lean=LeanReport(theorems={"lab_lemma": []}),
                           runner="lab runner", issued_at=utc_now_iso(), nonce=new_nonce())
        lab.publish("run_result", RunResultBody(paper_id=paper_id, version=1, job=job, runner="runner",
                                                bundle=SignedBundle.create(result, lab.runner_private).model_dump(mode="json")))

    paper("Checked at the lab", "lab_lemma", lean)
    paper("A theorem the lab's runner did not check", "missing_lemma", lean)
    paper("A result signed without its job", "lab_lemma", None)
    paper("A result signed for other files", "lab_lemma", other)
    await owner.post("/owner/statements", json=owner_statement(action="add_peer", peer_url=lab.base, peer_owner_key=lab.owner_public, trust="full"))
    async with client_for(lab) as client:
        stats = await federation.pull_due(client=client, force=True)
    assert stats[lab.base]["applied"] == 6
    assert len(stats[lab.base]["skipped"]) == 2 and all("does not cover" in s for s in stats[lab.base]["skipped"])
    papers = {p["title"]: p for p in await Api(transport).get("/papers", params={"limit": 50})}
    level = {}
    for title in papers:
        if title.startswith(("Checked at the lab", "A theorem the lab", "A result signed")):
            level[title] = (await Api(transport).get(f"/papers/{papers[title]['id']}"))["level"]
    assert level == {
        "Checked at the lab": "C2",
        "A theorem the lab's runner did not check": "C0",
        "A result signed without its job": "C1",
        "A result signed for other files": "C1",
    }


async def test_a_review_of_other_files_stops_the_pull(transport):
    """A signed review whose materials list other files than the published version is refused: the
    lab's server cannot pass a review of one paper off as a review of another."""
    from proofxiv_server.core import federation

    owner = await login(transport, "owner")
    _, key = await _problem("Lab.tamper_problem", "Federation tamper problem")
    lab = Lab("tamper.example")
    paper_id, files = lab.paper("A paper whose review is about something else", key)
    other = b"%PDF another paper"
    lab.blobs[sha256_hex(other)] = other
    lab.review(paper_id, {"paper.pdf": sha256_hex(other)}, "statement_audit")
    await owner.post("/owner/statements", json=owner_statement(action="add_peer", peer_url=lab.base, peer_owner_key=lab.owner_public, trust="full"))
    async with client_for(lab) as client:
        stats = await federation.pull_due(client=client, force=True)
    # the paper arrives, the review is skipped with its reason, and the pull goes on
    assert stats[lab.base]["applied"] == 1 and "other files" in stats[lab.base]["skipped"][0]
    peers = {p["base_url"]: p for p in (await Api(transport).get("/federation"))["peers"]}
    assert peers[lab.base]["status"] == "skipped_entries" and "other files" in peers[lab.base]["last_error"]
    async with client_for(lab) as client:
        again = await federation.pull_due(client=client, force=True)
    assert again[lab.base]["applied"] == 0  # not retried: the cursor moved past it


async def test_replays_and_addresses_a_lab_cannot_abuse(transport):
    """A lab's server cannot count one signed review twice, point us at internal addresses, move
    its outbox to another host, or take a login without its @host."""
    from proofxiv_server.core import federation
    from proofxiv_server.db.models import Operator
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import select

    owner = await login(transport, "owner")
    _, key = await _problem("Lab.replay_problem", "Federation replay problem")
    replay = Lab("replay.example")
    replay.submitter = "x" * 64
    paper_id, files = replay.paper("One anchor review, published twice", key)
    for kind in ("statement_audit", "novelty_audit", "anchor_review"):
        replay.review(paper_id, files, kind)
    replay.entries.append(replay.entries[-1])  # the same signed anchor review again

    inside = Lab("inside.example")
    inside.artifacts = "https://127.0.0.1/api/v1/artifacts/{sha256}"
    inside.paper("Files on the internal network", key)
    moved = Lab("moved.example")
    moved.outbox_path = "@evil.example/outbox"

    for lab, trust in ((replay, "full"), (inside, "review"), (moved, "full")):
        await owner.post("/owner/statements", json=owner_statement(action="add_peer", peer_url=lab.base, peer_owner_key=lab.owner_public, trust=trust))
    async with client_for(replay, inside, moved) as client:
        stats = await federation.pull_due(client=client, force=True)
    assert "not public" in stats[inside.base]["error"]
    assert "odd path" in stats[moved.base]["error"]

    papers ={p["title"]: p for p in await Api(transport).get("/papers", params={"limit": 100})}
    paper = await Api(transport).get(f"/papers/{papers['One anchor review, published twice']['id']}")
    assert len(paper["reviews"]) == 3 and paper["level"] != "C3"  # two anchors needed, one counted
    async with sessionmaker()() as s:
        logins = list((await s.execute(select(Operator.login).where(Operator.login.like("%@replay.example")))).scalars())
    assert logins and all(len(name) <= 64 and name.endswith("@replay.example") for name in logins)


async def test_a_lab_cannot_touch_our_keys_moderation_or_count_twice(transport):
    """What the security review found: a lab's owner attesting and revoking one of OUR keys, a lab
    restoring a paper our moderation hid, one task of the lab counted twice, and a trust downgrade
    that must take back what counted on the lab's signatures."""
    from proofxiv_server.core import federation
    from proofxiv_server.db.models import SigningKey
    from proofxiv_server.db.session import sessionmaker
    from sqlalchemy import select

    owner = await login(transport, "owner")
    anon = Api(transport)
    our_private, our_public = generate_keypair()
    ours = await login(transport, "ourreviewer")
    await ours.post("/me/keys", json={"public_key": our_public, "label": "weekly"})

    _, key = await _problem("Lab.attack_problem", "Federation attack problem")
    lab = Lab("attack.example")
    lab.statement(action="attest_key", role="reviewer", public_key=our_public, operator_login="weekly")
    lab.statement(action="revoke_key", key_fingerprint=SignedBundle.create({}, our_private).key_fingerprint, revoke_from="2000-01-01T00:00:00+00:00")
    paper_id, files = lab.paper("A paper with one anchor review published twice", key)
    lab.review(paper_id, files, "statement_audit")
    lab.review(paper_id, files, "novelty_audit")
    anchor = uuid.uuid4()
    lab.review(paper_id, files, "anchor_review", task_id=anchor)
    lab.review(paper_id, files, "anchor_review", task_id=anchor)  # the same task, signed again
    solid_id, solid_files = lab.paper("A paper the lab verified properly", key)
    for kind, adversarial in (("statement_audit", False), ("novelty_audit", False), ("anchor_review", False), ("anchor_review", True)):
        lab.review(solid_id, solid_files, kind, adversarial)
    await owner.post("/owner/statements", json=owner_statement(action="add_peer", peer_url=lab.base, peer_owner_key=lab.owner_public, trust="full"))
    async with client_for(lab) as client:
        await federation.pull_due(client=client, force=True)

    async with sessionmaker()() as s:
        mine = (await s.execute(select(SigningKey).where(SigningKey.public_key == our_public))).scalar_one()
        assert mine.revoked_at is None  # the lab's owner cannot revoke our keys
    papers = {p["title"]: p["id"] for p in await anon.get("/papers", params={"limit": 100})}
    twice = await anon.get(f"/papers/{papers['A paper with one anchor review published twice']}")
    assert len(twice["reviews"]) == 3 and twice["level"] != "C3"
    solid = await anon.get(f"/papers/{papers['A paper the lab verified properly']}")
    assert solid["level"] == "C3"

    # our moderation hides the paper; the lab cannot restore it
    await owner.post("/owner/statements", json=owner_statement(action="hide", target_type="paper", target_id=solid["id"], reason="our decision"))
    lab.publish("withdrawal", WithdrawalBody(paper_id=solid_id, hidden=False))
    async with client_for(lab) as client:
        await federation.pull_due(client=client, force=True)
    assert (await anon.get(f"/papers/{solid['id']}"))["hidden"] is True
    await owner.post("/owner/statements", json=owner_statement(action="unhide", target_type="paper", target_id=solid["id"], reason="restored"))

    # the owner stops trusting the lab fully: its signatures no longer count, our own checks take over
    await owner.post("/owner/statements", json=owner_statement(action="set_peer_trust", peer_url=lab.base, trust="review"))
    after = await anon.get(f"/papers/{solid['id']}")
    assert after["level"] != "C3" and all(r["removed"] for r in after["reviews"])
    assert {t["status"] for t in after["pending_tasks"]} == {"ready"} and len(after["pending_tasks"]) >= 4


async def test_full_trust_is_off_unless_enabled(transport):
    from proofxiv_server.config import get_settings

    get_settings().federation_full_trust = False
    owner = await login(transport, "owner")
    lab = Lab("off.example")
    statement = owner_statement(action="add_peer", peer_url=lab.base, peer_owner_key=lab.owner_public, trust="full")
    refused = await owner.post("/owner/statements", json=statement, expect=422)
    assert "not enabled" in refused["message"]
    await owner.post("/owner/statements", json=owner_statement(action="add_peer", peer_url=lab.base, peer_owner_key=lab.owner_public, trust="review"))
    await owner.post("/owner/statements", json=owner_statement(action="set_peer_trust", peer_url=lab.base, trust="full"), expect=422)


async def test_large_papers_shrink_pages_and_the_feed_can_be_read_again(transport, monkeypatch):
    """A long paper.json (many claims) is legal up to MAX_MANIFEST_BYTES: the follower asks for
    fewer entries at once instead of stopping. Larger ones are refused at submission and skipped
    by followers. A feed read again changes nothing, and raising a lab from index to review
    imports what was only listed."""
    from proofxiv_server.core import federation
    from proofxiv_server.db.session import sessionmaker

    owner = await login(transport, "owner")
    slug, key = await _problem("Lab.large_papers", "Federation large papers problem")
    lab = Lab("large.example")

    def claims(n: int) -> list[dict[str, str]]:
        return [{"key": f"c{i}", "kind": "informal", "statement": "x" * 1000} for i in range(n)]

    for i in range(3):
        lab.paper(f"A paper with many claims {i}", key)
        lab.entries[-1][1]["manifest"]["claims"] = claims(150)  # about 150 KB, like a real one
    lab.paper("A paper.json past the limit", key)
    lab.entries[-1][1]["manifest"]["claims"] = claims(600)
    monkeypatch.setattr(federation, "MAX_JSON_BYTES", 1024 * 1024)  # the four do not fit one page

    await owner.post("/owner/statements", json=owner_statement(action="add_peer", peer_url=lab.base, peer_owner_key=lab.owner_public, trust="index"))
    async with client_for(lab) as client:
        stats = (await federation.pull_due(client=client, force=True))[lab.base]
    assert stats["applied"] == 3 and stats["cursor"] == 4
    assert [s.split(":")[0] for s in stats["skipped"]] == ["entry 4 (paper_version)"]
    assert lab.limits[:3] == [100, 10, 1]
    assert len((await Api(transport).get(f"/problems/{slug}/precheck"))["federation"]) == 3

    # read again: nothing changes; raised to review: the listed papers are imported here
    await owner.post("/owner/statements", json=owner_statement(action="set_peer_trust", peer_url=lab.base, trust="review"))
    async with sessionmaker()() as s:
        assert await federation.rewind(s) >= 1
        await s.commit()
    async with client_for(lab) as client:
        stats = (await federation.pull_due(client=client, force=True))[lab.base]
    assert stats["applied"] == 3 and len(stats["skipped"]) == 1
    imported = [f["paper_id"] for f in (await Api(transport).get(f"/problems/{slug}"))["federation"]]
    assert len(imported) == 3 and all(imported)

    # the same limit at submission: a lab cannot accept a paper its followers would refuse
    fedora = await agent(transport, await login(transport, "fedora-large"), "fedora-large-agent")
    manifest = json.loads(MANIFEST) | {"claims": claims(600)}
    bundle = make_bundle({"paper.json": json.dumps(manifest), "paper.pdf": b"%PDF large"})
    await fedora.post("/papers", files={"bundle": ("p.tar.gz", bundle, "application/gzip")}, expect=413)
